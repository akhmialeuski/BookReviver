"""Tests for what only the local storage adapters promise: the directory layout of the uploads, sources and assets."""

from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest

from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.domain.enums import Rendition
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.ids import JobId, PageId, ProjectId, SourceId
from bookreviver.domain.keys import ProjectKeys
from tests.helpers.builders import make_page_version
from tests.helpers.storage import WriterFailedError, abandon_write, upload

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.ids import StorageKey

pytestmark = pytest.mark.anyio

STORE_TYPES_ARG: str = 'store_types'
PROJECT_ID: ProjectId = ProjectId(uuid4())
KEYS: ProjectKeys = ProjectKeys(PROJECT_ID)
JOB_ID: JobId = JobId(uuid4())
SOURCE_ID: SourceId = SourceId(uuid4())
PAGE_NAME: str = 'page.png'
PAGE_CONTENT: bytes = b'page'
MAX_BYTES: int = 1024
FILE_KEY: StorageKey = KEYS.version_rendition(make_page_version(page_id=PageId(uuid4())), Rendition.FULL_JPEG)


class TestLocalSourceStore:
    """Tests for LocalSourceStore."""

    async def test_stages_into_the_jobs_incoming_directory(self, tmp_path: Path) -> None:
        """Verify an upload lands in ``projects/<id>/incoming/<job_id>/`` under the root, as the architecture lays out.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalSourceStore(root=tmp_path)

        await store.stage(PROJECT_ID, JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)

        assert (tmp_path / 'projects' / str(PROJECT_ID) / 'incoming' / str(JOB_ID) / PAGE_NAME).read_bytes() == (
            PAGE_CONTENT
        )

    async def test_promotes_into_the_sources_own_directory(self, tmp_path: Path) -> None:
        """Verify a source lands in ``projects/<id>/sources/<source_id>/``, with nothing else left in ``sources/``.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalSourceStore(root=tmp_path)
        await store.stage(PROJECT_ID, JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)

        await store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[PAGE_NAME])

        sources_dir = tmp_path / 'projects' / str(PROJECT_ID) / 'sources'
        assert [path.name for path in sources_dir.iterdir()] == [str(SOURCE_ID)]
        assert (sources_dir / str(SOURCE_ID) / PAGE_NAME).read_bytes() == PAGE_CONTENT

    async def test_refused_promotion_leaves_no_hidden_directory(self, tmp_path: Path) -> None:
        """Verify a promotion onto an existing source removes the directory it gathered the files in.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalSourceStore(root=tmp_path)
        await store.stage(PROJECT_ID, JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)
        sources_dir = tmp_path / KEYS.sources_area
        (sources_dir / str(SOURCE_ID)).mkdir(parents=True)
        (sources_dir / str(SOURCE_ID) / PAGE_NAME).write_bytes(PAGE_CONTENT)

        with pytest.raises(ConflictError):
            await store.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[PAGE_NAME])

        assert [path.name for path in sources_dir.iterdir()] == [str(SOURCE_ID)]


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

    async def test_project_deletion_keeps_uploads_and_sources(self, tmp_path: Path) -> None:
        """Verify ``incoming/`` and ``sources/`` stay, since removing them is the source store's job.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sources = LocalSourceStore(root=tmp_path)
        assets = LocalAssetStore(root=tmp_path)
        await sources.stage(PROJECT_ID, JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)
        await sources.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[PAGE_NAME])
        await sources.stage(PROJECT_ID, JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)
        async with assets.writable(FILE_KEY) as path:
            path.write_bytes(PAGE_CONTENT)

        await assets.delete_project(PROJECT_ID)

        project_dir = tmp_path / 'projects' / str(PROJECT_ID)
        assert sorted(path.name for path in project_dir.iterdir()) == ['incoming', 'sources']

    @pytest.mark.parametrize(
        STORE_TYPES_ARG,
        [(LocalSourceStore, LocalAssetStore), (LocalAssetStore, LocalSourceStore)],
        ids=['sources-first', 'assets-first'],
    )
    async def test_both_stores_leave_no_project_directory(
        self, tmp_path: Path, store_types: tuple[type[LocalSourceStore | LocalAssetStore], ...]
    ) -> None:
        """Verify deleting a project from both stores, in either order, leaves nothing of it on disk.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param store_types: The two local stores in the order they delete the project.
        :type store_types: tuple[type[LocalSourceStore | LocalAssetStore], ...]
        """
        sources = LocalSourceStore(root=tmp_path)
        assets = LocalAssetStore(root=tmp_path)
        await sources.stage(PROJECT_ID, JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES)
        await sources.promote(PROJECT_ID, JOB_ID, SOURCE_ID, names=[PAGE_NAME])
        async with assets.writable(FILE_KEY) as path:
            path.write_bytes(PAGE_CONTENT)

        for store_type in store_types:
            await store_type(root=tmp_path).delete_project(PROJECT_ID)

        assert list((tmp_path / 'projects').iterdir()) == []
