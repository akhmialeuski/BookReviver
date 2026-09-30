"""Fixtures giving the SQLAlchemy adapter tests a fresh SQLite database with every table created and an owner."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.container import build_container
from bookreviver.app.settings import PersistenceBackend
from tests.helpers.seeding import commit_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.app.settings import Settings
    from bookreviver.domain.ids import AccountId


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


@pytest.fixture
async def fx_owner_id(fx_database: SqlDatabase) -> AccountId:
    """Commit an account that may own projects, which the owner key of the projects table requires.

    :param fx_database: Fresh SQLite database with every table created.
    :type fx_database: SqlDatabase
    :returns: Identifier of the committed account.
    :rtype: AccountId
    """
    return await commit_account(fx_database)
