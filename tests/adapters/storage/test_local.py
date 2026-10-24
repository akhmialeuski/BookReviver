"""Tests for what only the local storage adapters promise: the directory layout of the uploads, sources and assets."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalAssetStore, LocalBookDirectories, local
from bookreviver.domain.enums import Rendition
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.ids import BookId, JobId, PageId, SourceId
from bookreviver.domain.keys import BookKeys
from tests.helpers.builders import directory_of, make_page_version
from tests.helpers.storage import WriterFailedError, abandon_write, upload

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.ids import StorageKey

pytestmark = pytest.mark.anyio

BOOK_ID: BookId = BookId(uuid4())
OTHER_BOOK_ID: BookId = BookId(uuid4())
KEYS: BookKeys = BookKeys()
# The directory of the book under the storage root, as the local stores lay it out
BOOK_DIRECTORY: str = f'projects/{BOOK_ID}'
JOB_ID: JobId = JobId(uuid4())
SOURCE_ID: SourceId = SourceId(uuid4())
PAGE_NAME: str = 'page.png'
PAGE_CONTENT: bytes = b'page'
SOURCE_TAKEN: str = 'Another source was published under this identifier first.'
MAX_BYTES: int = 1024
FILE_KEY: StorageKey = KEYS.version_rendition(make_page_version(page_id=PageId(uuid4())), Rendition.FULL_JPEG)


class TestLocalSourceStore:
    """Tests for LocalSourceStore."""

    async def test_stages_into_the_jobs_incoming_directory(self, tmp_path: Path) -> None:
        """Verify an upload lands in ``projects/<id>/incoming/<job_id>/`` under the root, as the architecture lays out.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        stores = LocalBookDirectories(root=tmp_path)

        await stores.directory(directory_of(BOOK_ID)).sources.stage(
            JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES
        )

        assert (tmp_path / 'projects' / str(BOOK_ID) / 'incoming' / str(JOB_ID) / PAGE_NAME).read_bytes() == (
            PAGE_CONTENT
        )

    async def test_promotes_into_the_sources_own_directory(self, tmp_path: Path) -> None:
        """Verify a source lands in ``projects/<id>/sources/<source_id>/``, with nothing else left in ``sources/``.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        stores = LocalBookDirectories(root=tmp_path)
        await stores.directory(directory_of(BOOK_ID)).sources.stage(
            JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES
        )

        await stores.directory(directory_of(BOOK_ID)).sources.promote(JOB_ID, SOURCE_ID, names=[PAGE_NAME])

        sources_dir = tmp_path / 'projects' / str(BOOK_ID) / 'sources'
        assert [path.name for path in sources_dir.iterdir()] == [str(SOURCE_ID)]
        assert (sources_dir / str(SOURCE_ID) / PAGE_NAME).read_bytes() == PAGE_CONTENT

    async def test_refused_promotion_leaves_no_hidden_directory(self, tmp_path: Path) -> None:
        """Verify a promotion onto an existing source removes the directory it gathered the files in.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        stores = LocalBookDirectories(root=tmp_path)
        await stores.directory(directory_of(BOOK_ID)).sources.stage(
            JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES
        )
        sources_dir = tmp_path / BOOK_DIRECTORY / KEYS.sources_area
        (sources_dir / str(SOURCE_ID)).mkdir(parents=True)
        (sources_dir / str(SOURCE_ID) / PAGE_NAME).write_bytes(PAGE_CONTENT)

        with pytest.raises(ConflictError):
            await stores.directory(directory_of(BOOK_ID)).sources.promote(JOB_ID, SOURCE_ID, names=[PAGE_NAME])

        assert [path.name for path in sources_dir.iterdir()] == [str(SOURCE_ID)]

    async def test_promotion_refused_at_publication_moves_the_files_back(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a source that appears between the check and the publication leaves the upload staged as it was.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param monkeypatch: Replaces the publication with one that finds the source taken.
        :type monkeypatch: pytest.MonkeyPatch
        """
        monkeypatch.setattr(local, '_publish', AsyncMock(side_effect=ConflictError(SOURCE_TAKEN)))
        stores = LocalBookDirectories(root=tmp_path)
        await stores.directory(directory_of(BOOK_ID)).sources.stage(
            JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES
        )

        with pytest.raises(ConflictError):
            await stores.directory(directory_of(BOOK_ID)).sources.promote(JOB_ID, SOURCE_ID, names=[PAGE_NAME])

        expect((tmp_path / BOOK_DIRECTORY / KEYS.incoming(JOB_ID) / PAGE_NAME).read_bytes() == PAGE_CONTENT)
        expect(list((tmp_path / BOOK_DIRECTORY / KEYS.sources_area).iterdir()) == [])
        assert_expectations()

    async def test_discarding_all_staged_removes_every_upload_and_keeps_the_sources(self, tmp_path: Path) -> None:
        """Verify no book keeps an ``incoming/`` directory, and a book with a source keeps its sources.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        stores = LocalBookDirectories(root=tmp_path)
        other_sources_dir = tmp_path / 'projects' / str(OTHER_BOOK_ID) / BookKeys().sources_area
        await stores.directory(directory_of(BOOK_ID)).sources.stage(
            JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES
        )
        await stores.directory(directory_of(OTHER_BOOK_ID)).sources.stage(
            JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES
        )
        await stores.directory(directory_of(OTHER_BOOK_ID)).sources.promote(JOB_ID, SOURCE_ID, names=[PAGE_NAME])
        await stores.directory(directory_of(OTHER_BOOK_ID)).sources.stage(
            JOB_ID, [upload(PAGE_NAME, content=PAGE_CONTENT)], max_bytes=MAX_BYTES
        )

        await stores.discard_all_staged()

        expect(not (tmp_path / BOOK_DIRECTORY / KEYS.incoming_area).exists())
        expect([path.name for path in other_sources_dir.parent.iterdir()] == [other_sources_dir.name])
        assert_expectations()


class TestLocalAssetStore:
    """Tests for LocalAssetStore."""

    async def test_stores_key_as_path_under_root(self, tmp_path: Path) -> None:
        """Verify a key maps to the same relative path under the root.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalAssetStore(root=tmp_path, name=directory_of(BOOK_ID))

        async with store.writable(FILE_KEY) as path:
            path.write_bytes(PAGE_CONTENT)

        assert (tmp_path / BOOK_DIRECTORY / FILE_KEY).read_bytes() == PAGE_CONTENT

    async def test_failed_write_leaves_no_partial_file(self, tmp_path: Path) -> None:
        """Verify an abandoned write removes the hidden file it was writing.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        store = LocalAssetStore(root=tmp_path, name=directory_of(BOOK_ID))

        with pytest.raises(WriterFailedError):
            await abandon_write(store, key=FILE_KEY, content=PAGE_CONTENT)

        assert [path async for path in anyio.Path(tmp_path / BOOK_DIRECTORY / FILE_KEY).parent.iterdir()] == []
