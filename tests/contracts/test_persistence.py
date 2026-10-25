"""Contract of the persistence ports, run against the SQL adapter, the application's own.

Every stored book needs an owner the catalogue accepts, which ``fx_new_owner`` creates. A test that only names a
book that is never stored takes a bare identifier. The repositories of a book are used through ``Book.read`` and
``Book.change``, and those of the catalogue through ``read`` and ``change``.
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
from bookreviver.domain.ids import BookId, PageVersionId, SourceId
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
    directory_of,
    make_page,
    make_page_version,
    make_record,
    make_scan,
    make_source,
    new_account_id,
    new_book_id,
)
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import BookRecord, Page, Source
    from bookreviver.domain.ids import AccountId, PageId
    from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory, LocatedBookDatabases

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
REJECTION_DETAIL: str = 'The book already has this file as book.pdf.'
# Keys of the recorded size of a version, and the key of the processor that draws a blank leaf
HEIGHT_PX: str = 'height_px'
WIDTH_PX: str = 'width_px'
BLANK_PROCESSOR_KEY: str = 'pages.blank'


async def _add_book(book_id: BookId, *, databases: LocatedBookDatabases) -> list[Page]:
    """Add a source of ``PAGE_COUNT`` scans and one page per scan, the last kept out of the book, in one change.

    The change is ``Book.change`` of the book, so the book must be stored already.

    :param book_id: The identifier of the book.
    :type book_id: BookId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The pages in book order.
    :rtype: list[Page]
    """
    source = make_source()
    scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
    pages = [make_page(order_key=f'a{scan.number}', scan=scan) for scan in scans]
    pages[-1] = evolve(pages[-1], included=False)

    def add_rows(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Add the source, its scans and their pages.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Record of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        repos.sources.add(source)
        repos.scans.add_many(scans)
        repos.pages.add_many(pages)

    await databases.open(book_id).change(add_rows)
    return pages


def _unimported(*, status: SourceStatus, name: str, minutes: int = 0, typed: bool = True) -> Source:
    """Build a rejected or a skipped file of a book, which holds no scan and whose digest follows from its name.

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
        make_source(name=name, minutes=minutes),
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
    return LibraryEntry(
        book_id=BookId(uuid4()),
        owner_id=owner_id,
        directory=directory_of(new_book_id()),
        added_at=EPOCH + timedelta(minutes=minutes),
    )


