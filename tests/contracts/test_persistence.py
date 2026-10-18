"""Contract of the persistence ports, run against the SQL adapter, the application's own.

Every stored project needs an owner the database accepts, which ``fx_new_owner`` creates. A test that only names a
project that is never stored takes a bare account identifier.
"""

from functools import partial
from operator import attrgetter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    BlankFill,
    ContentType,
    FileType,
    PageKind,
    PageOrigin,
    RecipeKind,
    RejectionReason,
    Rendition,
    Side,
    SourceKind,
    SourceStatus,
    Stage,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import PageVersionId, SourceId
from bookreviver.domain.values import (
    BookDetails,
    MetadataSuggestion,
    PageSize,
    ProcessorRef,
    Renditions,
    SliceRequest,
)
from tests.helpers.builders import (
    FULL_DETAILS,
    make_page,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Page, Project, Source
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import Repositories, UnitOfWork
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 3
# Included pages, sources and scans of the book _add_book stores
BOOK: tuple[int, int, int] = (PAGE_COUNT - 1, 1, PAGE_COUNT)
OPERATION_ARG: str = 'operation'
# The one single-entity operation that takes the entity rather than its identifier
UPDATE_OPERATION: str = 'update'
# Order keys, of which the ones that differ only in case sort apart in byte order
KEY_ZZ: str = 'Zz'
KEY_A0: str = 'a0'
KEY_A0_UPPER: str = 'a0V'
KEY_A0_LOWER: str = 'a0v'
KEY_A0_W: str = 'a0W'
KEY_A1: str = 'a1'
KEY_A2: str = 'a2'
KEY_A3: str = 'a3'
KEY_A5: str = 'a5'
KEY_B0: str = 'b0'
# Order keys whose byte order differs from their order ignoring case, inserted against the book order
UNORDERED_KEYS: list[str] = [KEY_A1, KEY_A0_LOWER, KEY_A0_UPPER, KEY_A0, KEY_ZZ]
# The label a test gives to the pages it changes
PAGE_LABEL: str = '[4]'
# File names of the sources of the tests
FIRST_PDF: str = 'a.pdf'
SECOND_PDF: str = 'b.pdf'
FIRST_PART_PDF: str = 'part1.pdf'
SECOND_PART_PDF: str = 'part2.pdf'
NOTES_TXT: str = 'notes.txt'
# The text for the reader of a file that the import rejected
REJECTION_DETAIL: str = 'The project already has this file as book.pdf.'
# Keys of the recorded size of a version, and the key of the processor that draws a blank leaf
HEIGHT_PX: str = 'height_px'
WIDTH_PX: str = 'width_px'
BLANK_PROCESSOR_KEY: str = 'pages.blank'


async def _add_book(uow: UnitOfWork, project: Project) -> list[Page]:
    """Add a source of ``PAGE_COUNT`` scans and one page per scan, the last kept out of the book, in one change.

    The change is ``change_book`` of the project, so the project must be stored already.

    :param uow: Unit of work to add with, in which the project is stored.
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

    def add_rows(repos: Repositories, _project: Project) -> None:
        """Add the source, its scans and their pages.

        :param repos: Repositories of the transaction.
        :type repos: Repositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        repos.sources.add(source)
        repos.scans.add_many(scans)
        repos.pages.add_many(pages)

    await uow.change_book(project.id, add_rows)
    return pages


async def _add_projects(uow: UnitOfWork, *, projects: Sequence[Project]) -> None:
    """Add projects in one change outside the content of a book.

    :param uow: Unit of work to add with.
    :type uow: UnitOfWork
    :param projects: Projects to add.
    :type projects: Sequence[Project]
    """

    def add_rows(repos: Repositories) -> None:
        """Add the projects.

        :param repos: Repositories of the transaction.
        :type repos: Repositories
        """
        for project in projects:
            repos.projects.add(project)

    await uow.change(add_rows)


def _unimported(project: Project, *, status: SourceStatus, name: str, minutes: int = 0, typed: bool = True) -> Source:
    """Build a rejected or a skipped file of a project, which holds no scan and whose digest follows from its name.

    :param project: Project owning the file.
    :type project: Project
    :param status: Rejected or skipped.
    :type status: SourceStatus
    :param name: Name of the uploaded file; files of different names have different digests.
    :type name: str
    :param minutes: Minutes after the epoch the file was recorded.
    :type minutes: int
    :param typed: Whether the type of the file is known; a rejected file of an unknown type is an unsupported one.
    :type typed: bool
    :returns: A source that is not imported, with a reason and a detail when it is rejected.
    :rtype: Source
    """
    rejected = status is SourceStatus.REJECTED
    return evolve(
        make_source(project_id=project.id, name=name, minutes=minutes),
        kind=SourceKind.PDF if typed else None,
        file_type=FileType.PDF if typed else None,
        scan_count=0,
        status=status,
        rejection=(RejectionReason.DUPLICATE if typed else RejectionReason.UNSUPPORTED_TYPE) if rejected else None,
        detail=REJECTION_DETAIL if rejected else '',
    )


class TestProjectRepository:
    """Contract of ProjectRepository."""

    async def test_added_project_reads_back_after_commit(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a committed project is visible to a later unit of work, unchanged.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        await fx_uow().change(lambda repos: repos.projects.add(project))
        assert await fx_uow().read(lambda repos: repos.projects.get(project.id)) == project

    async def test_update_replaces_details(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify an update stores the new description, whose contributors and identifiers come back in their order.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        described = evolve(project, details=FULL_DETAILS)

        def add_and_describe(repos: Repositories) -> None:
            """Add the project and replace its details in the same change.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            """
            repos.projects.add(project)
            repos.projects.update(described)

        await fx_uow().change(add_and_describe)
        stored = await fx_uow().read(lambda repos: repos.projects.get(project.id))
        assert stored.details == FULL_DETAILS

    async def test_update_clears_lists_and_height(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify an update to empty lists and no height is stored, so a cleared field does not come back.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = evolve(make_project(owner_id=await fx_new_owner()), details=FULL_DETAILS)
        cleared = evolve(project, details=BookDetails(title=FULL_DETAILS.title))

        def add_and_clear(repos: Repositories) -> None:
            """Add the project and replace its details with empty ones in the same change.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            """
            repos.projects.add(project)
            repos.projects.update(cleared)

        await fx_uow().change(add_and_clear)
        stored = await fx_uow().read(lambda repos: repos.projects.get(project.id))
        assert stored.details == BookDetails(title=FULL_DETAILS.title)

    async def test_adding_a_stored_project_raises_conflict(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify adding a project under an identifier already stored is a ConflictError, never a silent overwrite.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        await fx_uow().change(lambda repos: repos.projects.add(project))
        duplicate = evolve(project, details=BookDetails(title='Other'))
        with pytest.raises(ConflictError, match=str(project.id)):
            await fx_uow().change(lambda repos: repos.projects.add(duplicate))

    @pytest.mark.parametrize(OPERATION_ARG, ['get', UPDATE_OPERATION, 'delete'])
    async def test_missing_project_raises_not_found(self, fx_uow: UnitOfWorkFactory, operation: str) -> None:
        """Verify every single-entity operation on an unknown project raises NotFoundError naming the project.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param operation: Name of the repository method called with the unknown project.
        :type operation: str
        """
        project = make_project(owner_id=new_account_id())
        argument = project if operation == UPDATE_OPERATION else project.id
        # The call is ``change``, because the project is missing and ``change_book`` would raise on entry
        with pytest.raises(NotFoundError, match=str(project.id)):
            await fx_uow().change(lambda repos: getattr(repos.projects, operation)(argument))

    async def test_list_for_owner_orders_pages_and_counts(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the owner's projects come newest first, sliced, with the counts of their books, and others are hidden.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id, other_owner_id = await fx_new_owner(), await fx_new_owner()
        older, newer = make_project(owner_id=owner_id, minutes=1), make_project(owner_id=owner_id, minutes=2)
        uow = fx_uow()
        # The owners are stored before the change, because storing one is a write of its own that waits for the change
        await _add_projects(uow, projects=[older, newer, make_project(owner_id=other_owner_id)])
        await _add_book(uow, older)
        reader = fx_uow()
        full = await reader.read(lambda repos: repos.projects.list_for_owner(owner_id, SliceRequest()))
        second = await reader.read(
            lambda repos: repos.projects.list_for_owner(owner_id, SliceRequest(offset=1, limit=1))
        )
        expect([item.project.id for item in full.items] == [newer.id, older.id])
        expect([(item.page_count, item.source_count, item.scan_count) for item in full.items] == [(0, 0, 0), BOOK])
        expect([item.image_page_count for item in full.items] == [0, PAGE_COUNT])
        expect(full.total == 2)
        expect([item.project.id for item in second.items] == [older.id])
        expect(second.total == 2)
        assert_expectations()

    async def test_overview_counts_the_book_of_one_project(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one project's overview counts its included pages, its sources and its scans, not another's.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = fx_uow()
        await _add_projects(uow, projects=[project, other])
        for owned in (project, other):
            await _add_book(uow, owned)
        overview = await fx_uow().read(lambda repos: repos.projects.overview(project))
        assert (overview.project, overview.page_count, overview.source_count, overview.scan_count) == (project, *BOOK)

    async def test_overview_counts_the_pages_with_an_image_kept_out_of_the_book_but_not_placeholders(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pages a run goes over are every page with an image, kept out of the book or not, no placeholder.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = fx_uow()
        await _add_projects(uow, projects=[project])
        await _add_book(uow, project)
        placeholder = make_page(project_id=project.id, order_key=KEY_B0)
        await uow.change_book(project.id, lambda repos, _project: repos.pages.add(placeholder))
        reader = fx_uow()
        overview = await reader.read(lambda repos: repos.projects.overview(project))
        listed = (
            await reader.read(lambda repos: repos.projects.list_for_owner(project.owner_id, SliceRequest()))
        ).items[0]
        expect((overview.page_count, overview.image_page_count) == (BOOK[0] + 1, PAGE_COUNT))
        expect(listed.image_page_count == PAGE_COUNT)
        assert_expectations()

    async def test_list_for_owner_breaks_ties_by_identifier(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify projects updated at the same moment list by identifier, so offset paging never skips or repeats one.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        tied = sorted((make_project(owner_id=owner_id, minutes=1) for _ in range(PAGE_COUNT)), key=attrgetter('id'))
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        await _add_projects(fx_uow(), projects=list(reversed(tied)))

        def list_whole_and_by_page(repos: Repositories) -> tuple[list[ProjectId], list[ProjectId]]:
            """List the owner's projects in one window and then one project at a time.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The identifiers of the whole listing, and of the pages of one project each, joined.
            :rtype: tuple[list[ProjectId], list[ProjectId]]
            """
            whole = [item.project.id for item in repos.projects.list_for_owner(owner_id, SliceRequest()).items]
            paged = [
                item.project.id
                for offset in range(PAGE_COUNT)
                for item in repos.projects.list_for_owner(owner_id, SliceRequest(offset=offset, limit=1)).items
            ]
            return whole, paged

        whole, paged = await fx_uow().read(list_whole_and_by_page)
        expect(whole == [project.id for project in tied])
        expect(paged == [project.id for project in tied])
        assert_expectations()

    async def test_deleted_cover_page_leaves_the_project_without_a_cover(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting the cover page empties the project's cover, so the list falls back to the first page.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        cover = make_page(project_id=project.id)
        await store_project(fx_uow(), project, cover)
        await fx_uow().change(lambda repos: repos.projects.update(evolve(project, cover_page_id=cover.id)))
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.delete(cover.id))
        stored = await fx_uow().read(lambda repos: repos.projects.get(project.id))
        assert stored.cover_page_id is None

    async def test_cover_outside_the_project_raises_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a project's cover must be one of its own stored pages, not a missing page or another book's.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        foreign = make_page(project_id=other.id)
        missing = make_page(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project)
        await store_project(uow, other, foreign)

        async def refuse_cover(page: Page) -> None:
            """Take the page as the cover of the project, which the repository refuses.

            :param page: Page that is not a stored page of the project.
            :type page: Page
            """
            with pytest.raises(NotFoundError, match=str(page.id)):
                await fx_uow().change(lambda repos: repos.projects.update(evolve(project, cover_page_id=page.id)))

        await refuse_cover(foreign)
        await refuse_cover(missing)

    async def test_delete_cascades_to_every_row_of_the_book(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a project removes its sources, scans, pages and versions, and leaves other projects.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        doomed, kept = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = fx_uow()

        async def seed(project: Project) -> Page:
            """Store the project with a book and a version of the first page of the book.

            :param project: Project to store.
            :type project: Project
            :returns: The first page of the book.
            :rtype: Page
            """
            await _add_projects(uow, projects=[project])
            first = (await _add_book(uow, project))[0]
            version = make_page_version(page_id=first.id)
            await uow.change_book(project.id, lambda repos, _project: repos.page_versions.add(version))
            return first

        first_pages = {project.id: await seed(project) for project in (doomed, kept)}
        await uow.change(lambda repos: repos.projects.delete(doomed.id))

        def count_rows(repos: Repositories) -> dict[ProjectId, tuple[int, ...]]:
            """Count the rows of the book of each project.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: By project, the number of its sources, scans, pages and versions of its first page.
            :rtype: dict[ProjectId, tuple[int, ...]]
            """
            return {
                project.id: (
                    len(repos.sources.list_for_project(project.id)),
                    repos.scans.list_for_project(project.id, SliceRequest()).total,
                    repos.pages.list_for_project(project.id, SliceRequest()).total,
                    len(repos.page_versions.list_for_page(first_pages[project.id].id)),
                )
                for project in (doomed, kept)
            }

        counts = await fx_uow().read(count_rows)
        assert counts == {doomed.id: (0, 0, 0, 0), kept.id: (1, PAGE_COUNT, PAGE_COUNT, 1)}


class TestSourceRepository:
    """Contract of SourceRepository."""

    async def test_added_source_reads_back_and_lists_in_import_order(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify committed sources read back unchanged and list by import time, without other projects' sources.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        later = make_source(project_id=project.id, name=SECOND_PART_PDF, minutes=2)
        earlier = evolve(
            make_source(project_id=project.id, name=FIRST_PART_PDF, minutes=1),
            metadata={'pdf_version': '1.4'},
            suggestion=MetadataSuggestion(title='Book', publication_year='1887'),
        )
        foreign = make_source(project_id=other.id)
        uow = fx_uow()
        await _add_projects(uow, projects=[project, other])
        await uow.change_book(project.id, lambda repos, _project: repos.sources.add_many([later, earlier]))
        await uow.change_book(other.id, lambda repos, _project: repos.sources.add(foreign))
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.sources.get(earlier.id)) == earlier)
        expect(await reader.read(lambda repos: repos.sources.list_for_project(project.id)) == [earlier, later])
        assert_expectations()

    async def test_find_by_sha256_looks_only_inside_the_project(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a digest finds the project's source with it, and nothing in a project without it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project, sources=[source])
        await store_project(uow, other)
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.sources.find_by_sha256(project.id, source.sha256)) == source)
        expect(await reader.read(lambda repos: repos.sources.find_by_sha256(other.id, source.sha256)) is None)
        assert_expectations()

    async def test_same_file_twice_in_a_project_raises_conflict(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a second source with the digest of a stored one is a ConflictError naming the digest.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        await store_project(fx_uow(), project, sources=[make_source(project_id=project.id)])
        duplicate = make_source(project_id=project.id)
        with pytest.raises(ConflictError, match=duplicate.sha256):
            await fx_uow().change_book(project.id, lambda repos, _project: repos.sources.add(duplicate))

    async def test_same_file_in_another_project_is_stored(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the digest is unique only within a project, so two books may share a file such as a cover.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        first, second = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = fx_uow()
        for project in (first, second):
            await store_project(uow, project, sources=[make_source(project_id=project.id)])

        def count_sources(repos: Repositories) -> list[int]:
            """Count the sources of each project.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The number of sources of the first project and of the second.
            :rtype: list[int]
            """
            return [len(repos.sources.list_for_project(project.id)) for project in (first, second)]

        assert await fx_uow().read(count_sources) == [1, 1]

    async def test_source_of_a_missing_project_raises_not_found(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify a source needs its project, reported by the missing identifier.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        project_id = make_project(owner_id=new_account_id()).id
        orphan = make_source(project_id=project_id)
        # The call is ``change``, because the project is missing and ``change_book`` would raise on entry
        with pytest.raises(NotFoundError, match=str(project_id)):
            await fx_uow().change(lambda repos: repos.sources.add(orphan))

    async def test_delete_cascades_to_its_scans_only(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a source removes its scans and leaves the scans of the project's other sources.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        doomed, kept = (
            make_source(project_id=project.id, name=FIRST_PDF),
            make_source(project_id=project.id, name=SECOND_PDF),
        )
        await store_project(
            fx_uow(),
            project,
            sources=[doomed, kept],
            scans=[make_scan(source=source, number=0) for source in (doomed, kept)],
        )
        await fx_uow().change_book(project.id, lambda repos, _project: repos.sources.delete(doomed.id))
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.scans.list_for_source(doomed.id)) == [])
        expect(len(await reader.read(lambda repos: repos.scans.list_for_source(kept.id))) == 1)
        assert_expectations()

    async def test_delete_leaves_the_pages_of_its_scans(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a source keeps the pages cut from its scans, with their versions, without their scan.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        uow = fx_uow()
        await _add_projects(uow, projects=[project])
        pages = await _add_book(uow, project)
        version = make_page_version(page_id=pages[0].id)
        await uow.change_book(project.id, lambda repos, _project: repos.page_versions.add(version))

        def delete_the_source(repos: Repositories, _project: Project) -> None:
            """Delete the only source of the project, which the work finds by listing.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.sources.delete(repos.sources.list_for_project(project.id)[0].id)

        await fx_uow().change_book(project.id, delete_the_source)
        reader = fx_uow()
        kept = await reader.read(lambda repos: repos.pages.list_for_project(project.id, SliceRequest()))
        expect(list(kept.items) == [evolve(page, scan_id=None) for page in pages])
        expect(await reader.read(lambda repos: repos.page_versions.list_for_page(pages[0].id)) == [version])
        assert_expectations()


class TestScanRepository:
    """Contract of ScanRepository."""

    async def test_list_by_ids_returns_the_stored_scans_among_them(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify scans read by identifier come back once each, and an identifier that is not stored is left out.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
        await store_project(fx_uow(), project, sources=[source], scans=scans)
        unstored = make_scan(source=source, number=PAGE_COUNT)
        wanted = [scans[0].id, scans[2].id, scans[2].id, unstored.id]
        reader = fx_uow()
        found = await reader.read(lambda repos: repos.scans.list_by_ids(wanted))
        expect(sorted(found, key=attrgetter('number')) == [scans[0], scans[2]])
        expect(await reader.read(lambda repos: repos.scans.list_by_ids([])) == [])
        assert_expectations()

    async def test_scans_read_back_by_number_within_their_source(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify committed scans read back unchanged and list by their number in the source.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scans = [
            evolve(make_scan(source=source, number=number), source_label=f'{number + 1}')
            for number in reversed(range(PAGE_COUNT))
        ]
        await store_project(fx_uow(), project, sources=[source], scans=scans)
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.scans.get(scans[0].id)) == scans[0])
        expect(await reader.read(lambda repos: repos.scans.list_for_source(source.id)) == list(reversed(scans)))
        assert_expectations()

    async def test_list_for_project_follows_the_import_order_of_sources(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the scans of a project list source by source in import order, sliced, with the full total.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first = make_source(project_id=project.id, name=FIRST_PART_PDF, minutes=1)
        second = make_source(project_id=project.id, name=SECOND_PART_PDF, minutes=2)
        expected = [make_scan(source=source, number=number) for source in (first, second) for number in range(2)]
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        await store_project(fx_uow(), project, sources=[second, first], scans=list(reversed(expected)))
        reader = fx_uow()
        everything = await reader.read(lambda repos: repos.scans.list_for_project(project.id, SliceRequest()))
        window = await reader.read(
            lambda repos: repos.scans.list_for_project(project.id, SliceRequest(offset=1, limit=2))
        )
        expect([scan.id for scan in everything.items] == [scan.id for scan in expected])
        expect([scan.id for scan in window.items] == [scan.id for scan in expected[1:3]])
        expect(window.total == len(expected))
        assert_expectations()

    async def test_update_stores_the_renditions_state(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a scan marked ready in a new renditions version and a PNG full image reads back so.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        await store_project(fx_uow(), project, sources=[source])
        ready = evolve(scan, renditions=Renditions(ready=True, version=2, full=Rendition.FULL_PNG))

        def add_and_ready(repos: Repositories, _project: Project) -> None:
            """Add the scan and replace it with its ready state in the same change.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.scans.add(scan)
            repos.scans.update(ready)

        await fx_uow().change_book(project.id, add_and_ready)
        assert await fx_uow().read(lambda repos: repos.scans.get(scan.id)) == ready

    async def test_list_unready_returns_the_scans_without_ready_renditions_in_import_order(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the unready scans of a project list source by source and by number, without ready or foreign ones.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        first = make_source(project_id=project.id, name=FIRST_PDF, minutes=1)
        second = make_source(project_id=project.id, name=SECOND_PDF, minutes=2)
        foreign = make_source(project_id=other.id, name='c.pdf', minutes=0)
        ready = Renditions(ready=True)
        scans = [
            make_scan(source=second, number=0),
            make_scan(source=second, number=1),
            evolve(make_scan(source=first, number=0), renditions=ready),
            make_scan(source=first, number=1),
            make_scan(source=first, number=2),
        ]
        uow = fx_uow()
        await store_project(uow, project, sources=[second, first], scans=scans)
        await store_project(uow, other, sources=[foreign], scans=[make_scan(source=foreign, number=0)])
        unready = await fx_uow().read(lambda repos: repos.scans.list_unready(project.id))
        assert [(scan.source_id, scan.number) for scan in unready] == [
            (first.id, 1),
            (first.id, 2),
            (second.id, 0),
            (second.id, 1),
        ]

    async def test_list_unready_is_empty_for_a_project_with_every_scan_ready(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a project whose scans are all ready, or that has none, has nothing left to cut.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await _add_projects(uow, projects=[project])
        expect(await uow.read(lambda repos: repos.scans.list_unready(project.id)) == [])
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))

        def add_ready_scan(repos: Repositories, _project: Project) -> None:
            """Add the source and its ready scan.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.sources.add(source)
            repos.scans.add(scan)

        await uow.change_book(project.id, add_ready_scan)
        expect(await fx_uow().read(lambda repos: repos.scans.list_unready(project.id)) == [])
        assert_expectations()

    async def test_same_number_twice_in_a_source_raises_conflict(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify two scans cannot hold the same position of one source.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project, sources=[source], scans=[make_scan(source=source, number=0)])
        clashing = [make_scan(source=source, number=1), make_scan(source=source, number=0)]
        with pytest.raises(ConflictError, match=str(source.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.scans.add_many(clashing))

    async def test_scan_of_a_missing_source_raises_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a scan cannot be stored for a source that does not exist, so no scan outlives its file.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project)
        orphan = make_scan(source=source, number=0)
        with pytest.raises(NotFoundError, match=str(source.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.scans.add(orphan))


class TestPageRepository:
    """Contract of PageRepository."""

    async def test_pages_list_in_the_byte_order_of_their_keys(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify pages list by order key compared byte by byte, sliced, with every page of the book in the total.

        Upper-case letters sort before lower-case ones, and two keys differing only in case are two positions, which
        a case-insensitive collation would get wrong.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        pages = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        await store_project(fx_uow(), project, *pages)
        reader = fx_uow()
        everything = await reader.read(lambda repos: repos.pages.list_for_project(project.id, SliceRequest()))
        window = await reader.read(
            lambda repos: repos.pages.list_for_project(project.id, SliceRequest(offset=1, limit=2))
        )
        beyond = await reader.read(
            lambda repos: repos.pages.list_for_project(project.id, SliceRequest(offset=len(UNORDERED_KEYS), limit=1))
        )
        expect([page.order_key for page in everything.items] == [KEY_ZZ, KEY_A0, KEY_A0_UPPER, KEY_A0_LOWER, KEY_A1])
        expect([page.order_key for page in window.items] == [KEY_A0, KEY_A0_UPPER])
        expect((window.total, list(beyond.items), beyond.total) == (len(UNORDERED_KEYS), [], len(UNORDERED_KEYS)))
        assert_expectations()

    async def test_last_order_key_is_the_greatest_of_the_project(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the last key is the greatest in byte order, None for an empty book, and blind to other projects.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other, empty = (make_project(owner_id=owner_id) for _ in range(3))
        uow = fx_uow()
        await store_project(uow, project, *[make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS])
        await store_project(uow, other, make_page(project_id=other.id, order_key=KEY_B0))
        await store_project(uow, empty)
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.pages.last_order_key(project.id)) == KEY_A1)
        expect(await reader.read(lambda repos: repos.pages.last_order_key(empty.id)) is None)
        assert_expectations()

    async def test_kind_tally_counts_the_pages_with_an_image_of_each_kind(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify each page counts under the kind it is processed by, and a placeholder and another book do not count.

        The kind is the blank kind for a blank page, and otherwise what the page shows: what the user set, else what the
        program found, else a plate counts as a picture in colour.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
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
        uow = fx_uow()
        await store_project(
            uow,
            project,
            text,
            plate,
            blank,
            found,
            by_hand,
            make_page(project_id=project.id, order_key=KEY_B0),
            sources=[source],
            scans=scans,
        )
        await store_project(uow, other, make_page(project_id=other.id, order_key=KEY_A0))
        tally = await fx_uow().read(lambda repos: repos.pages.kind_tally(project.id))
        assert dict(tally) == {
            RecipeKind.TEXT: 2,
            RecipeKind.COLOR_PICTURE: 1,
            RecipeKind.BW_PICTURE: 1,
            RecipeKind.BLANK: 1,
        }

    async def test_count_before_is_the_position_in_the_book(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page counts the pages before it in byte order, excluded ones included, and none of another project.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = (make_project(owner_id=owner_id) for _ in range(2))
        uow = fx_uow()
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        book[0] = evolve(book[0], included=False)
        await store_project(uow, project, *book)
        await store_project(uow, other, make_page(project_id=other.id, order_key='Zy'))

        def count_before_each(repos: Repositories) -> list[int]:
            """Count the pages before each page of the book.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The position of each page of the book, in the order they were stored.
            :rtype: list[int]
            """
            return [repos.pages.count_before(page) for page in book]

        positions = await fx_uow().read(count_before_each)
        # The keys are stored as a1, a0v, a0V, a0, Zz and sort as Zz, a0, a0V, a0v, a1
        expect(positions == [4, 3, 2, 1, 0])
        assert_expectations()

    async def test_list_for_scan_returns_the_pages_of_that_scan_by_slot(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the halves of a split scan list by slot, without the pages of other scans or of no scan.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        spread, other = make_scan(source=source, number=0), make_scan(source=source, number=1)
        right = evolve(make_page(project_id=project.id, order_key=KEY_A1, scan=spread), slot=2)
        left = evolve(make_page(project_id=project.id, order_key=KEY_A2, scan=spread), slot=1)
        elsewhere = make_page(project_id=project.id, order_key=KEY_A3, scan=other)
        placeholder = make_page(project_id=project.id, order_key='a4')
        uow = fx_uow()
        await store_project(uow, project, right, left, elsewhere, placeholder, sources=[source], scans=[spread, other])
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.pages.list_for_scan(spread.id)) == [left, right])
        expect(await reader.read(lambda repos: repos.pages.list_for_scan(other.id)) == [elsewhere])
        assert_expectations()

    async def test_included_only_lists_the_pages_of_the_book_and_counts_them(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a listing of the included pages skips the excluded ones in its window and in its total.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        book = [make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2)]
        book[1] = evolve(book[1], included=False)
        await store_project(fx_uow(), project, *book)
        reader = fx_uow()
        everything = await reader.read(
            lambda repos: repos.pages.list_for_project(project.id, SliceRequest(), included_only=True)
        )
        window = await reader.read(
            lambda repos: repos.pages.list_for_project(project.id, SliceRequest(offset=1, limit=1), included_only=True)
        )
        expect(list(everything.items) == [book[0], book[2]])
        expect((list(window.items), window.total) == ([book[2]], 2))
        assert_expectations()

    async def test_list_by_ids_returns_the_pages_in_book_order(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify pages named in any order, with repeats, come back once each in the byte order of their keys.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        await store_project(fx_uow(), project, *book)
        wanted = [book[0].id, book[2].id, book[4].id, book[2].id]
        listed = await fx_uow().read(lambda repos: repos.pages.list_by_ids(project.id, wanted))
        # Stored as a1, a0v, a0V, a0, Zz, so these three are a1, a0V and Zz
        assert [page.order_key for page in listed] == [KEY_ZZ, KEY_A0_UPPER, KEY_A1]

    async def test_list_by_ids_refuses_a_missing_page_and_a_page_of_another_project(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one identifier that names no page of the project fails the whole read, naming that identifier.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        own, foreign = make_page(project_id=project.id), make_page(project_id=other.id)
        uow = fx_uow()
        await store_project(uow, project, own)
        await store_project(uow, other, foreign)
        reader = fx_uow()
        missing = make_page(project_id=project.id)
        with pytest.raises(NotFoundError, match=str(foreign.id)):
            await reader.read(lambda repos: repos.pages.list_by_ids(project.id, [own.id, foreign.id]))
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await reader.read(lambda repos: repos.pages.list_by_ids(project.id, [own.id, missing.id]))

    async def test_list_for_source_returns_the_pages_of_its_scans_in_book_order(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pages of one source list in book order, without pages of other sources or without a scan.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first, second = (
            make_source(project_id=project.id, name=FIRST_PDF),
            make_source(project_id=project.id, name=SECOND_PDF),
        )
        scans = [
            make_scan(source=first, number=0),
            make_scan(source=second, number=0),
            make_scan(source=first, number=1),
        ]
        late, elsewhere, early = (
            make_page(project_id=project.id, order_key=key, scan=scan)
            for key, scan in zip((KEY_A2, KEY_A1, KEY_A0), scans, strict=True)
        )
        placeholder = make_page(project_id=project.id, order_key=KEY_A3)
        uow = fx_uow()
        await store_project(uow, project, late, elsewhere, early, placeholder, sources=[first, second], scans=scans)
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.pages.list_for_source(project.id, first.id)) == [early, late])
        expect(await reader.read(lambda repos: repos.pages.list_for_source(project.id, second.id)) == [elsewhere])
        expect(await reader.read(lambda repos: repos.pages.list_for_source(project.id, SourceId(uuid4()))) == [])
        assert_expectations()

    async def test_neighbour_key_is_the_nearest_key_on_a_side_without_the_excluded_pages(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the nearest key before or after another, in byte order, skipping pages left out and other projects.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        uow = fx_uow()
        await store_project(uow, project, *book)
        await store_project(uow, other, make_page(project_id=other.id, order_key=KEY_A0_W))
        # The keys are stored as a1, a0v, a0V, a0, Zz and sort as Zz, a0, a0V, a0v, a1
        by_key = {page.order_key: page.id for page in book}

        def look_around(repos: Repositories) -> list[str | None]:
            """Look for the neighbours of three pages, on each side and with pages left out.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The keys found, in the order of the looks, None where no page lies there.
            :rtype: list[str | None]
            """
            pages = repos.pages
            return [
                pages.neighbour_key(project.id, KEY_A0_UPPER, Side.BEFORE),
                pages.neighbour_key(project.id, KEY_A0_UPPER, Side.AFTER),
                pages.neighbour_key(project.id, KEY_A0_UPPER, Side.AFTER, excluding=[by_key[KEY_A0_LOWER]]),
                pages.neighbour_key(project.id, KEY_A0_UPPER, Side.BEFORE, excluding=[by_key[KEY_A0], by_key[KEY_ZZ]]),
                pages.neighbour_key(project.id, KEY_ZZ, Side.BEFORE),
                pages.neighbour_key(project.id, KEY_A1, Side.AFTER),
            ]

        assert await fx_uow().read(look_around) == [KEY_A0, KEY_A0_LOWER, KEY_A1, None, None, None]

    async def test_update_many_writes_every_page_in_one_transaction(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify several pages changed together read back changed, and the pages left out stay as they were.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        book = [make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2)]
        await store_project(fx_uow(), project, *book)
        moved = [evolve(book[0], order_key='a1V'), evolve(book[1], order_key='a1G')]
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.update_many(moved))
        everything = await fx_uow().read(lambda repos: repos.pages.list_for_project(project.id, SliceRequest()))
        assert list(everything.items) == [moved[1], moved[0], book[2]]

    async def test_get_after_update_many_in_the_same_change_returns_the_new_state(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page read back inside the change that updated it is the updated page, not the one stored before.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        book = [make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1)]
        await store_project(fx_uow(), project, *book)
        moved = [evolve(page, label=PAGE_LABEL, included=False) for page in book]

        def update_and_read(repos: Repositories, _project: Project) -> list[Page]:
            """Update both pages, then read them back in the same transaction.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            :returns: The pages as the transaction reads them after the update.
            :rtype: list[Page]
            """
            repos.pages.update_many(moved)
            return [repos.pages.get(page.id) for page in book]

        assert await fx_uow().change_book(project.id, update_and_read) == moved

    async def test_update_to_a_key_another_page_holds_raises_conflict(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page cannot take another page's position by update, as a move that lost a race would try to.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first, second = (make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1))
        await store_project(fx_uow(), project, first, second)
        taking = evolve(first, order_key=KEY_A1)
        uow = fx_uow()
        with pytest.raises(ConflictError, match=KEY_A1):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.update(taking))
        with pytest.raises(ConflictError, match=KEY_A1):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.update_many([taking]))

    async def test_update_many_with_a_missing_page_raises_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page that is not stored fails the update, naming its identifier.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        missing = make_page(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project)
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.update_many([missing]))

    async def test_updated_page_reads_back(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a page moved, numbered, given a kind and kept out of the book reads back so.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        await store_project(fx_uow(), project, page)
        changed = evolve(
            page, order_key=KEY_A0_UPPER, label=PAGE_LABEL, kind=PageKind.TITLE, included=False, notes='Stamp'
        )
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.update(changed))
        assert await fx_uow().read(lambda repos: repos.pages.get(page.id)) == changed

    async def test_same_order_key_twice_in_a_project_raises_conflict(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify two pages of one book cannot share a position, while another book may use the same key.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        uow = fx_uow()
        for owned in (project, other):
            await store_project(uow, owned, make_page(project_id=owned.id, order_key=KEY_A5))
        clashing = make_page(project_id=project.id, order_key=KEY_A5)
        with pytest.raises(ConflictError, match=KEY_A5):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.add(clashing))

    async def test_same_part_of_a_scan_twice_raises_conflict(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify one slot of a scan becomes one page at most, while any number of placeholders have no scan.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        uow = fx_uow()
        await store_project(
            uow,
            project,
            *[make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1)],
            make_page(project_id=project.id, order_key=KEY_A2, scan=scan),
            sources=[source],
            scans=[scan],
        )
        second_page = make_page(project_id=project.id, order_key=KEY_A3, scan=scan)
        with pytest.raises(ConflictError, match=str(scan.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.add(second_page))

    async def test_page_of_a_missing_parent_raises_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page needs its project and the scan it names, reported by the missing identifier.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        missing_scan = make_scan(source=make_source(project_id=project.id), number=0)
        orphan = make_page(project_id=project.id)
        uow = fx_uow()
        # The call is ``change``, because the project is missing and ``change_book`` would raise on entry
        with pytest.raises(NotFoundError, match=str(project.id)):
            await uow.change(lambda repos: repos.pages.add(orphan))
        await uow.change(lambda repos: repos.projects.add(project))
        scanned = make_page(project_id=project.id, scan=missing_scan)
        with pytest.raises(NotFoundError, match=str(missing_scan.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.add(scanned))

    async def test_delete_cascades_to_its_versions(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify deleting a page removes its versions and leaves the versions of other pages.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        doomed, kept = (make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1))
        await store_project(
            fx_uow(),
            project,
            doomed,
            kept,
            versions=[make_page_version(page_id=page.id) for page in (doomed, kept)],
        )
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.delete(doomed.id))
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.page_versions.list_for_page(doomed.id)) == [])
        expect(len(await reader.read(lambda repos: repos.page_versions.list_for_page(kept.id))) == 1)
        assert_expectations()

    async def test_missing_page_raises_not_found(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify reading a page that does not exist raises NotFoundError naming it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        page = make_page(project_id=make_project(owner_id=new_account_id()).id)
        with pytest.raises(NotFoundError, match=str(page.id)):
            await fx_uow().read(lambda repos: repos.pages.get(page.id))


class TestPageVersionRepository:
    """Contract of PageVersionRepository."""

    async def test_list_to_prepare_returns_pending_and_failed_versions_of_the_given_processors(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the versions still to write come back earliest first, and nothing else does.

        Ready and running versions, versions of other processors, such as the half of a split spread that failed, and
        versions of another project are left out.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
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
            processor=ProcessorRef(key=BLANK_PROCESSOR_KEY, version='1'),
            state=VersionState.FAILED,
        )
        ready = evolve(make_page_version(page_id=pages[2].id), state=VersionState.READY)
        running = evolve(make_page_version(page_id=pages[3].id), state=VersionState.RUNNING)
        spread_half = evolve(
            make_page_version(page_id=pages[4].id),
            processor=ProcessorRef(key='split.spread', version='1'),
            state=VersionState.FAILED,
        )
        uow = fx_uow()
        await store_project(uow, project, *pages, versions=[pending, failed, ready, running, spread_half])
        await store_project(uow, other, elsewhere, versions=[make_page_version(page_id=elsewhere.id)])
        reader = fx_uow()
        found = await reader.read(
            lambda repos: repos.page_versions.list_to_prepare(project.id, {'split.none', BLANK_PROCESSOR_KEY})
        )
        only_blank = await reader.read(
            lambda repos: repos.page_versions.list_to_prepare(project.id, {BLANK_PROCESSOR_KEY})
        )
        expect(list(found) == [failed, pending])
        expect(list(only_blank) == [failed])
        assert_expectations()

    async def test_base_sizes_are_those_of_the_base_versions_of_the_included_scan_pages(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify only the recorded sizes of base versions of included pages cut from a scan are returned.

        A page kept out, a generated leaf, a later version, a leaf drawn in place of a scan, a version recording no size
        and another project's page take no part.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
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
        leaf = evolve(make_page(project_id=project.id, order_key=KEY_B0), origin=PageOrigin.BLANK)
        foreign_page = make_page(project_id=other.id, scan=foreign_scan)
        sized = {WIDTH_PX: 2000, HEIGHT_PX: 3000, 'dpi': 300.0}
        # The sizes of the pages in order: counted, kept out, generated, recording none, recording no resolution, foreign
        recorded: list[dict[str, Any]] = [sized, sized, sized, {}, {WIDTH_PX: 10, HEIGHT_PX: 20}, sized]
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
                processor=ProcessorRef(key=BLANK_PROCESSOR_KEY, version='1'),
                data={WIDTH_PX: 7, HEIGHT_PX: 7},
            ),
        ]
        uow = fx_uow()
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
        found = await fx_uow().read(lambda repos: repos.page_versions.base_sizes(project.id))
        assert sorted(found, key=attrgetter(WIDTH_PX)) == [
            PageSize(width_px=10, height_px=20, dpi=None),
            PageSize(width_px=2000, height_px=3000, dpi=300.0),
        ]

    async def test_the_choice_of_a_leaf_survives_the_store_and_leaves_the_scan_linked(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a blank page reads back with the leaf chosen for it and its scan, and with the scan again after.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        page = make_page(project_id=project.id, scan=scan, kind=PageKind.BLANK)
        await store_project(fx_uow(), project, page, sources=[source], scans=[scan])

        def fill(repos: Repositories, _project: Project, *, blank_fill: BlankFill) -> Page:
            """Read the page as stored and replace it with the given blank fill.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            :param blank_fill: What the blank page is filled with.
            :type blank_fill: BlankFill
            :returns: The page as it was stored before the change.
            :rtype: Page
            """
            stored = repos.pages.get(page.id)
            repos.pages.update(evolve(stored, blank_fill=blank_fill))
            return stored

        stored = await fx_uow().change_book(project.id, partial(fill, blank_fill=BlankFill.PAPER))
        leaf = await fx_uow().change_book(project.id, partial(fill, blank_fill=BlankFill.SCAN))
        expect(stored.blank_fill is BlankFill.SCAN)
        expect((leaf.blank_fill, leaf.scan_id) == (BlankFill.PAPER, scan.id))
        expect((await fx_uow().read(lambda repos: repos.pages.get(page.id))).blank_fill is BlankFill.SCAN)
        assert_expectations()

    async def test_the_content_type_of_a_page_survives_the_store(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page reads back with no content type, then with one found, then with one set by hand.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        await store_project(fx_uow(), project, page)

        def set_content(repos: Repositories, _project: Project, *, content_type: ContentType, by_hand: bool) -> Page:
            """Read the page as stored and replace it with the given content type.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            :param content_type: Content type the page gets.
            :type content_type: ContentType
            :param by_hand: Whether the user set the content type.
            :type by_hand: bool
            :returns: The page as it was stored before the change.
            :rtype: Page
            """
            stored = repos.pages.get(page.id)
            repos.pages.update(evolve(stored, content_type=content_type, content_by_hand=by_hand))
            return stored

        by_found = partial(set_content, content_type=ContentType.BW_PICTURE, by_hand=False)
        by_user = partial(set_content, content_type=ContentType.COLOR_PICTURE, by_hand=True)
        added = await fx_uow().change_book(project.id, by_found)
        found = await fx_uow().change_book(project.id, by_user)
        by_hand = await fx_uow().read(lambda repos: repos.pages.get(page.id))
        expect((added.content_type, added.content_by_hand) == (None, False))
        expect((found.content_type, found.content_by_hand) == (ContentType.BW_PICTURE, False))
        expect((by_hand.content_type, by_hand.content_by_hand) == (ContentType.COLOR_PICTURE, True))
        assert_expectations()

    async def test_versions_read_back_in_creation_order(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page's versions read back unchanged, the earliest first, without other pages' versions.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page, other = (make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1))
        base = make_page_version(page_id=page.id, minutes=1)
        later = evolve(make_page_version(page_id=page.id, minutes=2), input_id=base.id, renditions=None)
        await store_project(fx_uow(), project, page, other, versions=[base, later, make_page_version(page_id=other.id)])
        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.page_versions.get(later.id)) == later)
        expect(await reader.read(lambda repos: repos.page_versions.list_for_page(page.id)) == [base, later])
        assert_expectations()

    async def test_base_versions_of_several_pages_read_in_one_call(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify only the versions without an input come back, for the pages asked for, the earliest first.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        first, second, unasked = (make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2))
        first_base = make_page_version(page_id=first.id, minutes=2)
        second_base = make_page_version(page_id=second.id, minutes=1)
        derived = evolve(make_page_version(page_id=first.id, minutes=3), input_id=first_base.id)
        await store_project(
            fx_uow(),
            project,
            first,
            second,
            unasked,
            versions=[first_base, second_base, derived, make_page_version(page_id=unasked.id)],
        )
        reader = fx_uow()
        both = await reader.read(lambda repos: repos.page_versions.list_base_versions([first.id, second.id]))
        expect(both == [second_base, first_base])
        expect(await reader.read(lambda repos: repos.page_versions.list_base_versions([])) == [])
        assert_expectations()

    async def test_version_of_a_missing_parent_raises_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a version needs its page and the input version it names, reported by the missing identifier.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        missing_input = make_page_version(page_id=page.id)
        orphan = make_page_version(page_id=page.id)
        uow = fx_uow()
        await store_project(uow, project)
        with pytest.raises(NotFoundError, match=str(page.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.page_versions.add(orphan))
        await uow.change_book(project.id, lambda repos, _project: repos.pages.add(page))
        fed = evolve(orphan, input_id=missing_input.id)
        with pytest.raises(NotFoundError, match=missing_input.id):
            await uow.change_book(project.id, lambda repos, _project: repos.page_versions.add(fed))

    async def test_deleted_input_leaves_the_versions_it_fed(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a version keeps the versions computed from it and empties their input.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        base = make_page_version(page_id=page.id)
        later = evolve(make_page_version(page_id=page.id, minutes=1), input_id=base.id)
        await store_project(fx_uow(), project, page, versions=[base, later])
        await fx_uow().change_book(project.id, lambda repos, _project: repos.page_versions.delete(base.id))
        listed = await fx_uow().read(lambda repos: repos.page_versions.list_for_page(page.id))
        assert listed == [evolve(later, input_id=None)]

    async def test_update_of_a_version_read_before_its_page_was_deleted_raises_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a version read earlier cannot be stored again once the deletion of its page has committed.

        A job writing the files of a page does exactly this when the page is deleted meanwhile, and the update must
        not bring the version of a deleted page back.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        page = make_page(project_id=project.id)
        version = make_page_version(page_id=page.id)
        await store_project(fx_uow(), project, page, versions=[version])
        reading = fx_uow()
        [read] = await reading.read(lambda repos: repos.page_versions.list_for_page(page.id))
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.delete(page.id))

        with pytest.raises(NotFoundError, match=version.id):
            await reading.change_book(
                project.id, lambda repos, _project: repos.page_versions.update(evolve(read, state=VersionState.READY))
            )

        assert await fx_uow().read(lambda repos: repos.page_versions.list_for_page(page.id)) == []


class TestSourceStatus:
    """Contract of SourceRepository for the files of an upload that the import rejected or skipped."""

    async def test_rejected_and_skipped_sources_read_back_with_their_status(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a rejected file keeps its reason and detail and a skipped one its status, with or without a type.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        rejected = _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF, minutes=1)
        untyped = _unimported(project, status=SourceStatus.REJECTED, name=NOTES_TXT, minutes=2, typed=False)
        skipped = _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF, minutes=3)
        await store_project(fx_uow(), project, sources=[rejected, untyped, skipped])
        reader = fx_uow()

        def read_all(repos: Repositories) -> list[Source]:
            """Read the three sources by their identifiers.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The rejected source, the rejected source without a type and the skipped one, as stored.
            :rtype: list[Source]
            """
            return [repos.sources.get(source.id) for source in (rejected, untyped, skipped)]

        stored = await reader.read(read_all)
        expect(stored == [rejected, untyped, skipped])
        expect(
            [(source.status, source.rejection, source.detail) for source in stored]
            == [
                (SourceStatus.REJECTED, RejectionReason.DUPLICATE, REJECTION_DETAIL),
                (SourceStatus.REJECTED, RejectionReason.UNSUPPORTED_TYPE, REJECTION_DETAIL),
                (SourceStatus.SKIPPED, None, ''),
            ]
        )
        expect((stored[1].kind, stored[1].file_type) == (None, None))
        assert_expectations()

    async def test_same_file_may_be_rejected_and_skipped_beside_the_imported_one(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the digest is unique among the imported sources only, so a duplicate is stored as a rejected one.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        imported = make_source(project_id=project.id)
        repeats = [
            evolve(_unimported(project, status=status, name=FIRST_PDF), sha256=imported.sha256)
            for status in (SourceStatus.REJECTED, SourceStatus.REJECTED, SourceStatus.SKIPPED)
        ]
        uow = fx_uow()
        await store_project(uow, project, sources=[imported])
        await uow.change_book(project.id, lambda repos, _project: repos.sources.add_many(repeats))

        listed = await fx_uow().read(lambda repos: repos.sources.list_unimported(project.id))

        assert {source.id for source in listed} == {source.id for source in repeats}

    async def test_second_imported_source_with_the_digest_raises_conflict_beside_unimported_ones(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a rejected file with a digest does not let a second imported source with it in.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        imported = make_source(project_id=project.id)
        rejected = evolve(_unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF), sha256=imported.sha256)
        uow = fx_uow()
        await store_project(uow, project, sources=[imported, rejected])
        duplicate = make_source(project_id=project.id)

        with pytest.raises(ConflictError, match=duplicate.sha256):
            await fx_uow().change_book(project.id, lambda repos, _project: repos.sources.add(duplicate))

    async def test_listing_and_digest_lookup_return_imported_sources_only(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify rejected and skipped files are neither listed as sources nor found by their digest.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        imported = make_source(project_id=project.id, name=FIRST_PDF)
        rejected = _unimported(project, status=SourceStatus.REJECTED, name=SECOND_PDF)
        skipped = _unimported(project, status=SourceStatus.SKIPPED, name=FIRST_PART_PDF)
        await store_project(fx_uow(), project, sources=[imported, rejected, skipped])
        reader = fx_uow()

        def find_digests(repos: Repositories) -> list[Source | None]:
            """Look up the digests of the rejected and the skipped files.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: What the lookup finds for the rejected file and for the skipped one.
            :rtype: list[Source | None]
            """
            return [repos.sources.find_by_sha256(project.id, source.sha256) for source in (rejected, skipped)]

        expect(await reader.read(lambda repos: repos.sources.list_for_project(project.id)) == [imported])
        expect(await reader.read(find_digests) == [None, None])
        assert_expectations()

    async def test_unimported_sources_list_in_recorded_order_inside_the_project(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the rejected and skipped files of a project list earliest first, without imported or foreign ones.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        later = _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF, minutes=2)
        earlier = _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF, minutes=1)
        uow = fx_uow()
        await store_project(uow, project, sources=[later, earlier, make_source(project_id=project.id, name=NOTES_TXT)])
        await store_project(uow, other, sources=[_unimported(other, status=SourceStatus.REJECTED, name=FIRST_PDF)])

        listed = await fx_uow().read(lambda repos: repos.sources.list_unimported(project.id))

        assert listed == [earlier, later]

    async def test_delete_unimported_keeps_imported_sources_and_other_projects(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify closing the report of an import deletes the project's rejected and skipped files and nothing else.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project, other = make_project(owner_id=owner_id), make_project(owner_id=owner_id)
        imported = make_source(project_id=project.id)
        foreign = _unimported(other, status=SourceStatus.REJECTED, name=FIRST_PDF)
        uow = fx_uow()
        await store_project(
            uow,
            project,
            sources=[
                imported,
                _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF),
                _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF),
            ],
        )
        await store_project(uow, other, sources=[foreign])

        await fx_uow().change_book(project.id, lambda repos, _project: repos.sources.delete_unimported(project.id))

        reader = fx_uow()
        expect(await reader.read(lambda repos: repos.sources.list_unimported(project.id)) == [])
        expect(await reader.read(lambda repos: repos.sources.list_for_project(project.id)) == [imported])
        expect(await reader.read(lambda repos: repos.sources.list_unimported(other.id)) == [foreign])
        assert_expectations()

    async def test_project_overview_counts_imported_sources_only(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the source count of a project does not include its rejected and skipped files.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        project = make_project(owner_id=owner_id)
        sources = [
            make_source(project_id=project.id),
            _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF),
            _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF),
        ]
        await store_project(fx_uow(), project, sources=sources)

        reader = fx_uow()
        listed = await reader.read(lambda repos: repos.projects.list_for_owner(owner_id, SliceRequest()))
        expect([item.source_count for item in listed.items] == [1])
        expect((await reader.read(lambda repos: repos.projects.overview(project))).source_count == 1)
        assert_expectations()
