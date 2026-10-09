"""Providers of the persistence adapters, one class per backend selectable in the settings."""

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.app.settings import PersistenceBackend, Settings
from bookreviver.ports.persistence import UnitOfWork


class MemoryPersistenceProvider(Provider):
    """In-memory persistence: one database per application, one unit of work per request."""

    @provide(scope=Scope.APP)
    def database(self, settings: Settings) -> InMemoryDatabase:
        """Create the empty in-memory database of the application.

        :param settings: Application settings, of which the wait limit of a change is read.
        :type settings: Settings
        :returns: Database shared by every unit of work of the application.
        :rtype: InMemoryDatabase
        """
        return InMemoryDatabase(wait_seconds=settings.change_wait_seconds)

    @provide(scope=Scope.REQUEST)
    def unit_of_work(self, database: InMemoryDatabase) -> UnitOfWork:
        """Open a unit of work over the application's in-memory database.

        :param database: The application's in-memory database.
        :type database: InMemoryDatabase
        :returns: Unit of work over a private copy of the database.
        :rtype: UnitOfWork
        """
        return InMemoryUnitOfWork(database)


class SqlAlchemyPersistenceProvider(Provider):
    """SQL persistence: one unit of work per request over the request session of ``DatabaseProvider``.

    The provider builds only the unit of work. The engine and the session come from ``DatabaseProvider``, which the
    account tables share, so selecting this backend adds no second connection pool.
    """

    @provide(scope=Scope.REQUEST)
    def unit_of_work(self, session: AsyncSession) -> UnitOfWork:
        """Open a unit of work over the session of the current request or job.

        :param session: Session of the current request or job, provided by ``DatabaseProvider``.
        :type session: AsyncSession
        :returns: Unit of work whose repositories share ``session``.
        :rtype: UnitOfWork
        """
        return SqlAlchemyUnitOfWork(session)


PERSISTENCE_PROVIDERS: dict[PersistenceBackend, type[Provider]] = {
    PersistenceBackend.SQLALCHEMY: SqlAlchemyPersistenceProvider,
    PersistenceBackend.MEMORY: MemoryPersistenceProvider,
}
