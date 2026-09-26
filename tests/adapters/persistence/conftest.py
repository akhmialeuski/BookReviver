"""Fixtures giving the SQLAlchemy adapter tests a fresh SQLite database with every table created."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.app.settings import Settings


@pytest.fixture
async def fx_database(fx_settings: Settings) -> AsyncIterator[SqlDatabase]:
    """Open the SQLite database the application would use for the test's data directory, with its schema."""
    fx_settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = SqlDatabase(fx_settings.resolved_database_url)
    await database.create_schema()
    yield database
    await database.dispose()
