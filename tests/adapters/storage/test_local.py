"""Tests for what only the local storage adapters promise: the directory layout and the keys they accept."""

from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest

from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.domain.ids import ProjectId, StorageKey
from tests.adapters.storage.samples import WriterFailedError, abandon_write, upload

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.anyio

KEY_ARG: str = 'key'
PROJECT_ID: ProjectId = ProjectId(uuid4())
PAGE_NAME: str = 'page.png'
PAGE_CONTENT: bytes = b'page'
MAX_BYTES: int = 1024
FILE_KEY: StorageKey = StorageKey('projects/book/pages/0/v1/full.jpg')


class TestLocalSourceStore:
    """Tests for LocalSourceStore."""

    async def test_stages_into_project_incoming_directory(self, tmp_path: Path) -> None:
        """Verify an upload lands in ``projects/<id>/incoming/`` under the root, as the architecture lays out."""
        store = LocalSourceStore(root=tmp_path)

        await store.stage(PROJECT_ID, [upload(PAGE_NAME, PAGE_CONTENT)], max_bytes=MAX_BYTES)

        assert (tmp_path / 'projects' / str(PROJECT_ID) / 'incoming' / PAGE_NAME).read_bytes() == PAGE_CONTENT


class TestLocalAssetStore:
    """Tests for LocalAssetStore."""

    async def test_stores_key_as_path_under_root(self, tmp_path: Path) -> None:
        """Verify a key maps to the same relative path under the root."""
        store = LocalAssetStore(root=tmp_path)

        async with store.writable(FILE_KEY) as path:
            path.write_bytes(PAGE_CONTENT)

        assert (tmp_path / FILE_KEY).read_bytes() == PAGE_CONTENT

    async def test_failed_write_leaves_no_partial_file(self, tmp_path: Path) -> None:
        """Verify an abandoned write removes the hidden file it was writing."""
        store = LocalAssetStore(root=tmp_path)

        with pytest.raises(WriterFailedError):
            await abandon_write(store, FILE_KEY, PAGE_CONTENT)

        assert [path async for path in anyio.Path(tmp_path / FILE_KEY).parent.iterdir()] == []

    @pytest.mark.parametrize(KEY_ARG, ['', '/etc/passwd', '../outside', 'projects/../../outside'])
    async def test_rejects_key_outside_root(self, tmp_path: Path, key: str) -> None:
        """Reject a key that is empty, absolute or climbs out of the root, before touching any file."""
        store = LocalAssetStore(root=tmp_path / 'storage')

        with pytest.raises(ValueError, match='does not name a path inside the storage root'):
            await store.delete_prefix(StorageKey(key))
