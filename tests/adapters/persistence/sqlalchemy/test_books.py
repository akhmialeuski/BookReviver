"""Tests for the registry of the databases of the books: making, opening, migrating, closing and removing a book.

The tests run on the files of a real storage root in the directory of the test, since a book is a file and its
migration is Alembic's. A book in use is held by ``BookDatabases.use``, and the tests order the removal of a book in
use by events, never by a sleep.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.books import BookDatabases
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.domain.errors import BookTooNewError, ConflictError, NotFoundError
from bookreviver.domain.ids import BookId
from bookreviver.domain.keys import KeySegment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

# The wait limit of the connections of the books of these tests, long enough that no test waits for it
WAIT_SECONDS: float = 10.0
# An idle limit no test reaches, and one every released book has passed at once
NEVER_IDLE_SECONDS: float = 3600.0
ALWAYS_IDLE_SECONDS: float = 0.0
# A revision the code does not ship, standing for a book saved by a newer release
NEWER_REVISION: str = 'ffffffffffff'
VERSION_TABLE: str = 'alembic_versions'
# A directory under the projects root that no store makes, which is not a book
NOT_A_BOOK: str = 'not-a-book'


def _new_book() -> BookId:
    """Give the identifier of a new book.

    :returns: A fresh identifier.
    :rtype: BookId
    """
    return BookId(uuid4())


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
async def fx_idle_books(fx_settings: Settings, fx_book_template: Path) -> AsyncIterator[BookDatabases]:
    """Open a registry whose books are idle as soon as nobody uses them.

    :param fx_settings: Settings naming the storage root.
    :type fx_settings: Settings
    :param fx_book_template: The blank file new books copy.
    :type fx_book_template: Path
    :returns: Iterator yielding the registry and closing its books afterwards.
    :rtype: AsyncIterator[BookDatabases]
    """
    books = BookDatabases(
        fx_settings.storage_root,
        tracer=NullTracer(),
        wait_seconds=WAIT_SECONDS,
        idle_seconds=ALWAYS_IDLE_SECONDS,
        blank=fx_book_template,
    )
    yield books
    await books.dispose()


class TestCreate:
    """Tests for the making of the database of a new book."""

    async def test_a_new_book_is_at_the_head_revision_in_the_directory_of_the_book(
        self, fx_books: BookDatabases
    ) -> None:
        """Verify a created book is a file ``book.db`` in ``projects/<id>`` at the head of the book schema.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        path = fx_books.path(book_id)
        async with fx_books.use(book_id) as database:
            heads = await _heads(database)
        expect(path.name == KeySegment.DATABASE)
        expect(path.parent.name == str(book_id))
        expect(tuple(_revisions(path)) == heads)
        assert_expectations()

    async def test_a_second_creation_of_one_book_is_refused(self, fx_books: BookDatabases) -> None:
        """Verify a book is made once, so two creations that race cannot both succeed.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        with pytest.raises(ConflictError, match=str(book_id)):
            await fx_books.create(book_id)

    async def test_a_registry_without_a_blank_file_makes_one_at_the_head_revision(self, fx_settings: Settings) -> None:
        """Verify the registry of the application, which is given no blank file, makes one by running the revisions.

        :param fx_settings: Settings naming the storage root.
        :type fx_settings: Settings
        """
        books = BookDatabases(
            fx_settings.storage_root, tracer=NullTracer(), wait_seconds=WAIT_SECONDS, idle_seconds=NEVER_IDLE_SECONDS
        )
        book_id = _new_book()
        try:
            await books.create(book_id)
            async with books.use(book_id) as database:
                heads = await _heads(database)
        finally:
            await books.dispose()
        assert tuple(_revisions(books.path(book_id))) == heads


class TestOpen:
    """Tests for the opening of a book by a unit of work, which migrates it and refuses a newer one."""

    async def test_a_book_without_a_file_is_not_found(self, fx_books: BookDatabases) -> None:
        """Verify a book that was never made, or was removed, answers like a missing one.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        with pytest.raises(NotFoundError, match=str(book_id)):
            async with fx_books.use(book_id):
                pass

    async def test_a_book_behind_the_code_is_brought_to_the_head_when_it_is_opened(
        self, fx_books: BookDatabases
    ) -> None:
        """Verify a file no revision has touched, as the oldest book would be, is migrated by its first use.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        path = fx_books.path(book_id)
        path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(path)):
            pass
        async with fx_books.use(book_id) as database:
            heads = await _heads(database)
        assert tuple(_revisions(path)) == heads

    async def test_a_book_of_a_newer_release_is_refused_and_left_as_it_is(self, fx_books: BookDatabases) -> None:
        """Verify a file at a revision the code does not ship is not opened and keeps its revision for the newer code.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        path = fx_books.path(book_id)
        with closing(sqlite3.connect(path)) as connection, connection:
            connection.execute(f'UPDATE {VERSION_TABLE} SET version_num = ?', (NEWER_REVISION,))
        with pytest.raises(BookTooNewError):
            async with fx_books.use(book_id):
                pass
        assert _revisions(path) == [NEWER_REVISION]

    async def test_two_uses_of_a_book_share_one_database(self, fx_books: BookDatabases) -> None:
        """Verify a book is opened once, so every unit of work of the book meets the others in its one write queue.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        async with fx_books.use(book_id) as first, fx_books.use(book_id) as second:
            assert first is second


class TestRemove:
    """Tests for the removal of a book, which waits for the units of work using it."""

    async def test_removal_waits_for_the_use_in_progress_and_then_deletes_the_files(
        self, fx_books: BookDatabases
    ) -> None:
        """Verify a removal asked while a unit of work uses the book deletes nothing until the use ends.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        path = fx_books.path(book_id)
        removed = anyio.Event()

        async def remove() -> None:
            """Remove the book and tell the test it is gone."""
            await fx_books.remove(book_id)
            removed.set()

        async with anyio.create_task_group() as group, fx_books.use(book_id):
            group.start_soon(remove)
            await anyio.wait_all_tasks_blocked()
            expect(not removed.is_set(), 'the removal waited for the use')
            expect(path.is_file(), 'the file stayed while the book was used')
        expect(not path.exists(), 'the file went once the use ended')
        expect(not path.parent.exists(), 'the empty directory of the book went too')
        assert_expectations()

    async def test_a_book_being_removed_is_not_opened_again(self, fx_books: BookDatabases) -> None:
        """Verify a unit of work asking for a book while it is being removed answers like a missing book.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        async with anyio.create_task_group() as group, fx_books.use(book_id):
            group.start_soon(fx_books.remove, book_id)
            await anyio.wait_all_tasks_blocked()
            with pytest.raises(NotFoundError):
                async with fx_books.use(book_id):
                    pass

    async def test_removal_keeps_the_other_files_of_the_book_and_a_second_removal_does_nothing(
        self, fx_books: BookDatabases
    ) -> None:
        """Verify the removal deletes only the database, leaves the directory with files of the stores in it, and repeats.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        path = fx_books.path(book_id)
        kept = path.parent / KeySegment.SOURCES
        kept.mkdir()
        await fx_books.remove(book_id)
        await fx_books.remove(book_id)
        expect(not path.exists())
        expect(kept.is_dir())
        assert_expectations()