class TestBookRecordRepository:
    """Contract of BookRecordRepository, the one record of a book."""

    async def test_created_book_reads_back_after_commit(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the record a book is created with is visible to a later catalogue database, unchanged.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        book_id = await store_book(fx_catalogue(), book_record, owner=await fx_new_owner(), databases=fx_databases())
        assert await fx_databases().open(book_id).read(lambda repos: repos.book_record.get()) == book_record

    async def test_update_replaces_details(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify an update stores the new description, whose contributors and identifiers come back in their order.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        described = evolve(book_record, details=FULL_DETAILS)
        book_id = await store_book(fx_catalogue(), book_record, owner=await fx_new_owner(), databases=fx_databases())
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_record.update(described))
        stored = await fx_databases().open(book_id).read(lambda repos: repos.book_record.get())
        assert stored.details == FULL_DETAILS

    async def test_update_clears_lists_and_height(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify an update to empty lists and no height is stored, so a cleared field does not come back.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = evolve(make_record(), details=FULL_DETAILS)
        cleared = evolve(book_record, details=BookDetails(title=FULL_DETAILS.title))
        book_id = await store_book(fx_catalogue(), book_record, owner=await fx_new_owner(), databases=fx_databases())
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_record.update(cleared))
        stored = await fx_databases().open(book_id).read(lambda repos: repos.book_record.get())
        assert stored.details == BookDetails(title=FULL_DETAILS.title)

    async def test_overview_counts_the_book_of_one_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the overview counts the included pages, the sources and the scans of the book.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, owner=await fx_new_owner(), databases=fx_databases())
        await _add_book(book_id, databases=fx_databases())
        overview = await fx_databases().open(book_id).read(lambda repos: repos.book_record.overview())
        assert (overview.book_record, overview.page_count, overview.source_count, overview.scan_count) == (
            book_record,
            *BOOK,
        )

    async def test_overview_counts_the_pages_with_an_image_kept_out_of_the_book_but_not_placeholders(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the pages a run goes over are every page with an image, kept out of the book or not, no placeholder.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        await _add_book(book_id, databases=fx_databases())
        placeholder = make_page(order_key=KEY_B0)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(placeholder))
        overview = await fx_databases().open(book_id).read(lambda repos: repos.book_record.overview())
        assert (overview.page_count, overview.image_page_count) == (BOOK[0] + 1, PAGE_COUNT)

    async def test_the_cover_is_the_first_page_of_the_kind_cover(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the overview names as the cover the page of the kind cover that comes first in the book.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        text, cover, back = (
            make_page(order_key=key, kind=kind)
            for key, kind in (('a0', PageKind.TEXT), ('a1', PageKind.COVER), ('a2', PageKind.COVER))
        )
        book_id = await store_book(
            fx_catalogue(), make_record(), back, text, cover, owner=await fx_new_owner(), databases=fx_databases()
        )
        overview = await fx_databases().open(book_id).read(lambda repos: repos.book_record.overview())
        assert overview.cover_page_id == cover.id

    async def test_a_book_without_a_cover_page_shows_its_first_page_and_an_empty_book_none(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the cover falls back to the first page of the book, and a book without pages has no cover.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        first, second = (make_page(order_key=key) for key in ('a0', 'a1'))
        empty = await store_book(fx_catalogue(), make_record(), owner=owner_id, databases=fx_databases())
        book_id = await store_book(
            fx_catalogue(), make_record(), second, first, owner=owner_id, databases=fx_databases()
        )

        async def cover_of(book: BookId) -> PageId | None:
            """Read the cover the overview of a book names.

            :param book: The book.
            :type book: BookId
            :returns: The cover page, or None.
            :rtype: PageId | None
            """
            return (await fx_databases().open(book).read(lambda repos: repos.book_record.overview())).cover_page_id

        assert [await cover_of(book) for book in (empty, book_id)] == [None, first.id]


class TestLibraryRepository:
    """Contract of LibraryRepository, which says which account owns which book."""

    async def test_added_entry_reads_back_after_commit(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a committed entry is visible to a later catalogue database, unchanged.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entry = _entry(await fx_new_owner())
        await fx_catalogue().change(lambda repos: repos.library.add(entry))
        assert await fx_catalogue().read(lambda repos: repos.library.get(entry.book_id)) == entry

    async def test_adding_a_listed_book_raises_conflict(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a book is in one library, so a second entry for it is a ConflictError and not an overwrite.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entry = _entry(await fx_new_owner())
        await fx_catalogue().change(lambda repos: repos.library.add(entry))
        stolen = evolve(entry, owner_id=await fx_new_owner())
        with pytest.raises(ConflictError, match=str(entry.book_id)):
            await fx_catalogue().change(lambda repos: repos.library.add(stolen))

    async def test_entry_of_an_account_that_is_not_stored_raises_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory
    ) -> None:
        """Verify the owner of an entry must be an account of the catalogue.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        """
        entry = _entry(new_account_id())
        with pytest.raises(NotFoundError):
            await fx_catalogue().change(lambda repos: repos.library.add(entry))

    async def test_deleted_entry_is_gone_and_a_second_deletion_is_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a deleted entry answers NotFoundError, and so does deleting it again.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entry = _entry(await fx_new_owner())
        await fx_catalogue().change(lambda repos: repos.library.add(entry))
        await fx_catalogue().change(lambda repos: repos.library.delete(entry.book_id))
        with pytest.raises(NotFoundError, match=str(entry.book_id)):
            await fx_catalogue().read(lambda repos: repos.library.get(entry.book_id))
        with pytest.raises(NotFoundError, match=str(entry.book_id)):
            await fx_catalogue().change(lambda repos: repos.library.delete(entry.book_id))

    async def test_list_for_owner_orders_slices_and_counts(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the owner's books come latest added first, sliced, with the total, and others are hidden.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id, other_owner_id = await fx_new_owner(), await fx_new_owner()
        older, newer = _entry(owner_id, minutes=1), _entry(owner_id, minutes=2)
        elsewhere = _entry(other_owner_id, minutes=3)
        await fx_catalogue().change(lambda repos: [repos.library.add(entry) for entry in (older, elsewhere, newer)])
        reader = fx_catalogue()
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
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify books added at the same moment list by identifier, so offset paging never skips or repeats one.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        tied = sorted((_entry(owner_id, minutes=1) for _ in range(PAGE_COUNT)), key=attrgetter('book_id'))
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        await fx_catalogue().change(lambda repos: [repos.library.add(entry) for entry in reversed(tied)])

        def list_whole_and_by_page(repos: CatalogueRepositories) -> tuple[list[BookId], list[BookId]]:
            """List the owner's books in one window and then one book at a time.

            :param repos: Repositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The identifiers of the whole listing, and of the pages of one book each, joined.
            :rtype: tuple[list[BookId], list[BookId]]
            """
            whole = [item.book_id for item in repos.library.list_for_owner(owner_id, SliceRequest()).items]
            paged = [
                item.book_id
                for offset in range(PAGE_COUNT)
                for item in repos.library.list_for_owner(owner_id, SliceRequest(offset=offset, limit=1)).items
            ]
            return whole, paged

        whole, paged = await fx_catalogue().read(list_whole_and_by_page)
        expect(whole == [entry.book_id for entry in tied])
        expect(paged == [entry.book_id for entry in tied])
        assert_expectations()

    async def test_list_all_returns_the_entries_of_every_account_by_identifier(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the pass over all books sees the books of every account, ordered by identifier.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        entries = [_entry(await fx_new_owner(), minutes=minute) for minute in range(PAGE_COUNT)]
        await fx_catalogue().change(lambda repos: [repos.library.add(entry) for entry in entries])
        listed = await fx_catalogue().read(lambda repos: repos.library.list_all())
        assert list(listed) == sorted(entries, key=attrgetter('book_id'))


class TestSourceRepository:
    """Contract of SourceRepository."""

    async def test_added_source_reads_back_and_lists_in_import_order(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify committed sources read back unchanged and list by import time, without other books' sources.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        later = make_source(name=SECOND_PART_PDF, minutes=2)
        earlier = evolve(
            make_source(name=FIRST_PART_PDF, minutes=1),
            metadata={'pdf_version': '1.4'},
            suggestion=MetadataSuggestion(title='Book', publication_year='1887'),
        )
        foreign = make_source()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, owner=owner_id, databases=fx_databases())
        other_id = await store_book(catalogue, other, owner=owner_id, databases=fx_databases())
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.sources.add_many([later, earlier]))
        await fx_databases().open(other_id).change(lambda repos, _book_record: repos.sources.add(foreign))
        expect(await fx_databases().open(book_id).read(lambda repos: repos.sources.get(earlier.id)) == earlier)
        expect(await fx_databases().open(book_id).read(lambda repos: repos.sources.list_all()) == [earlier, later])
        assert_expectations()

    async def test_find_by_sha256_looks_only_inside_the_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a digest finds the source of the book with it, and nothing in another book, whose file holds none.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        source = make_source()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, owner=owner_id, sources=[source], databases=fx_databases())
        other_id = await store_book(catalogue, other, owner=owner_id, databases=fx_databases())
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.sources.find_by_sha256(source.sha256)) == source
        )
        expect(
            await fx_databases().open(other_id).read(lambda repos: repos.sources.find_by_sha256(source.sha256)) is None
        )
        assert_expectations()

    async def test_same_file_twice_in_a_book_raises_conflict(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a second source with the digest of a stored one is a ConflictError naming the digest.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await store_book(
            fx_catalogue(), make_record(), owner=await fx_new_owner(), sources=[make_source()], databases=fx_databases()
        )
        duplicate = make_source()
        with pytest.raises(ConflictError, match=duplicate.sha256):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.sources.add(duplicate))

    async def test_same_file_in_another_book_is_stored(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the digest is unique only within a book, so two books may share a file such as a cover.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        catalogue = fx_catalogue()
        books = [
            await store_book(
                catalogue, make_record(), owner=owner_id, sources=[make_source()], databases=fx_databases()
            )
            for _ in range(2)
        ]

        async def sources_of(book_id: BookId) -> int:
            """Count the sources of one book.

            :param book_id: The book.
            :type book_id: BookId
            :returns: How many sources it has.
            :rtype: int
            """
            return await fx_databases().open(book_id).read(lambda repos: len(repos.sources.list_all()))

        counts = [await sources_of(book_id) for book_id in books]
        assert counts == [1, 1]

    async def test_source_of_a_missing_book_raises_not_found(self, fx_databases: BookDatabasesFactory) -> None:
        """Verify a source needs its book, reported by the missing identifier.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = new_book_id()
        orphan = make_source()
        # The book is not stored, so the change is refused before the source is added
        with pytest.raises(NotFoundError, match=str(book_id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.sources.add(orphan))

    async def test_delete_cascades_to_its_scans_only(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify deleting a source removes its scans and leaves the scans of the book's other sources.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        doomed, kept = (
            make_source(name=FIRST_PDF),
            make_source(name=SECOND_PDF),
        )
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            owner=await fx_new_owner(),
            sources=[doomed, kept],
            scans=[make_scan(source=source, number=0) for source in (doomed, kept)],
            databases=fx_databases(),
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.sources.delete(doomed.id))
        expect(await fx_databases().open(book_id).read(lambda repos: repos.scans.list_for_source(doomed.id)) == [])
        expect(len(await fx_databases().open(book_id).read(lambda repos: repos.scans.list_for_source(kept.id))) == 1)
        assert_expectations()

    async def test_delete_leaves_the_pages_of_its_scans(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify deleting a source keeps the pages cut from its scans, with their versions, without their scan.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        pages = await _add_book(book_id, databases=fx_databases())
        version = make_page_version(page_id=pages[0].id)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.add(version))

        def delete_the_source(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Delete the only source of the book, which the work finds by listing.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.sources.delete(repos.sources.list_all()[0].id)

        await fx_databases().open(book_id).change(delete_the_source)
        kept = await fx_databases().open(book_id).read(lambda repos: repos.pages.list_window(SliceRequest()))
        expect(list(kept.items) == [evolve(page, scan_id=None) for page in pages])
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(pages[0].id))
            == [version]
        )
        assert_expectations()


class TestScanRepository:
    """Contract of ScanRepository."""

    async def test_list_by_ids_returns_the_stored_scans_among_them(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify scans read by identifier come back once each, and an identifier that is not stored is left out.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        scans = [make_scan(source=source, number=number) for number in range(PAGE_COUNT)]
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            owner=await fx_new_owner(),
            sources=[source],
            scans=scans,
            databases=fx_databases(),
        )
        unstored = make_scan(source=source, number=PAGE_COUNT)
        wanted = [scans[0].id, scans[2].id, scans[2].id, unstored.id]
        found = await fx_databases().open(book_id).read(lambda repos: repos.scans.list_by_ids(wanted))
        expect(sorted(found, key=attrgetter('number')) == [scans[0], scans[2]])
        expect(await fx_databases().open(book_id).read(lambda repos: repos.scans.list_by_ids([])) == [])
        assert_expectations()

    async def test_scans_read_back_by_number_within_their_source(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify committed scans read back unchanged and list by their number in the source.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        scans = [
            evolve(make_scan(source=source, number=number), source_label=f'{number + 1}')
            for number in reversed(range(PAGE_COUNT))
        ]
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            owner=await fx_new_owner(),
            sources=[source],
            scans=scans,
            databases=fx_databases(),
        )
        expect(await fx_databases().open(book_id).read(lambda repos: repos.scans.get(scans[0].id)) == scans[0])
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.scans.list_for_source(source.id))
            == list(reversed(scans))
        )
        assert_expectations()

    async def test_list_for_book_follows_the_import_order_of_sources(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the scans of a book list source by source in import order, sliced, with the full total.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        first = make_source(name=FIRST_PART_PDF, minutes=1)
        second = make_source(name=SECOND_PART_PDF, minutes=2)
        expected = [make_scan(source=source, number=number) for source in (first, second) for number in range(2)]
        # Insert against the expected order, so neither insertion nor storage order can pass for it
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            owner=await fx_new_owner(),
            sources=[second, first],
            scans=list(reversed(expected)),
            databases=fx_databases(),
        )
        everything = await fx_databases().open(book_id).read(lambda repos: repos.scans.list_window(SliceRequest()))
        window = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.scans.list_window(SliceRequest(offset=1, limit=2)))
        )
        expect([scan.id for scan in everything.items] == [scan.id for scan in expected])
        expect([scan.id for scan in window.items] == [scan.id for scan in expected[1:3]])
        expect(window.total == len(expected))
        assert_expectations()

    async def test_update_stores_the_renditions_state(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a scan marked ready in a new renditions version and a PNG full image reads back so.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        scan = make_scan(source=source, number=0)
        book_id = await store_book(
            fx_catalogue(), make_record(), owner=await fx_new_owner(), sources=[source], databases=fx_databases()
        )
        ready = evolve(scan, renditions=Renditions(ready=True, version=2, full=Rendition.FULL_PNG))

        def add_and_ready(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the scan and replace it with its ready state in the same change.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.scans.add(scan)
            repos.scans.update(ready)

        await fx_databases().open(book_id).change(add_and_ready)
        assert await fx_databases().open(book_id).read(lambda repos: repos.scans.get(scan.id)) == ready

    async def test_list_unready_returns_the_scans_without_ready_renditions_in_import_order(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the unready scans of a book list source by source and by number, without ready or foreign ones.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        first = make_source(name=FIRST_PDF, minutes=1)
        second = make_source(name=SECOND_PDF, minutes=2)
        foreign = make_source(name='c.pdf', minutes=0)
        ready = Renditions(ready=True)
        scans = [
            make_scan(source=second, number=0),
            make_scan(source=second, number=1),
            evolve(make_scan(source=first, number=0), renditions=ready),
            make_scan(source=first, number=1),
            make_scan(source=first, number=2),
        ]
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue, book_record, owner=owner_id, sources=[second, first], scans=scans, databases=fx_databases()
        )
        await store_book(
            catalogue,
            other,
            owner=owner_id,
            sources=[foreign],
            scans=[make_scan(source=foreign, number=0)],
            databases=fx_databases(),
        )
        unready = await fx_databases().open(book_id).read(lambda repos: repos.scans.list_unready())
        assert [(scan.source_id, scan.number) for scan in unready] == [
            (first.id, 1),
            (first.id, 2),
            (second.id, 0),
            (second.id, 1),
        ]

    async def test_list_unready_is_empty_for_a_book_with_every_scan_ready(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a book whose scans are all ready, or that has none, has nothing left to cut.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        expect(await fx_databases().open(book_id).read(lambda repos: repos.scans.list_unready()) == [])
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))

        def add_ready_scan(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the source and its ready scan.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.sources.add(source)
            repos.scans.add(scan)

        await fx_databases().open(book_id).change(add_ready_scan)
        expect(await fx_databases().open(book_id).read(lambda repos: repos.scans.list_unready()) == [])
        assert_expectations()

    async def test_same_number_twice_in_a_source_raises_conflict(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify two scans cannot hold the same position of one source.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            make_record(),
            owner=await fx_new_owner(),
            sources=[source],
            scans=[make_scan(source=source, number=0)],
            databases=fx_databases(),
        )
        clashing = [make_scan(source=source, number=1), make_scan(source=source, number=0)]
        with pytest.raises(ConflictError, match=str(source.id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.scans.add_many(clashing))

    async def test_scan_of_a_missing_source_raises_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a scan cannot be stored for a source that does not exist, so no scan outlives its file.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        orphan = make_scan(source=source, number=0)
        with pytest.raises(NotFoundError, match=str(source.id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.scans.add(orphan))


class TestPageRepository:
    """Contract of PageRepository."""

    async def test_pages_list_in_the_byte_order_of_their_keys(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify pages list by order key compared byte by byte, sliced, with every page of the book in the total.

        Upper-case letters sort before lower-case ones, and two keys differing only in case are two positions, which
        a case-insensitive collation would get wrong.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        pages = [make_page(order_key=key) for key in UNORDERED_KEYS]
        book_id = await store_book(
            fx_catalogue(), make_record(), *pages, owner=await fx_new_owner(), databases=fx_databases()
        )
        everything = await fx_databases().open(book_id).read(lambda repos: repos.pages.list_window(SliceRequest()))
        window = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.pages.list_window(SliceRequest(offset=1, limit=2)))
        )
        beyond = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: repos.pages.list_window(SliceRequest(offset=len(UNORDERED_KEYS), limit=1)),
            )
        )
        expect([page.order_key for page in everything.items] == [KEY_ZZ, KEY_A0, KEY_A0_UPPER, KEY_A0_LOWER, KEY_A1])
        expect([page.order_key for page in window.items] == [KEY_A0, KEY_A0_UPPER])
        expect((window.total, list(beyond.items), beyond.total) == (len(UNORDERED_KEYS), [], len(UNORDERED_KEYS)))
        assert_expectations()

    async def test_last_order_key_is_the_greatest_of_the_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the last key is the greatest in byte order, None for an empty book, and blind to other books.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, empty = make_record(), make_record()
        catalogue = fx_catalogue()
        pages = [make_page(order_key=key) for key in UNORDERED_KEYS]
        book_id = await store_book(catalogue, book_record, *pages, owner=owner_id, databases=fx_databases())
        empty_id = await store_book(catalogue, empty, owner=owner_id, databases=fx_databases())
        expect(await fx_databases().open(book_id).read(lambda repos: repos.pages.last_order_key()) == KEY_A1)
        expect(await fx_databases().open(empty_id).read(lambda repos: repos.pages.last_order_key()) is None)
        assert_expectations()

    async def test_kind_tally_counts_the_pages_with_an_image_of_each_kind(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify each page counts under the kind it is processed by, and a placeholder and another book do not count.

        The kind is the blank kind for a blank page, and otherwise what the page shows: what the user set, else what the
        program found, else a plate counts as a picture in colour.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        source = make_source()
        scans = [make_scan(source=source, number=number) for number in range(5)]
        text, plate, blank, found, by_hand = (
            make_page(order_key=f'a{number}', scan=scan, kind=kind)
            for number, (scan, kind) in enumerate(
                zip(scans, (PageKind.TEXT, PageKind.PLATE, PageKind.BLANK, PageKind.TEXT, PageKind.PLATE), strict=True)
            )
        )
        found = evolve(found, content_type=ContentType.BW_PICTURE)
        by_hand = evolve(by_hand, content_type=ContentType.TEXT, content_by_hand=True)
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            book_record,
            text,
            plate,
            blank,
            found,
            by_hand,
            make_page(order_key=KEY_B0),
            owner=owner_id,
            sources=[source],
            scans=scans,
            databases=fx_databases(),
        )
        await store_book(catalogue, other, make_page(order_key=KEY_A0), owner=owner_id, databases=fx_databases())
        tally = await fx_databases().open(book_id).read(lambda repos: repos.pages.kind_tally())
        assert dict(tally) == {
            RecipeKind.TEXT: 2,
            RecipeKind.COLOR_PICTURE: 1,
            RecipeKind.BW_PICTURE: 1,
            RecipeKind.BLANK: 1,
        }

    async def test_count_before_is_the_position_in_the_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page counts the pages before it in byte order, excluded ones included, and none of another book.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        catalogue = fx_catalogue()
        book = [make_page(order_key=key) for key in UNORDERED_KEYS]
        book[0] = evolve(book[0], included=False)
        book_id = await store_book(catalogue, make_record(), *book, owner=owner_id, databases=fx_databases())

        def count_before_each(repos: BookRepositories) -> list[int]:
            """Count the pages before each page of the book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The position of each page of the book, in the order they were stored.
            :rtype: list[int]
            """
            return [repos.pages.count_before(page) for page in book]

        positions = await fx_databases().open(book_id).read(count_before_each)
        # The keys are stored as a1, a0v, a0V, a0, Zz and sort as Zz, a0, a0V, a0v, a1
        expect(positions == [4, 3, 2, 1, 0])
        assert_expectations()

    async def test_list_for_scan_returns_the_pages_of_that_scan_by_slot(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the halves of a split scan list by slot, without the pages of other scans or of no scan.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        spread, other = make_scan(source=source, number=0), make_scan(source=source, number=1)
        right = evolve(make_page(order_key=KEY_A1, scan=spread), slot=2)
        left = evolve(make_page(order_key=KEY_A2, scan=spread), slot=1)
        elsewhere = make_page(order_key=KEY_A3, scan=other)
        placeholder = make_page(order_key='a4')
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            make_record(),
            right,
            left,
            elsewhere,
            placeholder,
            owner=await fx_new_owner(),
            sources=[source],
            scans=[spread, other],
            databases=fx_databases(),
        )
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.pages.list_for_scan(spread.id)) == [left, right]
        )
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.pages.list_for_scan(other.id)) == [elsewhere]
        )
        assert_expectations()

    async def test_included_only_lists_the_pages_of_the_book_and_counts_them(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a listing of the included pages skips the excluded ones in its window and in its total.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = [make_page(order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2)]
        book[1] = evolve(book[1], included=False)
        book_id = await store_book(
            fx_catalogue(), make_record(), *book, owner=await fx_new_owner(), databases=fx_databases()
        )
        everything = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.pages.list_window(SliceRequest(), included_only=True))
        )
        window = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: repos.pages.list_window(SliceRequest(offset=1, limit=1), included_only=True),
            )
        )
        expect(list(everything.items) == [book[0], book[2]])
        expect((list(window.items), window.total) == ([book[2]], 2))
        assert_expectations()

    async def test_list_by_ids_returns_the_pages_in_book_order(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify pages named in any order, with repeats, come back once each in the byte order of their keys.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = [make_page(order_key=key) for key in UNORDERED_KEYS]
        book_id = await store_book(
            fx_catalogue(), make_record(), *book, owner=await fx_new_owner(), databases=fx_databases()
        )
        wanted = [book[0].id, book[2].id, book[4].id, book[2].id]
        listed = await fx_databases().open(book_id).read(lambda repos: repos.pages.list_by_ids(wanted))
        # Stored as a1, a0v, a0V, a0, Zz, so these three are a1, a0V and Zz
        assert [page.order_key for page in listed] == [KEY_ZZ, KEY_A0_UPPER, KEY_A1]

    async def test_list_by_ids_refuses_a_missing_page_and_a_page_of_another_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify one identifier that names no page of the book fails the whole read, naming that identifier.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        own, foreign = make_page(), make_page()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, own, owner=owner_id, databases=fx_databases())
        await store_book(catalogue, other, foreign, owner=owner_id, databases=fx_databases())
        missing = make_page()
        with pytest.raises(NotFoundError, match=str(foreign.id)):
            await fx_databases().open(book_id).read(lambda repos: repos.pages.list_by_ids([own.id, foreign.id]))
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await fx_databases().open(book_id).read(lambda repos: repos.pages.list_by_ids([own.id, missing.id]))

    async def test_list_for_source_returns_the_pages_of_its_scans_in_book_order(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the pages of one source list in book order, without pages of other sources or without a scan.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        first, second = (
            make_source(name=FIRST_PDF),
            make_source(name=SECOND_PDF),
        )
        scans = [
            make_scan(source=first, number=0),
            make_scan(source=second, number=0),
            make_scan(source=first, number=1),
        ]
        late, elsewhere, early = (
            make_page(order_key=key, scan=scan) for key, scan in zip((KEY_A2, KEY_A1, KEY_A0), scans, strict=True)
        )
        placeholder = make_page(order_key=KEY_A3)
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            make_record(),
            late,
            elsewhere,
            early,
            placeholder,
            owner=await fx_new_owner(),
            sources=[first, second],
            scans=scans,
            databases=fx_databases(),
        )
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.pages.list_for_source(first.id))
            == [early, late]
        )
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.pages.list_for_source(second.id)) == [elsewhere]
        )
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.pages.list_for_source(SourceId(uuid4()))) == []
        )
        assert_expectations()

    async def test_neighbour_key_is_the_nearest_key_on_a_side_without_the_excluded_pages(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the nearest key before or after another, in byte order, skipping pages left out and other books.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        book = [make_page(order_key=key) for key in UNORDERED_KEYS]
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, *book, owner=owner_id, databases=fx_databases())
        await store_book(catalogue, other, make_page(order_key=KEY_A0_W), owner=owner_id, databases=fx_databases())
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
                pages.neighbour_key(KEY_A0_UPPER, Side.BEFORE),
                pages.neighbour_key(KEY_A0_UPPER, Side.AFTER),
                pages.neighbour_key(KEY_A0_UPPER, Side.AFTER, excluding=[by_key[KEY_A0_LOWER]]),
                pages.neighbour_key(KEY_A0_UPPER, Side.BEFORE, excluding=[by_key[KEY_A0], by_key[KEY_ZZ]]),
                pages.neighbour_key(KEY_ZZ, Side.BEFORE),
                pages.neighbour_key(KEY_A1, Side.AFTER),
            ]

        assert await fx_databases().open(book_id).read(look_around) == [
            KEY_A0,
            KEY_A0_LOWER,
            KEY_A1,
            None,
            None,
            None,
        ]

    async def test_update_many_writes_every_page_in_one_transaction(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify several pages changed together read back changed, and the pages left out stay as they were.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = [make_page(order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2)]
        book_id = await store_book(
            fx_catalogue(), make_record(), *book, owner=await fx_new_owner(), databases=fx_databases()
        )
        moved = [evolve(book[0], order_key='a1V'), evolve(book[1], order_key='a1G')]
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.update_many(moved))
        everything = await fx_databases().open(book_id).read(lambda repos: repos.pages.list_window(SliceRequest()))
        assert list(everything.items) == [moved[1], moved[0], book[2]]

    async def test_get_after_update_many_in_the_same_change_returns_the_new_state(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page read back inside the change that updated it is the updated page, not the one stored before.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book = [make_page(order_key=key) for key in (KEY_A0, KEY_A1)]
        book_id = await store_book(
            fx_catalogue(), make_record(), *book, owner=await fx_new_owner(), databases=fx_databases()
        )
        moved = [evolve(page, manual_label=PAGE_LABEL, included=False) for page in book]

        def update_and_read(repos: BookRepositories, _book_record: BookRecord) -> list[Page]:
            """Update both pages, then read them back in the same transaction.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            :returns: The pages as the transaction reads them after the update.
            :rtype: list[Page]
            """
            repos.pages.update_many(moved)
            return [repos.pages.get(page.id) for page in book]

        assert await fx_databases().open(book_id).change(update_and_read) == moved

    async def test_update_to_a_key_another_page_holds_raises_conflict(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page cannot take another page's position by update, as a move that lost a race would try to.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        first, second = (make_page(order_key=key) for key in (KEY_A0, KEY_A1))
        book_id = await store_book(
            fx_catalogue(), make_record(), first, second, owner=await fx_new_owner(), databases=fx_databases()
        )
        taking = evolve(first, order_key=KEY_A1)
        with pytest.raises(ConflictError, match=KEY_A1):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.update(taking))
        with pytest.raises(ConflictError, match=KEY_A1):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.update_many([taking]))

    async def test_update_many_with_a_missing_page_raises_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page that is not stored fails the update, naming its identifier.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        missing = make_page()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.update_many([missing]))

    async def test_updated_page_reads_back(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page moved, numbered, given a kind and kept out of the book reads back so.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        book_id = await store_book(
            fx_catalogue(), make_record(), page, owner=await fx_new_owner(), databases=fx_databases()
        )
        changed = evolve(
            page, order_key=KEY_A0_UPPER, manual_label=PAGE_LABEL, kind=PageKind.TITLE, included=False, notes='Stamp'
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.update(changed))
        assert await fx_databases().open(book_id).read(lambda repos: repos.pages.get(page.id)) == changed

    async def test_same_order_key_twice_in_a_book_raises_conflict(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify two pages of one book cannot share a position, while another book may use the same key.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        catalogue = fx_catalogue()
        book_id, _ = [
            await store_book(
                catalogue, make_record(), make_page(order_key=KEY_A5), owner=owner_id, databases=fx_databases()
            )
            for _ in range(2)
        ]
        clashing = make_page(order_key=KEY_A5)
        with pytest.raises(ConflictError, match=KEY_A5):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(clashing))

    async def test_same_part_of_a_scan_twice_raises_conflict(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify one slot of a scan becomes one page at most, while any number of placeholders have no scan.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        scan = make_scan(source=source, number=0)
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            make_record(),
            *[make_page(order_key=key) for key in (KEY_A0, KEY_A1)],
            make_page(order_key=KEY_A2, scan=scan),
            owner=await fx_new_owner(),
            sources=[source],
            scans=[scan],
            databases=fx_databases(),
        )
        second_page = make_page(order_key=KEY_A3, scan=scan)
        with pytest.raises(ConflictError, match=str(scan.id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(second_page))

    async def test_page_of_a_missing_parent_raises_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page needs its book and the scan it names, reported by the missing identifier.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = new_book_id()
        missing_scan = make_scan(source=make_source(), number=0)
        orphan = make_page()
        catalogue = fx_catalogue()
        # The book is not stored, so the change is refused before the page is added
        with pytest.raises(NotFoundError, match=str(book_id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(orphan))
        await store_book(
            catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases(), book_id=book_id
        )
        scanned = make_page(scan=missing_scan)
        with pytest.raises(NotFoundError, match=str(missing_scan.id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(scanned))

    async def test_delete_cascades_to_its_versions(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify deleting a page removes its versions and leaves the versions of other pages.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        doomed, kept = (make_page(order_key=key) for key in (KEY_A0, KEY_A1))
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            doomed,
            kept,
            owner=await fx_new_owner(),
            versions=[make_page_version(page_id=page.id) for page in (doomed, kept)],
            databases=fx_databases(),
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.delete(doomed.id))
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(doomed.id)) == []
        )
        expect(
            len(await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(kept.id))) == 1
        )
        assert_expectations()

    async def test_missing_page_raises_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify reading a page that the book does not hold raises NotFoundError naming the page.

        :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await store_book(fx_catalogue(), make_record(), owner=await fx_new_owner(), databases=fx_databases())
        page = make_page()
        with pytest.raises(NotFoundError, match=str(page.id)):
            await fx_databases().open(book_id).read(lambda repos: repos.pages.get(page.id))


class TestPageVersionRepository:
    """Contract of PageVersionRepository."""

    async def test_list_to_prepare_returns_pending_and_failed_versions_of_the_given_processors(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the versions still to write come back earliest first, and nothing else does.

        Ready and running versions, versions of other processors, such as the half of a split spread that failed, and
        versions of another book are left out.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        pages = [make_page(order_key=f'a{number}') for number in range(5)]
        elsewhere = make_page()
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
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            book_record,
            *pages,
            owner=owner_id,
            versions=[pending, failed, ready, running, spread_half],
            databases=fx_databases(),
        )
        await store_book(
            catalogue,
            other,
            elsewhere,
            owner=owner_id,
            versions=[make_page_version(page_id=elsewhere.id)],
            databases=fx_databases(),
        )
        found = (
            await fx_databases()
            .open(book_id)
            .read(
                lambda repos: repos.page_versions.list_to_prepare({'split.none', BLANK_PROCESSOR_KEY}),
            )
        )
        only_blank = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_versions.list_to_prepare({BLANK_PROCESSOR_KEY}))
        )
        expect(list(found) == [failed, pending])
        expect(list(only_blank) == [failed])
        assert_expectations()

    async def test_base_sizes_are_those_of_the_base_versions_of_the_included_scan_pages(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify only the recorded sizes of base versions of included pages cut from a scan are returned.

        A page kept out, a generated leaf, a later version, a leaf drawn in place of a scan, a version recording no size
        and another book's page take no part.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        sources = [make_source() for _ in range(2)]
        scans = [make_scan(source=sources[0], number=number) for number in range(5)]
        foreign_scan = make_scan(source=sources[1], number=0)
        pages = [make_page(order_key=f'a{number}', scan=scan) for number, scan in enumerate(scans)]
        pages[1] = evolve(pages[1], included=False)
        leaf = evolve(make_page(order_key=KEY_B0), origin=PageOrigin.BLANK)
        foreign_page = make_page(scan=foreign_scan)
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
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            make_record(),
            *pages,
            leaf,
            owner=owner_id,
            sources=[sources[0]],
            scans=scans,
            versions=[*versions[:-1], *derived_pair],
            databases=fx_databases(),
        )
        await store_book(
            catalogue,
            make_record(),
            foreign_page,
            owner=owner_id,
            sources=[sources[1]],
            scans=[foreign_scan],
            versions=[versions[-1]],
            databases=fx_databases(),
        )
        found = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.base_sizes())
        assert sorted(found, key=attrgetter(WIDTH_PX)) == [
            PageSize(width_px=10, height_px=20, dpi=None),
            PageSize(width_px=2000, height_px=3000, dpi=300.0),
        ]

    async def test_the_choice_of_a_leaf_survives_the_store_and_leaves_the_scan_linked(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a blank page reads back with the leaf chosen for it and its scan, and with the scan again after.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        source = make_source()
        scan = make_scan(source=source, number=0)
        page = make_page(scan=scan, kind=PageKind.BLANK)
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            page,
            owner=await fx_new_owner(),
            sources=[source],
            scans=[scan],
            databases=fx_databases(),
        )

        def fill(repos: BookRepositories, _book_record: BookRecord, *, blank_fill: BlankFill) -> Page:
            """Read the page as stored and replace it with the given blank fill.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            :param blank_fill: What the blank page is filled with.
            :type blank_fill: BlankFill
            :returns: The page as it was stored before the change.
            :rtype: Page
            """
            stored = repos.pages.get(page.id)
            repos.pages.update(evolve(stored, blank_fill=blank_fill))
            return stored

        stored = await fx_databases().open(book_id).change(partial(fill, blank_fill=BlankFill.PAPER))
        leaf = await fx_databases().open(book_id).change(partial(fill, blank_fill=BlankFill.SCAN))
        expect(stored.blank_fill is BlankFill.SCAN)
        expect((leaf.blank_fill, leaf.scan_id) == (BlankFill.PAPER, scan.id))
        expect(
            (await fx_databases().open(book_id).read(lambda repos: repos.pages.get(page.id))).blank_fill
            is BlankFill.SCAN
        )
        assert_expectations()

    async def test_the_content_type_of_a_page_survives_the_store(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page reads back with no content type, then with one found, then with one set by hand.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        book_id = await store_book(
            fx_catalogue(), make_record(), page, owner=await fx_new_owner(), databases=fx_databases()
        )

        def set_content(
            repos: BookRepositories, _book_record: BookRecord, *, content_type: ContentType, by_hand: bool
        ) -> Page:
            """Read the page as stored and replace it with the given content type.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
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
        added = await fx_databases().open(book_id).change(by_found)
        found = await fx_databases().open(book_id).change(by_user)
        by_hand = await fx_databases().open(book_id).read(lambda repos: repos.pages.get(page.id))
        expect((added.content_type, added.content_by_hand) == (None, False))
        expect((found.content_type, found.content_by_hand) == (ContentType.BW_PICTURE, False))
        expect((by_hand.content_type, by_hand.content_by_hand) == (ContentType.COLOR_PICTURE, True))
        assert_expectations()

    async def test_versions_read_back_in_creation_order(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a page's versions read back unchanged, the earliest first, without other pages' versions.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page, other = (make_page(order_key=key) for key in (KEY_A0, KEY_A1))
        base = make_page_version(page_id=page.id, minutes=1)
        later = evolve(make_page_version(page_id=page.id, minutes=2), input_id=base.id, renditions=None)
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            page,
            other,
            owner=await fx_new_owner(),
            versions=[base, later, make_page_version(page_id=other.id)],
            databases=fx_databases(),
        )
        expect(await fx_databases().open(book_id).read(lambda repos: repos.page_versions.get(later.id)) == later)
        expect(
            await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(page.id))
            == [base, later]
        )
        assert_expectations()

    async def test_base_versions_of_several_pages_read_in_one_call(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify only the versions without an input come back, for the pages asked for, the earliest first.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        first, second, unasked = (make_page(order_key=key) for key in (KEY_A0, KEY_A1, KEY_A2))
        first_base = make_page_version(page_id=first.id, minutes=2)
        second_base = make_page_version(page_id=second.id, minutes=1)
        derived = evolve(make_page_version(page_id=first.id, minutes=3), input_id=first_base.id)
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            first,
            second,
            unasked,
            owner=await fx_new_owner(),
            versions=[first_base, second_base, derived, make_page_version(page_id=unasked.id)],
            databases=fx_databases(),
        )
        both = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: repos.page_versions.list_base_versions([first.id, second.id]))
        )
        expect(both == [second_base, first_base])
        expect(await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_base_versions([])) == [])
        assert_expectations()

    async def test_version_of_a_missing_parent_raises_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a version needs its page and the input version it names, reported by the missing identifier.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        missing_input = make_page_version(page_id=page.id)
        orphan = make_page_version(page_id=page.id)
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=await fx_new_owner(), databases=fx_databases())
        with pytest.raises(NotFoundError, match=str(page.id)):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.add(orphan))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(page))
        fed = evolve(orphan, input_id=missing_input.id)
        with pytest.raises(NotFoundError, match=missing_input.id):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.add(fed))

    async def test_deleted_input_leaves_the_versions_it_fed(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify deleting a version keeps the versions computed from it and empties their input.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        base = make_page_version(page_id=page.id)
        later = evolve(make_page_version(page_id=page.id, minutes=1), input_id=base.id)
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            page,
            owner=await fx_new_owner(),
            versions=[base, later],
            databases=fx_databases(),
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.page_versions.delete(base.id))
        listed = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(page.id))
        assert listed == [evolve(later, input_id=None)]

    async def test_update_of_a_version_read_before_its_page_was_deleted_raises_not_found(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a version read earlier cannot be stored again once the deletion of its page has committed.

        A job writing the files of a page does exactly this when the page is deleted meanwhile, and the update must
        not bring the version of a deleted page back.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        version = make_page_version(page_id=page.id)
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            page,
            owner=await fx_new_owner(),
            versions=[version],
            databases=fx_databases(),
        )
        [read] = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(page.id))
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.delete(page.id))

        with pytest.raises(NotFoundError, match=version.id):
            await (
                fx_databases()
                .open(book_id)
                .change(
                    lambda repos, _book_record: repos.page_versions.update(evolve(read, state=VersionState.READY)),
                )
            )

        assert await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(page.id)) == []


