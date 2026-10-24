"""The databases of the books: one SQLite file in the directory of each book, opened on first use and closed when idle.

The file of a book lies in the directory of the book, which the storage owns: it copies the blank file of a new book
into the directory, unpacks the file of an imported one and deletes the directory whole, and the registry is given the
path of the file. :class:`BookRegistry` opens a
:class:`~bookreviver.adapters.persistence.sqlalchemy.database.SqlDatabase` of the book schema the first time a
transaction asks for the book, brings the file to the head revision of that schema when it is behind, and refuses a file
at a revision this code does not ship with ``BookTooNewError``, leaving the file as it is for the newer code. Each open
book has engines and a write queue of its own, so a change of one book never waits for a change of another.

A new book starts as a copy of a blank file at the head revision, made once for the registry: running the revisions
takes about a second, and copying a file a few milliseconds.

A book that no transaction has used for the idle limit is closed, which disposes of its engines and their
connections, and the next use opens it again. :meth:`BookRegistry.keep_closing_idle` looks for such books ten times
within the limit, run by the application in a task group of its own. Closing a book for good waits until the
transactions using it have ended, so no transaction is cut off, and every later use of the book is refused, so the
storage can delete its file.

Every method runs on the event loop. The calls that block, on files or on Alembic, go to worker threads.
"""

from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Final

import anyio
from alembic.script import ScriptDirectory
from anyio import to_thread
from attrs import define, field

from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SchemaRevisions, SqlDatabase
from bookreviver.domain.errors import BookTooNewError, NotFoundError
from bookreviver.domain.keys import KeySegment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.domain.ids import BookId
    from bookreviver.ports.tracing import Tracer


@define
class OpenBook:
    """A book whose database is open, and the transactions using it.

    :ivar database: The database of the book.
    :ivar users: How many transactions are inside the book, or waiting for one.
    :ivar idle_since: Reading of the monotonic clock when the last transaction stopped using the book.
    :ivar released: Event a removal waits on until the last transaction stops using the book, or None.
    """

    database: SqlDatabase
    users: int = 0
    idle_since: float = field(factory=monotonic)
    released: anyio.Event | None = None


