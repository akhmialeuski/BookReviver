"""Fixtures giving the port contract tests every adapter of the port, built the way the application builds it.

The persistence port has one adapter, so its contract tests run on the root ``fx_database`` and ``fx_uow``.
"""

from typing import TYPE_CHECKING

import pytest
from dishka import make_async_container

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.app.providers.storage import STORAGE_PROVIDERS
from bookreviver.app.settings import Settings
from bookreviver.ports.storage import AssetStore, SourceStore
from tests.helpers.book_gate import hold_book_in_thread
from tests.helpers.job_boards import BoardAdapter
from tests.helpers.seeding import commit_account
from tests.helpers.step_executors import StepExecutorAdapter
from tests.helpers.tracers import TracerAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from dishka import AsyncContainer

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.ordering import OrderKeys
    from tests.helpers.database import BookHolder

type OwnerFactory = Callable[[], Awaitable[AccountId]]

# Every adapter of the OrderKeys port by name; each computes keys in memory, so none needs a provider
ORDER_KEYS_ADAPTERS: dict[str, type[OrderKeys]] = {'fractional-indexing': FractionalOrderKeys}


@pytest.fixture
def fx_hold_book() -> BookHolder:
    """Return a function holding a book by a change whose work blocks the thread it runs in.

    :returns: Function returning a context manager that holds the book while its block runs.
    :rtype: BookHolder
    """
    return hold_book_in_thread


@pytest.fixture
def fx_new_owner(fx_database: SqlDatabase) -> OwnerFactory:
    """Return a function creating an account that may own projects.

    The database checks the owner of a project against the account table of fastapi-users, so an account is a
    committed row.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :returns: Function returning the identifier of a new account.
    :rtype: OwnerFactory
    """

    async def new_owner() -> AccountId:
        """Create an account in the database of the test.

        :returns: Identifier of the new account.
        :rtype: AccountId
        """
        return await commit_account(fx_database)

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


@pytest.fixture(params=list(BoardAdapter), ids=str)
def fx_board_adapter(request: pytest.FixtureRequest) -> BoardAdapter:
    """Return each adapter of the JobBoard port in turn.

    :param request: Request of the parametrized fixture, whose ``param`` is the adapter.
    :type request: pytest.FixtureRequest
    :returns: The adapter under test, set up by ``open_board_under_test``.
    :rtype: BoardAdapter
    """
    return BoardAdapter(request.param)


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
    :param fx_settings: Settings with a fresh data directory of the test.
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
