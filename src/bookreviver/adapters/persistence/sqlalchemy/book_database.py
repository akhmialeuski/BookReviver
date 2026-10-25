"""Database of one book that runs the work of a use case whole in a worker thread, on one transaction of its file.

A :class:`SqlAlchemyBookDatabase` holds the directory name of a book, the path of its file, which it builds from the
storage root, the :class:`~bookreviver.adapters.persistence.sqlalchemy.books.BookRegistry` of the books, the event
channel of the book and a wait limit, and nothing else, so it keeps no state between calls and coroutines may share
one. Each call hands a synchronous function to the database of the book, which runs it in a thread of its own on a
session of a transaction that the call opens and ends:

- ``read`` runs on a reader connection of the book. Every statement of the work sees one committed state of the file, a
  write of another transaction in the meantime stays invisible to it, and the transaction ends by rollback when the
  work returns. A repository write in it is refused with ``NoChangeOpenError``, by the guards the database puts on the
  sessions of its readers.
- ``change`` runs on the one connection of the writer of the book, after the wait for its write queue, from ``BEGIN
  IMMEDIATE`` to the commit that follows the work. When the work raises, the transaction is rolled back and the
  exception goes on. It reads the record of the book first, so the work sees the record as the previous change
  committed it. Every file has a writer of its own, so a change of one book never waits for a change of another book or
  of the catalogue.
- A change that wrote a part of the book a reader asks for raises the version on the record of the book, in its own
  transaction, and is announced as ``BookChanged`` with the new version and the parts it wrote once it has committed.
  The parts are found from the tables the transaction wrote, by
  :class:`~bookreviver.adapters.persistence.sqlalchemy.changes.WrittenParts`.

A book is held open by the registry for the length of each call, so it is never closed under a transaction. The work
receives the repositories of its file bound to the session of the transaction. ``create`` copies the blank file of the
registry into the directory of a new book and stores the first record with no announcement, since nobody can watch a
book that is not listed yet. ``adopt`` takes the file of an imported book, which it brings to the head revision and
checks for a record, and ``close`` closes the book for good before the storage deletes its file.
"""

import sqlite3
from contextlib import closing
from functools import partial
from typing import TYPE_CHECKING, cast, override

from sqlalchemy import select, update
from sqlalchemy.exc import DatabaseError

from bookreviver.adapters.persistence.sqlalchemy.changes import WrittenParts
from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyBookRepositories
from bookreviver.adapters.persistence.sqlalchemy.tables import BookRecordRow
from bookreviver.domain.enums import BlockKind
from bookreviver.domain.errors import ConflictError, InvalidArchiveError, NotFoundError
from bookreviver.domain.events import BookChanged
from bookreviver.domain.keys import KeySegment
from bookreviver.ports.persistence import BookDatabase
from bookreviver.ports.runtime import BookEvents

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import BookDirectoryName
    from bookreviver.ports.persistence import BookRepositories


# Where SQLite keeps a database that lives in memory alone, as the copy of a book does
MEMORY_DATABASE: str = ':memory:'
NO_RECORD: str = 'The database of the archive holds no book.'
NOT_A_DATABASE: str = 'The database of the archive is not a database of a book.'


def database_file(root: Path, dir_name: BookDirectoryName) -> Path:
    """Name the database file of a book in its directory, which may not exist yet.

    :param root: Storage root of the directories of the books.
    :type root: Path
    :param dir_name: Name of the directory of the book.
    :type dir_name: BookDirectoryName
    :returns: The path of ``book.db`` in the directory of the book.
    :rtype: Path
    """
    return root / KeySegment.BOOKS / dir_name / KeySegment.DATABASE


