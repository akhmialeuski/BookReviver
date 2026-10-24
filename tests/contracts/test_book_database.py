"""Contract of the database of a book, run against the SQL adapter, the application's own.

``change`` runs its work on one transaction of the book, with the record of the book as the previous change committed
it, and ``read`` runs its work on one committed state. ``version`` and ``snapshot`` read the version and a copy of the
store of the book. A book that is not stored is found on the first call. Changes of one book that queue behind a held
book keep the writes of each. That every kind of change commits, rolls back, waits and gives up, and that no read
writes, is in ``test_unit_of_work.py``, where the book is run beside the catalogue. Concurrency is real: tasks of an
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
from bookreviver.domain.errors import NotFoundError
from tests.helpers.book_hold import hold_book_in_thread
from tests.helpers.builders import make_page, make_record

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.entities import BookRecord
    from bookreviver.ports.persistence import BookRepositories
    from tests.contracts.conftest import Book
    from tests.helpers.database import BookDatabasesFactory

pytestmark = pytest.mark.anyio

STAMP: str = 'Stamp'
FIRST_LABEL: str = '1'
# The first bytes of every file SQLite writes
SQLITE_HEADER: bytes = b'SQLite format 3\x00'
COPY_FILE: str = 'snapshot.db'


class TestChange:
    """Contract of BookDatabase.change() on the book it hands its work."""

    async def test_work_receives_the_stored_book(self, fx_databases: BookDatabasesFactory, fx_book: Book) -> None:
        """Verify the work is handed the book as stored, beside the repos.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: Stored book the change belongs to.
        :type fx_book: Book
        """
        database = fx_databases().open(fx_book.book_record.id)
        received = await database.change(lambda _repos, book_record: book_record)
        assert received == fx_book.book_record

    async def test_missing_book_raises_not_found_and_runs_no_work(self, fx_databases: BookDatabasesFactory) -> None:
        """Verify a change of a book that is not stored raises ``NotFoundError`` before its work runs.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        missing = make_record()
        ran: list[bool] = []
        database = fx_databases().open(missing.id)
        with pytest.raises(NotFoundError, match=str(missing.id)):
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
        first, second = fx_databases().open(fx_book.book_record.id), fx_databases().open(fx_book.book_record.id)
        await first.change(lambda repos, _book_record: repos.book_record.update(changed))
        received = await second.change(lambda _repos, book_record: book_record)
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
        book_id = fx_book.book_record.id
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
        book_record = fx_book.book_record
        read = await fx_databases().open(book_record.id).read(lambda repos: repos.book_record.get(book_record.id))
        assert read == book_record

    async def test_read_of_a_missing_book_raises_not_found_and_runs_no_work(
        self, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a read of a book that is not stored raises ``NotFoundError`` before its work runs.

        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        missing = make_record()
        ran: list[bool] = []
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await fx_databases().open(missing.id).read(lambda _repos: ran.append(True))
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
        missing = make_record()
        database = fx_databases().open(missing.id)
        with pytest.raises(NotFoundError, match=str(missing.id)):
            await database.version()
        with pytest.raises(NotFoundError, match=str(missing.id)):
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
        snapshot = await fx_databases().open(fx_book.book_record.id).snapshot()
        copied = tmp_path / COPY_FILE
        copied.write_bytes(snapshot)
        with closing(sqlite3.connect(copied)) as copy:
            records = copy.execute(f'SELECT COUNT(*) FROM {BOOK_RECORDS_TABLE}').fetchone()
        expect(snapshot.startswith(SQLITE_HEADER))
        expect(records == (1,))
        assert_expectations()
