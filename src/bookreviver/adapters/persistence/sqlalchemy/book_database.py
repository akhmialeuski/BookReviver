"""Database of one book that runs the work of a use case whole in a worker thread, on one transaction of its file.

A :class:`SqlAlchemyBookDatabase` holds the identifier of a book, the
:class:`~bookreviver.adapters.persistence.sqlalchemy.books.BookRegistry` of the books and a wait limit, and nothing
else, so it keeps no state between calls and coroutines may share one. Each call hands a synchronous function to the
database of the book, which runs it in a thread of its own on a session of a transaction that the call opens and ends:

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
receives the repositories of its file bound to the session of the transaction.
"""

import sqlite3
from contextlib import closing
from functools import partial
from typing import TYPE_CHECKING, cast, override

from sqlalchemy import select, update

from bookreviver.adapters.persistence.sqlalchemy.changes import WrittenParts
from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyBookRepositories
from bookreviver.adapters.persistence.sqlalchemy.tables import BookRecordRow
from bookreviver.domain.enums import BlockKind
from bookreviver.domain.events import BookChanged
from bookreviver.ports.persistence import BookDatabase, BookDatabases

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import EventPublisher


# Where SQLite keeps a database that lives in memory alone, as the copy of a book does
MEMORY_DATABASE: str = ':memory:'


class SqlAlchemyBookDatabase(BookDatabase):
    """Runs the work of a use case on the repositories of one transaction of one book.

    :ivar _book_id: Identifier of the book.
    :ivar _books: The registry of the databases of the books.
    :ivar _publisher: Where a committed change of the book is announced, with the new version of the book.
    :ivar _wait_seconds: How long a change waits for the writer of the book before it gives up with ``BookBusyError``.
    """

    def __init__(self, book_id: BookId, books: BookRegistry, *, publisher: EventPublisher, wait_seconds: float) -> None:
        """Run the transactions of a book with the given wait limit for its writer.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param books: The registry of the databases of the books, shared by every book database.
        :type books: BookRegistry
        :param publisher: Where a committed change of the book is announced.
        :type publisher: EventPublisher
        :param wait_seconds: How long a change waits for the writer of the book before it gives up with
                             ``BookBusyError``; :mod:`bookreviver.adapters.persistence.sqlalchemy.database` says what it
                             covers.
        :type wait_seconds: float
        """
        self._book_id = book_id
        self._books = books
        self._publisher = publisher
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
        async with self._books.use(self._book_id) as database:
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
        async with self._books.use(self._book_id) as database:
            done, changed = await database.write(
                partial(self._work_on_book, work),
                kind=BlockKind.BOOK,
                book=self._book_id,
                wait_seconds=self._wait_seconds,
            )
        # The change is announced once it committed, so no reader asks for the new version before it is there
        if changed is not None:
            await self._publisher.publish(changed)
        return done

    @override
    async def version(self) -> int:
        """Read the version of the book in one transaction of a reader of the book.

        :returns: The version of the book.
        :rtype: int
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        """
        async with self._books.use(self._book_id) as database:
            return await database.read(lambda session: session.scalars(select(BookRecordRow.version)).one())

    @override
    async def snapshot(self) -> bytes:
        """Copy the database of the book through SQLite's backup, in one transaction of a reader of the book.

        :returns: The database file as SQLite writes it, of the state committed when the copy began.
        :rtype: bytes
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        """
        async with self._books.use(self._book_id) as database:
            return await database.read(_copy_of_database)

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
        return done, BookChanged(book_id=self._book_id, version=raised.scalar_one(), resources=parts)


class SqlAlchemyBookDatabases(BookDatabases):
    """Opens the database of a book over the registry of the books, with a wait limit and a publisher.

    :ivar _books: The registry of the databases of the books.
    :ivar _publisher: Where a committed change of a book is announced, with the new version of the book.
    :ivar _wait_seconds: How long a change waits for the writer of its book before it gives up with ``BookBusyError``.
    """

    def __init__(self, books: BookRegistry, *, publisher: EventPublisher, wait_seconds: float) -> None:
        """Open the databases of the books with the given wait limit for a writer.

        :param books: The registry of the databases of the books, shared by every book database.
        :type books: BookRegistry
        :param publisher: Where a committed change of a book is announced.
        :type publisher: EventPublisher
        :param wait_seconds: How long a change waits for the writer of its book before it gives up with
                             ``BookBusyError``; a request waits less than a job.
        :type wait_seconds: float
        """
        self._books = books
        self._publisher = publisher
        self._wait_seconds = wait_seconds

    @override
    def open(self, book_id: BookId) -> SqlAlchemyBookDatabase:
        """Return the database of a book, without touching its file.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :returns: The database of the book.
        :rtype: SqlAlchemyBookDatabase
        """
        return SqlAlchemyBookDatabase(book_id, self._books, publisher=self._publisher, wait_seconds=self._wait_seconds)


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
