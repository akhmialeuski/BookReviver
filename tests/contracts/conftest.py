"""Fixtures giving the port contract tests every adapter of the port, built by the application's own providers."""

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

import pytest
from dishka import make_async_container

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.providers.core import CoreProvider
from bookreviver.app.providers.database import DatabaseProvider
from bookreviver.app.providers.persistence import PERSISTENCE_PROVIDERS
from bookreviver.app.providers.storage import STORAGE_PROVIDERS
from bookreviver.app.settings import PersistenceBackend, Settings
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.storage import AssetStore, SourceStore
from tests.helpers.builders import new_account_id
from tests.helpers.job_queues import QueueAdapter
from tests.helpers.seeding import commit_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from dishka import AsyncContainer

    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.ordering import OrderKeys

type UnitOfWorkFactory = Callable[[], Awaitable[UnitOfWork]]
type OwnerFactory = Callable[[], Awaitable[AccountId]]

# Every adapter of the OrderKeys port by name; each computes keys in memory, so none needs a provider
ORDER_KEYS_ADAPTERS: dict[str, type[OrderKeys]] = {'fractional-indexing': FractionalOrderKeys}


@pytest.fixture(params=list(PERSISTENCE_PROVIDERS), ids=str)
def fx_persistence_backend(request: pytest.FixtureRequest) -> PersistenceBackend:
    """Return each persistence backend in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the persistence backend.
    :type request: pytest.FixtureRequest
    :returns: The backend under test.
    :rtype: PersistenceBackend
    """
    return PersistenceBackend(request.param)


@pytest.fixture
async def fx_persistence(
    fx_persistence_backend: PersistenceBackend, fx_settings: Settings
) -> AsyncIterator[AsyncContainer]:
    """Yield a container holding the application's own provider of the backend under test.

    :param fx_persistence_backend: Persistence backend under test.
    :type fx_persistence_backend: PersistenceBackend
    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    container = make_async_container(
        CoreProvider(),
        DatabaseProvider(),
        PERSISTENCE_PROVIDERS[fx_persistence_backend](),
        context={Settings: fx_settings},
    )
    yield container
    await container.close()


@pytest.fixture
async def fx_uow_factory(fx_persistence: AsyncContainer) -> AsyncIterator[UnitOfWorkFactory]:
    """Yield a function opening a new unit of work of the backend under test.

    :param fx_persistence: Container holding the provider of the backend under test.
    :type fx_persistence: AsyncContainer
    :returns: Iterator yielding the opening function and closing every scope it opened afterwards.
    :rtype: AsyncIterator[UnitOfWorkFactory]
    """
    async with AsyncExitStack() as scopes:

        async def open_unit_of_work() -> UnitOfWork:
            """Open a request scope of the container and return the unit of work it provides.

            :returns: Unit of work of a new request scope, closed with the fixture.
            :rtype: UnitOfWork
            """
            scope = await scopes.enter_async_context(fx_persistence())
            return await scope.get(UnitOfWork)

        yield open_unit_of_work


@pytest.fixture
def fx_new_owner(fx_persistence_backend: PersistenceBackend, fx_persistence: AsyncContainer) -> OwnerFactory:
    """Return a function creating an account that may own projects in the backend under test.

    Only the SQL backend checks the owner of a project, against the account table of fastapi-users, so an account is
    a committed row there. The in-memory backend has no accounts, since no port stores them, and takes any identifier.

    :param fx_persistence_backend: Persistence backend under test.
    :type fx_persistence_backend: PersistenceBackend
    :param fx_persistence: Container holding the provider of the backend under test, and the SQL database.
    :type fx_persistence: AsyncContainer
    :returns: Function returning the identifier of a new account.
    :rtype: OwnerFactory
    """

    async def new_owner() -> AccountId:
        """Create an account in the backend under test.

        :returns: Identifier of the new account.
        :rtype: AccountId
        """
        match fx_persistence_backend:
            case PersistenceBackend.SQLALCHEMY:
                return await commit_account(await fx_persistence.get(SqlDatabase))
            case PersistenceBackend.MEMORY:
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
