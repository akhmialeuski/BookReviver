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
    """Yield a function opening a new unit of work, built by the application's own provider of one backend."""
    container = make_async_container(
        CoreProvider(),
        DatabaseProvider(),
        PERSISTENCE_PROVIDERS[request.param](),
        context={Settings: fx_settings},
    )
    async with AsyncExitStack() as scopes:

        async def open_unit_of_work() -> UnitOfWork:
            scope = await scopes.enter_async_context(container())
            return await scope.get(UnitOfWork)

        yield open_unit_of_work
    await container.close()


@pytest.fixture(params=list(STORAGE_PROVIDERS), ids=str)
async def fx_storage(request: pytest.FixtureRequest, fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
    """Yield a container holding the provider of one storage backend, rooted in the test's data directory."""
    container = make_async_container(STORAGE_PROVIDERS[request.param](), context={Settings: fx_settings})
    yield container
    await container.close()


@pytest.fixture
async def fx_source_store(fx_storage: AsyncContainer) -> SourceStore:
    """Return the source store of the storage backend under test."""
    return await fx_storage.get(SourceStore)


@pytest.fixture
async def fx_asset_store(fx_storage: AsyncContainer) -> AssetStore:
    """Return the asset store of the storage backend under test."""
    return await fx_storage.get(AssetStore)
