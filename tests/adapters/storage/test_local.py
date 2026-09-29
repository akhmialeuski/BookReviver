"""Tests for what only the local storage adapters promise: the directory layout and the keys they accept."""

from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest

from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.ids import ProjectId, StorageKey
from tests.helpers.storage import WriterFailedError, abandon_write, upload

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.anyio

KEY_ARG: str = 'key'
PROJECT_ID: ProjectId = ProjectId(uuid4())
PAGE_NAME: str = 'page.png'
PAGE_CONTENT: bytes = b'page'
MAX_BYTES: int = 1024
FILE_KEY: StorageKey = StorageKey('projects/book/pages/0/v1/full.jpg')
PROJECT_PAGE_KEY: StorageKey = StorageKey(f'projects/{PROJECT_ID}/pages/0/v0/full.jpg')
OLD_SOURCE_NAME: str = 'old.pdf'


class TestLocalSourceStore:
    """Tests for LocalSourceStore."""

    async def test_stages_into_project_incoming_directory(self, tmp_path: Path) -> None:
        """Verify an upload lands in ``projects/<id>/incoming/`` under the root, as the architecture lays out.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalSourceStore(root=tmp_path)

        await store.stage(PROJECT_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)

        assert (tmp_path / 'projects' / str(PROJECT_ID) / 'incoming' / PAGE_NAME).read_bytes() == PAGE_CONTENT

    async def test_promotion_never_replaces_source(self, tmp_path: Path) -> None:
        """Verify a staged upload is refused rather than moved over a source, however the two came to coexist.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalSourceStore(root=tmp_path)
        project_dir = tmp_path / 'projects' / str(PROJECT_ID)
        for area, name in (('source', OLD_SOURCE_NAME), ('incoming', PAGE_NAME)):
            (project_dir / area).mkdir(parents=True)
            (project_dir / area / name).write_bytes(PAGE_CONTENT)

        with pytest.raises(ConflictError):
            await store.promote(PROJECT_ID)

        assert [path.name for path in (project_dir / 'source').iterdir()] == [OLD_SOURCE_NAME]


class TestLocalAssetStore:
    """Tests for LocalAssetStore."""

    async def test_stores_key_as_path_under_root(self, tmp_path: Path) -> None:
        """Verify a key maps to the same relative path under the root.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalAssetStore(root=tmp_path)

        async with store.writable(FILE_KEY) as path:
            path.write_bytes(PAGE_CONTENT)

        assert (tmp_path / FILE_KEY).read_bytes() == PAGE_CONTENT

    async def test_failed_write_leaves_no_partial_file(self, tmp_path: Path) -> None:
        """Verify an abandoned write removes the hidden file it was writing.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalAssetStore(root=tmp_path)

        with pytest.raises(WriterFailedError):
            await abandon_write(store, key=FILE_KEY, content=PAGE_CONTENT)

        assert [path async for path in anyio.Path(tmp_path / FILE_KEY).parent.iterdir()] == []

    @pytest.mark.parametrize(KEY_ARG, ['', '/etc/passwd', '../outside', 'projects/../../outside'])
    async def test_rejects_key_outside_root(self, tmp_path: Path, key: str) -> None:
        """Reject a key that is empty, absolute or climbs out of the root, before touching any file.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param key: Storage key under test.
        :type key: str
        """
        store = LocalAssetStore(root=tmp_path / 'storage')

        with pytest.raises(ValueError, match='does not name a path inside the storage root'):
            await store.delete_prefix(StorageKey(key))

    async def test_project_deletion_keeps_source_directories(self, tmp_path: Path) -> None:
        """Verify ``source/`` and ``incoming/`` stay, since removing them is the source store's job.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalAssetStore(root=tmp_path)
        project_dir = tmp_path / 'projects' / str(PROJECT_ID)
        for area in ('source', 'incoming'):
            (project_dir / area).mkdir(parents=True)
            (project_dir / area / PAGE_NAME).write_bytes(PAGE_CONTENT)
        async with store.writable(PROJECT_PAGE_KEY) as path:
            path.write_bytes(PAGE_CONTENT)

        await store.delete_project(PROJECT_ID)

        assert sorted(path.name for path in project_dir.iterdir()) == ['incoming', 'source']

    async def test_both_stores_leave_no_project_directory(self, tmp_path: Path) -> None:
        """Verify deleting a project from the source store and then the asset store leaves nothing of it on disk.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sources = LocalSourceStore(root=tmp_path)
        assets = LocalAssetStore(root=tmp_path)
        await sources.stage(PROJECT_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)
        await sources.promote(PROJECT_ID)
        async with assets.writable(PROJECT_PAGE_KEY) as path:
            path.write_bytes(PAGE_CONTENT)

        await sources.delete_project(PROJECT_ID)
        await assets.delete_project(PROJECT_ID)

        assert list((tmp_path / 'projects').iterdir()) == []
