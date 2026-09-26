"""Fixtures of the API tests: the application runs against storage fakes, as API tests use in-memory adapters."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.memory import InMemoryDatabase
from tests.helpers.fakes_projects import FakeAssetStore, FakeSourceStore, FakeStorageProvider

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from dishka import Provider
    from fastapi import FastAPI


@pytest.fixture
def fx_source_store() -> FakeSourceStore:
    """Build the source store the application uses."""
    return FakeSourceStore()


@pytest.fixture
def fx_asset_store(tmp_path: Path) -> FakeAssetStore:
    """Build the asset store the application uses, over a fresh directory."""
    return FakeAssetStore(tmp_path / 'assets')


@pytest.fixture
def fx_extra_providers(fx_source_store: FakeSourceStore, fx_asset_store: FakeAssetStore) -> Sequence[Provider]:
    """Replace the storage adapters with the fakes above."""
    return (FakeStorageProvider(fx_source_store, fx_asset_store),)


@pytest.fixture
async def fx_database(fx_app: FastAPI) -> InMemoryDatabase:
    """Return the in-memory database of the running application, to store what earlier requests would have."""
    database = await fx_app.state.dishka_container.get(InMemoryDatabase)
    assert isinstance(database, InMemoryDatabase)
    return database
