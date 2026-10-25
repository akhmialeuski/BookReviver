"""Tests for the registry of the databases of the books: the blank file, opening, migrating and closing a book.

The tests run on files under the storage root of the test, since a book is a file and its migration is Alembic's, and
the storage, which owns the directories of the books, is not needed to make one. The registry names a book by the name
of its directory and builds the path of its file from the storage root. A book in use is held by
``BookRegistry.use``, and the tests order the closing of a book in use by events, never by a sleep.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.domain.errors import BookTooNewError, NotFoundError
from bookreviver.domain.ids import BookDirectoryName
from bookreviver.domain.keys import KeySegment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase

pytestmark = pytest.mark.anyio

# The wait limit of the connections of the books of these tests, long enough that no test waits for it
WAIT_SECONDS: float = 10.0
# An idle limit no test reaches, and one every released book has passed at once
NEVER_IDLE_SECONDS: float = 3600.0
ALWAYS_IDLE_SECONDS: float = 0.0
# A revision the code does not ship, standing for a book saved by a newer release
NEWER_REVISION: str = 'ffffffffffff'
VERSION_TABLE: str = 'alembic_versions'


def _book_file(root: Path, dir_name: BookDirectoryName) -> Path:
    """Name the database file of a book under the storage root, which may not exist yet.

    :param root: Storage root of the test.
    :type root: Path
    :param dir_name: Name of the directory of the book.
    :type dir_name: BookDirectoryName
    :returns: The path of ``book.db`` in the directory of the book.
    :rtype: Path
    """
    return root / KeySegment.BOOKS / dir_name / KeySegment.DATABASE


async def _new_book(books: BookRegistry, root: Path) -> BookDirectoryName:
    """Make the directory of a new book and copy the blank file of the registry into it, as a new book does.

    :param books: The registry, whose blank file is copied.
    :type books: BookRegistry
    :param root: Storage root of the registry, which gets a directory of the book.
    :type root: Path
    :returns: The name of the directory of the new book.
    :rtype: BookDirectoryName
    """
    dir_name = BookDirectoryName(str(uuid4()))
    _book_file(root, dir_name).parent.mkdir(parents=True)
    await books.copy_blank(dir_name)
    return dir_name


def _revisions(path: Path) -> list[str]:
    """Read the revisions the version table of a file records, with a plain connection.

    :param path: The file.
    :type path: Path
    :returns: The revisions, none for a file without the table.
    :rtype: list[str]
    """
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if VERSION_TABLE not in tables:
            return []
        return [row[0] for row in connection.execute(f'SELECT version_num FROM {VERSION_TABLE}')]


async def _heads(database: SqlDatabase) -> tuple[str, ...]:
    """Read the head revisions of the schema of a database.

    :param database: The database.
    :type database: SqlDatabase
    :returns: The heads its code ships.
    :rtype: tuple[str, ...]
    """
    return (await database.schema_revisions()).code


@pytest.fixture
async def fx_idle_books(fx_book_template: Path, fx_storage_root: Path) -> AsyncIterator[BookRegistry]:
    """Open a registry whose books are idle as soon as nobody uses them.

    :param fx_book_template: The blank file new books copy.
    :type fx_book_template: Path
    :param fx_storage_root: Storage root of the test, which holds the files of its books.
    :type fx_storage_root: Path
    :returns: Iterator yielding the registry and closing its books afterwards.
    :rtype: AsyncIterator[BookRegistry]
    """
    books = BookRegistry(
        root=fx_storage_root,
        tracer=NullTracer(),
        wait_seconds=WAIT_SECONDS,
        idle_seconds=ALWAYS_IDLE_SECONDS,
        blank=fx_book_template,
    )
    yield books
    await books.dispose()


class TestBlank:
    """Tests for the blank file new books start as a copy of."""

    async def test_a_registry_without_a_blank_file_makes_one_at_the_head_revision_with_no_record(
        self, fx_storage_root: Path
    ) -> None:
        """Verify the registry of the application, which is given no blank file, makes one by running the revisions.

        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        books = BookRegistry(
            root=fx_storage_root, tracer=NullTracer(), wait_seconds=WAIT_SECONDS, idle_seconds=NEVER_IDLE_SECONDS
        )
        try:
            path = _book_file(fx_storage_root, await _new_book(books, fx_storage_root))
            with closing(sqlite3.connect(path)) as connection:
                records = connection.execute('SELECT COUNT(*) FROM projects').fetchone()
            revisions = _revisions(path)
            again = _book_file(fx_storage_root, await _new_book(books, fx_storage_root))
        finally:
            await books.dispose()
        expect(records == (0,))
        expect(len(revisions) == 1)
        expect(_revisions(again) == revisions)
        expect(again.read_bytes() == path.read_bytes())
        assert_expectations()


