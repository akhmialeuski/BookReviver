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
    """Yield a container holding only the imaging provider, configured by the test settings.

    :param fx_settings: Settings of the test, whose ``imaging`` group configures the adapters.
    :type fx_settings: Settings
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    container = make_async_container(ImagingProvider(), context={Settings: fx_settings})
    yield container
    await container.close()


@pytest.fixture
async def fx_inspector(fx_imaging: AsyncContainer) -> SourceInspector:
    """Return the application's source inspector.

    :param fx_imaging: Container holding only the imaging provider.
    :type fx_imaging: AsyncContainer
    :returns: The inspector the imaging provider builds.
    :rtype: SourceInspector
    """
    return await fx_imaging.get(SourceInspector)


@pytest.fixture
async def fx_rasterizer(fx_imaging: AsyncContainer) -> PageRasterizer:
    """Return the application's page rasterizer.

    :param fx_imaging: Container holding only the imaging provider.
    :type fx_imaging: AsyncContainer
    :returns: The rasterizer the imaging provider builds.
    :rtype: PageRasterizer
    """
    return await fx_imaging.get(PageRasterizer)


@pytest.fixture
async def fx_tiler(fx_imaging: AsyncContainer) -> Tiler:
    """Return the application's tiler.

    :param fx_imaging: Container holding only the imaging provider.
    :type fx_imaging: AsyncContainer
    :returns: The tiler the imaging provider builds.
    :rtype: Tiler
    """
    return await fx_imaging.get(Tiler)
