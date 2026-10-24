"""Tests for the book use cases, against the database of the test and the local stores over a temporary directory."""

import io
import zipfile
from datetime import timedelta
from typing import TYPE_CHECKING, override
from uuid import uuid4

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalBookArchive, LocalSourceStore
from bookreviver.api.access import AccountBooks, books_of
from bookreviver.domain.enums import Orthography, Rendition
from bookreviver.domain.errors import DomainError, NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.keys import BookKeys, KeySegment
from bookreviver.domain.values import BookDetails, PageStepKey, Slice, SliceRequest
from bookreviver.services.book import BookTools
from bookreviver.services.books import BooksService
from bookreviver.services.stage_summaries import StageSummaries
from tests.helpers.builders import (
    DESKEW_STEP_ID,
    EPOCH,
    make_page,
    make_page_edit,
    make_page_step_state,
    make_record,
    new_account_id,
)
from tests.helpers.clock import FixedClock
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.scripted_runner import ScriptedRunner
from tests.helpers.seeding import commit_record, store_book
from tests.helpers.storage import BookFiles, SharedBookFileStores

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, BookOverview, BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookDatabases, BookRepositories, UnitOfWork
    from bookreviver.ports.storage import BookFileStores
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

NOW = EPOCH + timedelta(days=1)
PAGE_COUNT: int = 2
# The name of the database in the archive of a book
DATABASE_NAME: str = 'book.db'
# The hash an edit with a mask is stored under, and the content of its mask
MASK_EDIT_HASH: str = 'masked-edit'
FILE_CONTENT: bytes = b'derived file'
MASK_CONTENT: bytes = b'mask of the edit'


def _service(uow: UnitOfWork, stores: BookFileStores, root: Path, *, databases: BookDatabases) -> BooksService:
    """Build the book service for one request, over a unit of work, a catalogue with no processors and no jobs.

    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :param stores: Opens the source store and the asset store of a book of the test.
    :type stores: BookFileStores
    :param root: Storage root of the test, which the archive of the books packs and unpacks under.
    :type root: Path
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    :returns: The service, with a clock standing at ``NOW``.
    :rtype: BooksService
    """
    clock = FixedClock(NOW)
    board = FakeJobBoard(runner=ScriptedRunner(), publisher=RecordingEventBus(), clock=clock)
    stages = StageSummaries(catalogue=FakeCatalogue([]), board=board)
    return BooksService(
        uow=uow,
        databases=databases,
        tools=BookTools(stages=stages, clock=clock, archive=LocalBookArchive(root=root)),
        stores=stores,
    )


@pytest.fixture
def fx_service(
    fx_uow: UnitOfWorkFactory,
    fx_file_stores: SharedBookFileStores,
    fx_storage_root: Path,
    fx_databases: BookDatabasesFactory,
) -> Callable[[], BooksService]:
    """Return a function building the service for one request, with a clock standing at ``NOW``.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_file_stores: Opens the source store and the asset store of a book of the test.
    :type fx_file_stores: SharedBookFileStores
    :param fx_storage_root: Storage root of the test.
    :type fx_storage_root: Path
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: Function building a service over a new unit of work.
    :rtype: Callable[[], BooksService]
    """
    return lambda: _service(fx_uow(), fx_file_stores, fx_storage_root, databases=fx_databases())


class StoreFailedError(OSError):
    """Raised by the source store of a test in place of a real storage failure."""


class SourceStoreFailingOnce(LocalSourceStore):
    """The local source store of one book, whose first deletion fails before it removes anything."""

    def __init__(self, *, root: Path, book_id: BookId) -> None:
        """Keep the sources under ``root`` and fail the first deletion.

        :param root: Storage root shared with the asset store.
        :type root: Path
        :param book_id: The book whose sources the store holds.
        :type book_id: BookId
        """
        super().__init__(root=root, book_id=book_id)
        self._failed = False

    @override
    async def delete_all(self) -> None:
        """Fail on the first call, as a storage outage would, and remove the book's source on the later ones.

        :raises StoreFailedError: On the first call, with nothing removed.
        """
        if not self._failed:
            self._failed = True
            raise StoreFailedError
        await super().delete_all()