class TestSourceStatus:
    """Contract of SourceRepository for the files of an upload that the import rejected or skipped."""

    async def test_rejected_and_skipped_sources_read_back_with_their_status(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a rejected file keeps its reason and detail and a skipped one its status, with or without a type.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        rejected = _unimported(status=SourceStatus.REJECTED, name=FIRST_PDF, minutes=1)
        untyped = _unimported(status=SourceStatus.REJECTED, name=NOTES_TXT, minutes=2, typed=False)
        skipped = _unimported(status=SourceStatus.SKIPPED, name=SECOND_PDF, minutes=3)
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            owner=await fx_new_owner(),
            sources=[rejected, untyped, skipped],
            databases=fx_databases(),
        )

        def read_all(repos: BookRepositories) -> list[Source]:
            """Read the three sources by their identifiers.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The rejected source, the rejected source without a type and the skipped one, as stored.
            :rtype: list[Source]
            """
            return [repos.sources.get(source.id) for source in (rejected, untyped, skipped)]

        stored = await fx_databases().open(book_id).read(read_all)
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
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the digest is unique among the imported sources only, so a duplicate is stored as a rejected one.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        imported = make_source()
        repeats = [
            evolve(_unimported(status=status, name=FIRST_PDF), sha256=imported.sha256)
            for status in (SourceStatus.REJECTED, SourceStatus.REJECTED, SourceStatus.SKIPPED)
        ]
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue, make_record(), owner=await fx_new_owner(), sources=[imported], databases=fx_databases()
        )
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.sources.add_many(repeats))

        listed = await fx_databases().open(book_id).read(lambda repos: repos.sources.list_unimported())

        assert {source.id for source in listed} == {source.id for source in repeats}

    async def test_second_imported_source_with_the_digest_raises_conflict_beside_unimported_ones(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a rejected file with a digest does not let a second imported source with it in.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        imported = make_source()
        rejected = evolve(_unimported(status=SourceStatus.REJECTED, name=FIRST_PDF), sha256=imported.sha256)
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue, make_record(), owner=await fx_new_owner(), sources=[imported, rejected], databases=fx_databases()
        )
        duplicate = make_source()

        with pytest.raises(ConflictError, match=duplicate.sha256):
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.sources.add(duplicate))

    async def test_listing_and_digest_lookup_return_imported_sources_only(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify rejected and skipped files are neither listed as sources nor found by their digest.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        imported = make_source(name=FIRST_PDF)
        rejected = _unimported(status=SourceStatus.REJECTED, name=SECOND_PDF)
        skipped = _unimported(status=SourceStatus.SKIPPED, name=FIRST_PART_PDF)
        book_id = await store_book(
            fx_catalogue(),
            make_record(),
            owner=await fx_new_owner(),
            sources=[imported, rejected, skipped],
            databases=fx_databases(),
        )

        def find_digests(repos: BookRepositories) -> list[Source | None]:
            """Look up the digests of the rejected and the skipped files.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: What the lookup finds for the rejected file and for the skipped one.
            :rtype: list[Source | None]
            """
            return [repos.sources.find_by_sha256(source.sha256) for source in (rejected, skipped)]

        expect(await fx_databases().open(book_id).read(lambda repos: repos.sources.list_all()) == [imported])
        expect(await fx_databases().open(book_id).read(find_digests) == [None, None])
        assert_expectations()

    async def test_unimported_sources_list_in_recorded_order_inside_the_book(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the rejected and skipped files of a book list earliest first, without imported or foreign ones.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        later = _unimported(status=SourceStatus.SKIPPED, name=SECOND_PDF, minutes=2)
        earlier = _unimported(status=SourceStatus.REJECTED, name=FIRST_PDF, minutes=1)
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            book_record,
            owner=owner_id,
            sources=[later, earlier, make_source(name=NOTES_TXT)],
            databases=fx_databases(),
        )
        await store_book(
            catalogue,
            other,
            owner=owner_id,
            sources=[_unimported(status=SourceStatus.REJECTED, name=FIRST_PDF)],
            databases=fx_databases(),
        )

        listed = await fx_databases().open(book_id).read(lambda repos: repos.sources.list_unimported())

        assert listed == [earlier, later]

    async def test_delete_unimported_keeps_imported_sources_and_other_books(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify closing the report of an import deletes the book's rejected and skipped files and nothing else.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        book_record, other = make_record(), make_record()
        imported = make_source()
        foreign = _unimported(status=SourceStatus.REJECTED, name=FIRST_PDF)
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue,
            book_record,
            owner=owner_id,
            sources=[
                imported,
                _unimported(status=SourceStatus.REJECTED, name=FIRST_PDF),
                _unimported(status=SourceStatus.SKIPPED, name=SECOND_PDF),
            ],
            databases=fx_databases(),
        )
        other_id = await store_book(catalogue, other, owner=owner_id, sources=[foreign], databases=fx_databases())

        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.sources.delete_unimported())

        expect(await fx_databases().open(book_id).read(lambda repos: repos.sources.list_unimported()) == [])
        expect(await fx_databases().open(book_id).read(lambda repos: repos.sources.list_all()) == [imported])
        expect(await fx_databases().open(other_id).read(lambda repos: repos.sources.list_unimported()) == [foreign])
        assert_expectations()

    async def test_book_overview_counts_imported_sources_only(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the source count of a book does not include its rejected and skipped files.

        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        sources = [
            make_source(),
            _unimported(status=SourceStatus.REJECTED, name=FIRST_PDF),
            _unimported(status=SourceStatus.SKIPPED, name=SECOND_PDF),
        ]
        book_id = await store_book(
            fx_catalogue(), make_record(), owner=owner_id, sources=sources, databases=fx_databases()
        )

        overview = await fx_databases().open(book_id).read(lambda repos: repos.book_record.overview())
        assert overview.source_count == 1
