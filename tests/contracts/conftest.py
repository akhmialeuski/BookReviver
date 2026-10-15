"""Fixtures giving the port contract tests every adapter of the port, built the way the application builds it."""

from typing import TYPE_CHECKING, Protocol

import pytest
from dishka import make_async_container

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.app.providers.storage import STORAGE_PROVIDERS
from bookreviver.app.settings import PersistenceBackend, Settings
from bookreviver.ports.storage import AssetStore, SourceStore
from tests.helpers.book_gate import hold_book, hold_book_in_thread
from tests.helpers.builders import new_account_id
from tests.helpers.job_queues import QueueAdapter
from tests.helpers.schema import create_schema
from tests.helpers.seeding import commit_account
from tests.helpers.step_executors import StepExecutorAdapter
from tests.helpers.tracers import TracerAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from contextlib import AbstractAsyncContextManager

    from dishka import AsyncContainer

    from bookreviver.domain.ids import AccountId, ProjectId
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import UnitOfWork

type Database = InMemoryDatabase | SqlDatabase
type OwnerFactory = Callable[[], Awaitable[AccountId]]


class UnitOfWorkFactory(Protocol):
    """A function opening a new unit of work of the backend under test, over the database of the test."""

    def __call__(self, *, wait_seconds: float | None = None) -> UnitOfWork:
        """Open a unit of work over the database of the test.

        :param wait_seconds: How long a change of the unit of work waits for its turn before it gives up with
                             ``BookBusyError``, which a request gets shorter than a job. None for the limit of a job
                             in the settings of the test.
        :type wait_seconds: float | None
        :returns: A new unit of work.
        :rtype: UnitOfWork
        """


class BookHolder(Protocol):
    """A function holding the lock of a book of the backend under test, which every change of that book waits for."""

    def __call__(self, uow: UnitOfWork, project_id: ProjectId | None = None) -> AbstractAsyncContextManager[None]:
        """Hold the lock for the length of an ``async with`` block.

        :param uow: Unit of work of the test, which an adapter that runs the work in a thread holds through a change.
        :type uow: UnitOfWork
        :param project_id: Project whose book is held, or None for the changes outside the content of a book.
        :type project_id: ProjectId | None
        :returns: Context manager that returns once the lock is held and releases it when the block ends.
        :rtype: AbstractAsyncContextManager[None]
        """


# Every adapter of the OrderKeys port by name; each computes keys in memory, so none needs a provider
ORDER_KEYS_ADAPTERS: dict[str, type[OrderKeys]] = {'fractional-indexing': FractionalOrderKeys}


@pytest.fixture(params=list(PersistenceBackend), ids=str)
def fx_persistence_backend(request: pytest.FixtureRequest) -> PersistenceBackend:
    """Return each persistence backend in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the persistence backend.
    :type request: pytest.FixtureRequest
    :returns: The backend under test.
    :rtype: PersistenceBackend
    """
    return PersistenceBackend(request.param)


@pytest.fixture
async def fx_persistence(fx_persistence_backend: PersistenceBackend, fx_settings: Settings) -> AsyncIterator[Database]:
    """Yield the database of the backend under test, shared by every unit of work of the test.

    The SQL backend gets its schema from the models, recorded at the head revision, rather than from the migrations,
    which would make the suite several times slower; ``test_migrations.py`` proves the two schemas equal. Its
    connections open with the job limit of the settings as their busy timeout, as the application's do.

    :param fx_persistence_backend: Persistence backend under test.
    :type fx_persistence_backend: PersistenceBackend
    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the database and disposing of it afterwards.
    :rtype: AsyncIterator[Database]
    """
    if fx_persistence_backend is PersistenceBackend.MEMORY:
        yield InMemoryDatabase()
        return
    await create_schema(fx_settings)
    database = SqlDatabase(
        fx_settings.resolved_database_url, tracer=NullTracer(), wait_seconds=fx_settings.change_wait.job_seconds
    )
    try:
        yield database
    finally:
        await database.dispose()


@pytest.fixture
def fx_uow_factory(fx_persistence: Database, fx_settings: Settings) -> UnitOfWorkFactory:
    """Return a function opening a new unit of work of the backend under test.

    :param fx_persistence: Database of the backend under test.
    :type fx_persistence: Database
    :param fx_settings: Settings of the test, whose job limit is the wait of a unit of work opened with none.
    :type fx_settings: Settings
    :returns: Function opening a unit of work over ``fx_persistence``.
    :rtype: UnitOfWorkFactory
    """

    def open_unit_of_work(*, wait_seconds: float | None = None) -> UnitOfWork:
        """Open a unit of work over the database of the test.

        :param wait_seconds: How long a change waits for its turn, or None for the job limit of the settings.
        :type wait_seconds: float | None
        :returns: A new unit of work.
        :rtype: UnitOfWork
        """
        seconds = fx_settings.change_wait.job_seconds if wait_seconds is None else wait_seconds
        if isinstance(fx_persistence, SqlDatabase):
            return SqlAlchemyUnitOfWork(fx_persistence, wait_seconds=seconds)
        return InMemoryUnitOfWork(fx_persistence, wait_seconds=seconds)

    return open_unit_of_work