class SqlAlchemyBookDatabase(BookDatabase):
    """Runs the work of a use case on the repositories of one transaction of one book.

    :ivar _dir_name: Name of the directory of the book.
    :ivar _path: The database file of the book, ``book.db`` in its directory under the storage root.
    :ivar _books: The registry of the databases of the books.
    :ivar _events: The event channel of the book, where a committed change is announced with the new version.
    :ivar _wait_seconds: How long a change waits for the writer of the book before it gives up with ``BookBusyError``.
    """

    def __init__(self, dir_name: BookDirectoryName, *, root: Path, books: BookRegistry, wait_seconds: float) -> None:
        """Run the transactions of a book with the given wait limit for its writer.

        :param dir_name: Name of the directory of the book, which the event channel of the book is opened by too.
        :type dir_name: BookDirectoryName
        :param root: Storage root of the directories of the books, in which the file of the book lies.
        :type root: Path
        :param books: The registry of the databases of the books, shared by every book database.
        :type books: BookRegistry
        :param wait_seconds: How long a change waits for the writer of the book before it gives up with
                             ``BookBusyError``; :mod:`bookreviver.adapters.persistence.sqlalchemy.database` says what it
                             covers.
        :type wait_seconds: float
        """
        self._dir_name = dir_name
        self._path = database_file(root, dir_name)
        self._books = books
        self._events = BookEvents.open(dir_name)
        self._wait_seconds = wait_seconds

    @override
    async def read[T](self, work: Callable[[BookRepositories], T]) -> T:
        """Run ``work`` on a transaction of the book that only reads, in a thread of the book.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        :raises NoChangeOpenError: If the work writes.
        """
        async with self._books.use(self._dir_name, self._path) as database:
            return await database.read(lambda session: work(SqlAlchemyBookRepositories(session)))

    @override
    async def change[T](self, work: Callable[[BookRepositories, BookRecord], T]) -> T:
        """Run ``work`` on a transaction of the book that writes, with its record as the previous change committed it.

        :param work: Function over the repositories of the transaction and the record of the book read at its start.
        :type work: Callable[[BookRepositories, BookRecord], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        :raises BookBusyError: If the writer of the book or SQLite's write lock was not free within the wait limit.
        """
        async with self._books.use(self._dir_name, self._path) as database:
            done, changed = await database.write(
                partial(self._work_on_book, work),
                kind=BlockKind.BOOK,
                book=self._dir_name,
                wait_seconds=self._wait_seconds,
            )
        # The change is announced once it committed, so no reader asks for the new version before it is there
        if changed is not None:
            await self._events.publish(changed)
        return done

    @override
    async def version(self) -> int:
        """Read the version of the book in one transaction of a reader of the book.

        :returns: The version of the book.
        :rtype: int
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        """
        async with self._books.use(self._dir_name, self._path) as database:
            return await database.read(lambda session: session.scalars(select(BookRecordRow.version)).one())

    @override
    async def snapshot(self) -> bytes:
        """Copy the database of the book through SQLite's backup, in one transaction of a reader of the book.

        :returns: The database file as SQLite writes it, of the state committed when the copy began.
        :rtype: bytes
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        """
        async with self._books.use(self._dir_name, self._path) as database:
            return await database.read(_copy_of_database)

    @override
    async def create(self, book_record: BookRecord) -> None:
        """Copy the blank database into the directory of the book, and store the first record in one transaction.

        :param book_record: The record of the book.
        :type book_record: BookRecord
        :raises ConflictError: If the book has a database already.
        """
        try:
            await self._books.copy_blank(self._path)
        except FileExistsError as error:
            raise ConflictError(self._dir_name) from error
        async with self._books.use(self._dir_name, self._path) as database:
            await database.write(
                lambda session: SqlAlchemyBookRepositories(session).book_record.add(book_record),
                kind=BlockKind.BOOK,
                book=self._dir_name,
                wait_seconds=self._wait_seconds,
            )

    @override
    async def adopt(self) -> None:
        """Open the file an archive placed, which brings it to the head revision, and read its record.

        :raises BookTooNewError: If the file is at a revision this code does not ship.
        :raises InvalidArchiveError: If the file is not an SQLite database, or holds no record of a book.
        """
        try:
            async with self._books.use(self._dir_name, self._path) as database:
                await database.read(_the_record)
        except DatabaseError as error:
            raise InvalidArchiveError(NOT_A_DATABASE) from error

    @override
    async def close(self) -> None:
        """Close the book in the registry for good, once the transactions using it have ended."""
        await self._books.close(self._dir_name)

    def _work_on_book[T](
        self, work: Callable[[BookRepositories, BookRecord], T], session: Session
    ) -> tuple[T, BookChanged | None]:
        """Read the record of the book on the session of a change, hand it with the repositories to the work, prune.

        A group of pages is made by the first row that names it, so once the work is done the groups no row names any
        longer are removed in the same transaction.

        :param work: Function over the repositories of the transaction and the record of the book.
        :type work: Callable[[BookRepositories, BookRecord], T]
        :param session: Session of the transaction, which has begun.
        :type session: Session
        :returns: What the work returns, and the announcement of the change if it wrote a part of the book.
        :rtype: tuple[T, BookChanged | None]
        :raises NotFoundError: If the book is not stored.
        """
        repositories = SqlAlchemyBookRepositories(session)
        written = WrittenParts(session)
        done = work(repositories, repositories.book_record.get())
        repositories.groups.prune()
        session.flush()
        if not written.parts:
            return done, None
        parts = frozenset(written.parts)
        raise_version = update(BookRecordRow).values(version=BookRecordRow.version + 1).returning(BookRecordRow.version)
        raised = session.execute(raise_version)
        return done, BookChanged(version=raised.scalar_one(), resources=parts)


def _the_record(session: Session) -> BookRecord:
    """Read the one record of a book file an archive placed.

    :param session: Session of the transaction of the read.
    :type session: Session
    :returns: The record of the book.
    :rtype: BookRecord
    :raises InvalidArchiveError: If the file holds no record of a book.
    """
    try:
        return SqlAlchemyBookRepositories(session).book_record.get()
    except NotFoundError as error:
        raise InvalidArchiveError(NO_RECORD) from error


def _copy_of_database(session: Session) -> bytes:
    """Copy the database of the session's connection into memory with SQLite's backup, and return the copy as a file.

    :param session: Session of a transaction that reads the book.
    :type session: Session
    :returns: The database file as SQLite writes it.
    :rtype: bytes
    """
    # The transaction of the session has just checked the connection out, so the driver's connection is open
    source = cast('sqlite3.Connection', session.connection().connection.driver_connection)
    with closing(sqlite3.connect(MEMORY_DATABASE)) as copy:
        source.backup(copy)
        return copy.serialize()
