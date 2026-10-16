"""Unit of work that runs the work of a use case whole in a worker thread, on one SQLite transaction.

The unit of work holds the :class:`~bookreviver.adapters.persistence.sqlalchemy.database.SqlDatabase` and a wait limit
and nothing else, so it keeps no state between calls and coroutines may share one. Each call hands a synchronous
function to the database, which runs it in a thread of its own on a session of a transaction that the call opens and
ends:

- ``read`` runs on a reader connection of the database. Every statement of the work sees one committed state of the
  database, a write of another transaction in the meantime stays invisible to it, and the transaction ends by rollback
  when the work returns. A repository write in it is refused with ``NoChangeOpenError``, by the guards the database
  puts on the sessions of its readers.
- ``change`` and ``change_book`` run on the one connection of the writer, after the wait for the write queue, from
  ``BEGIN IMMEDIATE`` to the commit that follows the work. When the work raises, the transaction is rolled back and the
  exception goes on. ``change_book`` reads the project row first, so the work sees the project as the previous change
  committed it, and raises ``NotFoundError`` when it is not stored. SQLite has one writer, so a ``change_book`` waits
  for every other change and not only for those of its book.

The work receives :class:`~bookreviver.adapters.persistence.sqlalchemy.repositories.SqlAlchemyRepositories` bound to the
session of the transaction. The accounts of fastapi-users are tables of this adapter but not part of the port, so
``read_session`` and ``change_session`` run a function over the session itself, in the same threads and with the same
transactions, for the account database.
"""

from functools import partial
from typing import TYPE_CHECKING, override

from sqlalchemy import select

from bookreviver.adapters.persistence.sqlalchemy.mappers import ProjectMapper
from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyRepositories
from bookreviver.adapters.persistence.sqlalchemy.tables import ProjectRow
from bookreviver.domain.enums import BlockKind
from bookreviver.domain.errors import NotFoundError
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import Repositories


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Runs the work of a use case on the repositories of one transaction of the SQLite database.

    :ivar _database: The database whose threads and connections the transactions use.
    :ivar _wait_seconds: How long a change waits for the writer before it gives up with ``BookBusyError``.
    """

    def __init__(self, database: SqlDatabase, *, wait_seconds: float = DEFAULT_CHANGE_WAIT_SECONDS) -> None:
        """Run the transactions of ``database`` with the given wait limit for the writer.

        :param database: The application's database, shared by every unit of work.
        :type database: SqlDatabase
        :param wait_seconds: How long a change waits for the writer before it gives up with ``BookBusyError``;
                             :mod:`bookreviver.adapters.persistence.sqlalchemy.database` says what it covers.
        :type wait_seconds: float
        """
        self._database = database
        self._wait_seconds = wait_seconds

    @override
    async def read[T](self, work: Callable[[Repositories], T]) -> T:
        """Run ``work`` on a transaction that only reads, in a thread of the database.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Repositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NoChangeOpenError: If the work writes.
        """
        return await self.read_session(lambda session: work(SqlAlchemyRepositories(session)))

    @override
    async def change[T](self, work: Callable[[Repositories], T]) -> T:
        """Run ``work`` on a transaction that writes, in the writer thread of the database.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[Repositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If the writer or SQLite's write lock was not free within the wait limit.
        """
        return await self.change_session(lambda session: work(SqlAlchemyRepositories(session)))

    @override
    async def change_book[T](self, project_id: ProjectId, work: Callable[[Repositories, Project], T]) -> T:
        """Run ``work`` as ``change`` does, with the project as the previous change committed it.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project read at its start.
        :type work: Callable[[Repositories, Project], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the project is not stored.
        :raises BookBusyError: If the writer or SQLite's write lock was not free within the wait limit.
        """
        return await self._database.write(
            partial(self._work_on_book, project_id, work),
            kind=BlockKind.BOOK,
            book=project_id,
            wait_seconds=self._wait_seconds,
        )

    @staticmethod
    def _work_on_book[T](project_id: ProjectId, work: Callable[[Repositories, Project], T], session: Session) -> T:
        """Read the project of a book on the session of a change, and hand it with the repositories to the work.

        :param project_id: Project whose book is changed.
        :type project_id: ProjectId
        :param work: Function over the repositories of the transaction and the project.
        :type work: Callable[[Repositories, Project], T]
        :param session: Session of the transaction, which has begun.
        :type session: Session
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the project is not stored.
        """
        if (row := session.scalar(select(ProjectRow).where(ProjectRow.id == project_id))) is None:
            raise NotFoundError(project_id)
        return work(SqlAlchemyRepositories(session), ProjectMapper().to_entity(row))

    async def read_session[T](self, work: Callable[[Session], T]) -> T:
        """Run ``work`` over a session of a transaction that only reads, which is not part of the port.

        The account database reads its tables through it. The session is closed when the work returns, so the rows the
        work returns are detached and must be fully loaded.

        :param work: Function over the session of the transaction.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        :raises NoChangeOpenError: If the work writes.
        """
        return await self._database.read(work)

    async def change_session[T](self, work: Callable[[Session], T]) -> T:
        """Run ``work`` over a session of a transaction that writes, which is not part of the port.

        The account database writes its tables through it, in the same transaction as any other change: the work
        commits when it returns and rolls back when it raises. The rows the work returns are detached once the
        session is closed, and stay loaded, as the sessions do not expire them on commit.

        :param work: Function over the session of the transaction.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If the writer or SQLite's write lock was not free within the wait limit.
        """
        return await self._database.write(work, kind=BlockKind.OTHER, book=None, wait_seconds=self._wait_seconds)
