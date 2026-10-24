"""Unit of work that runs the work of a use case whole in a worker thread, on one SQLite transaction of one file.

The unit of work holds the :class:`~bookreviver.adapters.persistence.sqlalchemy.database.SqlDatabase` of the catalogue
and a wait limit, and nothing else, so it keeps no state between calls and coroutines may share one. Each call hands a
synchronous function to one database, which runs it in a thread of its own on a session of a transaction that the call
opens and ends:

- ``read`` runs on a reader connection of the catalogue. Every statement of the work sees one committed state of the
  file, a write of another transaction in the meantime stays invisible to it, and the transaction ends by rollback when
  the work returns. A repository write in it is refused with ``NoChangeOpenError``, by the guards the database puts on
  the sessions of its readers.
- ``change`` runs on the one connection of the writer of the catalogue, after the wait for its write queue, from
  ``BEGIN IMMEDIATE`` to the commit that follows the work. When the work raises, the transaction is rolled back and the
  exception goes on.

The transactions of one book are those of its
:class:`~bookreviver.adapters.persistence.sqlalchemy.book_database.SqlAlchemyBookDatabase`, which says how a book is
read, changed, versioned and copied. The accounts of fastapi-users are tables of the catalogue but not part of the port,
so ``read_session`` and ``change_session`` run a function over a session of the catalogue itself, in the same threads
and with the same transactions, for the account database.
"""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyCatalogueRepositories
from bookreviver.domain.enums import BlockKind
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.ports.persistence import CatalogueRepositories


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Runs the work of a use case on the repositories of one transaction of the catalogue.

    :ivar _catalogue: The database of the catalogue.
    :ivar _wait_seconds: How long a change waits for the writer of its file before it gives up with ``BookBusyError``.
    """

    def __init__(
        self,
        catalogue: SqlDatabase,
        *,
        wait_seconds: float = DEFAULT_CHANGE_WAIT_SECONDS,
    ) -> None:
        """Run the transactions of the catalogue with the given wait limit for its writer.

        :param catalogue: The database of the catalogue, shared by every unit of work.
        :type catalogue: SqlDatabase
        :param wait_seconds: How long a change waits for the writer of its file before it gives up with
                             ``BookBusyError``; :mod:`bookreviver.adapters.persistence.sqlalchemy.database` says what it
                             covers.
        :type wait_seconds: float
        """
        self._catalogue = catalogue
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