@pytest.fixture
def fx_flaky_service(
    fx_uow: UnitOfWorkFactory,
    fx_storage_root: Path,
    fx_databases: BookDatabasesFactory,
) -> Callable[[], BooksService]:
    """Return a function building the service for one request over a source store whose first deletion fails.

    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: Function building a service over a new unit of work and the one failing source store.
    :rtype: Callable[[], BooksService]
    """
    stores = SharedBookFileStores(root=fx_storage_root, source_store=SourceStoreFailingOnce)
    return lambda: _service(fx_uow(), stores, fx_storage_root, databases=fx_databases())


async def _stored(book_record: BookRecord, *, databases: BookDatabases) -> BookRecord:
    """Read the committed state of a book.

    :param book_record: Record of the book to read back.
    :type book_record: BookRecord
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    :returns: The book as committed.
    :rtype: BookRecord
    """
    return await databases.open(book_record.id).read(lambda repos: repos.book_record.get(book_record.id))


@pytest.fixture
def fx_files(fx_file_stores: SharedBookFileStores, fx_storage_root: Path) -> BookFiles:
    """Return the files of imported books in the stores the service works on.

    :param fx_file_stores: Opens the source store and the asset store of a book of the test.

    :type fx_file_stores: SharedBookFileStores
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :returns: Files of the test's books.
    :rtype: BookFiles
    """
    return BookFiles(stores=fx_file_stores, root=fx_storage_root)


