"""Contract of the database of a book, run against the SQL adapter, the application's own.

``change`` runs its work on one transaction of the book, with the record of the book as the previous change committed
it, and ``read`` runs its work on one committed state. ``version`` and ``snapshot`` read the version and a copy of the
store of the book. A book that is not stored is found on the first call. ``create`` stores the first record of a new
book, ``close`` ends the use of a book for good, and ``BookDatabases.adopt`` takes the file of an imported book in. Changes of one book that queue behind a held
book keep the writes of each. That every kind of change commits, rolls back, waits and gives up, and that no read
writes, is in ``test_catalogue_database.py``, where the book is run beside the catalogue. Concurrency is real: tasks of an
``anyio`` task group, ordered by a held book, by events and by ``anyio.wait_all_tasks_blocked`` and never by a sleep.
"""

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.tables import BOOK_RECORDS_TABLE
from bookreviver.domain.enums import ImagePolicy
from bookreviver.domain.errors import ConflictError, InvalidArchiveError, NotFoundError
from tests.helpers.book_hold import hold_book_in_thread
from tests.helpers.builders import directory_of, make_page, make_record, new_book_id

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.entities import BookRecord
    from bookreviver.ports.persistence import BookRepositories
    from tests.contracts.conftest import Book
    from tests.helpers.database import BookDatabasesFactory
    from tests.helpers.storage import SharedBookDirectories

pytestmark = pytest.mark.anyio

STAMP: str = 'Stamp'
FIRST_LABEL: str = '1'
# The first bytes of every file SQLite writes
SQLITE_HEADER: bytes = b'SQLite format 3\x00'
COPY_FILE: str = 'snapshot.db'
CASE_ARG: str = 'case'
# The content of a file that SQLite does not open as a database
NOT_A_DATABASE: bytes = b'not a database of a book, only some bytes of text that are long enough for a header'


