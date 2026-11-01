"""Fixtures of the tests of the account providers: the container of the application, built without running it."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.app.container import build_container

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer

    from bookreviver.app.settings import Settings
    from tests.harness.adapters.database import SharedDatabaseProvider


@pytest.fixture
async def fx_container(
    fx_settings: Settings, fx_shared_database: SharedDatabaseProvider
) -> AsyncIterator[AsyncContainer]:
    """Yield the application's container on the catalogue and the books of the test.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :param fx_shared_database: The catalogue and the books of the test, which the container is given in place of its
                               own.
    :type fx_shared_database: SharedDatabaseProvider
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    container = build_container(fx_settings, [fx_shared_database])
    yield container
    await container.close()
