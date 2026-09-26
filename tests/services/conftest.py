"""Fixtures of the service tests: in-memory persistence and storage fakes."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.memory import InMemoryDatabase
from tests.helpers.fakes_projects import FakeAssetStore, FakeSourceStore

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def fx_database() -> InMemoryDatabase:
    """Build an empty in-memory database."""
    return InMemoryDatabase()


@pytest.fixture
def fx_sources() -> FakeSourceStore:
    """Build a source store that records deletions."""
    return FakeSourceStore()


@pytest.fixture
def fx_assets(tmp_path: Path) -> FakeAssetStore:
    """Build an asset store over a fresh directory."""
    return FakeAssetStore(tmp_path)