class BookRegistry:
    """The open databases of the books, each opened from the file the storage keeps for it.

    :ivar IDLE_CHECKS: How many times within the idle limit the registry looks for books to close.
    :ivar BLANK_PREFIX: Prefix of the temporary directory that holds the blank file the registry makes.
    """

    IDLE_CHECKS: Final = 10
    BLANK_PREFIX: Final = 'bookreviver-blank-book-'

    def __init__(
        self,
        *,
        tracer: Tracer,
        wait_seconds: float,
        idle_seconds: float,
        blank: Path | None = None,
    ) -> None:
        """Serve the books whose files the callers name.

        :param tracer: Tracer the write blocks of every book are timed with.
        :type tracer: Tracer
        :param wait_seconds: The busy timeout every connection of a book gets, which is the longest wait limit of a
                             catalogue database.
        :type wait_seconds: float
        :param idle_seconds: How long a book stays open after the last transaction used it.
        :type idle_seconds: float
        :param blank: A file of the book schema at the head revision that new books are copied from, or None to make one
                      on the first creation of a book.
        :type blank: Path | None
        """
        self._tracer = tracer
        self._wait_seconds = wait_seconds
        self._idle_seconds = idle_seconds
        self._blank = blank
        self._blank_directory: TemporaryDirectory[str] | None = None
        self._revisions: tuple[tuple[str, ...], frozenset[str]] | None = None
        self._open: dict[BookId, OpenBook] = {}
        # Never emptied: a closed book is deleted, and a new book gets a new identifier
        self._closed: set[BookId] = set()
        # Taken to open, create and close books, never around a transaction
        self._lock = anyio.Lock()

    @asynccontextmanager
    async def use(self, book_id: BookId, path: Path) -> AsyncIterator[SqlDatabase]:
        """Hold the database of a book open for the caller, opening it from its file first when it is closed.

        :param book_id: The book.
        :type book_id: BookId
        :param path: The database file of the book.
        :type path: Path
        :returns: Iterator yielding the open database of the book once.
        :rtype: AsyncIterator[SqlDatabase]
        :raises NotFoundError: If the file does not exist, or the book was closed for good.
        :raises BookTooNewError: If the database is at a revision of the book schema that this code does not ship.
        """
        book = await self._acquire(book_id, path)
        try:
            yield book.database
        finally:
            book.users -= 1
            book.idle_since = monotonic()
            if not book.users and book.released is not None:
                book.released.set()

    async def close(self, book_id: BookId) -> None:
        """Close the database of a book for good, once no transaction uses it, so its file can be deleted.

        A book that is not open, or closed already, is only marked closed, so a deletion broken off half way can run
        again.

        :param book_id: The book.
        :type book_id: BookId
        """
        async with self._lock:
            self._closed.add(book_id)
            book = self._open.pop(book_id, None)
        if book is None:
            return
        if book.users:
            released = book.released = anyio.Event()
            await released.wait()
        await book.database.dispose()

    async def close_idle(self) -> None:
        """Close every open book that no transaction has used for the idle limit."""
        since = monotonic() - self._idle_seconds
        async with self._lock:
            idle = [book_id for book_id, book in self._open.items() if not book.users and book.idle_since <= since]
            closing = [self._open.pop(book_id) for book_id in idle]
        for book in closing:
            await book.database.dispose()

    async def keep_closing_idle(self) -> None:
        """Close the idle books ten times within the idle limit, until the task is cancelled."""
        while True:
            await anyio.sleep(self._idle_seconds / self.IDLE_CHECKS)
            await self.close_idle()

    async def dispose(self) -> None:
        """Close every open book and remove the blank file the registry made, at the end of the application."""
        async with self._lock:
            closing = list(self._open.values())
            self._open.clear()
        for book in closing:
            await book.database.dispose()
        if self._blank_directory is not None:
            await to_thread.run_sync(self._blank_directory.cleanup)
            self._blank_directory = None

    async def _acquire(self, book_id: BookId, path: Path) -> OpenBook:
        """Count one more user of a book, opening its database first when it is closed.

        The open book is looked up and counted with no await between, so a removal or a closing never finds it unused
        while a caller has it in hand.

        :param book_id: The book.
        :type book_id: BookId
        :param path: The database file of the book.
        :type path: Path
        :returns: The open book, counted.
        :rtype: OpenBook
        :raises NotFoundError: If the file does not exist, or the book was closed for good.
        :raises BookTooNewError: If the database is at a revision of the book schema that this code does not ship.
        """
        if (book := self._open.get(book_id)) is not None:
            book.users += 1
            return book
        async with self._lock:
            if book_id in self._closed:
                raise NotFoundError(book_id)
            if (book := self._open.get(book_id)) is None:
                if not await anyio.Path(path).is_file():
                    raise NotFoundError(book_id)
                database = self._database(path)
                try:
                    await self._migrate(database)
                except BaseException:
                    await database.dispose()
                    raise
                book = self._open[book_id] = OpenBook(database=database)
            book.users += 1
            return book

    def _database(self, path: Path) -> SqlDatabase:
        """Configure the database of the book schema over a file.

        :param path: The file.
        :type path: Path
        :returns: The database, which opens no connection yet.
        :rtype: SqlDatabase
        """
        return SqlDatabase(
            f'sqlite:///{path}', schema=Schema.BOOK, tracer=self._tracer, wait_seconds=self._wait_seconds
        )

    async def _migrate(self, database: SqlDatabase) -> None:
        """Bring the file of a book to the head revision of the book schema, unless it is there already.

        :param database: The database of the book, not yet handed to any caller.
        :type database: SqlDatabase
        :raises BookTooNewError: If the file is at a revision this code does not ship.
        """
        if self._revisions is None:
            self._revisions = await to_thread.run_sync(_shipped_revisions)
        code, shipped = self._revisions
        revisions = SchemaRevisions(database=await database.current_revisions(), code=code, shipped=shipped)
        if revisions.unknown:
            raise BookTooNewError
        if not revisions.is_current:
            await to_thread.run_sync(database.migrations.upgrade)

    async def blank(self) -> Path:
        """Return the blank file new books are copied from, making it in a temporary directory the first time.

        :returns: A file of the book schema at the head revision, with no rows.
        :rtype: Path
        """
        async with self._lock:
            if self._blank is None:
                directory = self._blank_directory = await to_thread.run_sync(
                    partial(TemporaryDirectory, prefix=self.BLANK_PREFIX)
                )
                # The name of a temporary directory is absolute, so the URL of the file needs no resolving
                blank = Path(directory.name) / KeySegment.DATABASE
                database = self._database(blank)
                try:
                    await to_thread.run_sync(database.migrations.upgrade)
                finally:
                    await database.dispose()
                self._blank = blank
            return self._blank


def _shipped_revisions() -> tuple[tuple[str, ...], frozenset[str]]:
    """Read the heads and every revision of the book schema, once for a registry, in a worker thread.

    :returns: The head revisions and the set of every revision the code ships.
    :rtype: tuple[tuple[str, ...], frozenset[str]]
    """
    script = ScriptDirectory(str(Schema.BOOK.migrations))
    return tuple(script.get_heads()), frozenset(revision.revision for revision in script.walk_revisions())
