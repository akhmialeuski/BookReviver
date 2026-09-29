"""Uploads as the API receives them, writers that give up half-way, and the files of imported books."""

import io
from typing import TYPE_CHECKING

from fastapi import UploadFile

from bookreviver.domain.enums import PageAsset

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import ProjectId, StorageKey
    from bookreviver.ports.storage import AssetStore, SourceStore

# More than one read chunk of the local store, so staging has to stream
LARGE_CONTENT: bytes = bytes(range(256)) * 10_240
SOURCE_NAME: str = 'book.pdf'
MAX_SOURCE_BYTES: int = 1024
PAGE_IMAGE_CONTENT: bytes = b'jpeg'


class WriterFailedError(Exception):
    """Raised by a test writer to abandon a write half-way."""


def upload(name: str | None, *, content: bytes = b'page') -> UploadFile:
    """Return an in-memory upload named as a browser would send it.

    :param name: File name as the browser sends it, possibly with a client-side path, or None for no name.
    :type name: str | None
    :param content: Bytes of the uploaded file.
    :type content: bytes
    :returns: FastAPI's upload, the type the API hands to the source store.
    :rtype: UploadFile
    """
    return UploadFile(file=io.BytesIO(content), filename=name)


async def abandon_write(store: AssetStore, *, key: StorageKey, content: bytes) -> None:
    """Start writing ``content`` at ``key`` and fail before the write completes.

    :param store: Asset store to write into.
    :type store: AssetStore
    :param key: Key the abandoned write targets.
    :type key: StorageKey
    :param content: Bytes written before the writer fails.
    :type content: bytes
    :raises WriterFailedError: Always, from inside the write.
    """
    async with store.writable(key) as path:
        path.write_bytes(content)
        raise WriterFailedError


class BookFiles:
    """The source and a page image of imported books in the two stores, and what is left of them on disk.

    The disk checks read the layout of the local stores, which are the test adapters of the storage ports.
    """

    def __init__(self, *, sources: SourceStore, assets: AssetStore, root: Path) -> None:
        """Work on the stores under test.

        :param sources: Source store receiving the uploads.
        :type sources: SourceStore
        :param assets: Asset store receiving the page images.
        :type assets: AssetStore
        :param root: Local storage root both stores share.
        :type root: Path
        """
        self._sources = sources
        self._assets = assets
        self._root = root

    async def store(self, page: Page) -> None:
        """Store a source for the page's project and the page's full image, as an import would.

        :param page: Page whose project and image are stored.
        :type page: Page
        """
        await self._sources.stage(page.project_id, [upload(SOURCE_NAME)], max_bytes=MAX_SOURCE_BYTES)
        await self._sources.promote(page.project_id)
        async with self._assets.writable(page.asset_key(PageAsset.FULL)) as path:
            path.write_bytes(PAGE_IMAGE_CONTENT)

    def kept(self, page: Page) -> bool:
        """Return whether the source of the page's project and the page's full image are still on disk.

        :param page: Page stored earlier with ``store``.
        :type page: Page
        :returns: True when both files are there with their content.
        :rtype: bool
        """
        source = self._project_dir(page.project_id) / 'source' / SOURCE_NAME
        image = self._root / page.asset_key(PageAsset.FULL)
        return source.is_file() and image.read_bytes() == PAGE_IMAGE_CONTENT

    def gone(self, project_id: ProjectId) -> bool:
        """Return whether nothing of the project, not even its directory, is left on disk.

        :param project_id: Project whose files were deleted.
        :type project_id: ProjectId
        :returns: True when the project's directory does not exist.
        :rtype: bool
        """
        return not self._project_dir(project_id).exists()

    def _project_dir(self, project_id: ProjectId) -> Path:
        """Return the directory of the project under the storage root.

        :param project_id: Project owning the directory.
        :type project_id: ProjectId
        :returns: Path of ``projects/<id>`` under the root.
        :rtype: Path
        """
        return self._root / 'projects' / str(project_id)
