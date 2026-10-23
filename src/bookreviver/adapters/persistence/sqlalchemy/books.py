"""The databases of the books: one SQLite file in the directory of each book, opened on first use and closed when idle.

The file of a book is ``projects/<id>/book.db`` under the storage root, beside the directories of the files of the
book, so the directory of a book holds the whole book. :class:`BookDatabases` opens a
:class:`~bookreviver.adapters.persistence.sqlalchemy.database.SqlDatabase` of the book schema the first time a unit of
work asks for the book, brings the file to the head revision of that schema when it is behind, and refuses a file at a
revision this code does not ship with ``BookTooNewError``, leaving the file as it is for the newer code. Each open book
has engines and a write queue of its own, so a change of one book never waits for a change of another.

A new book starts as a copy of a blank file at the head revision, made once for the registry: running the revisions
takes about a second, and copying a file a few milliseconds. The copy refuses to replace a file that exists, so two
creations of one book cannot both succeed.

A book that no unit of work has used for the idle limit is closed, which disposes of its engines and their
connections, and the next use opens it again. :meth:`BookDatabases.keep_closing_idle` looks for such books ten times
within the limit, run by the application in a task group of its own. Removing a book waits until the units of work
using it have ended, so no transaction is cut off, and then deletes the database, its write-ahead log and the
directory of the book when nothing else is left in it.

Every method runs on the event loop. The calls that block, on files or on Alembic, go to worker threads.
"""

import shutil
from contextlib import asynccontextmanager, suppress
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic
from typing import TYPE_CHECKING, Final
from uuid import UUID

import anyio
from alembic.script import ScriptDirectory
from anyio import to_thread
from attrs import define, field

from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SchemaRevisions, SqlDatabase
from bookreviver.domain.errors import BookTooNewError, ConflictError, NotFoundError
from bookreviver.domain.ids import BookId
from bookreviver.domain.keys import BookKeys, KeySegment

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.ports.tracing import Tracer

# The files SQLite keeps beside a database in the write-ahead log, removed with it
JOURNAL_SUFFIXES: Final = ('-wal', '-shm')


@define
class OpenBook:
    """A book whose database is open, and the units of work using it.

    :ivar database: The database of the book.
    :ivar users: How many units of work are inside a transaction of the book, or waiting for one.
    :ivar idle_since: Reading of the monotonic clock when the last unit of work stopped using the book.
    :ivar released: Event a removal waits on until the last unit of work stops using the book, or None.
    """

    database: SqlDatabase
    users: int = 0
    idle_since: float = field(factory=monotonic)
    released: anyio.Event | None = None


