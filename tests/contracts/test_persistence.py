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
    BlankFill,
    ContentType,
    JobKind,
    JobState,
    PageKind,
    PageOrigin,
    RecipeKind,
    RejectionReason,
    Rendition,
    Side,
    Stage,
    VersionState,
)
from bookreviver.domain.errors import ConcurrentChangeError, ConflictError, NotFoundError
from bookreviver.domain.ids import PageVersionId, SourceId
from bookreviver.domain.values import (
    BookDetails,
    ImportRequest,
    ImportResult,
    MetadataSuggestion,
    PageSize,
    ProcessorRef,
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
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, Project
    from bookreviver.ports.persistence import UnitOfWork
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 3
# Two finished jobs and a queued one of the same kind in one project
FINISHED_AND_QUEUED_JOBS: int = 3
# Updates a page takes in a test, which raise its revision from zero
PAGE_UPDATES: int = 2
# A run, a job writing page images and an import of one project, which do not exclude each other
THREE_JOBS: int = 3
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
    """Add a source of ``PAGE_COUNT`` scans and one page per scan, the last kept out of the book, in one block.

    The block is ``change_book`` of the project, so the project must be stored already and no block may be open.

    :param uow: Unit of work to add to, with no block open, in which the project is stored.
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
    async with uow.change_book(project.id):
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
        async with uow.change():
            await uow.projects.add(project)
        assert await (await fx_uow_factory()).projects.get(project.id) == project

    async def test_update_replaces_details(self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify an update stores the new description, whose contributors and identifiers come back in their order.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.projects.add(project)
            described = evolve(project, details=FULL_DETAILS)
            await uow.projects.update(described)
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
        async with uow.change():
            await uow.projects.add(project)
            cleared = evolve(project, details=BookDetails(title=FULL_DETAILS.title))
            await uow.projects.update(cleared)
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
        async with uow.change():
            await uow.projects.add(project)
        duplicate = evolve(project, details=BookDetails(title='Other'))
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError, match=str(project.id)):
            async with uow.change():
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
        uow = await fx_uow_factory()
        argument = project if operation == UPDATE_OPERATION else project.id
        # The block is ``change``, because the project is missing and ``change_book`` would raise on entry
        with pytest.raises(NotFoundError, match=str(project.id)):
            async with uow.change():
                await getattr(uow.projects, operation)(argument)

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
        async with uow.change():
            for project in (older, newer, make_project(owner_id=await fx_new_owner())):
                await uow.projects.add(project)
        await _add_book(uow, older)
        repository = (await fx_uow_factory()).projects
        full = await repository.list_for_owner(owner_id, SliceRequest())
        second = await repository.list_for_owner(owner_id, SliceRequest(offset=1, limit=1))
        expect([item.project.id for item in full.items] == [newer.id, older.id])
        expect([(item.page_count, item.source_count, item.scan_count) for item in full.items] == [(0, 0, 0), BOOK])
        expect([item.image_page_count for item in full.items] == [0, PAGE_COUNT])
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
        async with uow.change():
            for owned in (project, other):
                await uow.projects.add(owned)
        for owned in (project, other):
            await _add_book(uow, owned)
        projects = (await fx_uow_factory()).projects
        overview = await projects.overview(project)
        assert (overview.project, overview.page_count, overview.source_count, overview.scan_count) == (project, *BOOK)

    async def test_overview_counts_the_pages_with_an_image_kept_out_of_the_book_but_not_placeholders(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pages a run goes over are every page with an image, kept out of the book or not, no placeholder.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.projects.add(project)
        await _add_book(uow, project)
        async with uow.change_book(project.id):
            await uow.pages.add(make_page(project_id=project.id, order_key='b0'))
        projects = (await fx_uow_factory()).projects
        overview = await projects.overview(project)
        listed = (await projects.list_for_owner(project.owner_id, SliceRequest())).items[0]
        expect((overview.page_count, overview.image_page_count) == (BOOK[0] + 1, PAGE_COUNT))
        expect(listed.image_page_count == PAGE_COUNT)
        assert_expectations()

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
        async with uow.change():
            for project in reversed(tied):
                await uow.projects.add(project)
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
        await store_project(uow, project, cover)
        async with uow.change():
            await uow.projects.update(evolve(project, cover_page_id=cover.id))
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            await uow.pages.delete(cover.id)
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
        await store_project(uow, project)
        await store_project(uow, other, foreign)
        for page in (foreign, missing):
            uow = await fx_uow_factory()
            with pytest.raises(NotFoundError, match=str(page.id)):
                async with uow.change():
                    await uow.projects.update(evolve(project, cover_page_id=page.id))

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
            async with uow.change():
                await uow.projects.add(project)
            first_pages[project.id] = (await _add_book(uow, project))[0]
            async with uow.change_book(project.id):
                await uow.page_versions.add(make_page_version(page_id=first_pages[project.id].id))
            async with uow.change():
                await uow.jobs.add(make_job(project_id=project.id))
        async with uow.change():
            await uow.projects.delete(doomed.id)
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
        async with uow.change():
            for owned in (project, other):
                await uow.projects.add(owned)
            await uow.jobs.add(job)
        async with uow.change_book(project.id):
            await uow.sources.add_many([later, earlier])
        async with uow.change_book(other.id):
            await uow.sources.add(make_source(project_id=other.id))
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
        await store_project(uow, project, sources=[source])
        await store_project(uow, other)
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
        await store_project(uow, project, sources=[make_source(project_id=project.id)])
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError, match=make_source(project_id=project.id).sha256):
            async with uow.change_book(project.id):
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
            await store_project(uow, project, sources=[make_source(project_id=project.id)])
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
        # The block is ``change``, because the project is missing and ``change_book`` would raise on entry
        with pytest.raises(NotFoundError, match=str(project.id)):
            async with uow.change():
                await uow.sources.add(make_source(project_id=project.id))
        async with uow.change():
            await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(missing_job.id)):
            async with uow.change_book(project.id):
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
        async with uow.change():
            await uow.projects.add(project)
            await uow.jobs.add(job)
        async with uow.change_book(project.id):
            await uow.sources.add(source)
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.jobs.delete(job.id)
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
        await store_project(
            uow,
            project,
            sources=[doomed, kept],
            scans=[make_scan(source=source, number=0) for source in (doomed, kept)],
        )
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            await uow.sources.delete(doomed.id)
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
        async with uow.change():
            await uow.projects.add(project)
        pages = await _add_book(uow, project)
        version = make_page_version(page_id=pages[0].id)
        async with uow.change_book(project.id):
            await uow.page_versions.add(version)
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            await uow.sources.delete((await uow.sources.list_for_project(project.id))[0].id)
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
        await store_project(uow, project, sources=[source], scans=scans)
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
        await store_project(uow, project, sources=[source], scans=scans)
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
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        await store_project(uow, project, sources=[second, first], scans=list(reversed(expected)))
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
        await store_project(uow, project, sources=[source])
        ready = evolve(scan, renditions=Renditions(ready=True, version=2, full=Rendition.FULL_PNG))
        async with uow.change_book(project.id):
            await uow.scans.add(scan)
            await uow.scans.update(ready)
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
        await store_project(uow, project, sources=[second, first], scans=scans)
        await store_project(uow, other, sources=[foreign], scans=[make_scan(source=foreign, number=0)])
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
        async with uow.change():
            await uow.projects.add(project)
        expect(await uow.scans.list_unready(project.id) == [])
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))
        async with uow.change_book(project.id):
            await uow.sources.add(source)
            await uow.scans.add(scan)
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
        await store_project(uow, project, sources=[source], scans=[make_scan(source=source, number=0)])
        with pytest.raises(ConflictError, match=str(source.id)):
            async with uow.change_book(project.id):
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
        await store_project(uow, project)
        with pytest.raises(NotFoundError, match=str(source.id)):
            async with uow.change_book(project.id):
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
        await store_project(uow, project, *[make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS])
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
        await store_project(uow, project, *[make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS])
        await store_project(uow, other, make_page(project_id=other.id, order_key='b0'))
        await store_project(uow, empty)
        pages = (await fx_uow_factory()).pages
        expect(await pages.last_order_key(project.id) == 'a1')
        expect(await pages.last_order_key(empty.id) is None)
        assert_expectations()

    async def test_kind_tally_counts_the_pages_with_an_image_of_each_kind(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify each page counts under the kind it is processed by, and a placeholder and another book do not count.

        The kind is the blank kind for a blank page, and otherwise what the page shows: what the user set, else what the
        program found, else a plate counts as a picture in colour.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        source = make_source(project_id=project.id)
        scans = [make_scan(source=source, number=number) for number in range(5)]
        text, plate, blank, found, by_hand = (
            make_page(project_id=project.id, order_key=f'a{number}', scan=scan, kind=kind)
            for number, (scan, kind) in enumerate(
                zip(scans, (PageKind.TEXT, PageKind.PLATE, PageKind.BLANK, PageKind.TEXT, PageKind.PLATE), strict=True)
            )
        )
        found = evolve(found, content_type=ContentType.BW_PICTURE)
        by_hand = evolve(by_hand, content_type=ContentType.TEXT, content_by_hand=True)
        uow = await fx_uow_factory()
        await store_project(
            uow,
            project,
            text,
            plate,
            blank,
            found,
            by_hand,
            make_page(project_id=project.id, order_key='b0'),
            sources=[source],
            scans=scans,
        )
        await store_project(uow, other, make_page(project_id=other.id, order_key='a0'))
        tally = await (await fx_uow_factory()).pages.kind_tally(project.id)
        assert dict(tally) == {
            RecipeKind.TEXT: 2,
            RecipeKind.COLOR_PICTURE: 1,
            RecipeKind.BW_PICTURE: 1,
            RecipeKind.BLANK: 1,
        }

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
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        book[0] = evolve(book[0], included=False)
        await store_project(uow, project, *book)
        await store_project(uow, other, make_page(project_id=other.id, order_key='Zy'))
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
        await store_project(uow, project, right, left, elsewhere, placeholder, sources=[source], scans=[spread, other])
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
        await store_project(uow, project, *book)
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
        await store_project(uow, project, *book)
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
        await store_project(uow, project, own)
        await store_project(uow, other, foreign)
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
        await store_project(uow, project, late, elsewhere, early, placeholder, sources=[first, second], scans=scans)
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
        await store_project(uow, project, *[make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS])
        await store_project(uow, other, make_page(project_id=other.id, order_key='a0W'))
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
        await store_project(uow, project, *book)
        await store_project(uow, other, make_page(project_id=other.id, order_key='a0W'))
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
        await store_project(uow, project, *book)
        moved = [evolve(book[0], order_key='a1V'), evolve(book[1], order_key='a1G')]
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            await uow.pages.update_many(moved)
        pages = (await fx_uow_factory()).pages
        everything = await pages.list_for_project(project.id, SliceRequest())
        assert list(everything.items) == [evolve(page, revision=1) for page in (moved[1], moved[0])] + [book[2]]

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
        await store_project(uow, project, first, second)
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError, match='a1'):
            async with uow.change_book(project.id):
                await uow.pages.update(evolve(first, order_key='a1'))
        with pytest.raises(ConflictError, match='a1'):
            async with uow.change_book(project.id):
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
        await store_project(uow, project)
        with pytest.raises(NotFoundError, match=str(missing.id)):
            async with uow.change_book(project.id):
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
        await store_project(uow, project, page)
        changed = evolve(page, order_key='a0V', label='[4]', kind=PageKind.TITLE, included=False, notes='Stamp')
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            await uow.pages.update(changed)
        assert await (await fx_uow_factory()).pages.get(page.id) == evolve(changed, revision=1)

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
            await store_project(uow, owned, make_page(project_id=owned.id, order_key='a5'))
        with pytest.raises(ConflictError, match='a5'):
            async with uow.change_book(project.id):
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
        await store_project(
            uow,
            project,
            *[make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1')],
            make_page(project_id=project.id, order_key='a2', scan=scan),
            sources=[source],
            scans=[scan],
        )
        with pytest.raises(ConflictError, match=str(scan.id)):
            async with uow.change_book(project.id):
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
        # The block is ``change``, because the project is missing and ``change_book`` would raise on entry
        with pytest.raises(NotFoundError, match=str(project.id)):
            async with uow.change():
                await uow.pages.add(make_page(project_id=project.id))
        async with uow.change():
            await uow.projects.add(project)
        with pytest.raises(NotFoundError, match=str(missing_scan.id)):
            async with uow.change_book(project.id):
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
        await store_project(
            uow, project, doomed, kept, versions=[make_page_version(page_id=page.id) for page in (doomed, kept)]
        )
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            await uow.pages.delete(doomed.id)
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


class TestPageRevision:
    """Contract of the revision of a page, which refuses a write over a change the writer never read."""

    async def test_update_raises_the_revision_of_the_page_by_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a new page starts at revision zero and every update raises its revision by one.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        uow = await fx_uow_factory()
        await store_project(uow, project, page)
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            first = await uow.pages.update(evolve(await uow.pages.get(page.id), label='1'))
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            second = await uow.pages.update(evolve(await uow.pages.get(page.id), label='2'))
        expect(page.revision == 0)
        expect(first.revision == 1)
        expect(second.revision == PAGE_UPDATES)
        expect((await (await fx_uow_factory()).pages.get(page.id)).revision == PAGE_UPDATES)
        assert_expectations()

    async def test_update_over_a_page_another_transaction_changed_raises_concurrent_change(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a write of a page read before another transaction changed it is refused and writes nothing.

        Both transactions change a different field, so the refusal is the revision's and no unique key's. The stored
        page keeps the change of the transaction that committed first, and the refused one can read it again and write
        over it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        uow = await fx_uow_factory()
        await store_project(uow, project, page)
        slow = await fx_uow_factory()
        read = await slow.pages.get(page.id)
        fast = await fx_uow_factory()
        async with fast.change_book(project.id):
            await fast.pages.update(evolve(await fast.pages.get(page.id), label='12'))

        with pytest.raises(ConcurrentChangeError):
            async with slow.change_book(project.id):
                await slow.pages.update(evolve(read, notes='Stamp'))

        stored = await (await fx_uow_factory()).pages.get(page.id)
        expect((stored.label, stored.notes) == ('12', ''))
        async with slow.change_book(project.id):
            await slow.pages.update(evolve(await slow.pages.get(page.id), notes='Stamp'))
        stored = await (await fx_uow_factory()).pages.get(page.id)
        expect((stored.label, stored.notes) == ('12', 'Stamp'))
        assert_expectations()

    async def test_update_many_over_a_page_another_transaction_changed_writes_none(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a batch holding one page that another transaction changed meanwhile writes none of its pages.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first, second = (make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1'))
        uow = await fx_uow_factory()
        await store_project(uow, project, first, second)
        slow = await fx_uow_factory()
        read = await slow.pages.list_by_ids(project.id, [first.id, second.id])
        fast = await fx_uow_factory()
        async with fast.change_book(project.id):
            await fast.pages.update(evolve(await fast.pages.get(second.id), notes='Stamp'))

        with pytest.raises(ConcurrentChangeError):
            async with slow.change_book(project.id):
                await slow.pages.update_many([evolve(page, label='9') for page in read])

        stored = (await (await fx_uow_factory()).pages.list_for_project(project.id, SliceRequest())).items
        assert [(page.label, page.notes) for page in stored] == [('', ''), ('', 'Stamp')]


class TestPageVersionRepository:
    """Contract of PageVersionRepository."""

    async def test_list_to_prepare_returns_pending_and_failed_versions_of_the_given_processors(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the versions still to write come back earliest first, and nothing else does.

        Ready and running versions, versions of other processors, such as the half of a split spread that failed, and
        versions of another project are left out.

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
            make_page_version(page_id=pages[1].id, minutes=1),
            stage=Stage.PAGE_ORDER,
            processor=ProcessorRef(key='pages.blank', version='1'),
            state=VersionState.FAILED,
        )
        ready = evolve(make_page_version(page_id=pages[2].id), state=VersionState.READY)
        running = evolve(make_page_version(page_id=pages[3].id), state=VersionState.RUNNING)
        spread_half = evolve(
            make_page_version(page_id=pages[4].id),
            processor=ProcessorRef(key='split.spread', version='1'),
            state=VersionState.FAILED,
        )
        uow = await fx_uow_factory()
        await store_project(uow, project, *pages, versions=[pending, failed, ready, running, spread_half])
        await store_project(uow, other, elsewhere, versions=[make_page_version(page_id=elsewhere.id)])
        versions = (await fx_uow_factory()).page_versions
        found = await versions.list_to_prepare(project.id, {'split.none', 'pages.blank'})
        expect(list(found) == [failed, pending])
        expect(list(await versions.list_to_prepare(project.id, {'pages.blank'})) == [failed])
        assert_expectations()

    async def test_base_sizes_are_those_of_the_base_versions_of_the_included_scan_pages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify only the recorded sizes of base versions of included pages cut from a scan are returned.

        A page kept out, a generated leaf, a later version, a leaf drawn in place of a scan, a version recording no size
        and another project's page take no part.

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
        # A later version of a page, and a leaf of the page order in place of the scan of a page that is counted, whose
        # size must not be counted twice
        derived_pair = [
            evolve(make_page_version(page_id=pages[2].id, minutes=1), input_id=versions[0].id, data=sized),
            evolve(
                make_page_version(page_id=pages[4].id, minutes=2),
                id=PageVersionId('0123456789abcdef'),
                stage=Stage.PAGE_ORDER,
                processor=ProcessorRef(key='pages.blank', version='1'),
                data={'width_px': 7, 'height_px': 7},
            ),
        ]
        uow = await fx_uow_factory()
        await store_project(
            uow,
            project,
            *pages,
            leaf,
            sources=[sources[0]],
            scans=scans,
            versions=[*versions[:-1], *derived_pair],
        )
        await store_project(
            uow, other, foreign_page, sources=[sources[1]], scans=[foreign_scan], versions=[versions[-1]]
        )
        found = await (await fx_uow_factory()).page_versions.base_sizes(project.id)
        assert sorted(found, key=attrgetter('width_px')) == [
            PageSize(width_px=10, height_px=20, dpi=None),
            PageSize(width_px=2000, height_px=3000, dpi=300.0),
        ]

    async def test_the_choice_of_a_leaf_survives_the_store_and_leaves_the_scan_linked(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a blank page reads back with the leaf chosen for it and its scan, and with the scan again after.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        page = make_page(project_id=project.id, scan=scan, kind=PageKind.BLANK)
        uow = await fx_uow_factory()
        await store_project(uow, project, page, sources=[source], scans=[scan])
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            stored = await uow.pages.get(page.id)
            await uow.pages.update(evolve(stored, blank_fill=BlankFill.PAPER))

        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            leaf = await uow.pages.get(page.id)
            await uow.pages.update(evolve(leaf, blank_fill=BlankFill.SCAN))

        expect(stored.blank_fill is BlankFill.SCAN)
        expect((leaf.blank_fill, leaf.scan_id) == (BlankFill.PAPER, scan.id))
        expect((await (await fx_uow_factory()).pages.get(page.id)).blank_fill is BlankFill.SCAN)
        assert_expectations()

    async def test_the_content_type_of_a_page_survives_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page reads back with no content type, then with one found, then with one set by hand.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        uow = await fx_uow_factory()
        await store_project(uow, project, page)
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            added = await uow.pages.get(page.id)
            await uow.pages.update(evolve(added, content_type=ContentType.BW_PICTURE))
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            found = await uow.pages.get(page.id)
            await uow.pages.update(evolve(found, content_type=ContentType.COLOR_PICTURE, content_by_hand=True))

        by_hand = await (await fx_uow_factory()).pages.get(page.id)
        expect((added.content_type, added.content_by_hand) == (None, False))
        expect((found.content_type, found.content_by_hand) == (ContentType.BW_PICTURE, False))
        expect((by_hand.content_type, by_hand.content_by_hand) == (ContentType.COLOR_PICTURE, True))
        assert_expectations()

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
        await store_project(uow, project, page, other, versions=[base, later, make_page_version(page_id=other.id)])
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
        await store_project(
            uow,
            project,
            first,
            second,
            unasked,
            versions=[first_base, second_base, derived, make_page_version(page_id=unasked.id)],
        )
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
        await store_project(uow, project)
        with pytest.raises(NotFoundError, match=str(page.id)):
            async with uow.change_book(project.id):
                await uow.page_versions.add(make_page_version(page_id=page.id))
        async with uow.change_book(project.id):
            await uow.pages.add(page)
        with pytest.raises(NotFoundError, match=missing_input.id):
            async with uow.change_book(project.id):
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
        await store_project(uow, project, page, versions=[base, later])
        uow = await fx_uow_factory()
        async with uow.change_book(project.id):
            await uow.page_versions.delete(base.id)
        assert await (await fx_uow_factory()).page_versions.list_for_page(page.id) == [evolve(later, input_id=None)]

    async def test_update_of_a_version_deleted_with_its_page_by_another_transaction_raises_not_found(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a transaction that read a version cannot store it again once another committed its page's deletion.

        A job writing the files of a page does exactly this when the page is deleted meanwhile, and the commit of the
        update must not bring the version of a deleted page back.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        version = make_page_version(page_id=page.id)
        uow = await fx_uow_factory()
        await store_project(uow, project, page, versions=[version])
        reading = await fx_uow_factory()
        [read] = await reading.page_versions.list_for_page(page.id)
        deleting = await fx_uow_factory()
        async with deleting.change_book(project.id):
            await deleting.pages.delete(page.id)

        with pytest.raises(NotFoundError, match=version.id):
            async with reading.change_book(project.id):
                await reading.page_versions.update(evolve(read, state=VersionState.READY))

        assert await (await fx_uow_factory()).page_versions.list_for_page(page.id) == []


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
        async with uow.change():
            await uow.projects.add(project)
            for job in (old, new, done):
                await uow.jobs.add(job)
        listed = await (await fx_uow_factory()).jobs.list_for_project(project.id, {JobState.QUEUED, JobState.FAILED})
        assert [job.id for job in listed] == [new.id, old.id]

    async def test_list_for_projects_reads_the_jobs_of_the_given_projects_in_the_given_states(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one query returns the matching jobs of several projects, newest first, and none of another project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        first, second, other = (make_project(owner_id=owner_id) for _ in range(3))
        wanted = [
            make_job(project_id=first.id, state=JobState.RUNNING, minutes=1),
            make_job(project_id=second.id, state=JobState.QUEUED, minutes=3),
        ]
        ignored = [
            make_job(project_id=first.id, state=JobState.SUCCEEDED, minutes=2),
            make_job(project_id=other.id, state=JobState.RUNNING, minutes=4),
        ]
        uow = await fx_uow_factory()
        async with uow.change():
            for project in (first, second, other):
                await uow.projects.add(project)
            for job in (*wanted, *ignored):
                await uow.jobs.add(job)
        jobs = (await fx_uow_factory()).jobs
        listed = await jobs.list_for_projects({first.id, second.id}, JobState.active())
        expect([job.id for job in listed] == [wanted[1].id, wanted[0].id])
        expect(await jobs.list_for_projects(set(), JobState.active()) == [])
        assert_expectations()

    @pytest.mark.parametrize('first_state', sorted(JobState.active()), ids=str)
    async def test_second_active_prepare_job_of_a_project_raises_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory, first_state: JobState
    ) -> None:
        """Verify a project stores one queued or running job writing page images, whichever state the first is in.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param first_state: Active state of the job already stored.
        :type first_state: JobState
        """
        project = make_project(owner_id=await fx_new_owner())
        setup = await fx_uow_factory()
        async with setup.change():
            await setup.projects.add(project)
            await setup.jobs.add(evolve(make_job(project_id=project.id, state=first_state), kind=JobKind.PREPARE_PAGES))
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError):
            async with uow.change():
                await uow.jobs.add(evolve(make_job(project_id=project.id), kind=JobKind.PREPARE_PAGES))

    @pytest.mark.parametrize('finished_state', FINAL_STATES, ids=str)
    async def test_finished_prepare_job_leaves_room_for_the_next_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory, finished_state: JobState
    ) -> None:
        """Verify a finished job writing page images, whatever its final state, does not keep the next from queueing.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param finished_state: Final state of the earlier jobs.
        :type finished_state: JobState
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.projects.add(project)
            for minutes in (1, 2):
                finished = make_job(project_id=project.id, state=finished_state, minutes=minutes)
                await uow.jobs.add(evolve(finished, kind=JobKind.PREPARE_PAGES))
            await uow.jobs.add(evolve(make_job(project_id=project.id, minutes=3), kind=JobKind.PREPARE_PAGES))
        listed = await (await fx_uow_factory()).jobs.list_for_project(project.id, EVERY_STATE)
        assert len(listed) == FINISHED_AND_QUEUED_JOBS

    async def test_an_active_import_and_an_active_prepare_job_of_a_project_do_not_conflict(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the two kinds of job are limited apart, so images are written while a project imports.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.projects.add(project)
            await uow.jobs.add(make_job(project_id=project.id, state=JobState.RUNNING))
            await uow.jobs.add(evolve(make_job(project_id=project.id), kind=JobKind.PREPARE_PAGES))
        kinds = [job.kind for job in await (await fx_uow_factory()).jobs.list_for_project(project.id, EVERY_STATE)]
        assert sorted(kinds) == sorted([JobKind.IMPORT_SOURCE, JobKind.PREPARE_PAGES])

    @pytest.mark.parametrize(
        ('first', 'second'),
        [(first, second) for first in sorted(JobKind.processing()) for second in sorted(JobKind.processing())],
        ids=str,
    )
    async def test_a_project_stores_one_active_job_that_processes_its_versions(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory, first: JobKind, second: JobKind
    ) -> None:
        """Verify a project has one run, preview or measure and one tile cutting or collection, each queued or running.

        A job of the other group is stored beside the running one, and waits as queued.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param first: Kind of the job already stored.
        :type first: JobKind
        :param second: Kind of the job that is queued after it.
        :type second: JobKind
        """
        project = make_project(owner_id=await fx_new_owner())
        setup = await fx_uow_factory()
        async with setup.change():
            await setup.projects.add(project)
            await setup.jobs.add(evolve(make_job(project_id=project.id, state=JobState.RUNNING), kind=first))
        uow = await fx_uow_factory()
        queued = evolve(make_job(project_id=project.id), kind=second)
        if (first in JobKind.requested()) == (second in JobKind.requested()):
            with pytest.raises(ConflictError):
                async with uow.change():
                    await uow.jobs.add(queued)
        else:
            async with uow.change():
                await uow.jobs.add(queued)

    @pytest.mark.parametrize(
        ('first', 'second'),
        [(JobKind.RUN_STAGE, JobKind.CUT_TILES), (JobKind.COLLECT_VERSIONS, JobKind.MEASURE_BOOK)],
        ids=str,
    )
    async def test_a_project_never_has_two_processing_jobs_running(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory, first: JobKind, second: JobKind
    ) -> None:
        """Verify a request and a housekeeping job of one project are not stored as running together.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param first: Kind of the job already running.
        :type first: JobKind
        :param second: Kind of the job of the other group that is refused as running.
        :type second: JobKind
        """
        project = make_project(owner_id=await fx_new_owner())
        setup = await fx_uow_factory()
        async with setup.change():
            await setup.projects.add(project)
            await setup.jobs.add(evolve(make_job(project_id=project.id, state=JobState.RUNNING), kind=first))
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError):
            async with uow.change():
                await uow.jobs.add(evolve(make_job(project_id=project.id, state=JobState.RUNNING), kind=second))

    async def test_processing_jobs_leave_room_for_other_jobs_of_the_project_and_for_other_projects(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an active run does not keep an import or a prepare job from queueing, nor another project's run.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = await fx_uow_factory()
        async with uow.change():
            for owned in (project, other):
                await uow.projects.add(owned)
            await uow.jobs.add(evolve(make_job(project_id=project.id), kind=JobKind.RUN_STAGE))
            await uow.jobs.add(evolve(make_job(project_id=project.id), kind=JobKind.PREPARE_PAGES))
            await uow.jobs.add(make_job(project_id=project.id))
            await uow.jobs.add(evolve(make_job(project_id=other.id), kind=JobKind.RUN_STAGE))
        listed = await (await fx_uow_factory()).jobs.list_for_project(project.id, EVERY_STATE)
        assert len(listed) == THREE_JOBS

    @pytest.mark.parametrize('finished_state', FINAL_STATES, ids=str)
    async def test_finished_processing_job_leaves_room_for_the_next_one(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory, finished_state: JobState
    ) -> None:
        """Verify a finished job that processed versions, whatever its final state, does not keep the next from queueing.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param finished_state: Final state of the earlier jobs.
        :type finished_state: JobState
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.projects.add(project)
            for minutes in (1, 2):
                finished = make_job(project_id=project.id, state=finished_state, minutes=minutes)
                await uow.jobs.add(evolve(finished, kind=JobKind.RUN_STAGE))
            await uow.jobs.add(evolve(make_job(project_id=project.id, minutes=3), kind=JobKind.COLLECT_VERSIONS))
        listed = await (await fx_uow_factory()).jobs.list_for_project(project.id, EVERY_STATE)
        assert len(listed) == FINISHED_AND_QUEUED_JOBS

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
        async with setup.change():
            await setup.projects.add(project)
            await setup.jobs.add(make_job(project_id=project.id, state=first_state))
        uow = await fx_uow_factory()
        with pytest.raises(ConflictError):
            async with uow.change():
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
        async with uow.change():
            await uow.projects.add(project)
            # Two finished imports share the project, and an active one joins them
            await uow.jobs.add(make_job(project_id=project.id, state=finished_state, minutes=1))
            await uow.jobs.add(make_job(project_id=project.id, state=finished_state, minutes=2))
            await uow.jobs.add(make_job(project_id=project.id, state=JobState.QUEUED, minutes=3))
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
        async with uow.change():
            for project in (first, second):
                await uow.projects.add(project)
                await uow.jobs.add(make_job(project_id=project.id, state=JobState.RUNNING))
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
        async with uow.change():
            await uow.projects.add(project)
            await uow.jobs.add(job)
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
        async with uow.change():
            await uow.projects.add(project)
            await uow.jobs.add(job)
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
            async with uow.change():
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
        async with uow.change():
            await uow.projects.add(project)
            await uow.jobs.add(job)
        succeeded = evolve(job, state=JobState.SUCCEEDED)
        async with uow.change():
            expect(await uow.jobs.update_if_state(succeeded, expected=JobState.active()) == succeeded)
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
        async with setup.change():
            await setup.projects.add(project)
            await setup.jobs.add(job)
        canceller = await fx_uow_factory()
        seen = await canceller.jobs.get(job.id)
        worker = await fx_uow_factory()
        async with worker.change():
            await worker.jobs.update(evolve(job, state=JobState.SUCCEEDED))
        cancelled = evolve(seen, state=JobState.CANCELLED)
        async with canceller.change():
            expect(await canceller.jobs.update_if_state(cancelled, expected=JobState.active()) is None)
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
            async with uow.change():
                await uow.jobs.update_if_state(job, expected=JobState.active())
