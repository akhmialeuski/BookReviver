"""Settings of a test with a fresh data directory, its storage root, and the account the tests act as."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.app.settings import AuthSettings, Settings
from bookreviver.domain.accounts import Actor
from tests.harness.builders.domain import new_account_id

if TYPE_CHECKING:
    from pathlib import Path


TEST_SECRET: str = 'test-secret-that-is-long-enough-for-signing'


@pytest.fixture
def fx_settings(tmp_path: Path) -> Settings:
    """Build settings with a fresh data directory, whose SQLite file the test creates from the schema template.

    :param tmp_path: Temporary directory of the test, holding the data directory.
    :type tmp_path: Path
    :returns: Settings with a test secret, cookies allowed over plain HTTP and no built frontend, so a build on the
              developer's machine never changes what a test sees.
    :rtype: Settings
    """
    return Settings(
        data_dir=tmp_path / 'data',
        frontend_dir=tmp_path / 'no-frontend',
        auth=AuthSettings(secret=TEST_SECRET, cookie_secure=False),
    )


@pytest.fixture
def fx_storage_root(fx_settings: Settings) -> Path:
    """Return the storage root of the test, which holds the directory of every book as the application's does.

    :param fx_settings: Settings of the test, which name the storage root.
    :type fx_settings: Settings
    :returns: Directory the stores, the archive and the books of the test write under, created by the first write.
    :rtype: Path
    """
    return fx_settings.storage_root


@pytest.fixture
def fx_actor() -> Actor:
    """Build the account the API tests act as.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())