class TestOpen:
    """Tests for the opening of a book by a transaction, which migrates it and refuses a newer one."""

    async def test_a_book_without_a_file_is_not_found(self, fx_books: BookRegistry) -> None:
        """Verify a book whose file does not exist answers like a missing one.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        """
        dir_name = BookDirectoryName(str(uuid4()))
        with pytest.raises(NotFoundError, match=str(dir_name)):
            async with fx_books.use(dir_name):
                pass

    async def test_a_new_book_is_at_the_head_revision(self, fx_books: BookRegistry, fx_storage_root: Path) -> None:
        """Verify a copy of the blank file opens at the head of the book schema.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_books, fx_storage_root)
        path = _book_file(fx_storage_root, dir_name)
        async with fx_books.use(dir_name) as database:
            heads = await _heads(database)
        assert tuple(_revisions(path)) == heads

    async def test_a_book_behind_the_code_is_brought_to_the_head_when_it_is_opened(
        self, fx_books: BookRegistry, fx_storage_root: Path
    ) -> None:
        """Verify a file no revision has touched, as the oldest book would be, is migrated by its first use.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = BookDirectoryName(str(uuid4()))
        path = _book_file(fx_storage_root, dir_name)
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)):
            pass
        async with fx_books.use(dir_name) as database:
            heads = await _heads(database)
        assert tuple(_revisions(path)) == heads

    async def test_a_book_of_a_newer_release_is_refused_and_left_as_it_is(
        self, fx_books: BookRegistry, fx_storage_root: Path
    ) -> None:
        """Verify a file at a revision the code does not ship is not opened and keeps its revision for the newer code.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_books, fx_storage_root)
        path = _book_file(fx_storage_root, dir_name)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(f'UPDATE {VERSION_TABLE} SET version_num = ?', (NEWER_REVISION,))
        with pytest.raises(BookTooNewError):
            async with fx_books.use(dir_name):
                pass
        assert _revisions(path) == [NEWER_REVISION]

    async def test_two_uses_of_a_book_share_one_database(self, fx_books: BookRegistry, fx_storage_root: Path) -> None:
        """Verify a book is opened once, so every transaction of the book meets the others in its one write queue.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_books, fx_storage_root)
        async with fx_books.use(dir_name) as first, fx_books.use(dir_name) as second:
            assert first is second


class TestClose:
    """Tests for the closing of a book for good, which waits for the transactions using it."""

    async def test_close_waits_for_the_use_in_progress_and_leaves_the_file(
        self, fx_books: BookRegistry, fx_storage_root: Path
    ) -> None:
        """Verify a close asked while a transaction uses the book returns once the use ends, and keeps the file.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_books, fx_storage_root)
        path = _book_file(fx_storage_root, dir_name)
        closed = anyio.Event()

        async def close() -> None:
            """Close the book and tell the test it is closed."""
            await fx_books.close(dir_name)
            closed.set()

        async with anyio.create_task_group() as group, fx_books.use(dir_name):
            group.start_soon(close)
            await anyio.wait_all_tasks_blocked()
            expect(not closed.is_set(), 'the close waited for the use')
        expect(closed.is_set(), 'the close ended with the use')
        expect(path.is_file(), 'the file is left to the storage')
        assert_expectations()

    async def test_a_closed_book_is_not_opened_again(self, fx_books: BookRegistry, fx_storage_root: Path) -> None:
        """Verify a transaction asking for a book while it is being closed, or after, answers like a missing book.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_books, fx_storage_root)
        async with anyio.create_task_group() as group, fx_books.use(dir_name):
            group.start_soon(fx_books.close, dir_name)
            await anyio.wait_all_tasks_blocked()
            with pytest.raises(NotFoundError):
                async with fx_books.use(dir_name):
                    pass
        with pytest.raises(NotFoundError, match=str(dir_name)):
            async with fx_books.use(dir_name):
                pass

    async def test_a_book_never_opened_is_closed_and_a_second_close_does_nothing(
        self, fx_books: BookRegistry, fx_storage_root: Path
    ) -> None:
        """Verify closing a book nobody opened, twice, refuses its later use.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_books, fx_storage_root)
        await fx_books.close(dir_name)
        await fx_books.close(dir_name)
        with pytest.raises(NotFoundError):
            async with fx_books.use(dir_name):
                pass


class TestCloseIdle:
    """Tests for the closing of the books that nobody used for the idle limit."""

    async def test_an_idle_book_is_closed_and_opened_again_by_its_next_use(
        self, fx_idle_books: BookRegistry, fx_storage_root: Path
    ) -> None:
        """Verify a released book is closed, and the next use opens a new database over the same file.

        :param fx_idle_books: Registry whose books are idle as soon as they are released.
        :type fx_idle_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_idle_books, fx_storage_root)
        async with fx_idle_books.use(dir_name) as first:
            pass
        await fx_idle_books.close_idle()
        async with fx_idle_books.use(dir_name) as second:
            assert second is not first

    async def test_a_book_in_use_is_never_closed(self, fx_idle_books: BookRegistry, fx_storage_root: Path) -> None:
        """Verify a book that a transaction holds stays open however long it has been held.

        :param fx_idle_books: Registry whose books are idle as soon as they are released.
        :type fx_idle_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_idle_books, fx_storage_root)
        async with fx_idle_books.use(dir_name) as held:
            await fx_idle_books.close_idle()
            async with fx_idle_books.use(dir_name) as again:
                assert again is held

    async def test_a_book_used_within_the_limit_stays_open(self, fx_books: BookRegistry, fx_storage_root: Path) -> None:
        """Verify a book released within the idle limit is kept, so the next use finds it open.

        :param fx_books: Registry of the books of the test, whose idle limit no test reaches.
        :type fx_books: BookRegistry
        :param fx_storage_root: Storage root of the test, which holds the files of its books.
        :type fx_storage_root: Path
        """
        dir_name = await _new_book(fx_books, fx_storage_root)
        async with fx_books.use(dir_name) as first:
            pass
        await fx_books.close_idle()
        async with fx_books.use(dir_name) as second:
            assert second is first
