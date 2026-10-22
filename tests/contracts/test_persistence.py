"""Contract of the persistence ports, run against the SQL adapter, the application's own.

Every stored book needs an owner the catalogue accepts, which ``fx_new_owner`` creates. A test that only names a
book that is never stored takes a bare identifier. The repositories of a book are used through ``read_book`` and
``change_book``, and those of the catalogue through ``read`` and ``change``.
"""

from datetime import timedelta
from functools import partial
from operator import attrgetter
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import LibraryEntry
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
from bookreviver.domain.ids import PageVersionId, ProjectId, SourceId
from bookreviver.domain.values import (
    BookDetails,
    MetadataSuggestion,
    PageSize,
    ProcessorRef,
    Renditions,
    SliceRequest,
)
from tests.helpers.builders import (
    EPOCH,
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
    from bookreviver.domain.entities import Page, Project, Source
    from bookreviver.domain.ids import AccountId, PageId
    from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories, UnitOfWork
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

    def add_rows(repos: BookRepositories, _project: Project) -> None:
        """Add the source, its scans and their pages.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        repos.sources.add(source)
        repos.scans.add_many(scans)
        repos.pages.add_many(pages)

    await uow.change_book(project.id, add_rows)
    return pages


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


def _entry(owner_id: AccountId, *, minutes: int = 0) -> LibraryEntry:
    """Build the entry of a book that joined the library of an account ``minutes`` after the epoch.

    :param owner_id: Account owning the book.
    :type owner_id: AccountId
    :param minutes: Minutes after the epoch the book was added.
    :type minutes: int
    :returns: An entry of a book with a fresh identifier.
    :rtype: LibraryEntry
    """
    return LibraryEntry(project_id=ProjectId(uuid4()), owner_id=owner_id, added_at=EPOCH + timedelta(minutes=minutes))


class TestProjectRepository:
    """Contract of ProjectRepository, the one project row of a book."""

    async def test_created_project_reads_back_after_commit(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the project a book is created with is visible to a later unit of work, unchanged.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=await fx_new_owner())
        assert await fx_uow().read_book(project.id, lambda repos: repos.projects.get(project.id)) == project

    async def test_update_replaces_details(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify an update stores the new description, whose contributors and identifiers come back in their order.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        described = evolve(project, details=FULL_DETAILS)
        await store_project(fx_uow(), project, owner=await fx_new_owner())
        await fx_uow().change_book(project.id, lambda repos, _project: repos.projects.update(described))
        stored = await fx_uow().read_book(project.id, lambda repos: repos.projects.get(project.id))
        assert stored.details == FULL_DETAILS

    async def test_update_clears_lists_and_height(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify an update to empty lists and no height is stored, so a cleared field does not come back.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = evolve(make_project(), details=FULL_DETAILS)
        cleared = evolve(project, details=BookDetails(title=FULL_DETAILS.title))
        await store_project(fx_uow(), project, owner=await fx_new_owner())
        await fx_uow().change_book(project.id, lambda repos, _project: repos.projects.update(cleared))
        stored = await fx_uow().read_book(project.id, lambda repos: repos.projects.get(project.id))
        assert stored.details == BookDetails(title=FULL_DETAILS.title)

    async def test_overview_counts_the_book_of_one_project(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the overview counts the included pages, the sources and the scans of the book.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner())
        await _add_book(uow, project)
        overview = await fx_uow().read_book(project.id, lambda repos: repos.projects.overview(project))
        assert (overview.project, overview.page_count, overview.source_count, overview.scan_count) == (project, *BOOK)

    async def test_overview_counts_the_pages_with_an_image_kept_out_of_the_book_but_not_placeholders(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pages a run goes over are every page with an image, kept out of the book or not, no placeholder.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner())
        await _add_book(uow, project)
        placeholder = make_page(project_id=project.id, order_key=KEY_B0)
        await uow.change_book(project.id, lambda repos, _project: repos.pages.add(placeholder))
        overview = await fx_uow().read_book(project.id, lambda repos: repos.projects.overview(project))
        assert (overview.page_count, overview.image_page_count) == (BOOK[0] + 1, PAGE_COUNT)

    async def test_the_cover_is_the_first_page_of_the_kind_cover(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the overview names as the cover the page of the kind cover that comes first in the book.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        text, cover, back = (
            make_page(project_id=project.id, order_key=key, kind=kind)
            for key, kind in (('a0', PageKind.TEXT), ('a1', PageKind.COVER), ('a2', PageKind.COVER))
        )
        await store_project(fx_uow(), project, back, text, cover, owner=await fx_new_owner())
        overview = await fx_uow().read_book(project.id, lambda repos: repos.projects.overview(project))
        assert overview.cover_page_id == cover.id

    async def test_a_book_without_a_cover_page_shows_its_first_page_and_an_empty_book_none(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the cover falls back to the first page of the book, and a book without pages has no cover.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        empty, project = make_project(), make_project()
        first, second = (make_page(project_id=project.id, order_key=key) for key in ('a0', 'a1'))
        await store_project(fx_uow(), empty, owner=owner_id)
        await store_project(fx_uow(), project, second, first, owner=owner_id)

        async def cover_of(book: Project) -> PageId | None:
            """Read the cover the overview of a book names.

            :param book: The project of the book.
            :type book: Project
            :returns: The cover page, or None.
            :rtype: PageId | None
            """
            return (await fx_uow().read_book(book.id, lambda repos: repos.projects.overview(book))).cover_page_id

        assert [await cover_of(book) for book in (empty, project)] == [None, first.id]


class TestLibraryRepository:
    """Contract of LibraryRepository, which says which account owns which book."""

    async def test_added_entry_reads_back_after_commit(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a committed entry is visible to a later unit of work, unchanged.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entry = _entry(await fx_new_owner())
        await fx_uow().change(lambda repos: repos.library.add(entry))
        assert await fx_uow().read(lambda repos: repos.library.get(entry.project_id)) == entry

    async def test_adding_a_listed_book_raises_conflict(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a book is in one library, so a second entry for it is a ConflictError and not an overwrite.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entry = _entry(await fx_new_owner())
        await fx_uow().change(lambda repos: repos.library.add(entry))
        stolen = evolve(entry, owner_id=await fx_new_owner())
        with pytest.raises(ConflictError, match=str(entry.project_id)):
            await fx_uow().change(lambda repos: repos.library.add(stolen))

    async def test_entry_of_an_account_that_is_not_stored_raises_not_found(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify the owner of an entry must be an account of the catalogue.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        entry = _entry(new_account_id())
        with pytest.raises(NotFoundError):
            await fx_uow().change(lambda repos: repos.library.add(entry))

    async def test_deleted_entry_is_gone_and_a_second_deletion_is_not_found(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a deleted entry answers NotFoundError, and so does deleting it again.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entry = _entry(await fx_new_owner())
        await fx_uow().change(lambda repos: repos.library.add(entry))
        await fx_uow().change(lambda repos: repos.library.delete(entry.project_id))
        with pytest.raises(NotFoundError, match=str(entry.project_id)):
            await fx_uow().read(lambda repos: repos.library.get(entry.project_id))
        with pytest.raises(NotFoundError, match=str(entry.project_id)):
            await fx_uow().change(lambda repos: repos.library.delete(entry.project_id))

    async def test_list_for_owner_orders_slices_and_counts(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the owner's books come latest added first, sliced, with the total, and others are hidden.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id, other_owner_id = await fx_new_owner(), await fx_new_owner()
        older, newer = _entry(owner_id, minutes=1), _entry(owner_id, minutes=2)
        elsewhere = _entry(other_owner_id, minutes=3)
        await fx_uow().change(lambda repos: [repos.library.add(entry) for entry in (older, elsewhere, newer)])
        reader = fx_uow()
        full = await reader.read(lambda repos: repos.library.list_for_owner(owner_id, SliceRequest()))
        second = await reader.read(
            lambda repos: repos.library.list_for_owner(owner_id, SliceRequest(offset=1, limit=1))
        )
        beyond = await reader.read(lambda repos: repos.library.list_for_owner(owner_id, SliceRequest(offset=5)))
        expect(list(full.items) == [newer, older])
        expect(full.total == 2)
        expect(list(second.items) == [older])
        expect(second.total == 2)
        expect((list(beyond.items), beyond.total) == ([], 2))
        assert_expectations()

    async def test_list_for_owner_breaks_ties_by_identifier(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify books added at the same moment list by identifier, so offset paging never skips or repeats one.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        tied = sorted((_entry(owner_id, minutes=1) for _ in range(PAGE_COUNT)), key=attrgetter('project_id'))
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        await fx_uow().change(lambda repos: [repos.library.add(entry) for entry in reversed(tied)])

        def list_whole_and_by_page(repos: CatalogueRepositories) -> tuple[list[ProjectId], list[ProjectId]]:
            """List the owner's books in one window and then one book at a time.

            :param repos: Repositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The identifiers of the whole listing, and of the pages of one book each, joined.
            :rtype: tuple[list[ProjectId], list[ProjectId]]
            """
            whole = [item.project_id for item in repos.library.list_for_owner(owner_id, SliceRequest()).items]
            paged = [
                item.project_id
                for offset in range(PAGE_COUNT)
                for item in repos.library.list_for_owner(owner_id, SliceRequest(offset=offset, limit=1)).items
            ]
            return whole, paged

        whole, paged = await fx_uow().read(list_whole_and_by_page)
        expect(whole == [entry.project_id for entry in tied])
        expect(paged == [entry.project_id for entry in tied])
        assert_expectations()

    async def test_list_all_returns_the_entries_of_every_account_by_identifier(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pass over all books sees the books of every account, ordered by identifier.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entries = [_entry(await fx_new_owner(), minutes=minute) for minute in range(PAGE_COUNT)]
        await fx_uow().change(lambda repos: [repos.library.add(entry) for entry in entries])
        listed = await fx_uow().read(lambda repos: repos.library.list_all())
        assert list(listed) == sorted(entries, key=attrgetter('project_id'))


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
        project, other = make_project(), make_project()
        later = make_source(project_id=project.id, name=SECOND_PART_PDF, minutes=2)
        earlier = evolve(
            make_source(project_id=project.id, name=FIRST_PART_PDF, minutes=1),
            metadata={'pdf_version': '1.4'},
            suggestion=MetadataSuggestion(title='Book', publication_year='1887'),
        )
        foreign = make_source(project_id=other.id)
        uow = fx_uow()
        await store_project(uow, project, owner=owner_id)
        await store_project(uow, other, owner=owner_id)
        await uow.change_book(project.id, lambda repos, _project: repos.sources.add_many([later, earlier]))
        await uow.change_book(other.id, lambda repos, _project: repos.sources.add(foreign))
        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.sources.get(earlier.id)) == earlier)
        expect(
            await reader.read_book(project.id, lambda repos: repos.sources.list_for_project(project.id))
            == [earlier, later]
        )
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
        project, other = make_project(), make_project()
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project, owner=owner_id, sources=[source])
        await store_project(uow, other, owner=owner_id)
        reader = fx_uow()
        expect(
            await reader.read_book(project.id, lambda repos: repos.sources.find_by_sha256(project.id, source.sha256))
            == source
        )
        expect(
            await reader.read_book(project.id, lambda repos: repos.sources.find_by_sha256(other.id, source.sha256))
            is None
        )
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
        project = make_project()
        await store_project(fx_uow(), project, owner=await fx_new_owner(), sources=[make_source(project_id=project.id)])
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
        first, second = make_project(), make_project()
        uow = fx_uow()
        for project in (first, second):
            await store_project(uow, project, owner=owner_id, sources=[make_source(project_id=project.id)])

        async def sources_of(project: Project) -> int:
            """Count the sources of one book.

            :param project: The book.
            :type project: Project
            :returns: How many sources it has.
            :rtype: int
            """
            return await fx_uow().read_book(project.id, lambda repos: len(repos.sources.list_for_project(project.id)))

        counts = [await sources_of(project) for project in (first, second)]
        assert counts == [1, 1]

    async def test_source_of_a_missing_project_raises_not_found(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify a source needs its project, reported by the missing identifier.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        project_id = make_project().id
        orphan = make_source(project_id=project_id)
        # The book is not stored, so the change is refused before the source is added
        with pytest.raises(NotFoundError, match=str(project_id)):
            await fx_uow().change_book(project_id, lambda repos, _project: repos.sources.add(orphan))

    async def test_delete_cascades_to_its_scans_only(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify deleting a source removes its scans and leaves the scans of the project's other sources.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        doomed, kept = (
            make_source(project_id=project.id, name=FIRST_PDF),
            make_source(project_id=project.id, name=SECOND_PDF),
        )
        await store_project(
            fx_uow(),
            project,
            owner=await fx_new_owner(),
            sources=[doomed, kept],
            scans=[make_scan(source=source, number=0) for source in (doomed, kept)],
        )
        await fx_uow().change_book(project.id, lambda repos, _project: repos.sources.delete(doomed.id))
        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.scans.list_for_source(doomed.id)) == [])
        expect(len(await reader.read_book(project.id, lambda repos: repos.scans.list_for_source(kept.id))) == 1)
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
        project = make_project()
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner())
        pages = await _add_book(uow, project)
        version = make_page_version(page_id=pages[0].id)
        await uow.change_book(project.id, lambda repos, _project: repos.page_versions.add(version))

        def delete_the_source(repos: BookRepositories, _project: Project) -> None:
            """Delete the only source of the project, which the work finds by listing.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.sources.delete(repos.sources.list_for_project(project.id)[0].id)

        await fx_uow().change_book(project.id, delete_the_source)
        reader = fx_uow()
        kept = await reader.read_book(
            project.id, lambda repos: repos.pages.list_for_project(project.id, SliceRequest())
        )
        expect(list(kept.items) == [evolve(page, scan_id=None) for page in pages])
        expect(
            await reader.read_book(project.id, lambda repos: repos.page_versions.list_for_page(pages[0].id))
            == [version]
        )
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
        project = make_project()
        source = make_source(project_id=project.id)
        scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
        await store_project(fx_uow(), project, owner=await fx_new_owner(), sources=[source], scans=scans)
        unstored = make_scan(source=source, number=PAGE_COUNT)
        wanted = [scans[0].id, scans[2].id, scans[2].id, unstored.id]
        reader = fx_uow()
        found = await reader.read_book(project.id, lambda repos: repos.scans.list_by_ids(wanted))
        expect(sorted(found, key=attrgetter('number')) == [scans[0], scans[2]])
        expect(await reader.read_book(project.id, lambda repos: repos.scans.list_by_ids([])) == [])
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
        project = make_project()
        source = make_source(project_id=project.id)
        scans = [
            evolve(make_scan(source=source, number=number), source_label=f'{number + 1}')
            for number in reversed(range(PAGE_COUNT))
        ]
        await store_project(fx_uow(), project, owner=await fx_new_owner(), sources=[source], scans=scans)
        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.scans.get(scans[0].id)) == scans[0])
        expect(
            await reader.read_book(project.id, lambda repos: repos.scans.list_for_source(source.id))
            == list(reversed(scans))
        )
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
        project = make_project()
        first = make_source(project_id=project.id, name=FIRST_PART_PDF, minutes=1)
        second = make_source(project_id=project.id, name=SECOND_PART_PDF, minutes=2)
        expected = [make_scan(source=source, number=number) for source in (first, second) for number in range(2)]
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        await store_project(
            fx_uow(), project, owner=await fx_new_owner(), sources=[second, first], scans=list(reversed(expected))
        )
        reader = fx_uow()
        everything = await reader.read_book(
            project.id, lambda repos: repos.scans.list_for_project(project.id, SliceRequest())
        )
        window = await reader.read_book(
            project.id, lambda repos: repos.scans.list_for_project(project.id, SliceRequest(offset=1, limit=2))
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
        project = make_project()
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        await store_project(fx_uow(), project, owner=await fx_new_owner(), sources=[source])
        ready = evolve(scan, renditions=Renditions(ready=True, version=2, full=Rendition.FULL_PNG))

        def add_and_ready(repos: BookRepositories, _project: Project) -> None:
            """Add the scan and replace it with its ready state in the same change.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.scans.add(scan)
            repos.scans.update(ready)

        await fx_uow().change_book(project.id, add_and_ready)
        assert await fx_uow().read_book(project.id, lambda repos: repos.scans.get(scan.id)) == ready

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
        project, other = make_project(), make_project()
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
        await store_project(uow, project, owner=owner_id, sources=[second, first], scans=scans)
        await store_project(uow, other, owner=owner_id, sources=[foreign], scans=[make_scan(source=foreign, number=0)])
        unready = await fx_uow().read_book(project.id, lambda repos: repos.scans.list_unready(project.id))
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
        project = make_project()
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner())
        expect(await uow.read_book(project.id, lambda repos: repos.scans.list_unready(project.id)) == [])
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))

        def add_ready_scan(repos: BookRepositories, _project: Project) -> None:
            """Add the source and its ready scan.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.sources.add(source)
            repos.scans.add(scan)

        await uow.change_book(project.id, add_ready_scan)
        expect(await fx_uow().read_book(project.id, lambda repos: repos.scans.list_unready(project.id)) == [])
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
        project = make_project()
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await store_project(
            uow, project, owner=await fx_new_owner(), sources=[source], scans=[make_scan(source=source, number=0)]
        )
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
        project = make_project()
        source = make_source(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner())
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
        project = make_project()
        pages = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        await store_project(fx_uow(), project, *pages, owner=await fx_new_owner())
        reader = fx_uow()
        everything = await reader.read_book(
            project.id, lambda repos: repos.pages.list_for_project(project.id, SliceRequest())
        )
        window = await reader.read_book(
            project.id, lambda repos: repos.pages.list_for_project(project.id, SliceRequest(offset=1, limit=2))
        )
        beyond = await reader.read_book(
            project.id,
            lambda repos: repos.pages.list_for_project(project.id, SliceRequest(offset=len(UNORDERED_KEYS), limit=1)),
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
        project, empty = make_project(), make_project()
        uow = fx_uow()
        pages = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        await store_project(uow, project, *pages, owner=owner_id)
        await store_project(uow, empty, owner=owner_id)
        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.pages.last_order_key(project.id)) == KEY_A1)
        expect(await reader.read_book(empty.id, lambda repos: repos.pages.last_order_key(empty.id)) is None)
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
        project, other = make_project(), make_project()
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
            owner=owner_id,
            sources=[source],
            scans=scans,
        )
        await store_project(uow, other, make_page(project_id=other.id, order_key=KEY_A0), owner=owner_id)
        tally = await fx_uow().read_book(project.id, lambda repos: repos.pages.kind_tally(project.id))
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
        project = make_project()
        uow = fx_uow()
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        book[0] = evolve(book[0], included=False)
        await store_project(uow, project, *book, owner=owner_id)

        def count_before_each(repos: BookRepositories) -> list[int]:
            """Count the pages before each page of the book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The position of each page of the book, in the order they were stored.
            :rtype: list[int]
            """
            return [repos.pages.count_before(page) for page in book]

        positions = await fx_uow().read_book(project.id, count_before_each)
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
        project = make_project()
        source = make_source(project_id=project.id)
        spread, other = make_scan(source=source, number=0), make_scan(source=source, number=1)
        right = evolve(make_page(project_id=project.id, order_key=KEY_A1, scan=spread), slot=2)
        left = evolve(make_page(project_id=project.id, order_key=KEY_A2, scan=spread), slot=1)
        elsewhere = make_page(project_id=project.id, order_key=KEY_A3, scan=other)
        placeholder = make_page(project_id=project.id, order_key='a4')
        uow = fx_uow()
        await store_project(
            uow,
            project,
            right,
            left,
            elsewhere,
            placeholder,
            owner=await fx_new_owner(),
            sources=[source],
            scans=[spread, other],
        )
        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.pages.list_for_scan(spread.id)) == [left, right])
        expect(await reader.read_book(project.id, lambda repos: repos.pages.list_for_scan(other.id)) == [elsewhere])
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
        project = make_project()
        book = [make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2)]
        book[1] = evolve(book[1], included=False)
        await store_project(fx_uow(), project, *book, owner=await fx_new_owner())
        reader = fx_uow()
        everything = await reader.read_book(
            project.id, lambda repos: repos.pages.list_for_project(project.id, SliceRequest(), included_only=True)
        )
        window = await reader.read_book(
            project.id,
            lambda repos: repos.pages.list_for_project(project.id, SliceRequest(offset=1, limit=1), included_only=True),
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
        project = make_project()
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        await store_project(fx_uow(), project, *book, owner=await fx_new_owner())
        wanted = [book[0].id, book[2].id, book[4].id, book[2].id]
        listed = await fx_uow().read_book(project.id, lambda repos: repos.pages.list_by_ids(project.id, wanted))
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
        project, other = make_project(), make_project()
        own, foreign = make_page(project_id=project.id), make_page(project_id=other.id)
        uow = fx_uow()
        await store_project(uow, project, own, owner=owner_id)
        await store_project(uow, other, foreign, owner=owner_id)
        reader = fx_uow()
        missing = make_page(project_id=project.id)
        with pytest.raises(NotFoundError, match=str(foreign.id)):
            await reader.read_book(project.id, lambda repos: repos.pages.list_by_ids(project.id, [own.id, foreign.id]))
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await reader.read_book(project.id, lambda repos: repos.pages.list_by_ids(project.id, [own.id, missing.id]))

    async def test_list_for_source_returns_the_pages_of_its_scans_in_book_order(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pages of one source list in book order, without pages of other sources or without a scan.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
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
        await store_project(
            uow,
            project,
            late,
            elsewhere,
            early,
            placeholder,
            owner=await fx_new_owner(),
            sources=[first, second],
            scans=scans,
        )
        reader = fx_uow()
        expect(
            await reader.read_book(project.id, lambda repos: repos.pages.list_for_source(project.id, first.id))
            == [early, late]
        )
        expect(
            await reader.read_book(project.id, lambda repos: repos.pages.list_for_source(project.id, second.id))
            == [elsewhere]
        )
        expect(
            await reader.read_book(project.id, lambda repos: repos.pages.list_for_source(project.id, SourceId(uuid4())))
            == []
        )
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
        project, other = make_project(), make_project()
        book = [make_page(project_id=project.id, order_key=key) for key in UNORDERED_KEYS]
        uow = fx_uow()
        await store_project(uow, project, *book, owner=owner_id)
        await store_project(uow, other, make_page(project_id=other.id, order_key=KEY_A0_W), owner=owner_id)
        # The keys are stored as a1, a0v, a0V, a0, Zz and sort as Zz, a0, a0V, a0v, a1
        by_key = {page.order_key: page.id for page in book}

        def look_around(repos: BookRepositories) -> list[str | None]:
            """Look for the neighbours of three pages, on each side and with pages left out.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
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

        assert await fx_uow().read_book(project.id, look_around) == [KEY_A0, KEY_A0_LOWER, KEY_A1, None, None, None]

    async def test_update_many_writes_every_page_in_one_transaction(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify several pages changed together read back changed, and the pages left out stay as they were.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        book = [make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2)]
        await store_project(fx_uow(), project, *book, owner=await fx_new_owner())
        moved = [evolve(book[0], order_key='a1V'), evolve(book[1], order_key='a1G')]
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.update_many(moved))
        everything = await fx_uow().read_book(
            project.id, lambda repos: repos.pages.list_for_project(project.id, SliceRequest())
        )
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
        project = make_project()
        book = [make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1)]
        await store_project(fx_uow(), project, *book, owner=await fx_new_owner())
        moved = [evolve(page, manual_label=PAGE_LABEL, included=False) for page in book]

        def update_and_read(repos: BookRepositories, _project: Project) -> list[Page]:
            """Update both pages, then read them back in the same transaction.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
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
        project = make_project()
        first, second = (make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1))
        await store_project(fx_uow(), project, first, second, owner=await fx_new_owner())
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
        project = make_project()
        missing = make_page(project_id=project.id)
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner())
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.update_many([missing]))

    async def test_updated_page_reads_back(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify a page moved, numbered, given a kind and kept out of the book reads back so.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        page = make_page(project_id=project.id)
        await store_project(fx_uow(), project, page, owner=await fx_new_owner())
        changed = evolve(
            page, order_key=KEY_A0_UPPER, manual_label=PAGE_LABEL, kind=PageKind.TITLE, included=False, notes='Stamp'
        )
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.update(changed))
        assert await fx_uow().read_book(page.project_id, lambda repos: repos.pages.get(page.id)) == changed

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
        project, other = make_project(), make_project()
        uow = fx_uow()
        for owned in (project, other):
            await store_project(uow, owned, make_page(project_id=owned.id, order_key=KEY_A5), owner=owner_id)
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
        project = make_project()
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        uow = fx_uow()
        await store_project(
            uow,
            project,
            *[make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1)],
            make_page(project_id=project.id, order_key=KEY_A2, scan=scan),
            owner=await fx_new_owner(),
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
        project = make_project()
        missing_scan = make_scan(source=make_source(project_id=project.id), number=0)
        orphan = make_page(project_id=project.id)
        uow = fx_uow()
        # The book is not stored, so the change is refused before the page is added
        with pytest.raises(NotFoundError, match=str(project.id)):
            await uow.change_book(project.id, lambda repos, _project: repos.pages.add(orphan))
        await store_project(uow, project, owner=await fx_new_owner())
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
        project = make_project()
        doomed, kept = (make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1))
        await store_project(
            fx_uow(),
            project,
            doomed,
            kept,
            owner=await fx_new_owner(),
            versions=[make_page_version(page_id=page.id) for page in (doomed, kept)],
        )
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.delete(doomed.id))
        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.page_versions.list_for_page(doomed.id)) == [])
        expect(len(await reader.read_book(project.id, lambda repos: repos.page_versions.list_for_page(kept.id))) == 1)
        assert_expectations()

    async def test_missing_page_raises_not_found(self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory) -> None:
        """Verify reading a page that the book does not hold raises NotFoundError naming the page.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=await fx_new_owner())
        page = make_page(project_id=project.id)
        with pytest.raises(NotFoundError, match=str(page.id)):
            await fx_uow().read_book(project.id, lambda repos: repos.pages.get(page.id))


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
        project, other = make_project(), make_project()
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
        await store_project(
            uow, project, *pages, owner=owner_id, versions=[pending, failed, ready, running, spread_half]
        )
        await store_project(uow, other, elsewhere, owner=owner_id, versions=[make_page_version(page_id=elsewhere.id)])
        reader = fx_uow()
        found = await reader.read_book(
            project.id,
            lambda repos: repos.page_versions.list_to_prepare(project.id, {'split.none', BLANK_PROCESSOR_KEY}),
        )
        only_blank = await reader.read_book(
            project.id, lambda repos: repos.page_versions.list_to_prepare(project.id, {BLANK_PROCESSOR_KEY})
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
        project, other = make_project(), make_project()
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
            owner=owner_id,
            sources=[sources[0]],
            scans=scans,
            versions=[*versions[:-1], *derived_pair],
        )
        await store_project(
            uow,
            other,
            foreign_page,
            owner=owner_id,
            sources=[sources[1]],
            scans=[foreign_scan],
            versions=[versions[-1]],
        )
        found = await fx_uow().read_book(project.id, lambda repos: repos.page_versions.base_sizes(project.id))
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
        project = make_project()
        source = make_source(project_id=project.id)
        scan = make_scan(source=source, number=0)
        page = make_page(project_id=project.id, scan=scan, kind=PageKind.BLANK)
        await store_project(fx_uow(), project, page, owner=await fx_new_owner(), sources=[source], scans=[scan])

        def fill(repos: BookRepositories, _project: Project, *, blank_fill: BlankFill) -> Page:
            """Read the page as stored and replace it with the given blank fill.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
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
        expect(
            (await fx_uow().read_book(page.project_id, lambda repos: repos.pages.get(page.id))).blank_fill
            is BlankFill.SCAN
        )
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
        project = make_project()
        page = make_page(project_id=project.id)
        await store_project(fx_uow(), project, page, owner=await fx_new_owner())

        def set_content(
            repos: BookRepositories, _project: Project, *, content_type: ContentType, by_hand: bool
        ) -> Page:
            """Read the page as stored and replace it with the given content type.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
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
        by_hand = await fx_uow().read_book(page.project_id, lambda repos: repos.pages.get(page.id))
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
        project = make_project()
        page, other = (make_page(project_id=project.id, order_key=key) for key in (KEY_A0, KEY_A1))
        base = make_page_version(page_id=page.id, minutes=1)
        later = evolve(make_page_version(page_id=page.id, minutes=2), input_id=base.id, renditions=None)
        await store_project(
            fx_uow(),
            project,
            page,
            other,
            owner=await fx_new_owner(),
            versions=[base, later, make_page_version(page_id=other.id)],
        )
        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.page_versions.get(later.id)) == later)
        expect(
            await reader.read_book(page.project_id, lambda repos: repos.page_versions.list_for_page(page.id))
            == [base, later]
        )
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
        project = make_project()
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
            owner=await fx_new_owner(),
            versions=[first_base, second_base, derived, make_page_version(page_id=unasked.id)],
        )
        reader = fx_uow()
        both = await reader.read_book(
            project.id, lambda repos: repos.page_versions.list_base_versions([first.id, second.id])
        )
        expect(both == [second_base, first_base])
        expect(await reader.read_book(project.id, lambda repos: repos.page_versions.list_base_versions([])) == [])
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
        project = make_project()
        page = make_page(project_id=project.id)
        missing_input = make_page_version(page_id=page.id)
        orphan = make_page_version(page_id=page.id)
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner())
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
        project = make_project()
        page = make_page(project_id=project.id)
        base = make_page_version(page_id=page.id)
        later = evolve(make_page_version(page_id=page.id, minutes=1), input_id=base.id)
        await store_project(fx_uow(), project, page, owner=await fx_new_owner(), versions=[base, later])
        await fx_uow().change_book(project.id, lambda repos, _project: repos.page_versions.delete(base.id))
        listed = await fx_uow().read_book(page.project_id, lambda repos: repos.page_versions.list_for_page(page.id))
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
        project = make_project()
        page = make_page(project_id=project.id)
        version = make_page_version(page_id=page.id)
        await store_project(fx_uow(), project, page, owner=await fx_new_owner(), versions=[version])
        reading = fx_uow()
        [read] = await reading.read_book(page.project_id, lambda repos: repos.page_versions.list_for_page(page.id))
        await fx_uow().change_book(project.id, lambda repos, _project: repos.pages.delete(page.id))

        with pytest.raises(NotFoundError, match=version.id):
            await reading.change_book(
                project.id, lambda repos, _project: repos.page_versions.update(evolve(read, state=VersionState.READY))
            )

        assert await fx_uow().read_book(page.project_id, lambda repos: repos.page_versions.list_for_page(page.id)) == []


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
        project = make_project()
        rejected = _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF, minutes=1)
        untyped = _unimported(project, status=SourceStatus.REJECTED, name=NOTES_TXT, minutes=2, typed=False)
        skipped = _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF, minutes=3)
        await store_project(fx_uow(), project, owner=await fx_new_owner(), sources=[rejected, untyped, skipped])
        reader = fx_uow()

        def read_all(repos: BookRepositories) -> list[Source]:
            """Read the three sources by their identifiers.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The rejected source, the rejected source without a type and the skipped one, as stored.
            :rtype: list[Source]
            """
            return [repos.sources.get(source.id) for source in (rejected, untyped, skipped)]

        stored = await reader.read_book(project.id, read_all)
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
        project = make_project()
        imported = make_source(project_id=project.id)
        repeats = [
            evolve(_unimported(project, status=status, name=FIRST_PDF), sha256=imported.sha256)
            for status in (SourceStatus.REJECTED, SourceStatus.REJECTED, SourceStatus.SKIPPED)
        ]
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner(), sources=[imported])
        await uow.change_book(project.id, lambda repos, _project: repos.sources.add_many(repeats))

        listed = await fx_uow().read_book(project.id, lambda repos: repos.sources.list_unimported(project.id))

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
        project = make_project()
        imported = make_source(project_id=project.id)
        rejected = evolve(_unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF), sha256=imported.sha256)
        uow = fx_uow()
        await store_project(uow, project, owner=await fx_new_owner(), sources=[imported, rejected])
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
        project = make_project()
        imported = make_source(project_id=project.id, name=FIRST_PDF)
        rejected = _unimported(project, status=SourceStatus.REJECTED, name=SECOND_PDF)
        skipped = _unimported(project, status=SourceStatus.SKIPPED, name=FIRST_PART_PDF)
        await store_project(fx_uow(), project, owner=await fx_new_owner(), sources=[imported, rejected, skipped])
        reader = fx_uow()

        def find_digests(repos: BookRepositories) -> list[Source | None]:
            """Look up the digests of the rejected and the skipped files.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: What the lookup finds for the rejected file and for the skipped one.
            :rtype: list[Source | None]
            """
            return [repos.sources.find_by_sha256(project.id, source.sha256) for source in (rejected, skipped)]

        expect(
            await reader.read_book(project.id, lambda repos: repos.sources.list_for_project(project.id)) == [imported]
        )
        expect(await reader.read_book(project.id, find_digests) == [None, None])
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
        project, other = make_project(), make_project()
        later = _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF, minutes=2)
        earlier = _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF, minutes=1)
        uow = fx_uow()
        await store_project(
            uow, project, owner=owner_id, sources=[later, earlier, make_source(project_id=project.id, name=NOTES_TXT)]
        )
        await store_project(
            uow, other, owner=owner_id, sources=[_unimported(other, status=SourceStatus.REJECTED, name=FIRST_PDF)]
        )

        listed = await fx_uow().read_book(project.id, lambda repos: repos.sources.list_unimported(project.id))

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
        project, other = make_project(), make_project()
        imported = make_source(project_id=project.id)
        foreign = _unimported(other, status=SourceStatus.REJECTED, name=FIRST_PDF)
        uow = fx_uow()
        await store_project(
            uow,
            project,
            owner=owner_id,
            sources=[
                imported,
                _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF),
                _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF),
            ],
        )
        await store_project(uow, other, owner=owner_id, sources=[foreign])

        await fx_uow().change_book(project.id, lambda repos, _project: repos.sources.delete_unimported(project.id))

        reader = fx_uow()
        expect(await reader.read_book(project.id, lambda repos: repos.sources.list_unimported(project.id)) == [])
        expect(
            await reader.read_book(project.id, lambda repos: repos.sources.list_for_project(project.id)) == [imported]
        )
        expect(await reader.read_book(other.id, lambda repos: repos.sources.list_unimported(other.id)) == [foreign])
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
        project = make_project()
        sources = [
            make_source(project_id=project.id),
            _unimported(project, status=SourceStatus.REJECTED, name=FIRST_PDF),
            _unimported(project, status=SourceStatus.SKIPPED, name=SECOND_PDF),
        ]
        await store_project(fx_uow(), project, owner=owner_id, sources=sources)

        overview = await fx_uow().read_book(project.id, lambda repos: repos.projects.overview(project))
        assert overview.source_count == 1