class BookDatabases:
    """The open databases of the books under one storage root.

    :ivar IDLE_CHECKS: How many times within the idle limit the registry looks for books to close.
    :ivar BLANK_PREFIX: Prefix of the temporary directory that holds the blank file the registry makes.
    """

    IDLE_CHECKS: Final = 10
    BLANK_PREFIX: Final = 'bookreviver-blank-book-'

    def __init__(
        self,
        storage_root: Path,
        *,
        tracer: Tracer,
        wait_seconds: float,
        idle_seconds: float,
        blank: Path | None = None,
    ) -> None:
        """Serve the books whose directories lie under ``storage_root``.

        :param storage_root: The root of the storage, which holds the directory of every book under ``projects``.
        :type storage_root: Path
        :param tracer: Tracer the write blocks of every book are timed with.
        :type tracer: Tracer
        :param wait_seconds: The busy timeout every connection of a book gets, which is the longest wait limit of a
                             unit of work.
        :type wait_seconds: float
        :param idle_seconds: How long a book stays open after the last unit of work used it.
        :type idle_seconds: float
        :param blank: A file of the book schema at the head revision that new books are copied from, or None to make one
                      on the first creation of a book.
        :type blank: Path | None
        """
        self._root = storage_root
        self._tracer = tracer
        self._wait_seconds = wait_seconds
        self._idle_seconds = idle_seconds
        self._blank = blank
        self._blank_directory: TemporaryDirectory[str] | None = None
        self._revisions: tuple[tuple[str, ...], frozenset[str]] | None = None
        self._open: dict[BookId, OpenBook] = {}
        self._removing: set[BookId] = set()
        # Taken to open, create and close books, never around a transaction
        self._lock = anyio.Lock()

    def path(self, book_id: BookId) -> Path:
        """Return where the database of a book lives.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The path of ``projects/<id>/book.db`` under the storage root.
        :rtype: Path
        """
        return self._root / BookKeys(book_id).database

    @asynccontextmanager
    async def use(self, book_id: BookId) -> AsyncIterator[SqlDatabase]:
        """Hold the database of a book open for one unit of work, opening it first when it is closed.

        :param book_id: The book.
        :type book_id: BookId
        :returns: Iterator yielding the open database of the book once.
        :rtype: AsyncIterator[SqlDatabase]
        :raises NotFoundError: If the book has no database, or it is being removed.
        :raises BookTooNewError: If the database is at a revision of the book schema that this code does not ship.
        """
        book = await self._acquire(book_id)
        try:
            yield book.database
        finally:
            book.users -= 1
            book.idle_since = monotonic()
            if not book.users and book.released is not None:
                book.released.set()

    async def create(self, book_id: BookId) -> None:
        """Make the database of a new book, empty and at the head revision, in a new directory of the book.

        :param book_id: The new book.
        :type book_id: BookId
        :raises ConflictError: If the book has a database already.
        """
        blank = await self._blank_file()
        try:
            await to_thread.run_sync(_copy_new, blank, self.path(book_id))
        except FileExistsError as error:
            raise ConflictError(book_id) from error

    async def remove(self, book_id: BookId) -> None:
        """Close the database of a book once no unit of work uses it, and delete its files.

        A book without a database is left as it is, so a removal broken off half way can run again.

        :param book_id: The book.
        :type book_id: BookId
        """
        async with self._lock:
            self._removing.add(book_id)
            book = self._open.pop(book_id, None)
        try:
            if book is not None:
                if book.users:
                    released = book.released = anyio.Event()
                    await released.wait()
                await book.database.dispose()
            await to_thread.run_sync(_delete_database, self.path(book_id))
        finally:
            self._removing.discard(book_id)

    async def stored(self) -> list[BookId]:
        """Return every book that has a database under the storage root.

        :returns: The identifiers of the books, in order.
        :rtype: list[BookId]
        """
        return await to_thread.run_sync(self._stored)

    async def close_idle(self) -> None:
        """Close every open book that no unit of work has used for the idle limit."""
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

    async def _acquire(self, book_id: BookId) -> OpenBook:
        """Count one more user of a book, opening its database first when it is closed.

        The open book is looked up and counted with no await between, so a removal or a closing never finds it unused
        while a unit of work has it in hand.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The open book, counted.
        :rtype: OpenBook
        :raises NotFoundError: If the book has no database, or it is being removed.
        :raises BookTooNewError: If the database is at a revision of the book schema that this code does not ship.
        """
        if (book := self._open.get(book_id)) is not None:
            book.users += 1
            return book
        async with self._lock:
            if book_id in self._removing:
                raise NotFoundError(book_id)
            if (book := self._open.get(book_id)) is None:
                path = self.path(book_id)
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

        :param database: The database of the book, not yet handed to any unit of work.
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

    async def _blank_file(self) -> Path:
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

    def _stored(self) -> list[BookId]:
        """List the books that have a database, in a worker thread.

        Every directory under ``projects`` is named by the identifier of its book, which is how the stores make them.

        :returns: The identifiers of the books, in order.
        :rtype: list[BookId]
        """
        books = self._root / KeySegment.BOOKS
        if not books.is_dir():
            return []
        found: list[BookId] = []
        for directory in books.iterdir():
            with suppress(ValueError):
                book_id = BookId(UUID(directory.name))
                if self.path(book_id).is_file():
                    found.append(book_id)
        return sorted(found)


def _shipped_revisions() -> tuple[tuple[str, ...], frozenset[str]]:
    """Read the heads and every revision of the book schema, once for a registry, in a worker thread.

    :returns: The head revisions and the set of every revision the code ships.
    :rtype: tuple[tuple[str, ...], frozenset[str]]
    """
    script = ScriptDirectory(str(Schema.BOOK.migrations))
    return tuple(script.get_heads()), frozenset(revision.revision for revision in script.walk_revisions())


def _copy_new(blank: Path, target: Path) -> None:
    """Copy the blank file to the place of a new database, refusing to replace a file that exists.

    :param blank: The blank file.
    :type blank: Path
    :param target: The place of the database of the new book.
    :type target: Path
    :raises FileExistsError: If a file exists at ``target``.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    with blank.open('rb') as source, target.open('xb') as copy:
        shutil.copyfileobj(source, copy)


def _delete_database(path: Path) -> None:
    """Delete a database with its write-ahead log, and the directory of the book when nothing else is left in it.

    :param path: The database of the book.
    :type path: Path
    """
    for file in (path, *(path.with_name(path.name + suffix) for suffix in JOURNAL_SUFFIXES)):
        file.unlink(missing_ok=True)
    with suppress(FileNotFoundError):
        if not any(path.parent.iterdir()):
            path.parent.rmdir()
