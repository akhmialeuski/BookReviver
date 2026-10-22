"""Unit of work that runs the work of a use case whole in a worker thread, on one SQLite transaction of one file.

The unit of work holds the :class:`~bookreviver.adapters.persistence.sqlalchemy.database.SqlDatabase` of the catalogue,
the :class:`~bookreviver.adapters.persistence.sqlalchemy.books.BookDatabases` of the books and a wait limit, and
nothing else, so it keeps no state between calls and coroutines may share one. Each call hands a synchronous function
to one database, which runs it in a thread of its own on a session of a transaction that the call opens and ends:

- ``read`` and ``read_book`` run on a reader connection of the catalogue or of the book. Every statement of the work
  sees one committed state of the file, a write of another transaction in the meantime stays invisible to it, and the
  transaction ends by rollback when the work returns. A repository write in it is refused with ``NoChangeOpenError``,
  by the guards the database puts on the sessions of its readers.
- ``change`` and ``change_book`` run on the one connection of the writer of the catalogue or of the book, after the wait
  for its write queue, from ``BEGIN IMMEDIATE`` to the commit that follows the work. When the work raises, the
  transaction is rolled back and the exception goes on. ``change_book`` reads the project row first, so the work sees
  the project as the previous change committed it. Every file has a writer of its own, so a change of one book never
  waits for a change of another book or of the catalogue.

A book is held open by the registry for the length of each call, so it is never closed under a transaction. The work
receives the repositories of its file bound to the session of the transaction. The accounts of fastapi-users are
tables of the catalogue but not part of the port, so ``read_session`` and ``change_session`` run a function over a
session of the catalogue itself, in the same threads and with the same transactions, for the account database.
"""

from functools import partial
from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.repositories import (
    SqlAlchemyBookRepositories,
    SqlAlchemyCatalogueRepositories,
)
from bookreviver.domain.enums import BlockKind
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.books import BookDatabases
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Runs the work of a use case on the repositories of one transaction of the catalogue or of a book.

    :ivar _catalogue: The database of the catalogue.
    :ivar _books: The registry of the databases of the books.
    :ivar _wait_seconds: How long a change waits for the writer of its file before it gives up with ``BookBusyError``.
    """

    def __init__(
        self, catalogue: SqlDatabase, books: BookDatabases, *, wait_seconds: float = DEFAULT_CHANGE_WAIT_SECONDS
    ) -> None:
        """Run the transactions of the catalogue and of the books with the given wait limit for a writer.

        :param catalogue: The database of the catalogue, shared by every unit of work.
        :type catalogue: SqlDatabase
        :param books: The registry of the databases of the books, shared by every unit of work.
        :type books: BookDatabases
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
    async def read_book[T](self, project_id: ProjectId, work: Callable[[BookRepositories], T]) -> T:
        """Run ``work`` on a transaction of the book that only reads, in a thread of the book.

        :param project_id: Project whose book is read.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        :raises NoChangeOpenError: If the work writes.
        """
        async with self._books.use(project_id) as database:
            return await database.read(lambda session: work(SqlAlchemyBookRepositories(session)))

    @override
    async def change_book[T](self, project_id: ProjectId, work: Callable[[BookRepositories, Project], T]) -> T:
        """Run ``work`` on a transaction of the book that writes, with the project as the previous change committed it.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project read at its start.
        :type work: Callable[[BookRepositories, Project], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the file of the book is at a revision this code does not ship.
        :raises BookBusyError: If the writer of the book or SQLite's write lock was not free within the wait limit.
        """
        async with self._books.use(project_id) as database:
            return await database.write(
                partial(self._work_on_book, project_id, work),
                kind=BlockKind.BOOK,
                book=project_id,
                wait_seconds=self._wait_seconds,
            )

    @override
    async def create_book(self, project: Project) -> Project:
        """Make the file of a new book and store its project in it.

        A failure after the file was made leaves a file the library does not list, which the start removes.

        :param project: The project of the new book.
        :type project: Project
        :returns: The project as stored.
        :rtype: Project
        :raises ConflictError: If a book with this identifier is stored.
        """
        await self._books.create(project.id)
        async with self._books.use(project.id) as database:
            return await database.write(
                lambda session: SqlAlchemyBookRepositories(session).projects.add(project),
                kind=BlockKind.BOOK,
                book=project.id,
                wait_seconds=self._wait_seconds,
            )

    @override
    async def delete_book(self, project_id: ProjectId) -> None:
        """Remove the file of a book once the units of work using it have ended.

        :param project_id: Project whose book is removed.
        :type project_id: ProjectId
        """
        await self._books.remove(project_id)

    @override
    async def stored_books(self) -> Collection[ProjectId]:
        """Return the books that have a file under the storage root.

        :returns: The identifiers of the stored books.
        :rtype: Collection[ProjectId]
        """
        return await self._books.stored()

    @staticmethod
    def _work_on_book[T](project_id: ProjectId, work: Callable[[BookRepositories, Project], T], session: Session) -> T:
        """Read the project of a book on the session of a change, hand it with the repositories to the work, then prune.

        A group of pages is made by the first row that names it, so once the work is done the groups no row names any
        longer are removed in the same transaction.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project.
        :type work: Callable[[BookRepositories, Project], T]
        :param session: Session of the transaction, which has begun.
        :type session: Session
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the project is not stored.
        """
        repositories = SqlAlchemyBookRepositories(session)
        done = work(repositories, repositories.projects.get(project_id))
        repositories.groups.prune()
        return done

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
