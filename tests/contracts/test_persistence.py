"""Contract of the persistence ports, run against every adapter registered in the conftest.

Every stored project needs an owner the backend accepts, which ``fx_new_owner`` creates. A test that only names a
project that is never stored takes a bare account identifier.
"""

from operator import attrgetter
from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobState, PageKind
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.values import BookDetails, MetadataSuggestion, Renditions, SliceRequest
from tests.helpers.builders import (
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
        """Verify an update stores the new description.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        renamed = evolve(project, details=BookDetails(title='Renamed', authors='A. Author'))
        await uow.projects.update(renamed)
        await uow.commit()
        assert (await (await fx_uow_factory()).projects.get(project.id)).details == renamed.details

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
        """Verify a scan marked ready in a new renditions version reads back so.

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
        ready = evolve(scan, renditions=Renditions(ready=True, version=2))
        await uow.scans.update(ready)
        await uow.commit()
        assert await (await fx_uow_factory()).scans.get(scan.id) == ready

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
        old = make_job(project_id=project.id, state=JobState.RUNNING, minutes=1)
        new = make_job(project_id=project.id, state=JobState.QUEUED, minutes=2)
        done = make_job(project_id=project.id, state=JobState.SUCCEEDED, minutes=3)
        uow = await fx_uow_factory()
        await uow.projects.add(project)
        for job in (old, new, done):
            await uow.jobs.add(job)
        await uow.commit()
        active = await (await fx_uow_factory()).jobs.list_for_project(project.id, {JobState.QUEUED, JobState.RUNNING})
        assert [job.id for job in active] == [new.id, old.id]

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