@pytest.fixture
def fx_hold_book(fx_persistence: Database) -> BookHolder:
    """Return a function holding the lock of a book, in the way the backend under test allows.

    The in-memory backend runs the work of a change in the event loop, so the test takes the lock of its database. The
    SQL backend runs the work in a thread, so a change whose work blocks that thread holds the book.

    :param fx_persistence: Database of the backend under test.
    :type fx_persistence: Database
    :returns: Function returning a context manager that holds the book while its block runs.
    :rtype: BookHolder
    """

    def hold(uow: UnitOfWork, project_id: ProjectId | None = None) -> AbstractAsyncContextManager[None]:
        """Hold the lock of a book, or of the changes outside books, until the block ends.

        :param uow: Unit of work whose change holds the book, for a backend that holds it through a change.
        :type uow: UnitOfWork
        :param project_id: Project whose book is held, or None for the changes outside the content of a book.
        :type project_id: ProjectId | None
        :returns: Context manager holding the lock.
        :rtype: AbstractAsyncContextManager[None]
        """
        if isinstance(fx_persistence, InMemoryDatabase):
            return hold_book(fx_persistence, project_id)
        return hold_book_in_thread(uow, project_id)

    return hold


@pytest.fixture
def fx_new_owner(fx_persistence: Database) -> OwnerFactory:
    """Return a function creating an account that may own projects in the backend under test.

    Only the SQL backend checks the owner of a project, against the account table of fastapi-users, so an account is
    a committed row there. The in-memory backend has no accounts, since no port stores them, and takes any identifier.

    :param fx_persistence: Database of the backend under test.
    :type fx_persistence: Database
    :returns: Function returning the identifier of a new account.
    :rtype: OwnerFactory
    """

    async def new_owner() -> AccountId:
        """Create an account in the backend under test.

        :returns: Identifier of the new account.
        :rtype: AccountId
        """
        if isinstance(fx_persistence, SqlDatabase):
            return await commit_account(fx_persistence)
        return new_account_id()

    return new_owner


@pytest.fixture(params=list(ORDER_KEYS_ADAPTERS), ids=str)
def fx_order_keys(request: pytest.FixtureRequest) -> OrderKeys:
    """Return each adapter of the OrderKeys port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` names the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test.
    :rtype: OrderKeys
    """
    return ORDER_KEYS_ADAPTERS[request.param]()


@pytest.fixture(params=list(QueueAdapter), ids=str)
def fx_queue_adapter(request: pytest.FixtureRequest) -> QueueAdapter:
    """Return each adapter of the JobQueue port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_queue_under_test``.
    :rtype: QueueAdapter
    """
    return QueueAdapter(request.param)


@pytest.fixture(params=list(StepExecutorAdapter), ids=str)
def fx_step_executor_adapter(request: pytest.FixtureRequest) -> StepExecutorAdapter:
    """Return each adapter of the StepExecutor port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_executor_under_test``.
    :rtype: StepExecutorAdapter
    """
    return StepExecutorAdapter(request.param)


@pytest.fixture(params=list(TracerAdapter), ids=str)
def fx_tracer_adapter(request: pytest.FixtureRequest) -> TracerAdapter:
    """Return each adapter of the Tracer port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_tracer_under_test``.
    :rtype: TracerAdapter
    """
    return TracerAdapter(request.param)


@pytest.fixture(params=[adapter for adapter in TracerAdapter if adapter.keeps_spans], ids=str)
def fx_recording_adapter(request: pytest.FixtureRequest) -> TracerAdapter:
    """Return each adapter of the Tracer port that keeps the spans it is given, in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_tracer_under_test``.
    :rtype: TracerAdapter
    """
    return TracerAdapter(request.param)


@pytest.fixture(params=list(STORAGE_PROVIDERS), ids=str)
async def fx_storage(request: pytest.FixtureRequest, fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
    """Yield a container holding the provider of one storage backend, rooted in the test's data directory.

    :param request: Request of the parametrized fixture, whose ``param`` is the storage backend.
    :type request: pytest.FixtureRequest
    :param fx_settings: Settings with in-memory persistence and a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    container = make_async_container(STORAGE_PROVIDERS[request.param](), context={Settings: fx_settings})
    yield container
    await container.close()


@pytest.fixture
async def fx_source_store(fx_storage: AsyncContainer) -> SourceStore:
    """Return the source store of the storage backend under test.

    :param fx_storage: Container holding the provider of one storage backend.
    :type fx_storage: AsyncContainer
    :returns: The source store the backend's provider builds.
    :rtype: SourceStore
    """
    return await fx_storage.get(SourceStore)


@pytest.fixture
async def fx_asset_store(fx_storage: AsyncContainer) -> AssetStore:
    """Return the asset store of the storage backend under test.

    :param fx_storage: Container holding the provider of one storage backend.
    :type fx_storage: AsyncContainer
    :returns: The asset store the backend's provider builds.
    :rtype: AssetStore
    """
    return await fx_storage.get(AssetStore)
