"""Fixtures of the API tests: the dependency container and the stored files of the running application.

The database is the root ``fx_database``, which the application runs on as well.
"""

from typing import TYPE_CHECKING

import pytest
from dishka import AsyncContainer

from bookreviver.ports.runtime import EventStream, JobBoard
from bookreviver.ports.storage import AssetStore, SourceStore
from tests.helpers.board_idle import JobsIdle
from tests.helpers.storage import BookFiles

if TYPE_CHECKING:
    from fastapi import FastAPI

    from bookreviver.app.settings import Settings


@pytest.fixture
def fx_container(fx_app: FastAPI) -> AsyncContainer:
    """Return the dependency container of the running application.

    :param fx_app: The running application, on whose state dishka keeps its container.
    :type fx_app: FastAPI
    :returns: The application's container, holding its adapters.
    :rtype: AsyncContainer
    """
    container = fx_app.state.dishka_container
    assert isinstance(container, AsyncContainer)
    return container


@pytest.fixture
async def fx_idle(fx_container: AsyncContainer) -> JobsIdle:
    """Return the wait that a test calls to let the jobs of a book on the application's job board finish.

    :param fx_container: Container of the running application.
    :type fx_container: AsyncContainer
    :returns: The wait, called with the identifier of the book.
    :rtype: JobsIdle
    """
    return JobsIdle(await fx_container.get(JobBoard), await fx_container.get(EventStream))


@pytest.fixture
async def fx_files(fx_container: AsyncContainer, fx_settings: Settings) -> BookFiles:
    """Return the files of imported books in the stores of the running application.

    :param fx_container: Container of the application, holding the local stores.
    :type fx_container: AsyncContainer
    :param fx_settings: Settings of the application, which name the storage root.
    :type fx_settings: Settings
    :returns: Files of the test's books, in the application's own stores.
    :rtype: BookFiles
    """
    return BookFiles(
        sources=await fx_container.get(SourceStore),
        assets=await fx_container.get(AssetStore),
        root=fx_settings.storage_root,
    )


@pytest.fixture
async def fx_asset_store(fx_container: AsyncContainer) -> AssetStore:
    """Return the asset store of the running application, to store the files that earlier requests would have.

    :param fx_container: Container of the application, holding the local stores.
    :type fx_container: AsyncContainer
    :returns: The store every request of the application reads derived files from.
    :rtype: AssetStore
    """
    return await fx_container.get(AssetStore)


@pytest.fixture
async def fx_source_store(fx_container: AsyncContainer) -> SourceStore:
    """Return the source store of the running application, to store the sources that earlier requests would have.

    :param fx_container: Container of the application, holding the local stores.
    :type fx_container: AsyncContainer
    :returns: The store the application keeps uploads and sources in.
    :rtype: SourceStore
    """
    return await fx_container.get(SourceStore)
