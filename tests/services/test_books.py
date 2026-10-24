"""Tests for the book use cases, against the database of the test and the local stores over a temporary directory."""

import io
import zipfile
from typing import TYPE_CHECKING, override
from uuid import uuid4

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.api.access import AccountBooks, books_of
from bookreviver.domain.enums import Rendition
from bookreviver.domain.errors import DomainError, NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.keys import BookKeys, KeySegment
from bookreviver.domain.values import PageStepKey, Slice, SliceRequest
from bookreviver.services.books import BooksService
from bookreviver.services.stage_summaries import StageSummaries
from tests.helpers.builders import (
    DESKEW_STEP_ID,
    make_page,
    make_page_edit,
    make_page_step_state,
    make_record,
    new_account_id,
    new_book_id,
    open_for,
)
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.scripted_runner import ScriptedRunner
from tests.helpers.seeding import commit_record, store_book
from tests.helpers.storage import BookFiles, SharedBookDirectories, SharedDirectory

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.storage import LocalSourceStore
    from bookreviver.domain.entities import Actor, BookOverview, BookRecord, Page
    from bookreviver.domain.ids import BookDirectoryName, BookId
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory, LocatedBookDatabases

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 2
# The name of the database in the archive of a book
DATABASE_NAME: str = 'book.db'
# The hash an edit with a mask is stored under, and the content of its mask
MASK_EDIT_HASH: str = 'masked-edit'
FILE_CONTENT: bytes = b'derived file'
MASK_CONTENT: bytes = b'mask of the edit'


def _stages() -> StageSummaries:
    """Build the stage sums over a catalogue with no processors and a board with no jobs.

    :returns: The stage summaries.
    :rtype: StageSummaries
    """
    board = FakeJobBoard(runner=ScriptedRunner(), publisher=RecordingEventBus())
    return StageSummaries(processors=FakeCatalogue([]), board=board)


def _service(
    catalogue: CatalogueDatabase, stores: SharedBookDirectories, *, databases: LocatedBookDatabases
) -> BooksService:
    """Build the book service for one request, over a catalogue database.

    :param catalogue: Catalogue database of the request.
    :type catalogue: CatalogueDatabase
    :param stores: The directories of the books of the test.
    :type stores: SharedBookDirectories
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The service.
    :rtype: BooksService
    """
    return BooksService(catalogue=catalogue, databases=databases, directories=stores)


@pytest.fixture
def fx_service(
    fx_catalogue: CatalogueDatabaseFactory, fx_directories: SharedBookDirectories, fx_databases: BookDatabasesFactory
) -> Callable[[], BooksService]:
    """Return a function building the service for one request.

    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_directories: The directories of the books of the test.
    :type fx_directories: SharedBookDirectories
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: Function building a service over a new catalogue database.
    :rtype: Callable[[], BooksService]
    """
    return lambda: _service(fx_catalogue(), fx_directories, databases=fx_databases())


class StoreFailedError(OSError):
    """Raised by the source store of a test in place of a real storage failure."""


class DirectoryFailingOnce(SharedDirectory):
    """The local directory of one book, whose first deletion fails before it removes anything."""

    def __init__(self, *, root: Path, name: BookDirectoryName, sources: LocalSourceStore) -> None:
        """Keep the directory of the book under ``root`` and fail its first deletion.

        :param root: Storage root of the directories.
        :type root: Path
        :param name: Name of the directory of the book.
        :type name: BookDirectoryName
        :param sources: The source store of the book.
        :type sources: LocalSourceStore
        """
        super().__init__(root=root, name=name, sources=sources)
        self._failed = False

    @override
    async def delete(self) -> None:
        """Fail on the first call, as a storage outage would, and remove the directory on the later ones.

        :raises StoreFailedError: On the first call, with nothing removed.
        """
        if not self._failed:
            self._failed = True
            raise StoreFailedError
        await super().delete()


@pytest.fixture
def fx_flaky_directories(fx_storage_root: Path) -> SharedBookDirectories:
    """Return the directories of the books of the test, whose first deletion of a directory fails.

    :param fx_storage_root: Storage root of the directories.
    :type fx_storage_root: Path
    :returns: The directories, each of whose deletion fails once.
    :rtype: SharedBookDirectories
    """
    return SharedBookDirectories(root=fx_storage_root, directory_type=DirectoryFailingOnce)


async def _stored(book_id: BookId, *, databases: LocatedBookDatabases) -> BookRecord:
    """Read the committed state of a book.

    :param book_id: Identifier of the book to read back.
    :type book_id: BookId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The book as committed.
    :rtype: BookRecord
    """
    return await databases.open(book_id).read(lambda repos: repos.book_record.get())


