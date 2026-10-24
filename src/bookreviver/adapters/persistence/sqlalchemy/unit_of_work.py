"""Unit of work that runs the work of a use case whole in a worker thread, on one SQLite transaction of one file.

The unit of work holds the :class:`~bookreviver.adapters.persistence.sqlalchemy.database.SqlDatabase` of the catalogue,
the :class:`~bookreviver.adapters.persistence.sqlalchemy.books.BookRegistry` of the books and a wait limit, and
nothing else, so it keeps no state between calls and coroutines may share one. Each call hands a synchronous function
to one database, which runs it in a thread of its own on a session of a transaction that the call opens and ends:

- ``read`` runs on a reader connection of the catalogue. Every statement of the work sees one committed state of the
  file, a write of another transaction in the meantime stays invisible to it, and the transaction ends by rollback when
  the work returns. A repository write in it is refused with ``NoChangeOpenError``, by the guards the database puts on
  the sessions of its readers.
- ``change`` runs on the one connection of the writer of the catalogue, after the wait for its write queue, from
  ``BEGIN IMMEDIATE`` to the commit that follows the work. When the work raises, the transaction is rolled back and the
  exception goes on.

The transactions of one book are those of its
:class:`~bookreviver.adapters.persistence.sqlalchemy.book_database.SqlAlchemyBookDatabase`, which says how a book is
read, changed, versioned and copied; the unit of work only makes, removes and adopts the files of the books. The
accounts of fastapi-users are tables of the catalogue but not part of the port, so ``read_session`` and
``change_session`` run a function over a session of the catalogue itself, in the same threads and with the same
transactions, for the account database.
"""

from functools import partial
from typing import TYPE_CHECKING, override

from sqlalchemy import func, select, update
from sqlalchemy.exc import DatabaseError

from bookreviver.adapters.persistence.sqlalchemy.repositories import (
    SqlAlchemyBookRepositories,
    SqlAlchemyCatalogueRepositories,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import BookRecordRow
from bookreviver.domain.enums import BlockKind
from bookreviver.domain.errors import InvalidArchiveError
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import CatalogueRepositories


NOT_ONE_RECORD: str = 'The database of the archive does not hold exactly one book.'
NOT_A_DATABASE: str = 'The database of the archive is not a database of a book.'


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Runs the work of a use case on the repositories of one transaction of the catalogue, and makes the books.

    :ivar _catalogue: The database of the catalogue.
    :ivar _books: The registry of the databases of the books.
    :ivar _wait_seconds: How long a change waits for the writer of its file before it gives up with ``BookBusyError``.
    """

    def __init__(
        self,
        catalogue: SqlDatabase,
        books: BookRegistry,
        *,
        wait_seconds: float = DEFAULT_CHANGE_WAIT_SECONDS,
    ) -> None:
        """Run the transactions of the catalogue and the lifecycle of the books with the given wait limit for a writer.

        :param catalogue: The database of the catalogue, shared by every unit of work.
        :type catalogue: SqlDatabase
        :param books: The registry of the databases of the books, shared by every unit of work.
        :type books: BookRegistry
        :param wait_seconds: How long a change waits for the writer of its file before it gives up with
                             ``BookBusyError``; :mod:`bookreviver.adapters.persistence.sqlalchemy.database` says what it
                             covers.
        :type wait_seconds: float
        """
        self._catalogue = catalogue
        self._books = books
        self._wait_seconds = wait_seconds

    @override
    async def read[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run ``work`` on a transaction of the catalogue that only reads, in a thread of the catalogue.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NoChangeOpenError: If the work writes.
        """
        return await self.read_session(lambda session: work(SqlAlchemyCatalogueRepositories(session)))

    @override
    async def change[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run ``work`` on a transaction of the catalogue that writes, in the writer thread of the catalogue.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If the writer or SQLite's write lock was not free within the wait limit.
        """
        return await self.change_session(lambda session: work(SqlAlchemyCatalogueRepositories(session)))

    @override
    async def create_book(self, book_record: BookRecord) -> BookRecord:
        """Make the file of a new book and store the record of the book in it.

        A failure after the file was made leaves a file the library does not list.

        :param book_record: The record of the new book.
        :type book_record: BookRecord
        :returns: The record of the book as stored.
        :rtype: BookRecord
        :raises ConflictError: If a book with this identifier is stored.
        """
        await self._books.create(book_record.id)
        async with self._books.use(book_record.id) as database:
            return await database.write(
                lambda session: SqlAlchemyBookRepositories(session).book_record.add(book_record),
                kind=BlockKind.BOOK,
                book=book_record.id,
                wait_seconds=self._wait_seconds,
            )

    @override
    async def delete_book(self, book_id: BookId) -> None:
        """Remove the file of a book once the units of work using it have ended.

        :param book_id: Identifier of the book to remove.
        :type book_id: BookId
        """
        await self._books.remove(book_id)

    @override
    async def adopt_book(self, book_id: BookId) -> BookRecord:
        """Open the file an archive placed for a new book, which brings it to the head revision, and rename its record.

        :param book_id: Identifier of the new book, whose file the archive placed.
        :type book_id: BookId
        :returns: The record of the new book.
        :rtype: BookRecord
        :raises NotFoundError: If no file was placed for the book.
        :raises BookTooNewError: If the file is at a revision this code does not ship.
        :raises InvalidArchiveError: If the file is not an SQLite database, or does not hold exactly one record.
        """
        try:
            async with self._books.use(book_id) as database:
                return await database.write(
                    partial(_rename_the_record, book_id),
                    kind=BlockKind.BOOK,
                    book=book_id,
                    wait_seconds=self._wait_seconds,
                )
        except DatabaseError as error:
            raise InvalidArchiveError(NOT_A_DATABASE) from error

    async def read_session[T](self, work: Callable[[Session], T]) -> T:
        """Run ``work`` over a session of the catalogue that only reads, which is not part of the port.

        The account database reads its tables through it. The session is closed when the work returns, so the rows the
        work returns are detached and must be fully loaded.

        :param work: Function over the session of the transaction.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        :raises NoChangeOpenError: If the work writes.
        """
        return await self._catalogue.read(work)

    async def change_session[T](self, work: Callable[[Session], T]) -> T:
        """Run ``work`` over a session of the catalogue that writes, which is not part of the port.

        The account database writes its tables through it, in the same transaction as any other change of the
        catalogue: the work commits when it returns and rolls back when it raises. The rows the work returns are
        detached once the session is closed, and stay loaded, as the sessions do not expire them on commit.

        :param work: Function over the session of the transaction.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If the writer or SQLite's write lock was not free within the wait limit.
        """
        return await self._catalogue.write(work, kind=BlockKind.OTHER, book=None, wait_seconds=self._wait_seconds)


def _rename_the_record(book_id: BookId, session: Session) -> BookRecord:
    """Give the one record of a book file the identifier of the book it now is.

    No other table of a book names the book, so only the row of its record changes.

    :param book_id: Identifier of the new book.
    :type book_id: BookId
    :param session: Session of the transaction of the change.
    :type session: Session
    :returns: The record under its new identifier.
    :rtype: BookRecord
    :raises InvalidArchiveError: If the file does not hold exactly one record of a book.
    """
    if session.scalar(select(func.count()).select_from(BookRecordRow)) != 1:
        raise InvalidArchiveError(NOT_ONE_RECORD)
    session.execute(update(BookRecordRow).values(id=book_id))
    return SqlAlchemyBookRepositories(session).book_record.get(book_id)
