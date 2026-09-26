"""Fixtures giving the storage contract tests each storage adapter, built by the application's own provider."""

from typing import TYPE_CHECKING

import pytest
from dishka import make_async_container

from bookreviver.app.providers.storage import StorageProvider
from bookreviver.app.settings import Settings
from bookreviver.ports.storage import AssetStore, SourceStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer


# A future S3 provider joins this list and runs the same contract
@pytest.fixture(params=[StorageProvider], ids=lambda provider: provider.__name__)
async def fx_storage(request: pytest.FixtureRequest, fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
    """Yield a container holding one storage provider, rooted in the test's data directory."""
    container = make_async_container(request.param(), context={Settings: fx_settings})
    yield container
    await container.close()


@pytest.fixture
async def fx_source_store(fx_storage: AsyncContainer) -> SourceStore:
    """Return the source store of the storage provider under test."""
    return await fx_storage.get(SourceStore)


@pytest.fixture
async def fx_asset_store(fx_storage: AsyncContainer) -> AssetStore:
    """Return the asset store of the storage provider under test."""
    return await fx_storage.get(AssetStore)
