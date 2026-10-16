"""Providers of the persistence adapters, one class per backend selectable in the settings."""

from dishka import Provider, Scope, alias, provide

# Neither exported by dishka's top level nor documented
from dishka.entities.marker import HasContext
from starlette.requests import Request

from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.app.settings import PersistenceBackend, Settings
from bookreviver.ports.persistence import ChangeWaitSeconds, UnitOfWork


class ChangeWaitProvider(Provider):
    """The limit a unit of work waits for the write lock with, chosen by the context of the scope it lives in.

    A unit of work that serves an HTTP request must answer in seconds, so its limit is short and a busy book is
    reported at once as a problem the interface can retry. A background job must not lose its work to a short wait,
    and the work of the startup in the lifespan is no request either, so both get the long limit. Neither is told
    apart by a flag that every caller would have to pass. dishka's FastAPI integration puts the ``Request`` into the
    context of the scope of every HTTP request, the Taskiq integration puts the ``TaskiqMessage`` into the scope of a
    job, and the lifespan puts nothing, so the presence of the ``Request`` in the context is what makes a scope a
    request, and every other scope falls back to the default factory.
    """

    @provide(scope=Scope.REQUEST)
    def job_limit(self, settings: Settings) -> ChangeWaitSeconds:
        """Give the limit of a unit of work that serves no HTTP request.

        :param settings: Application settings, of which the limit of a job is read.
        :type settings: Settings
        :returns: The long limit, for a background job and the work of the startup.
        :rtype: ChangeWaitSeconds
        """
        return ChangeWaitSeconds(settings.change_wait.job_seconds)

    # Registered after the default, so that dishka takes it in a scope whose context has a Request
    @provide(scope=Scope.REQUEST, when=HasContext(Request))
    def request_limit(self, settings: Settings) -> ChangeWaitSeconds:
        """Give the limit of a unit of work that serves an HTTP request.

        :param settings: Application settings, of which the limit of a request is read.
        :type settings: Settings
        :returns: The short limit, so a request never hangs for the wait of a job.
        :rtype: ChangeWaitSeconds
        """
        return ChangeWaitSeconds(settings.change_wait.request_seconds)


class MemoryPersistenceProvider(Provider):
    """In-memory persistence: one database per application, one unit of work per request."""

    @provide(scope=Scope.APP)
    def database(self) -> InMemoryDatabase:
        """Create the empty in-memory database of the application.

        :returns: Database shared by every unit of work of the application.
        :rtype: InMemoryDatabase
        """
        return InMemoryDatabase()

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
    """SQL persistence: the unit of work of the request that ``DatabaseProvider`` builds is the port's.

    The provider builds nothing. The engines and the unit of work come from ``DatabaseProvider``, which the account
    tables share, so selecting this backend adds no second connection pool and no second unit of work.
    """

    unit_of_work = alias(source=SqlAlchemyUnitOfWork, provides=UnitOfWork)


PERSISTENCE_PROVIDERS: dict[PersistenceBackend, type[Provider]] = {
    PersistenceBackend.SQLALCHEMY: SqlAlchemyPersistenceProvider,
    PersistenceBackend.MEMORY: MemoryPersistenceProvider,
}