class TestStored:
    """Tests for the listing of the books that have a database."""

    async def test_lists_the_directories_of_books_with_a_database_and_nothing_else(
        self, fx_books: BookDatabases, fx_settings: Settings
    ) -> None:
        """Verify a directory without a database, and one not named by a book, are not books.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        :param fx_settings: Settings naming the storage root.
        :type fx_settings: Settings
        """
        made = sorted([_new_book(), _new_book()])
        for book_id in made:
            await fx_books.create(book_id)
        books_root = fx_settings.storage_root / KeySegment.BOOKS
        (books_root / str(_new_book())).mkdir()
        (books_root / NOT_A_BOOK).mkdir()
        assert await fx_books.stored() == made

    async def test_a_storage_root_with_no_book_lists_none(self, fx_books: BookDatabases) -> None:
        """Verify a storage root that no book has been made under yet lists no book.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        assert await fx_books.stored() == []


class TestCloseIdle:
    """Tests for the closing of the books that nobody used for the idle limit."""

    async def test_an_idle_book_is_closed_and_opened_again_by_its_next_use(self, fx_idle_books: BookDatabases) -> None:
        """Verify a released book is closed, and the next use opens a new database over the same file.

        :param fx_idle_books: Registry whose books are idle as soon as they are released.
        :type fx_idle_books: BookDatabases
        """
        book_id = _new_book()
        await fx_idle_books.create(book_id)
        async with fx_idle_books.use(book_id) as first:
            pass
        await fx_idle_books.close_idle()
        async with fx_idle_books.use(book_id) as second:
            assert second is not first

    async def test_a_book_in_use_is_never_closed(self, fx_idle_books: BookDatabases) -> None:
        """Verify a book that a unit of work holds stays open however long it has been held.

        :param fx_idle_books: Registry whose books are idle as soon as they are released.
        :type fx_idle_books: BookDatabases
        """
        book_id = _new_book()
        await fx_idle_books.create(book_id)
        async with fx_idle_books.use(book_id) as held:
            await fx_idle_books.close_idle()
            async with fx_idle_books.use(book_id) as again:
                assert again is held

    async def test_a_book_used_within_the_limit_stays_open(self, fx_books: BookDatabases) -> None:
        """Verify a book released within the idle limit is kept, so the next use finds it open.

        :param fx_books: Registry of the books of the test, whose idle limit no test reaches.
        :type fx_books: BookDatabases
        """
        book_id = _new_book()
        await fx_books.create(book_id)
        async with fx_books.use(book_id) as first:
            pass
        await fx_books.close_idle()
        async with fx_books.use(book_id) as second:
            assert second is first
