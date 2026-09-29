"""Fixtures giving the port contract tests every adapter of the port, built by the application's own providers."""

from contextlib import AsyncExitStack
from typing import TYPE_CHECKING

import pytest
from dishka import make_async_container

from bookreviver.app.providers.core import CoreProvider
from bookreviver.app.providers.database import DatabaseProvider
from bookreviver.app.providers.persistence import PERSISTENCE_PROVIDERS
from bookreviver.app.providers.storage import STORAGE_PROVIDERS
from bookreviver.app.settings import Settings
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.storage import AssetStore, SourceStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from dishka import AsyncContainer

type UnitOfWorkFactory = Callable[[], Awaitable[UnitOfWork]]


@pytest.fixture(params=list(PERSISTENCE_PROVIDERS), ids=str)
async def fx_uow_factory(request: pytest.FixtureRequest, fx_settings: Settings) -> AsyncIterator[UnitOfWorkFactory]:
    """Yield a function opening a new unit of work, built by the application's own provider of one backend.

    :param request: Request of the parametrized fixture, whose ``param`` is the persistence backend.
    :type request: pytest.FixtureRequest
    :param fx_settings: Settings with in-memory persistence and a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the opening function and closing the container afterwards.
    :rtype: AsyncIterator[UnitOfWorkFactory]
    """
    container = make_async_container(
        CoreProvider(),
        DatabaseProvider(),
        PERSISTENCE_PROVIDERS[request.param](),
        context={Settings: fx_settings},
    )
    async with AsyncExitStack() as scopes:

        async def open_unit_of_work() -> UnitOfWork:
            """Open a request scope of the container and return the unit of work it provides.

            :returns: Unit of work of a new request scope, closed with the fixture.
            :rtype: UnitOfWork
            """
            scope = await scopes.enter_async_context(container())
            return await scope.get(UnitOfWork)

        yield open_unit_of_work
    await container.close()


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