class TestList:
    """Tests for the list of books: the books of the access dependencies, then the overview of each Book."""

    async def test_lists_only_the_actors_books_newest_first(
        self,
        fx_service: Callable[[], BooksService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify another account's book is hidden and the actor's come most recently updated first.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        older, newer, other = make_record(minutes=1), make_record(minutes=2), make_record(minutes=3)
        for book_record, owner in (
            (older, fx_actor.account_id),
            (newer, fx_actor.account_id),
            (other, new_account_id()),
        ):
            await commit_record(fx_database, fx_uow(), book_record, owner=owner, databases=fx_databases())

        result = await _listed(fx_service(), fx_uow(), fx_actor)

        expect([item.book_record.id for item in result.items] == [newer.id, older.id])
        expect(result.total == 2)
        assert_expectations()


class TestCreate:
    """Tests for BooksService.create()."""

    async def test_creates_an_empty_book_owned_by_the_actor(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the book is committed with the actor as owner and the clock's time on both timestamps.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        details = BookDetails(title='Book', orthography=Orthography.PRE_REFORM)

        book = await fx_service().create(fx_actor, details)

        overview = await book.overview()
        stored = await _stored(overview.book_record, databases=fx_databases())
        entry = await fx_uow().read(lambda repos: repos.library.get(stored.id))
        expect((book.id, book.owner_id) == (stored.id, fx_actor.account_id))
        expect(stored == overview.book_record)
        expect((entry.owner_id, entry.added_at) == (fx_actor.account_id, NOW))
        expect(stored.details == details)
        expect((stored.created_at, stored.updated_at) == (NOW, NOW))
        expect(overview.page_count == 0)
        assert_expectations()


class TestOpenAsset:
    """Tests for BooksService.open_asset()."""

    async def test_gives_the_path_of_a_stored_file(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_file_stores: SharedBookFileStores,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the owner reads a stored derived file through the path the service hands out.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_file_stores: Opens the source store and the asset store of a book of the test.
        :type fx_file_stores: SharedBookFileStores
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        await store_book(fx_uow(), book_record, owner=fx_actor.account_id, databases=fx_databases())
        key = BookKeys().book
        async with fx_file_stores.assets(book_record.id).writable(key) as target:
            target.write_bytes(FILE_CONTENT)

        async with fx_service().open_asset(book_record.id, key) as path:
            assert path.read_bytes() == FILE_CONTENT

    async def test_gives_a_file_of_another_accounts_book_with_no_owner_checked(
        self,
        fx_service: Callable[[], BooksService],
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_file_stores: SharedBookFileStores,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a stored derived file is read by its key alone, whoever owns its book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_file_stores: Opens the source store and the asset store of a book of the test.
        :type fx_file_stores: SharedBookFileStores
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        await commit_record(fx_database, fx_uow(), book_record, owner=new_account_id(), databases=fx_databases())
        key = BookKeys().book
        async with fx_file_stores.assets(book_record.id).writable(key) as target:
            target.write_bytes(FILE_CONTENT)

        async with fx_service().open_asset(book_record.id, key) as path:
            assert path.read_bytes() == FILE_CONTENT

    @pytest.mark.parametrize(
        'area', [KeySegment.SOURCES, KeySegment.INCOMING, 'notes'], ids=['sources', 'incoming', 'unknown-area']
    )
    async def test_key_outside_the_assets_is_not_found(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        area: str,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a key in the book's source area, its uploads or any other area is not found, not an error.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param area: Directory of the book the key lies in.
        :type area: str
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        await store_book(fx_uow(), book_record, owner=fx_actor.account_id, databases=fx_databases())

        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(book_record.id, StorageKey(f'{area}/{uuid4()}')):
                pytest.fail('A key outside the assets was opened.')

    async def test_key_without_a_file_is_not_found(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a key of the actor's book with nothing stored behind it is not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        await store_book(fx_uow(), book_record, owner=fx_actor.account_id, databases=fx_databases())

        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(book_record.id, BookKeys().book):
                pytest.fail('A file that is not stored was opened.')


class TestDelete:
    """Tests for BooksService.delete()."""

    async def test_removes_the_book_then_all_its_files(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify the row, its pages, its source and its derived files go, and a neighbour's files stay.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        doomed, kept = make_record(), make_record()
        doomed_page, kept_page = make_page(), make_page()
        for book_record, page in ((doomed, doomed_page), (kept, kept_page)):
            await store_book(fx_uow(), book_record, page, owner=fx_actor.account_id, databases=fx_open_book.databases)
            await fx_files.store(book_record.id, page)

        await fx_service().delete(fx_open_book(fx_actor, doomed.id))

        remaining = await fx_uow().read(lambda repos: repos.library.list_for_owner(fx_actor.account_id, SliceRequest()))
        expect([entry.book_id for entry in remaining.items] == [kept.id])
        expect(doomed.id not in await fx_uow().stored_books())
        expect(fx_files.gone(doomed.id))
        expect(fx_files.kept(kept_page))
        assert_expectations()

    async def test_failed_deletion_can_be_repeated_until_nothing_is_left(
        self,
        fx_flaky_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a deletion stopped by a storage failure keeps the book, and repeating it removes everything.

        :param fx_flaky_service: Function building the service over a source store whose first deletion fails.
        :type fx_flaky_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_record = make_record()
        page = make_page()
        await store_book(fx_uow(), book_record, page, owner=fx_actor.account_id, databases=fx_open_book.databases)
        await fx_files.store(book_record.id, page)

        with pytest.raises(StoreFailedError):
            await fx_flaky_service().delete(fx_open_book(fx_actor, book_record.id))

        expect(await _stored(book_record, databases=fx_open_book.databases) == book_record)
        assert_expectations()
        await fx_flaky_service().delete(fx_open_book(fx_actor, book_record.id))

        with pytest.raises(NotFoundError):
            await _stored(book_record, databases=fx_open_book.databases)
        assert fx_files.gone(book_record.id)


class TestDeleteAll:
    """Tests for AccountBooks.delete_all(), which deletes the books of an account before the account goes."""

    async def test_removes_every_book_of_the_actor_with_its_files(
        self,
        fx_service: Callable[[], BooksService],
        fx_database: SqlDatabase,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify every book of the actor goes with its files, and another account's book stays.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        owned = [make_record(minutes=minute) for minute in range(PAGE_COUNT + 1)]
        kept = make_record()
        pages = {book_record.id: make_page() for book_record in (*owned, kept)}
        for book_record in (*owned, kept):
            owner = new_account_id() if book_record is kept else fx_actor.account_id
            await commit_record(
                fx_database,
                fx_open_book.uow(),
                book_record,
                pages[book_record.id],
                owner=owner,
                databases=fx_open_book.databases,
            )
            await fx_files.store(book_record.id, pages[book_record.id])

        await AccountBooks(uow=fx_open_book.uow(), books=fx_service()).delete_all(fx_actor.account_id)

        remaining = await fx_open_book.uow().read(
            lambda repos: repos.library.list_for_owner(fx_actor.account_id, SliceRequest())
        )
        expect(remaining.total == 0)
        expect(all(fx_files.gone(book_record.id) for book_record in owned))
        expect(await _stored(kept, databases=fx_open_book.databases) == kept)
        expect(fx_files.kept(pages[kept.id]))
        assert_expectations()

    async def test_actor_without_books_is_not_an_error(
        self, fx_service: Callable[[], BooksService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify deleting the books of an account that has none succeeds, as for a fresh account.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        await AccountBooks(uow=fx_uow(), books=fx_service()).delete_all(fx_actor.account_id)


async def _listed(service: BooksService, uow: UnitOfWork, actor: Actor) -> Slice[BookOverview]:
    """List the books of the actor as the list route does: the books the access dependencies read, then their overviews.

    :param service: The book service of the request.
    :type service: BooksService
    :param uow: Unit of work the books of the actor are read through.
    :type uow: UnitOfWork
    :param actor: Account whose books are listed.
    :type actor: Actor
    :returns: The overviews of the actor's books, the latest added first, and their number.
    :rtype: Slice[BookOverview]
    """
    books = await books_of(uow, service, actor.account_id)
    return Slice(items=[await book.overview() for book in books], total=len(books))


async def _archive(service: BooksService, book_id: BookId) -> io.BytesIO:
    """Read the whole archive of a book, opened by the service with its archive, as the browser downloads it.

    :param service: The book service of the request.
    :type service: BooksService
    :param book_id: The book.
    :type book_id: BookId
    :returns: The archive, ready to be read from its start.
    :rtype: io.BytesIO
    """
    archived = await (await service.open(book_id)).export()
    return io.BytesIO(b''.join([piece async for piece in archived.pieces]))


class TestExportAndImport:
    """Tests for Book.export() and BooksService.import_archive(), which carry a book from one instance to another."""

    async def test_an_imported_book_is_a_new_book_of_the_actor_with_the_pages_and_files_of_the_archive(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the import keeps the title, the page and its file, under a new identifier the actor owns.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service works on.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record, page = make_record(), make_page()
        await store_book(fx_uow(), book_record, page, owner=fx_actor.account_id, databases=fx_databases())
        await fx_files.store(book_record.id, page)
        archive = await _archive(fx_service(), book_record.id)

        imported = await (await fx_service().import_archive(fx_actor, archive)).overview()

        copy_id = imported.book_record.id
        pages = await fx_databases().open(copy_id).read(lambda repos: repos.pages.list_window(SliceRequest()).items)
        listed = await _listed(fx_service(), fx_uow(), fx_actor)
        expect(copy_id != book_record.id)
        expect(imported.book_record.details == book_record.details)
        expect([stored.id for stored in pages] == [page.id])
        expect(fx_files.kept(page, book_id=copy_id))
        expect([overview.book_record.id for overview in listed.items] == [copy_id, book_record.id])
        assert_expectations()

    async def test_an_imported_book_reads_the_mask_of_its_edit(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
        fx_file_stores: SharedBookFileStores,
    ) -> None:
        """Verify the imported book finds the mask of a manual edit under the key its database keeps for the edit.

        The database of a book keeps the key of the mask, so the key has to stay true when the book moves under a new
        identifier.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_file_stores: Opens the source store and the asset store of a book of the test.
        :type fx_file_stores: SharedBookFileStores
        """
        book_record, page = make_record(), make_page()
        await store_book(fx_uow(), book_record, page, owner=fx_actor.account_id, databases=fx_databases())
        directory = BookKeys().page_edit(page.id, DESKEW_STEP_ID, MASK_EDIT_HASH)
        edit = evolve(make_page_edit(page_id=page.id), mask_key=StorageKey(f'{directory}/{Rendition.MASK}'))
        await (
            fx_databases()
            .open(book_record.id)
            .change(
                lambda repos, _book_record: repos.page_step_states.save(
                    make_page_step_state(page_id=page.id, edit=edit)
                )
            )
        )
        async with fx_file_stores.assets(book_record.id).writable(StorageKey(f'{directory}/{Rendition.MASK}')) as path:
            path.write_bytes(MASK_CONTENT)

        imported = await fx_service().import_archive(fx_actor, await _archive(fx_service(), book_record.id))

        state = await imported.read(
            lambda repos: repos.page_step_states.find(PageStepKey(page.id, edit.stage, edit.step_id))
        )
        assert state is not None
        assert state.edit is not None
        assert state.edit.mask_key is not None
        async with imported.assets.readable(state.edit.mask_key) as path:
            assert path.read_bytes() == MASK_CONTENT

    async def test_a_file_that_is_no_archive_of_a_book_leaves_no_book_behind(
        self, fx_service: Callable[[], BooksService], fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify an archive whose database holds no book is refused and its unpacked files are removed.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        written = io.BytesIO()
        with zipfile.ZipFile(written, mode='w') as archive:
            archive.writestr(DATABASE_NAME, b'not a database')
        written.seek(0)

        with pytest.raises(DomainError):
            await fx_service().import_archive(fx_actor, written)

        expect(await fx_uow().stored_books() == [])
        expect((await _listed(fx_service(), fx_uow(), fx_actor)).total == 0)
        assert_expectations()


class TestSweep:
    """Tests for BooksService.sweep(), which finishes the creations and deletions that were broken off half way."""

    async def test_a_book_stored_without_an_entry_goes_with_its_files(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a creation that stored the book and never listed it is undone, and a listed book stays.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        broken, whole = make_record(), make_record()
        broken_page, whole_page = make_page(), make_page()
        await fx_uow().create_book(broken)
        await store_book(fx_uow(), whole, whole_page, owner=fx_actor.account_id, databases=fx_databases())
        for book_record, page in ((broken, broken_page), (whole, whole_page)):
            await fx_files.store(book_record.id, page)

        await fx_service().sweep()

        expect(set(await fx_uow().stored_books()) == {whole.id})
        expect(fx_files.gone(broken.id))
        expect(fx_files.kept(whole_page))
        assert_expectations()

    async def test_an_entry_without_a_book_goes_with_the_files_that_are_left(
        self,
        fx_service: Callable[[], BooksService],
        fx_uow: UnitOfWorkFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a deletion that removed the book and never removed its entry is finished.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        page = make_page()
        await store_book(fx_uow(), book_record, page, owner=fx_actor.account_id, databases=fx_databases())
        await fx_files.store(book_record.id, page)
        await fx_uow().delete_book(book_record.id)

        await fx_service().sweep()

        entries = await fx_uow().read(lambda repos: repos.library.list_all())
        expect(entries == [])
        expect(fx_files.gone(book_record.id))
        assert_expectations()


class SourceStoreWaiting(LocalSourceStore):
    """The local source store of one book, whose deletion stops until the test lets it go.

    :ivar reached: Set when the deletion of the files of a book has begun.
    :ivar proceed: Set by the test to let the deletion carry on.
    """

    def __init__(self, *, root: Path, book_id: BookId) -> None:
        """Keep the files under ``root``, with both events unset.

        :param root: Storage root, created on the first write.
        :type root: Path
        :param book_id: The book whose sources the store holds.
        :type book_id: BookId
        """
        super().__init__(root=root, book_id=book_id)
        self.reached = anyio.Event()
        self.proceed = anyio.Event()

    @override
    async def delete_all(self) -> None:
        """Tell the test the deletion has begun, wait for it, and remove the files."""
        self.reached.set()
        await self.proceed.wait()
        await super().delete_all()


class TestDeleteHoldsNoTransactionOverTheFiles:
    """Tests for the deletion of a book, whose files go outside any transaction and whose row goes in one."""

    async def test_a_change_of_the_book_opens_while_its_files_are_being_removed(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_storage_root: Path,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a change of the book is not kept waiting by a slow store, and the deletion ends the row after it.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_storage_root: Storage root both stores share.
        :type fx_storage_root: Path
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_record = make_record()
        await store_book(fx_uow(), book_record, owner=fx_actor.account_id, databases=fx_open_book.databases)
        stores = SharedBookFileStores(root=fx_storage_root, source_store=SourceStoreWaiting)
        sources = stores.sources(book_record.id)
        assert isinstance(sources, SourceStoreWaiting)
        opened = False

        def mark_opened(_repos: BookRepositories, _book_record: BookRecord) -> None:
            """Note that the change of the book was granted.

            :param _repos: Repositories of the transaction, which the work does not use.
            :type _repos: BookRepositories
            :param _book_record: Record of the book, which the work does not use.
            :type _book_record: BookRecord
            """
            nonlocal opened
            opened = True

        async def open_a_change() -> None:
            """Open and leave a change of the book, which has to be granted at once."""
            await fx_open_book.databases.open(book_record.id).change(mark_opened)

        async with anyio.create_task_group() as group:
            group.start_soon(
                _service(fx_uow(), stores, fx_storage_root, databases=fx_open_book.databases).delete,
                fx_open_book(fx_actor, book_record.id),
            )
            await sources.reached.wait()
            group.start_soon(open_a_change)
            await anyio.wait_all_tasks_blocked()
            expect(opened)
            sources.proceed.set()

        with pytest.raises(NotFoundError):
            await _stored(book_record, databases=fx_open_book.databases)
        assert_expectations()
