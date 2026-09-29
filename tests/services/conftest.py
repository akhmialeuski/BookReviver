"""Fixtures of the service tests: in-memory persistence and the local stores over a temporary directory."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.memory import InMemoryDatabase
from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def fx_database() -> InMemoryDatabase:
    """Build an empty in-memory database.

    :returns: Database shared by every unit of work of the test.
    :rtype: InMemoryDatabase
    """
    return InMemoryDatabase()


@pytest.fixture
def fx_storage_root(tmp_path: Path) -> Path:
    """Return the storage root both stores of the test share.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: Directory under the temporary directory, created by the first write.
    :rtype: Path
    """
    return tmp_path / 'storage'


@pytest.fixture
def fx_source_store(fx_storage_root: Path) -> LocalSourceStore:
    """Build the source store over the test's storage root.

    :param fx_storage_root: Storage root shared with the asset store.
    :type fx_storage_root: Path
    :returns: Local source store, the test adapter of the port.
    :rtype: LocalSourceStore
    """
    return LocalSourceStore(root=fx_storage_root)


@pytest.fixture
def fx_asset_store(fx_storage_root: Path) -> LocalAssetStore:
    """Build the asset store over the test's storage root.

    :param fx_storage_root: Storage root shared with the source store.
    :type fx_storage_root: Path
    :returns: Local asset store, the test adapter of the port.
    :rtype: LocalAssetStore
    """
    return LocalAssetStore(root=fx_storage_root)
