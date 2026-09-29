"""Fixtures giving the SQLAlchemy adapter tests a fresh SQLite database with every table created."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.container import build_container
from bookreviver.app.settings import PersistenceBackend

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.app.settings import Settings


@pytest.fixture
async def fx_database(fx_settings: Settings) -> AsyncIterator[SqlDatabase]:
    """Open the database through the application's own container, built for the SQL backend.

    Building the whole container, not only the database provider, also checks that the production dependency graph
    with this backend resolves.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the open database and closing it with the container afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    container = build_container(fx_settings.model_copy(update={'persistence': PersistenceBackend.SQLALCHEMY}))
    yield await container.get(SqlDatabase)
    await container.close()
