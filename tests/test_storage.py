"""Tests for the project file storage."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.storage import ProjectStorage

if TYPE_CHECKING:
    from pathlib import Path

PROJECT_ID: int = 7
OLD_FILE: str = 'old.pdf'
NEW_FILE: str = 'new.pdf'
CACHED_FILE: str = 'preview-00000.webp'
OLD_BYTES: bytes = b'old book'
NEW_BYTES: bytes = b'new book'


@pytest.fixture
def fx_storage(tmp_path: Path) -> ProjectStorage:
    """Build a storage rooted in a fresh directory."""
    return ProjectStorage(root=tmp_path / 'projects')


@pytest.fixture
def fx_imported(fx_storage: ProjectStorage) -> ProjectStorage:
    """Give the project an imported source and one cached render."""
    source_dir = fx_storage.source_dir(PROJECT_ID)
    source_dir.mkdir(parents=True)
    (source_dir / OLD_FILE).write_bytes(OLD_BYTES)
    cache_dir = fx_storage.cache_dir(PROJECT_ID)
    cache_dir.mkdir()
    (cache_dir / CACHED_FILE).write_bytes(b'render')
    return fx_storage


@pytest.mark.anyio
class TestStartIncoming:
    """Tests for ProjectStorage.start_incoming()."""

    async def test_drops_files_left_by_an_interrupted_upload(self, fx_imported: ProjectStorage) -> None:
        """Verify a new upload starts empty and the current source is not touched."""
        leftover_dir = fx_imported.incoming_dir(PROJECT_ID)
        leftover_dir.mkdir()
        (leftover_dir / NEW_FILE).write_bytes(NEW_BYTES)
        incoming_dir = await fx_imported.start_incoming(PROJECT_ID)
        expect(list(incoming_dir.iterdir()) == [])
        expect((fx_imported.source_dir(PROJECT_ID) / OLD_FILE).read_bytes() == OLD_BYTES)
        assert_expectations()


@pytest.mark.anyio
class TestPromoteIncoming:
    """Tests for ProjectStorage.promote_incoming()."""

    async def test_replaces_source_and_drops_stale_cache(self, fx_imported: ProjectStorage) -> None:
        """Verify the incoming files become the source and renders of the old source are gone."""
        incoming_dir = await fx_imported.start_incoming(PROJECT_ID)
        (incoming_dir / NEW_FILE).write_bytes(NEW_BYTES)
        source_dir = await fx_imported.promote_incoming(PROJECT_ID)
        expect(sorted(path.name for path in source_dir.iterdir()) == [NEW_FILE])
        expect(not fx_imported.incoming_dir(PROJECT_ID).exists())
        expect(not fx_imported.cache_dir(PROJECT_ID).exists())
        assert_expectations()

    async def test_first_import_creates_source(self, fx_storage: ProjectStorage) -> None:
        """Verify promotion works when the project had no source yet."""
        incoming_dir = await fx_storage.start_incoming(PROJECT_ID)
        (incoming_dir / NEW_FILE).write_bytes(NEW_BYTES)
        source_dir = await fx_storage.promote_incoming(PROJECT_ID)
        assert (source_dir / NEW_FILE).read_bytes() == NEW_BYTES


@pytest.mark.anyio
class TestDiscardIncoming:
    """Tests for ProjectStorage.discard_incoming()."""

    async def test_keeps_current_source_and_cache(self, fx_imported: ProjectStorage) -> None:
        """Verify a rejected upload disappears while the imported book and its renders stay."""
        incoming_dir = await fx_imported.start_incoming(PROJECT_ID)
        (incoming_dir / NEW_FILE).write_bytes(NEW_BYTES)
        await fx_imported.discard_incoming(PROJECT_ID)
        expect(not incoming_dir.exists())
        expect((fx_imported.source_dir(PROJECT_ID) / OLD_FILE).read_bytes() == OLD_BYTES)
        expect((fx_imported.cache_dir(PROJECT_ID) / CACHED_FILE).exists())
        assert_expectations()


@pytest.mark.anyio
class TestDeleteProject:
    """Tests for ProjectStorage.delete_project()."""

    async def test_removes_every_file_of_the_project(self, fx_imported: ProjectStorage) -> None:
        """Verify deletion removes the project directory, and repeating it is harmless."""
        await fx_imported.delete_project(PROJECT_ID)
        await fx_imported.delete_project(PROJECT_ID)
        assert not fx_imported.project_dir(PROJECT_ID).exists()