class TestChange:
    """Contract of BookDatabase.change() on the book it hands its work."""

    async def test_work_receives_the_stored_book(self, fx_databases: BookDatabasesFactory, fx_book: Book) -> None:
        """Verify the work is handed the book as stored, beside the repos.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book the change belongs to.
        :type fx_book: Book
        """
        database = fx_databases().open(fx_book.book_id)
        received = await database.change(lambda _repos, book_id: book_id)
        assert received == fx_book.book_record

    async def test_missing_book_raises_not_found_and_runs_no_work(self, fx_databases: BookDatabasesFactory) -> None:
        """Verify a change of a book that is not stored raises ``NotFoundError`` before its work runs.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        missing_id = new_book_id()
        ran: list[bool] = []
        database = fx_databases().open(missing_id)
        with pytest.raises(NotFoundError, match=str(missing_id)):
            await database.change(lambda _repos, _book_record: ran.append(True))
        assert ran == []

    async def test_work_receives_the_book_as_the_previous_change_left_it(
        self, fx_databases: BookDatabasesFactory, fx_book: Book
    ) -> None:
        """Verify a change reads the book once the change before it has committed, not as it was stored first.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book both changes belong to.
        :type fx_book: Book
        """
        changed = evolve(fx_book.book_record, image_policy=ImagePolicy.LOSSLESS)
        first, second = fx_databases().open(fx_book.book_id), fx_databases().open(fx_book.book_id)
        await first.change(lambda repos, _book_record: repos.book_record.update(changed))
        received = await second.change(lambda _repos, book_id: book_id)
        assert received == changed

    async def test_two_changes_that_read_and_write_one_page_keep_both_writes(
        self, fx_databases: BookDatabasesFactory, fx_book: Book
    ) -> None:
        """Verify two changes that each read a page and write another field of it both keep their writes.

        Both are started while the book is held, so each would write the page as it read it if the second were let in
        before the first had committed.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book both changes belong to.
        :type fx_book: Book
        """
        book_id = fx_book.book_id
        page = make_page()
        first, second = fx_databases().open(book_id), fx_databases().open(book_id)
        await first.change(lambda repos, _book_record: repos.pages.add(page))

        def label_page(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Read the page and write its label.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.pages.update(evolve(repos.pages.get(page.id), manual_label=FIRST_LABEL))

        def note_page(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Read the page and write its notes.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.pages.update(evolve(repos.pages.get(page.id), notes=STAMP))

        async with anyio.create_task_group() as tasks, hold_book_in_thread(fx_databases().open(book_id)):
            tasks.start_soon(first.change, label_page)
            await anyio.wait_all_tasks_blocked()
            tasks.start_soon(second.change, note_page)
            await anyio.wait_all_tasks_blocked()
        stored = await fx_databases().open(book_id).read(lambda repos: repos.pages.get(page.id))
        assert (stored.manual_label, stored.notes) == (FIRST_LABEL, STAMP)


class TestRead:
    """Contract of BookDatabase.read() on what it returns and on a book that is not stored."""

    async def test_read_returns_what_the_work_returns(self, fx_databases: BookDatabasesFactory, fx_book: Book) -> None:
        """Verify ``read`` hands back the value of its work.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book that is read.
        :type fx_book: Book
        """
        read = await fx_databases().open(fx_book.book_id).read(lambda repos: repos.book_record.get())
        assert read == fx_book.book_record

    async def test_read_of_a_missing_book_raises_not_found_and_runs_no_work(
        self, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a read of a book that is not stored raises ``NotFoundError`` before its work runs.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        missing_id = new_book_id()
        ran: list[bool] = []
        with pytest.raises(NotFoundError, match=str(missing_id)):
            await fx_databases().open(missing_id).read(lambda _repos: ran.append(True))
        assert ran == []


class TestVersionAndSnapshot:
    """Contract of BookDatabase.version() and snapshot() on a stored book and on one that is not stored."""

    async def test_version_and_snapshot_of_a_missing_book_raise_not_found(
        self, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the version and the snapshot of a book that is not stored are ``NotFoundError``.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        missing_id = new_book_id()
        database = fx_databases().open(missing_id)
        with pytest.raises(NotFoundError, match=str(missing_id)):
            await database.version()
        with pytest.raises(NotFoundError, match=str(missing_id)):
            await database.snapshot()

    async def test_snapshot_is_a_database_file_that_holds_the_record_of_the_book(
        self, fx_databases: BookDatabasesFactory, fx_book: Book, tmp_path: Path
    ) -> None:
        """Verify the snapshot is the store in the form it keeps on disk, which opens as a database of one book.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book that is copied.
        :type fx_book: Book
        :param tmp_path: Directory of the test, where the snapshot is written as a file.
        :type tmp_path: Path
        """
        snapshot = await fx_databases().open(fx_book.book_id).snapshot()
        copied = tmp_path / COPY_FILE
        copied.write_bytes(snapshot)
        with closing(sqlite3.connect(copied)) as copy:
            records = copy.execute(f'SELECT COUNT(*) FROM {BOOK_RECORDS_TABLE}').fetchone()
        expect(snapshot.startswith(SQLITE_HEADER))
        expect(records == (1,))
        assert_expectations()


class TestCreateAndClose:
    """Contract of BookDatabase.create() and close(), which begin and end the life of the database of a book."""

    async def test_a_new_book_starts_with_no_record_and_create_stores_its_record(
        self, fx_databases: BookDatabasesFactory, fx_directories: SharedBookDirectories
    ) -> None:
        """Verify the blank database of a new book holds no record until ``create``, which a read then returns.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_directories: The directories of the books of the test.
        :type fx_directories: SharedBookDirectories
        """
        databases, book_id, book_record = fx_databases(), new_book_id(), make_record()
        await fx_directories.create(directory_of(book_id), await databases.blank())
        database = databases.open(book_id)
        with pytest.raises(NotFoundError):
            await database.read(lambda repos: repos.book_record.get())

        await database.create(book_record)

        assert await database.read(lambda repos: repos.book_record.get()) == book_record

    async def test_a_book_is_created_once(self, fx_databases: BookDatabasesFactory, fx_book: Book) -> None:
        """Verify a second record of a book is a conflict and leaves the first.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book.
        :type fx_book: Book
        """
        database = fx_databases().open(fx_book.book_id)
        with pytest.raises(ConflictError):
            await database.create(evolve(fx_book.book_record, details=evolve(fx_book.book_record.details, title=STAMP)))
        assert await database.read(lambda repos: repos.book_record.get()) == fx_book.book_record

    async def test_a_closed_book_answers_not_found_and_another_book_stays(
        self, fx_databases: BookDatabasesFactory, fx_book: Book, fx_other_book: Book
    ) -> None:
        """Verify nothing of a closed book can be read or changed, a second close does nothing, and another book reads.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book that is closed.
        :type fx_book: Book
        :param fx_other_book: Another stored book.
        :type fx_other_book: Book
        """
        databases = fx_databases()

        await databases.open(fx_book.book_id).close()
        await databases.open(fx_book.book_id).close()

        with pytest.raises(NotFoundError, match=str(fx_book.book_id)):
            await databases.open(fx_book.book_id).read(lambda repos: repos.book_record.get())
        with pytest.raises(NotFoundError, match=str(fx_book.book_id)):
            await databases.open(fx_book.book_id).change(lambda _repos, _book_record: None)
        assert await databases.open(fx_other_book.book_id).read(lambda repos: repos.book_record.get()) == (
            fx_other_book.book_record
        )

    async def test_close_waits_for_the_transaction_in_progress(
        self, fx_databases: BookDatabasesFactory, fx_book: Book
    ) -> None:
        """Verify a close that comes while a change of the book runs returns only once that change has committed.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book.
        :type fx_book: Book
        """
        databases = fx_databases()
        entered, release = anyio.Event(), anyio.Event()
        order: list[str] = []

        async def hold_a_change() -> None:
            """Run a change of the book that waits for the test before it commits."""

            def work(repos: BookRepositories, _book_record: BookRecord) -> None:
                """Add a page, tell the test the change runs, and wait for its word.

                :param repos: Repositories of the transaction.
                :type repos: BookRepositories
                :param _book_record: Record of the book, which the work does not need.
                :type _book_record: BookRecord
                """
                repos.pages.add(make_page())
                anyio.from_thread.run_sync(entered.set)
                anyio.from_thread.run(release.wait)

            await databases.open(fx_book.book_id).change(work)
            order.append('committed')

        async def close() -> None:
            """Close the book once the change runs."""
            await entered.wait()
            await databases.open(fx_book.book_id).close()
            order.append('closed')

        async with anyio.create_task_group() as group:
            group.start_soon(hold_a_change)
            group.start_soon(close)
            await entered.wait()
            await anyio.wait_all_tasks_blocked()
            release.set()

        assert order == ['committed', 'closed']


class TestAdopt:
    """Contract of BookDatabases.adopt(), which takes the database file an archive placed for a new book."""

    async def test_a_copy_of_a_book_is_adopted_and_reads_as_the_book(
        self, fx_databases: BookDatabasesFactory, fx_directories: SharedBookDirectories, fx_book: Book
    ) -> None:
        """Verify the snapshot of a book, placed as the file of a new book, reads back with the same record.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_directories: The directories of the books of the test.
        :type fx_directories: SharedBookDirectories
        :param fx_book: Stored book that is copied.
        :type fx_book: Book
        """
        databases, copy_id = fx_databases(), new_book_id()
        directory = await fx_directories.create(directory_of(copy_id), await databases.blank())
        directory.database.write_bytes(await databases.open(fx_book.book_id).snapshot())

        await databases.adopt(copy_id, directory.database)

        assert await databases.open(copy_id).read(lambda repos: repos.book_record.get()) == fx_book.book_record

    @pytest.mark.parametrize(
        CASE_ARG, [pytest.param(None, id='no-record'), pytest.param(NOT_A_DATABASE, id='not-a-database')]
    )
    async def test_a_file_that_is_no_book_is_refused(
        self, fx_databases: BookDatabasesFactory, fx_directories: SharedBookDirectories, case: bytes | None
    ) -> None:
        """Verify a blank database, which holds no record, and a file that is no database are refused.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_directories: The directories of the books of the test.
        :type fx_directories: SharedBookDirectories
        :param case: Content written over the blank file, or None to keep the blank file.
        :type case: bytes | None
        """
        databases, copy_id = fx_databases(), new_book_id()
        directory = await fx_directories.create(directory_of(copy_id), await databases.blank())
        if case is not None:
            directory.database.write_bytes(case)

        with pytest.raises(InvalidArchiveError):
            await databases.adopt(copy_id, directory.database)
