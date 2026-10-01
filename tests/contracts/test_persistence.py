"""Contract of the persistence ports, run against every adapter registered in the conftest.

Every stored project needs an owner the backend accepts, which ``fx_new_owner`` creates. A test that only names a
project that is never stored takes a bare account identifier.
"""

from operator import attrgetter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    JobState,
    PageKind,
    PageOrigin,
    RejectionReason,
    Rendition,
    Side,
    Stage,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import SourceId
from bookreviver.domain.values import (
    BookDetails,
    ImportRequest,
    ImportResult,
    MetadataSuggestion,
    PageSize,
    RejectedFile,
    Renditions,
    SliceRequest,
    SourceFile,
)
from tests.helpers.builders import (
    FULL_DETAILS,
    make_job,
    make_page,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, Project
    from bookreviver.ports.persistence import UnitOfWork
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 3
# Included pages, sources and scans of the book _add_book stores
BOOK: tuple[int, int, int] = (PAGE_COUNT - 1, 1, PAGE_COUNT)
EVERY_STATE: frozenset[JobState] = frozenset(JobState)
FINAL_STATES: list[JobState] = [state for state in JobState if state.is_final]
OPERATION_ARG: str = 'operation'
# The one single-entity operation that takes the entity rather than its identifier
UPDATE_OPERATION: str = 'update'
# Order keys whose byte order differs from their order ignoring case, inserted against the book order
UNORDERED_KEYS: list[str] = ['a1', 'a0v', 'a0V', 'a0', 'Zz']


async def _add_book(uow: UnitOfWork, project: Project) -> list[Page]:
    """Add a source of ``PAGE_COUNT`` scans and one page per scan, the last kept out of the book.

    :param uow: Unit of work to add to, in which the project is stored.
    :type uow: UnitOfWork
    :param project: Project of the book.
    :type project: Project
    :returns: The pages in book order.
    :rtype: list[Page]
    """
    source = make_source(project_id=project.id)
    scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
    pages = [make_page(project_id=project.id, order_key=f'a{scan.number}', scan=scan) for scan in scans]
    pages[-1] = evolve(pages[-1], included=False)
    await uow.sources.add(source)
    await uow.scans.add_many(scans)
    await uow.pages.add_many(pages)
    return pages


class TestProjectRepository:
    """Contract of ProjectRepository."""

    async def test_added_project_reads_back_after_commit(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a committed project is visible to a later unit of work, unchanged.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.commit()
        assert await (await fx_uow_factory()).projects.get(project.id) == project

    async def test_rolled_back_project_is_gone(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify rollback discards an uncommitted project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.rollback()
        with pytest.raises(NotFoundError):
            await (await fx_uow_factory()).projects.get(project.id)

    async def test_update_replaces_details(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify an update stores the new description, whose contributors and identifiers come back in their order.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        described = evolve(project, details=FULL_DETAILS)
        await uow.projects.update(described)
        await uow.commit()
        assert (await (await fx_uow_factory()).projects.get(project.id)).details == FULL_DETAILS

    async def test_update_clears_lists_and_height(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an update to empty lists and no height is stored, so a cleared field does not come back.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = evolve(make_project(owner_id=await fx_new_owner()), details=FULL_DETAILS)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        cleared = evolve(project, details=BookDetails(title=FULL_DETAILS.title))
        await uow.projects.update(cleared)
        await uow.commit()
        assert (await (await fx_uow_factory()).projects.get(project.id)).details == BookDetails(
            title=FULL_DETAILS.title
        )

    async def test_adding_a_stored_project_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify adding a project under an identifier already stored is a ConflictError, never a silent overwrite.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.commit()
        duplicate = evolve(project, details=BookDetails(title='Other'))
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError, match=str(project.id)):
            await uow.projects.add(duplicate)

    @pytest.mark.parametrize(OPERATION_ARG, ['get', UPDATE_OPERATION, 'delete'])
    async def test_missing_project_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory, operation: str) -> None:
        """Verify every single-entity operation on an unknown project raises NotFoundError naming the project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param operation: Name of the repository method called with the unknown project.
        :type operation: str
        """
        project = make_project(owner_id=new_account_id())
        repository = (await fx_uow_factory()).projects
        argument = project if operation == UPDATE_OPERATION else project.id
        with pytest.raises(NotFoundError, match=str(project.id)):
            await getattr(repository, operation)(argument)

    async def test_list_for_owner_orders_pages_and_counts(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the owner's projects come newest first, sliced, with the counts of their books, and others are hidden.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        older, newer = make_project(owner_id=owner_id, minutes=1), make_project(owner_id=owner_id, minutes=2)
        uow = await fx_uow_factory()
        for project in (older, newer, make_project(owner_id=await fx_new_owner())):
            await uow.projects.add(project)
        await _add_book(uow, older)
        await uow.commit()
        repository = (await fx_uow_factory()).projects
        full = await repository.list_for_owner(owner_id, SliceRequest())
        second = await repository.list_for_owner(owner_id, SliceRequest(offset=1, limit=1))
        expect([item.project.id for item in full.items] == [newer.id, older.id])
        expect([(item.page_count, item.source_count, item.scan_count) for item in full.items] == [(0, 0, 0), BOOK])
        expect(full.total == 2)
        expect([item.project.id for item in second.items] == [older.id])
        expect(second.total == 2)
        assert_expectations()

    async def test_overview_counts_the_book_of_one_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one project's overview counts its included pages, its sources and its scans, not another's.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
            await _add_book(uow, owned)
        await uow.commit()
        projects = (await fx_uow_factory()).projects
        overview = await projects.overview(project)
        assert (overview.project, overview.page_count, overview.source_count, overview.scan_count) == (project, *BOOK)

    async def test_list_for_owner_breaks_ties_by_identifier(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify projects updated at the same moment list by identifier, so offset paging never skips or repeats one.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        tied = sorted((make_project(owner_id=owner_id, minutes=1) for _ in range(PAGE_COUNT)), key=attrgetter('id'))
        uow = await fx_uow_factory()
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        for project in reversed(tied):
            await uow.projects.add(project)
        await uow.commit()
        repository = (await fx_uow_factory()).projects
        full = await repository.list_for_owner(owner_id, SliceRequest())
        paged = [
            item.project.id
            for offset in range(PAGE_COUNT)
            for item in (await repository.list_for_owner(owner_id, SliceRequest(offset=offset, limit=1))).items
        ]
        expect([item.project.id for item in full.items] == [project.id for project in tied])
        expect(paged == [project.id for project in tied])
        assert_expectations()

    async def test_deleted_cover_page_leaves_the_project_without_a_cover(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting the cover page empties the project's cover, so the list falls back to the first page.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        cover = make_page(project_id=project.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add(cover)
        await uow.projects.update(evolve(project, cover_page_id=cover.id))
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.pages.delete(cover.id)
        await uow.commit()
        assert (await (await fx_uow_factory()).projects.get(project.id)).cover_page_id is None

    async def test_cover_outside_the_project_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a project's cover must be one of its own stored pages, not a missing page or another book's.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        foreign = make_page(project_id=other.id)
        missing = make_page(project_id=project.id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.pages.add(foreign)
        await uow.commit()
        for page in (foreign, missing):
            uow = await fx_uow_factory()
            with pytest.raises(NotFoundError, match=str(page.id)):
                await uow.projects.update(evolve(project, cover_page_id=page.id))
            await uow.rollback()

    async def test_delete_cascades_to_every_row_of_the_book(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a project removes its sources, scans, pages, versions and jobs, and leaves other projects.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        doomed, kept = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        first_pages = {}
        for project in (doomed, kept):
            await uow.projects.add(project)
            first_pages[project.id] = (await _add_book(uow, project))[0]
            await uow.page_versions.add(make_page_version(page_id=first_pages[project.id].id))
            await uow.jobs.add(make_job(project_id=project.id))
        await uow.projects.delete(doomed.id)
        await uow.commit()
        after = await fx_uow_factory()
        counts = {
            project.id: (
                len(await after.sources.list_for_project(project.id)),
                (await after.scans.list_for_project(project.id, SliceRequest())).total,
                (await after.pages.list_for_project(project.id, SliceRequest())).total,
                len(await after.page_versions.list_for_page(first_pages[project.id].id)),
                len(await after.jobs.list_for_project(project.id, EVERY_STATE)),
            )
            for project in (doomed, kept)
        }
        assert counts == {doomed.id: (0, 0, 0, 0, 0), kept.id: (1, PAGE_COUNT, PAGE_COUNT, 1, 1)}


class TestSourceRepository:
    """Contract of SourceRepository."""

    async def test_added_source_reads_back_and_lists_in_import_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify committed sources read back unchanged and list by import time, without other projects' sources.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        job = make_job(project_id=project.id)
        later = make_source(project_id=project.id, name='part2.pdf', minutes=2)
        earlier = evolve(
            make_source(project_id=project.id, name='part1.pdf', minutes=1),
            import_job_id=job.id,
            metadata={'pdf_version': '1.4'},
            suggestion=MetadataSuggestion(title='Book', publication_year='1887'),
        )
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.jobs.add(job)
        await uow.sources.add_many([later, earlier, make_source(project_id=other.id)])
        await uow.commit()
        sources = (await fx_uow_factory()).sources
        expect(await sources.get(earlier.id) == earlier)
        expect(await sources.list_for_project(project.id) == [earlier, later])
        assert_expectations()

    async def test_find_by_sha256_looks_only_inside_the_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a digest finds the project's source with it, and nothing in a project without it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        source = make_source(project_id=project.id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.sources.add(source)
        await uow.commit()
        sources = (await fx_uow_factory()).sources
        expect(await sources.find_by_sha256(project.id, source.sha256) == source)
        expect(await sources.find_by_sha256(other.id, source.sha256) is None)
        assert_expectations()

    async def test_same_file_twice_in_a_project_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a second source with the digest of a stored one is a ConflictError naming the digest.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add(make_source(project_id=project.id))
        await uow.commit()
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError, match=make_source(project_id=project.id).sha256):
            await uow.sources.add(make_source(project_id=project.id))

    async def test_same_file_in_another_project_is_stored(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the digest is unique only within a project, so two books may share a file such as a cover.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        first, second = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for project in (first, second):
            await uow.projects.add(project)
            await uow.sources.add(make_source(project_id=project.id))
        await uow.commit()
        sources = (await fx_uow_factory()).sources
        assert [len(await sources.list_for_project(project.id)) for project in (first, second)] == [1, 1]

    async def test_source_of_a_missing_parent_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a source needs its project and the import job it names, reported by the missing identifier.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        missing_job = make_job(project_id=project.id)
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError, match=str(project.id)):
            await uow.sources.add(make_source(project_id=project.id))
        await uow.rollback()
        await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(missing_job.id)):
            await uow.sources.add(evolve(make_source(project_id=project.id), import_job_id=missing_job.id))

    async def test_deleted_import_job_leaves_its_sources(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a job keeps the sources it imported and empties their import job.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        job = make_job(project_id=project.id)
        source = evolve(make_source(project_id=project.id), import_job_id=job.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.jobs.add(job)
        await uow.sources.add(source)
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.jobs.delete(job.id)
        await uow.commit()
        assert await (await fx_uow_factory()).sources.get(source.id) == evolve(source, import_job_id=None)

    async def test_delete_cascades_to_its_scans_only(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a source removes its scans and leaves the scans of the project's other sources.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        doomed, kept = (
            make_source(project_id=project.id, name='a.pdf'),
            make_source(project_id=project.id, name='b.pdf'),
        )
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add_many([doomed, kept])
        await uow.scans.add_many([make_scan(source=source, number=0) for source in (doomed, kept)])
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.sources.delete(doomed.id)
        await uow.commit()
        scans = (await fx_uow_factory()).scans
        expect(await scans.list_for_source(doomed.id) == [])
        expect(len(await scans.list_for_source(kept.id)) == 1)
        assert_expectations()

    async def test_delete_leaves_the_pages_of_its_scans(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a source keeps the pages cut from its scans, with their versions, without their scan.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        pages = await _add_book(uow, project)
        version = make_page_version(page_id=pages[0].id)
        await uow.page_versions.add(version)
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.sources.delete((await uow.sources.list_for_project(project.id))[0].id)
        await uow.commit()
        after = await fx_uow_factory()
        kept = await after.pages.list_for_project(project.id, SliceRequest())
        expect(list(kept.items) == [evolve(page, scan_id=None) for page in pages])
        expect(await after.page_versions.list_for_page(pages[0].id) == [version])
        assert_expectations()


class TestScanRepository:
    """Contract of ScanRepository."""

    async def test_list_by_ids_returns_the_stored_scans_among_them(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify scans read by identifier come back once each, and an identifier that is not stored is left out.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add(source)
        await uow.scans.add_many(scans)
        await uow.commit()
        repository = (await fx_uow_factory()).scans
        unstored = make_scan(source=source, number=PAGE_COUNT)
        found = await repository.list_by_ids([scans[0].id, scans[2].id, scans[2].id, unstored.id])
        expect(sorted(found, key=attrgetter('number')) == [scans[0], scans[2]])
        expect(await repository.list_by_ids([]) == [])
        assert_expectations()

    async def test_scans_read_back_by_number_within_their_source(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify committed scans read back unchanged and list by their number in the source.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scans = [
            evolve(make_scan(source=source, number=number), source_label=f'{number + 1}')
            for number in reversed(range(PAGE_COUNT))
        ]
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add(source)
        await uow.scans.add_many(scans)
        await uow.commit()
        repository = (await fx_uow_factory()).scans
        expect(await repository.get(scans[0].id) == scans[0])
        expect(await repository.list_for_source(source.id) == list(reversed(scans)))
        assert_expectations()

    async def test_list_for_project_follows_the_import_order_of_sources(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the scans of a project list source by source in import order, sliced, with the full total.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first = make_source(project_id=project.id, name='part1.pdf', minutes=1)
        second = make_source(project_id=project.id, name='part2.pdf', minutes=2)
        expected = [make_scan(source=source, number=number) for source in (first, second) for number in range(2)]
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add_many([second, first])
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        await uow.scans.add_many(list(reversed(expected)))
        await uow.commit()
        scans = (await fx_uow_factory()).scans
        everything = await scans.list_for_project(project.id, SliceRequest())
        window = await scans.list_for_project(project.id, SliceRequest(offset=1, limit=2))
        expect([scan.id for scan in everything.items] == [scan.id for scan in expected])
        expect([scan.id for scan in window.items] == [scan.id for scan in expected[1:3]])
        expect(window.total == len(expected))
        assert_expectations()

    async def test_update_stores_the_renditions_state(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a scan marked ready in a new renditions version and a PNG full image reads back so.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add(source)
        await uow.scans.add(scan)
        ready = evolve(scan, renditions=Renditions(ready=True, version=2, full=Rendition.FULL_PNG))
        await uow.scans.update(ready)
        await uow.commit()
        assert await (await fx_uow_factory()).scans.get(scan.id) == ready

    async def test_list_unready_returns_the_scans_without_ready_renditions_in_import_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the unready scans of a project list source by source and by number, without ready or foreign ones.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        first = make_source(project_id=project.id, name='a.pdf', minutes=1)
        second = make_source(project_id=project.id, name='b.pdf', minutes=2)
        foreign = make_source(project_id=other.id, name='c.pdf', minutes=0)
        ready = Renditions(ready=True)
        scans = [
            make_scan(source=second, number=0),
            make_scan(source=second, number=1),
            evolve(make_scan(source=first, number=0), renditions=ready),
            make_scan(source=first, number=1),
            make_scan(source=first, number=2),
        ]
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.projects.add(other)
        await uow.sources.add_many([second, first, foreign])
        await uow.scans.add_many([*scans, make_scan(source=foreign, number=0)])
        await uow.commit()
        unready = await (await fx_uow_factory()).scans.list_unready(project.id)
        assert [(scan.source_id, scan.number) for scan in unready] == [
            (first.id, 1),
            (first.id, 2),
            (second.id, 0),
            (second.id, 1),
        ]

    async def test_list_unready_is_empty_for_a_project_with_every_scan_ready(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a project whose scans are all ready, or that has none, has nothing left to cut.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        expect(await uow.scans.list_unready(project.id) == [])
        await uow.sources.add(source)
        await uow.scans.add(evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True)))
        await uow.commit()
        expect(await (await fx_uow_factory()).scans.list_unready(project.id) == [])
        assert_expectations()

    async def test_same_number_twice_in_a_source_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify two scans cannot hold the same position of one source.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add(source)
        await uow.scans.add(make_scan(source=source, number=0))
        with pytest.raises(ConflictError, match=str(source.id)):
            await uow.scans.add_many([make_scan(source=source, number=1), make_scan(source=source, number=0)])

    async def test_scan_of_a_missing_source_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a scan cannot be stored for a source that does not exist, so no scan outlives its file.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(source.id)):
            await uow.scans.add(make_scan(source=source, number=0))


class TestPageRepository:
    """Contract of PageRepository."""

    async def test_pages_list_in_the_byte_order_of_their_keys(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify pages list by order key compared byte by byte, sliced, with every page of the book in the total.

        Upper-case letters sort before lower-case ones, and two keys differing only in case are two positions, which
        a case-insensitive collation would get wrong.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many([make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS])
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        everything = await pages.list_for_project(project.id, SliceRequest())
        window = await pages.list_for_project(project.id, SliceRequest(offset=1, limit=2))
        beyond = await pages.list_for_project(project.id, SliceRequest(offset=len(UNORDERED_KEYS), limit=1))
        expect([page.order_key for page in everything.items] == ['Zz', 'a0', 'a0V', 'a0v', 'a1'])
        expect([page.order_key for page in window.items] == ['a0', 'a0V'])
        expect((window.total, list(beyond.items), beyond.total) == (len(UNORDERED_KEYS), [], len(UNORDERED_KEYS)))
        assert_expectations()

    async def test_last_order_key_is_the_greatest_of_the_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the last key is the greatest in byte order, None for an empty book, and blind to other projects.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other, empty = (make_project(owner_id=owner_id) for _ in range(3))
        uow = await fx_uow_factory()
        for owned in (project, other, empty):
            await uow.projects.add(owned)
        await uow.pages.add_many([make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS])
        await uow.pages.add(make_page(project_id=other.id, order_key='b0'))
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        expect(await pages.last_order_key(project.id) == 'a1')
        expect(await pages.last_order_key(empty.id) is None)
        assert_expectations()

    async def test_count_before_is_the_position_in_the_book(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page counts the pages before it in byte order, excluded ones included, and none of another project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = (make_project(owner_id=owner_id) for _ in range(2))
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.projects.add(other)
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        book[0] = evolve(book[0], included=False)
        await uow.pages.add_many([*book, make_page(project_id=other.id, order_key='Zy')])
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        positions = [await pages.count_before(page) for page in book]
        # The keys are stored as a1, a0v, a0V, a0, Zz and sort as Zz, a0, a0V, a0v, a1
        expect(positions == [4, 3, 2, 1, 0])
        assert_expectations()

    async def test_list_for_scan_returns_the_pages_of_that_scan_by_slot(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the halves of a split scan list by slot, without the pages of other scans or of no scan.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        spread, other = make_scan(source=source, number=0), make_scan(source=source, number=1)
        right = evolve(make_page(project_id=project.id, order_key='a1', scan=spread), slot=2)
        left = evolve(make_page(project_id=project.id, order_key='a2', scan=spread), slot=1)
        elsewhere = make_page(project_id=project.id, order_key='a3', scan=other)
        placeholder = make_page(project_id=project.id, order_key='a4')
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add(source)
        await uow.scans.add_many([spread, other])
        await uow.pages.add_many([right, left, elsewhere, placeholder])
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        expect(await pages.list_for_scan(spread.id) == [left, right])
        expect(await pages.list_for_scan(other.id) == [elsewhere])
        assert_expectations()

    async def test_included_only_lists_the_pages_of_the_book_and_counts_them(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a listing of the included pages skips the excluded ones in its window and in its total.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        book = [make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1', 'a2')]
        book[1] = evolve(book[1], included=False)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many(book)
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        everything = await pages.list_for_project(project.id, SliceRequest(), included_only=True)
        window = await pages.list_for_project(project.id, SliceRequest(offset=1, limit=1), included_only=True)
        expect(list(everything.items) == [book[0], book[2]])
        expect((list(window.items), window.total) == ([book[2]], 2))
        assert_expectations()

    async def test_list_by_ids_returns_the_pages_in_book_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify pages named in any order, with repeats, come back once each in the byte order of their keys.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many(book)
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        wanted = [book[0].id, book[2].id, book[4].id, book[2].id]
        # Stored as a1, a0v, a0V, a0, Zz, so these three are a1, a0V and Zz
        assert [page.order_key for page in await pages.list_by_ids(project.id, wanted)] == ['Zz', 'a0V', 'a1']

    async def test_list_by_ids_refuses_a_missing_page_and_a_page_of_another_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one identifier that names no page of the project fails the whole read, naming that identifier.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        own, foreign = make_page(project_id=project.id), make_page(project_id=other.id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.pages.add_many([own, foreign])
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        missing = make_page(project_id=project.id)
        with pytest.raises(NotFoundError, match=str(foreign.id)):
            await pages.list_by_ids(project.id, [own.id, foreign.id])
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await pages.list_by_ids(project.id, [own.id, missing.id])

    async def test_list_for_source_returns_the_pages_of_its_scans_in_book_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pages of one source list in book order, without pages of other sources or without a scan.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first, second = (
            make_source(project_id=project.id, name='a.pdf'),
            make_source(project_id=project.id, name='b.pdf'),
        )
        scans = [
            make_scan(source=first, number=0),
            make_scan(source=second, number=0),
            make_scan(source=first, number=1),
        ]
        late, elsewhere, early = (
            make_page(project_id=project.id, order_key=key, scan=scan)
            for key, scan in zip(('a2', 'a1', 'a0'), scans, strict=True)
        )
        placeholder = make_page(project_id=project.id, order_key='a3')
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add_many([first, second])
        await uow.scans.add_many(scans)
        await uow.pages.add_many([late, elsewhere, early, placeholder])
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        expect(await pages.list_for_source(project.id, first.id) == [early, late])
        expect(await pages.list_for_source(project.id, second.id) == [elsewhere])
        expect(await pages.list_for_source(project.id, SourceId(uuid4())) == [])
        assert_expectations()

    async def test_list_range_returns_the_pages_between_two_keys_inclusive_in_book_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify both ends belong to the range, byte order decides it, a reversed range is empty, other books are out.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.pages.add_many([make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS])
        await uow.pages.add(make_page(project_id=other.id, order_key='a0W'))
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        # The keys sort as Zz, a0, a0V, a0v, a1
        inclusive = await pages.list_range(project.id, 'a0', 'a0v')
        single = await pages.list_range(project.id, 'a0V', 'a0V')
        reversed_range = await pages.list_range(project.id, 'a0v', 'a0')
        expect([page.order_key for page in inclusive] == ['a0', 'a0V', 'a0v'])
        expect([page.order_key for page in single] == ['a0V'])
        expect(list(reversed_range) == [])
        assert_expectations()

    async def test_neighbour_key_is_the_nearest_key_on_a_side_without_the_excluded_pages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the nearest key before or after another, in byte order, skipping pages left out and other projects.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.pages.add_many([*book, make_page(project_id=other.id, order_key='a0W')])
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        # The keys are stored as a1, a0v, a0V, a0, Zz and sort as Zz, a0, a0V, a0v, a1
        by_key = {page.order_key: page.id for page in book}
        expect(await pages.neighbour_key(project.id, 'a0V', Side.BEFORE) == 'a0')
        expect(await pages.neighbour_key(project.id, 'a0V', Side.AFTER) == 'a0v')
        expect(await pages.neighbour_key(project.id, 'a0V', Side.AFTER, excluding=[by_key['a0v']]) == 'a1')
        expect(
            await pages.neighbour_key(project.id, 'a0V', Side.BEFORE, excluding=[by_key['a0'], by_key['Zz']]) is None
        )
        expect(await pages.neighbour_key(project.id, 'Zz', Side.BEFORE) is None)
        expect(await pages.neighbour_key(project.id, 'a1', Side.AFTER) is None)
        assert_expectations()

    async def test_update_many_writes_every_page_in_one_transaction(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify several pages changed together read back changed, and the pages left out stay as they were.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        book = [make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1', 'a2')]
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many(book)
        await uow.commit()
        moved = [evolve(book[0], order_key='a1V'), evolve(book[1], order_key='a1G')]
        uow = await fx_uow_factory()
        await uow.pages.update_many(moved)
        await uow.commit()
        pages = (await fx_uow_factory()).pages
        everything = await pages.list_for_project(project.id, SliceRequest())
        assert list(everything.items) == [moved[1], moved[0], book[2]]

    async def test_update_to_a_key_another_page_holds_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page cannot take another page's position by update, as a move that lost a race would try to.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first, second = (make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1'))
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many([first, second])
        await uow.commit()
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError, match='a1'):
            await uow.pages.update(evolve(first, order_key='a1'))
        await uow.rollback()
        with pytest.raises(ConflictError, match='a1'):
            await uow.pages.update_many([evolve(first, order_key='a1')])

    async def test_update_many_with_a_missing_page_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page that is not stored fails the update, naming its identifier.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        missing = make_page(project_id=project.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await uow.pages.update_many([missing])

    async def test_updated_page_reads_back(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a page moved, numbered, given a kind and kept out of the book reads back so.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add(page)
        await uow.commit()
        changed = evolve(page, order_key='a0V', label='[4]', kind=PageKind.TITLE, included=False, notes='Stamp')
        uow = await fx_uow_factory()
        await uow.pages.update(changed)
        await uow.commit()
        assert await (await fx_uow_factory()).pages.get(page.id) == changed

    async def test_same_order_key_twice_in_a_project_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify two pages of one book cannot share a position, while another book may use the same key.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
            await uow.pages.add(make_page(project_id=owned.id, order_key='a5'))
        with pytest.raises(ConflictError, match='a5'):
            await uow.pages.add(make_page(project_id=project.id, order_key='a5'))

    async def test_same_part_of_a_scan_twice_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one slot of a scan becomes one page at most, while any number of placeholders have no scan.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.sources.add(source)
        await uow.scans.add(scan)
        await uow.pages.add_many([make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1')])
        await uow.pages.add(make_page(project_id=project.id, order_key='a2', scan=scan))
        with pytest.raises(ConflictError, match=str(scan.id)):
            await uow.pages.add(make_page(project_id=project.id, order_key='a3', scan=scan))

    async def test_page_of_a_missing_parent_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page needs its project and the scan it names, reported by the missing identifier.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        missing_scan = make_scan(source=make_source(project_id=project.id), number=0)
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError, match=str(project.id)):
            await uow.pages.add(make_page(project_id=project.id))
        await uow.rollback()
        await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(missing_scan.id)):
            await uow.pages.add(make_page(project_id=project.id, scan=missing_scan))

    async def test_delete_cascades_to_its_versions(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a page removes its versions and leaves the versions of other pages.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        doomed, kept = (make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1'))
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many([doomed, kept])
        await uow.page_versions.add_many([make_page_version(page_id=page.id) for page in (doomed, kept)])
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.pages.delete(doomed.id)
        await uow.commit()
        versions = (await fx_uow_factory()).page_versions
        expect(await versions.list_for_page(doomed.id) == [])
        expect(len(await versions.list_for_page(kept.id)) == 1)
        assert_expectations()

    async def test_missing_page_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify reading a page that does not exist raises NotFoundError naming it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        page = make_page(project_id=make_project(owner_id=new_account_id()).id)
        with pytest.raises(NotFoundError, match=str(page.id)):
            await (await fx_uow_factory()).pages.get(page.id)


class TestPageVersionRepository:
    """Contract of PageVersionRepository."""

    async def test_list_to_prepare_returns_pending_and_failed_versions_of_the_stages_of_the_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the versions still to write come back earliest first, and nothing else does.

        Ready and running versions, versions of other stages and versions of another project are left out.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        pages = [make_page(project_id=project.id, order_key=f'a{number}') for number in range(5)]
        elsewhere = make_page(project_id=other.id)
        pending = make_page_version(page_id=pages[0].id, minutes=3)
        failed = evolve(
            make_page_version(page_id=pages[1].id, minutes=1), stage=Stage.PAGE_ORDER, state=VersionState.FAILED
        )
        ready = evolve(make_page_version(page_id=pages[2].id), state=VersionState.READY)
        running = evolve(make_page_version(page_id=pages[3].id), state=VersionState.RUNNING)
        later_stage = evolve(make_page_version(page_id=pages[4].id), stage=Stage.GEOMETRY)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.pages.add_many([*pages, elsewhere])
        await uow.page_versions.add_many(
            [pending, failed, ready, running, later_stage, make_page_version(page_id=elsewhere.id)]
        )
        await uow.commit()
        versions = (await fx_uow_factory()).page_versions
        found = await versions.list_to_prepare(project.id, {Stage.PAGE_SPLIT, Stage.PAGE_ORDER})
        expect(list(found) == [failed, pending])
        expect(list(await versions.list_to_prepare(project.id, {Stage.PAGE_ORDER})) == [failed])
        assert_expectations()

    async def test_base_sizes_are_those_of_the_base_versions_of_the_included_scan_pages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify only the recorded sizes of base versions of included pages cut from a scan are returned.

        A page kept out, a generated leaf, a later version, a version recording no size and another project's page take
        no part.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        sources = [make_source(project_id=owned.id) for owned in (project, other)]
        scans = [make_scan(source=sources[0], number=number) for number in range(5)]
        foreign_scan = make_scan(source=sources[1], number=0)
        pages = [
            make_page(project_id=project.id, order_key=f'a{number}', scan=scan) for number, scan in enumerate(scans)
        ]
        pages[1] = evolve(pages[1], included=False)
        leaf = evolve(make_page(project_id=project.id, order_key='b0'), origin=PageOrigin.BLANK)
        foreign_page = make_page(project_id=other.id, scan=foreign_scan)
        sized = {'width_px': 2000, 'height_px': 3000, 'dpi': 300.0}
        # The sizes of the pages in order: counted, kept out, generated, recording none, recording no resolution, foreign
        recorded: list[dict[str, Any]] = [sized, sized, sized, {}, {'width_px': 10, 'height_px': 20}, sized]
        versions = [
            evolve(make_page_version(page_id=page.id), data=data)
            for page, data in zip([*pages[:2], leaf, *pages[3:], foreign_page], recorded, strict=True)
        ]
        derived = evolve(make_page_version(page_id=pages[2].id, minutes=1), input_id=versions[0].id, data=sized)
        uow = await fx_uow_factory()
        for owned in (project, other):
            await uow.projects.add(owned)
        await uow.sources.add_many(sources)
        await uow.scans.add_many([*scans, foreign_scan])
        await uow.pages.add_many([*pages, leaf, foreign_page])
        await uow.page_versions.add_many(versions)
        await uow.page_versions.add(derived)
        await uow.commit()
        found = await (await fx_uow_factory()).page_versions.base_sizes(project.id)
        assert sorted(found, key=attrgetter('width_px')) == [
            PageSize(width_px=10, height_px=20, dpi=None),
            PageSize(width_px=2000, height_px=3000, dpi=300.0),
        ]

    async def test_versions_read_back_in_creation_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page's versions read back unchanged, the earliest first, without other pages' versions.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page, other = (make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1'))
        base = make_page_version(page_id=page.id, minutes=1)
        later = evolve(make_page_version(page_id=page.id, minutes=2), input_id=base.id, renditions=None)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many([page, other])
        await uow.page_versions.add_many([base, later, make_page_version(page_id=other.id)])
        await uow.commit()
        versions = (await fx_uow_factory()).page_versions
        expect(await versions.get(later.id) == later)
        expect(await versions.list_for_page(page.id) == [base, later])
        assert_expectations()

    async def test_base_versions_of_several_pages_read_in_one_call(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify only the versions without an input come back, for the pages asked for, the earliest first.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first, second, unasked = (make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1', 'a2'))
        first_base = make_page_version(page_id=first.id, minutes=2)
        second_base = make_page_version(page_id=second.id, minutes=1)
        derived = evolve(make_page_version(page_id=first.id, minutes=3), input_id=first_base.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add_many([first, second, unasked])
        await uow.page_versions.add_many([first_base, second_base, derived, make_page_version(page_id=unasked.id)])
        await uow.commit()
        versions = (await fx_uow_factory()).page_versions
        expect(await versions.list_base_versions([first.id, second.id]) == [second_base, first_base])
        expect(await versions.list_base_versions([]) == [])
        assert_expectations()

    async def test_version_of_a_missing_parent_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a version needs its page and the input version it names, reported by the missing identifier.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        missing_input = make_page_version(page_id=page.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(page.id)):
            await uow.page_versions.add(make_page_version(page_id=page.id))
        await uow.rollback()
        await uow.projects.add(project)
        await uow.pages.add(page)
        with pytest.raises(NotFoundError, match=missing_input.id):
            await uow.page_versions.add(evolve(make_page_version(page_id=page.id), input_id=missing_input.id))

    async def test_deleted_input_leaves_the_versions_it_fed(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a version keeps the versions computed from it and empties their input.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        base = make_page_version(page_id=page.id)
        later = evolve(make_page_version(page_id=page.id, minutes=1), input_id=base.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.pages.add(page)
        await uow.page_versions.add_many([base, later])
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.page_versions.delete(base.id)
        await uow.commit()
        assert await (await fx_uow_factory()).page_versions.list_for_page(page.id) == [evolve(later, input_id=None)]


class TestJobRepository:
    """Contract of JobRepository."""

    async def test_list_filters_by_state_newest_first(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify only jobs in the asked states are listed, newest first.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        old = make_job(project_id=project.id, state=JobState.FAILED, minutes=1)
        new = make_job(project_id=project.id, state=JobState.QUEUED, minutes=2)
        done = make_job(project_id=project.id, state=JobState.SUCCEEDED, minutes=3)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        for job in (old, new, done):
            await uow.jobs.add(job)
        await uow.commit()
        listed = await (await fx_uow_factory()).jobs.list_for_project(project.id, {JobState.QUEUED, JobState.FAILED})
        assert [job.id for job in listed] == [new.id, old.id]

    @pytest.mark.parametrize('first_state', sorted(JobState.active()), ids=str)
    async def test_second_active_import_of_a_project_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory, first_state: JobState
    ) -> None:
        """Verify a project stores one queued or running import, so two uploads that raced cannot both be kept.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param first_state: Active state of the import already stored.
        :type first_state: JobState
        """
        project = make_project(owner_id=await fx_new_owner())
        setup = await fx_uow_factory()
        await setup.projects.add(project)
        await setup.jobs.add(make_job(project_id=project.id, state=first_state))
        await setup.commit()
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError):
            await uow.jobs.add(make_job(project_id=project.id, state=JobState.QUEUED))

    @pytest.mark.parametrize('finished_state', FINAL_STATES, ids=str)
    async def test_finished_import_leaves_room_for_the_next_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory, finished_state: JobState
    ) -> None:
        """Verify a finished import, whatever its final state, does not keep the project from importing again.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param finished_state: Final state of the earlier imports.
        :type finished_state: JobState
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        # Two finished imports share the project, and an active one joins them
        await uow.jobs.add(make_job(project_id=project.id, state=finished_state, minutes=1))
        await uow.jobs.add(make_job(project_id=project.id, state=finished_state, minutes=2))
        await uow.jobs.add(make_job(project_id=project.id, state=JobState.QUEUED, minutes=3))
        await uow.commit()
        active = await (await fx_uow_factory()).jobs.list_for_project(project.id, JobState.active())
        assert len(active) == 1

    async def test_active_imports_of_different_projects_are_stored(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the one-import rule is per project, so two books import at the same time.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        first, second = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        for project in (first, second):
            await uow.projects.add(project)
            await uow.jobs.add(make_job(project_id=project.id, state=JobState.RUNNING))
        await uow.commit()
        for project in (first, second):
            assert len(await (await fx_uow_factory()).jobs.list_for_project(project.id, JobState.active())) == 1

    async def test_import_request_and_result_read_back(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the files an import was asked for, and what it did with them, read back as they were stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        request = ImportRequest(files=[SourceFile(name='book.pdf', size_bytes=4096, sha256='0' * 64)])
        result = ImportResult(
            imported=[SourceId(uuid4())],
            rejected=[
                RejectedFile(file_name='again.pdf', reason=RejectionReason.DUPLICATE, detail='Same as book.pdf.')
            ],
            skipped=['later.pdf'],
        )
        job = evolve(make_job(project_id=project.id, state=JobState.SUCCEEDED), request=request, result=result)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.jobs.add(job)
        await uow.commit()
        stored = await (await fx_uow_factory()).jobs.get(job.id)
        expect(stored == job)
        expect(stored.request is not None and list(stored.request.files) == list(request.files))
        assert_expectations()

    async def test_job_without_a_request_or_result_reads_back_without_them(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a job that carries no request or result, such as a queued one, reads back with neither.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        job = make_job(project_id=project.id)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.jobs.add(job)
        await uow.commit()
        stored = await (await fx_uow_factory()).jobs.get(job.id)
        assert (stored.request, stored.result) == (None, None)

    async def test_job_of_a_missing_project_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a job cannot be stored for a project that does not exist, so no job outlives its book.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        project_id = make_project(owner_id=new_account_id()).id
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError, match=str(project_id)):
            await uow.jobs.add(make_job(project_id=project_id))

    async def test_guarded_update_replaces_job_in_expected_state(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a guarded write replaces a job whose state is expected, and the change reads back after commit.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        job = make_job(project_id=project.id, state=JobState.RUNNING)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        await uow.jobs.add(job)
        await uow.commit()
        succeeded = evolve(job, state=JobState.SUCCEEDED)
        expect(await uow.jobs.update_if_state(succeeded, expected=JobState.active()) == succeeded)
        await uow.commit()
        expect(await (await fx_uow_factory()).jobs.get(job.id) == succeeded)
        assert_expectations()

    async def test_guarded_update_judges_state_committed_meanwhile(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a guarded write judges the state another unit committed after this one read the job, and keeps it.

        This is a cancellation racing a worker: the canceller reads the job running, the worker commits it as
        succeeded, and the cancellation must then leave the job succeeded.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        job = make_job(project_id=project.id, state=JobState.RUNNING)
        setup = await fx_uow_factory()
        await setup.projects.add(project)
        await setup.jobs.add(job)
        await setup.commit()
        canceller = await fx_uow_factory()
        seen = await canceller.jobs.get(job.id)
        worker = await fx_uow_factory()
        await worker.jobs.update(evolve(job, state=JobState.SUCCEEDED))
        await worker.commit()
        cancelled = evolve(seen, state=JobState.CANCELLED)
        expect(await canceller.jobs.update_if_state(cancelled, expected=JobState.active()) is None)
        await canceller.commit()
        expect((await (await fx_uow_factory()).jobs.get(job.id)).state is JobState.SUCCEEDED)
        assert_expectations()

    async def test_guarded_update_of_missing_job_raises_not_found(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify a guarded write of a job that is not stored reports it missing rather than in another state.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        job = make_job(project_id=make_project(owner_id=new_account_id()).id)
        uow = await fx_uow_factory()
        with pytest.raises(NotFoundError, match=str(job.id)):
            await uow.jobs.update_if_state(job, expected=JobState.active())


class TestUnitOfWork:
    """Contract of UnitOfWork isolation between concurrent units."""

    async def test_concurrent_commits_keep_each_others_changes(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a commit publishes only its own changes and never reverts another unit's commit.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        first, second = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        # Both units start before either commits, as two overlapping requests do
        early = await fx_uow_factory()
        late = await fx_uow_factory()
        await late.projects.add(second)
        await late.commit()
        await early.projects.add(first)
        await early.commit()
        listed = await (await fx_uow_factory()).projects.list_for_owner(owner_id, SliceRequest())
        assert {item.project.id for item in listed.items} == {first.id, second.id}