@pytest.fixture
def fx_files(fx_directories: SharedBookDirectories, fx_storage_root: Path) -> BookFiles:
    """Return the files of imported books in the stores the service works on.

    :param fx_directories: Opens the source store and the asset store of a book of the test.

    :type fx_directories: SharedBookDirectories
    :param fx_storage_root: Storage root both stores share.
    :type fx_storage_root: Path
    :returns: Files of the test's books.
    :rtype: BookFiles
    """
    return BookFiles(stores=fx_directories, root=fx_storage_root)


class TestList:
    """Tests for the list of books: the books of the access dependencies, then the overview of each Book."""

    async def test_lists_only_the_actors_books_newest_first(
        self,
        fx_service: Callable[[], BooksService],
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify another account's book is hidden and the actor's come most recently updated first.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        older_id, newer_id, _ = [
            await commit_record(
                fx_database, fx_catalogue(), make_record(minutes=minutes), owner=owner, databases=fx_databases()
            )
            for minutes, owner in ((1, fx_actor.account_id), (2, fx_actor.account_id), (3, new_account_id()))
        ]

        result = await _listed(fx_service(), fx_catalogue(), fx_actor)

        expect([item.book_id for item in result.items] == [newer_id, older_id])
        expect(result.total == 2)
        assert_expectations()


class TestOpenAsset:
    """Tests for BooksService.open_asset()."""

    async def test_gives_the_path_of_a_stored_file(
        self,
        fx_service: Callable[[], BooksService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_directories: SharedBookDirectories,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the owner reads a stored derived file through the path the service hands out.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await store_book(fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_databases())
        key = BookKeys().book
        async with fx_directories.assets(book_id).writable(key) as target:
            target.write_bytes(FILE_CONTENT)

        async with fx_service().open_asset(book_id, key) as path:
            assert path.read_bytes() == FILE_CONTENT

    async def test_a_file_of_a_book_the_library_does_not_list_is_not_found(
        self, fx_service: Callable[[], BooksService], fx_directories: SharedBookDirectories
    ) -> None:
        """Verify a file left in a directory no library entry names, as a broken deletion leaves it, is not served.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_directories: The directories of the books of the test.
        :type fx_directories: SharedBookDirectories
        """
        book_id, key = new_book_id(), BookKeys().book
        async with fx_directories.assets(book_id).writable(key) as target:
            target.write_bytes(FILE_CONTENT)

        with pytest.raises(NotFoundError, match=str(book_id)):
            async with fx_service().open_asset(book_id, key):
                pass

    async def test_gives_a_file_of_another_accounts_book_with_no_owner_checked(
        self,
        fx_service: Callable[[], BooksService],
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_directories: SharedBookDirectories,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a stored derived file is read by its key alone, whoever owns its book.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await commit_record(
            fx_database, fx_catalogue(), make_record(), owner=new_account_id(), databases=fx_databases()
        )
        key = BookKeys().book
        async with fx_directories.assets(book_id).writable(key) as target:
            target.write_bytes(FILE_CONTENT)

        async with fx_service().open_asset(book_id, key) as path:
            assert path.read_bytes() == FILE_CONTENT

    @pytest.mark.parametrize(
        'area', [KeySegment.SOURCES, KeySegment.INCOMING, 'notes'], ids=['sources', 'incoming', 'unknown-area']
    )
    async def test_key_outside_the_assets_is_not_found(
        self,
        fx_service: Callable[[], BooksService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        area: str,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a key in the book's source area, its uploads or any other area is not found, not an error.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param area: Directory of the book the key lies in.
        :type area: str
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await store_book(fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_databases())

        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(book_id, StorageKey(f'{area}/{uuid4()}')):
                pytest.fail('A key outside the assets was opened.')

    async def test_key_without_a_file_is_not_found(
        self,
        fx_service: Callable[[], BooksService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a key of the actor's book with nothing stored behind it is not found.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await store_book(fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_databases())

        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(book_id, BookKeys().book):
                pytest.fail('A file that is not stored was opened.')


class TestDelete:
    """Tests for BooksService.delete()."""

    async def test_removes_the_entry_then_the_book_with_all_its_files(
        self,
        fx_service: Callable[[], BooksService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify the entry, the book with its pages, its source and its derived files go, and a neighbour's files stay.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        doomed_page, kept_page = make_page(), make_page()
        doomed_id, kept_id = [
            await store_book(
                fx_catalogue(), make_record(), page, owner=fx_actor.account_id, databases=fx_open_book.databases
            )
            for page in (doomed_page, kept_page)
        ]
        for book_id, page in ((doomed_id, doomed_page), (kept_id, kept_page)):
            await fx_files.store(book_id, page)

        await fx_service().delete(fx_open_book(fx_actor, doomed_id))

        remaining = await fx_catalogue().read(
            lambda repos: repos.library.list_for_owner(fx_actor.account_id, SliceRequest())
        )
        expect([entry.book_id for entry in remaining.items] == [kept_id])
        expect(fx_files.gone(doomed_id))
        expect(fx_files.kept(kept_page))
        assert_expectations()

    async def test_a_failed_deletion_has_removed_the_entry_and_the_book_finishes_it_when_deleted_again(
        self,
        fx_service: Callable[[], BooksService],
        fx_flaky_directories: SharedBookDirectories,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a deletion stopped by a storage failure has taken the book out of the library, and Book.delete ends it.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_flaky_directories: The directories of the books, whose first deletion fails.
        :type fx_flaky_directories: SharedBookDirectories
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        page = make_page()
        book_id = await store_book(
            fx_open_book.catalogue(), make_record(), page, owner=fx_actor.account_id, databases=fx_open_book.databases
        )
        await fx_files.store(book_id, page)
        book = open_for(fx_actor, book_id, databases=fx_open_book.databases, files=fx_flaky_directories)

        with pytest.raises(StoreFailedError):
            await fx_service().delete(book)

        expect(await fx_open_book.catalogue().read(lambda repos: repos.library.list_all()) == [])
        expect(fx_files.kept(page))
        assert_expectations()
        await book.delete()
        assert fx_files.gone(book_id)


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
        kept = make_record()
        pages: dict[BookId, Page] = {}
        for book_record in (*(make_record(minutes=minute) for minute in range(PAGE_COUNT + 1)), kept):
            owner = new_account_id() if book_record is kept else fx_actor.account_id
            page = make_page()
            book_id = await commit_record(
                fx_database, fx_open_book.catalogue(), book_record, page, owner=owner, databases=fx_open_book.databases
            )
            pages[book_id] = page
            await fx_files.store(book_id, page)
        *owned, kept_id = pages

        await AccountBooks(catalogue=fx_open_book.catalogue(), books=fx_service()).delete_all(fx_actor.account_id)

        remaining = await fx_open_book.catalogue().read(
            lambda repos: repos.library.list_for_owner(fx_actor.account_id, SliceRequest())
        )
        expect(remaining.total == 0)
        expect(all(fx_files.gone(book_id) for book_id in owned))
        expect(await _stored(kept_id, databases=fx_open_book.databases) == kept)
        expect(fx_files.kept(pages[kept_id]))
        assert_expectations()

    async def test_actor_without_books_is_not_an_error(
        self, fx_service: Callable[[], BooksService], fx_catalogue: CatalogueDatabaseFactory, fx_actor: Actor
    ) -> None:
        """Verify deleting the books of an account that has none succeeds, as for a fresh account.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        """
        await AccountBooks(catalogue=fx_catalogue(), books=fx_service()).delete_all(fx_actor.account_id)


async def _listed(service: BooksService, catalogue: CatalogueDatabase, actor: Actor) -> Slice[BookOverview]:
    """List the books of the actor as the list route does: the books the access dependencies read, then their overviews.

    :param service: The book service of the request.
    :type service: BooksService
    :param catalogue: Catalogue database the books of the actor are read through.
    :type catalogue: CatalogueDatabase
    :param actor: Account whose books are listed.
    :type actor: Actor
    :returns: The overviews of the actor's books, the latest added first, and their number.
    :rtype: Slice[BookOverview]
    """
    books = await books_of(catalogue, service, actor.account_id)
    stages = _stages()
    return Slice(items=[await stages.overview(book) for book in books], total=len(books))


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
        fx_catalogue: CatalogueDatabaseFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the import keeps the title, the page and its file, under a new identifier the actor owns.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_files: Files of imported books in the stores the service works on.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record, page = make_record(), make_page()
        book_id = await store_book(
            fx_catalogue(), book_record, page, owner=fx_actor.account_id, databases=fx_databases()
        )
        await fx_files.store(book_id, page)
        archive = await _archive(fx_service(), book_id)

        imported = await _stages().overview(await fx_service().import_archive(fx_actor, archive))

        copy_id = imported.book_id
        pages = await fx_databases().open(copy_id).read(lambda repos: repos.pages.list_window(SliceRequest()).items)
        listed = await _listed(fx_service(), fx_catalogue(), fx_actor)
        expect(copy_id != book_id)
        expect(imported.book_record.details == book_record.details)
        expect([stored.id for stored in pages] == [page.id])
        expect(fx_files.kept(page, book_id=copy_id))
        expect([overview.book_id for overview in listed.items] == [copy_id, book_id])
        assert_expectations()

    async def test_an_imported_book_reads_the_mask_of_its_edit(
        self,
        fx_service: Callable[[], BooksService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_databases: BookDatabasesFactory,
        fx_directories: SharedBookDirectories,
    ) -> None:
        """Verify the imported book finds the mask of a manual edit under the key its database keeps for the edit.

        The database of a book keeps the key of the mask, so the key has to stay true when the book moves under a new
        identifier.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        """
        book_record, page = make_record(), make_page()
        book_id = await store_book(
            fx_catalogue(), book_record, page, owner=fx_actor.account_id, databases=fx_databases()
        )
        directory = BookKeys().page_edit(page.id, DESKEW_STEP_ID, MASK_EDIT_HASH)
        edit = evolve(make_page_edit(page_id=page.id), mask_key=StorageKey(f'{directory}/{Rendition.MASK}'))
        await (
            fx_databases()
            .open(book_id)
            .change(
                lambda repos, _book_record: repos.page_step_states.save(
                    make_page_step_state(page_id=page.id, edit=edit)
                )
            )
        )
        async with fx_directories.assets(book_id).writable(StorageKey(f'{directory}/{Rendition.MASK}')) as path:
            path.write_bytes(MASK_CONTENT)

        imported = await fx_service().import_archive(fx_actor, await _archive(fx_service(), book_id))

        state = await imported.read(
            lambda repos: repos.page_step_states.find(PageStepKey(page.id, edit.stage, edit.step_id))
        )
        assert state is not None
        assert state.edit is not None
        assert state.edit.mask_key is not None
        async with imported.assets.readable(state.edit.mask_key) as path:
            assert path.read_bytes() == MASK_CONTENT

    async def test_a_file_that_is_no_archive_of_a_book_leaves_no_book_behind(
        self,
        fx_service: Callable[[], BooksService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_storage_root: Path,
    ) -> None:
        """Verify an archive whose database holds no book is refused and its unpacked files are removed.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_storage_root: Storage root the archive is unpacked under.
        :type fx_storage_root: Path
        """
        written = io.BytesIO()
        with zipfile.ZipFile(written, mode='w') as archive:
            archive.writestr(DATABASE_NAME, b'not a database')
        written.seek(0)

        with pytest.raises(DomainError):
            await fx_service().import_archive(fx_actor, written)

        expect(list((fx_storage_root / KeySegment.BOOKS).glob('*')) == [])
        expect((await _listed(fx_service(), fx_catalogue(), fx_actor)).total == 0)
        assert_expectations()


class TestDeleteWaitsForTheTransactionInProgress:
    """Tests for the deletion of a book, which removes its directory only after its transactions have ended."""

    async def test_the_directory_goes_once_the_change_in_progress_has_committed(
        self,
        fx_service: Callable[[], BooksService],
        fx_catalogue: CatalogueDatabaseFactory,
        fx_files: BookFiles,
        fx_actor: Actor,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a deletion that comes while a change of the book runs leaves the directory until the change commits.

        :param fx_service: Function building the service for one request.
        :type fx_service: Callable[[], BooksService]
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_files: Files of imported books in the stores the service deletes from.
        :type fx_files: BookFiles
        :param fx_actor: Account the service acts for.
        :type fx_actor: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        book_id = await store_book(
            fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_open_book.databases
        )
        entered, release = anyio.Event(), anyio.Event()
        committed = False

        def hold(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add a page, tell the test the change runs, and wait for its word before the commit.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not use.
            :type _book_record: BookRecord
            """
            repos.pages.add(make_page())
            anyio.from_thread.run_sync(entered.set)
            anyio.from_thread.run(release.wait)

        async def change() -> None:
            """Run the held change of the book and note its commit."""
            nonlocal committed
            await fx_open_book.databases.open(book_id).change(hold)
            committed = True

        async with anyio.create_task_group() as group:
            group.start_soon(change)
            await entered.wait()
            group.start_soon(fx_service().delete, fx_open_book(fx_actor, book_id))
            await anyio.wait_all_tasks_blocked()
            expect(not fx_files.gone(book_id), 'the directory stayed while the change ran')
            release.set()

        expect(committed)
        expect(fx_files.gone(book_id), 'the directory went after the change')
        assert_expectations()
