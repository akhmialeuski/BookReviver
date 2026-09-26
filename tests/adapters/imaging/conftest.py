"""Fixtures giving the imaging tests the adapters exactly as the application's provider builds them."""

from typing import TYPE_CHECKING

import pytest
from dishka import make_async_container

from bookreviver.app.providers.imaging import ImagingProvider
from bookreviver.app.settings import Settings
from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer


@pytest.fixture
async def fx_imaging(fx_settings: Settings) -> AsyncIterator[AsyncContainer]:
    """Yield a container holding only the imaging provider, configured by the test settings."""
    container = make_async_container(ImagingProvider(), context={Settings: fx_settings})
    yield container
    await container.close()


@pytest.fixture
async def fx_inspector(fx_imaging: AsyncContainer) -> SourceInspector:
    """Return the application's source inspector."""
    return await fx_imaging.get(SourceInspector)


@pytest.fixture
async def fx_rasterizer(fx_imaging: AsyncContainer) -> PageRasterizer:
    """Return the application's page rasterizer."""
    return await fx_imaging.get(PageRasterizer)


@pytest.fixture
async def fx_tiler(fx_imaging: AsyncContainer) -> Tiler:
    """Return the application's tiler."""
    return await fx_imaging.get(Tiler)
