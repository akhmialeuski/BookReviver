"""Fixtures giving the SQLAlchemy adapter tests a fresh SQLite database with every table created."""

from typing import TYPE_CHECKING

import pytest
from dishka import make_async_container

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.providers.core import CoreProvider
from bookreviver.app.providers.database import DatabaseProvider
from bookreviver.app.settings import Settings

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
async def fx_database(fx_settings: Settings) -> AsyncIterator[SqlDatabase]:
    """Open the database through the application's own provider, for the test's data directory.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the open database and closing it with the container afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    container = make_async_container(CoreProvider(), DatabaseProvider(), context={Settings: fx_settings})
    yield await container.get(SqlDatabase)
    await container.close()
