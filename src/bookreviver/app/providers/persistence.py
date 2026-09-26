"""Providers of the persistence adapters, one class per backend selectable in the settings."""

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.app.settings import PersistenceBackend
from bookreviver.ports.persistence import UnitOfWork


class MemoryPersistenceProvider(Provider):
    """In-memory persistence: one database per application, one unit of work per request."""

    @provide(scope=Scope.APP)
    def database(self) -> InMemoryDatabase:
        """Create the empty in-memory database of the application."""
        return InMemoryDatabase()

    @provide(scope=Scope.REQUEST)
    def unit_of_work(self, database: InMemoryDatabase) -> UnitOfWork:
        """Open a unit of work over the application's in-memory database."""
        return InMemoryUnitOfWork(database)


class SqlAlchemyPersistenceProvider(Provider):
    """SQL persistence: one unit of work per request over the request session of ``DatabaseProvider``."""

    @provide(scope=Scope.REQUEST)
    def unit_of_work(self, session: AsyncSession) -> UnitOfWork:
        """Open a unit of work over the session of the current request or job."""
        return SqlAlchemyUnitOfWork(session)


PERSISTENCE_PROVIDERS: dict[PersistenceBackend, type[Provider]] = {
    PersistenceBackend.SQLALCHEMY: SqlAlchemyPersistenceProvider,
    PersistenceBackend.MEMORY: MemoryPersistenceProvider,
}
