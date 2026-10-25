"""Uploads as the API receives them, writers that give up half-way, and the files of imported books."""

import io
from typing import TYPE_CHECKING, override
from uuid import uuid4

from fastapi import UploadFile

from bookreviver.adapters.storage import LocalBookDirectory, LocalSourceStore
from bookreviver.domain.enums import Rendition
from bookreviver.domain.ids import JobId, SourceId
from bookreviver.domain.keys import BookKeys, KeySegment
from bookreviver.ports.storage import BookDirectory
from tests.helpers.builders import directory_of, make_page_version

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Page
    from bookreviver.domain.ids import BookDirectoryName, BookId, PageId, StorageKey
    from bookreviver.ports.storage import AssetStore

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


def book_directory(root: Path, book_id: BookId) -> Path:
    """Return the directory of a book under a storage root, as the local stores lay it out, for a test that reads the disk.

    :param root: The storage root.
    :type root: Path
    :param book_id: The book.
    :type book_id: BookId
    :returns: The path of ``projects/<book_id>``, under which the keys of the book name its files.
    :rtype: Path
    """
    return root / KeySegment.BOOKS / str(book_id)


class SharedDirectory(LocalBookDirectory):
    """The local directory of one book, whose source store is one a test chose."""

    def __init__(self, *, root: Path, name: BookDirectoryName, sources: LocalSourceStore) -> None:
        """Keep the directory of one book under ``root``, with the given source store.

        :param root: Storage root of the directories of the books.
        :type root: Path
        :param name: Name of the directory of the book.
        :type name: BookDirectoryName
        :param sources: The source store of the book, such as one that fails or waits where a test asks.
        :type sources: LocalSourceStore
        """
        super().__init__(root=root, name=name)
        self._sources = sources

    @property
    @override
    def sources(self) -> LocalSourceStore:
        """The source store of the book the test chose."""
        return self._sources

    @property
    @override
    def assets(self) -> LocalAssetStore:
        """The local asset store of the book."""
        return self._assets


class SharedBookDirectories:
    """The opener of the local directories of the books of a test, which hands out one directory per book.

    A book opens its directory again each time it is opened, and the local directories keep no state, so handing out
    the same objects changes nothing but lets a test patch the store the book reaches. A test registers ``open`` as the
    way the books open their directory. ``sources`` and ``assets`` reach the stores of a book in one call, as a test
    seeds and reads them.
    """

    def __init__(
        self,
        *,
        root: Path,
        source_store: type[LocalSourceStore] = LocalSourceStore,
        directory_type: type[SharedDirectory] = SharedDirectory,
    ) -> None:
        """Keep the directories of the books under ``root``, with no directory handed out yet.

        :param root: Storage root of the directories.
        :type root: Path
        :param source_store: Class of the source store of one book, such as one that fails or waits where a test asks.
        :type source_store: type[LocalSourceStore]
        :param directory_type: Class of the directory of one book, such as one whose deletion fails or waits.
        :type directory_type: type[SharedDirectory]
        """
        self._files_root = root
        self._source_store = source_store
        self._directory_type = directory_type
        self._directories: dict[BookDirectoryName, SharedDirectory] = {}

    def open(self, name: BookDirectoryName) -> SharedDirectory:
        """Return the one directory of a book.

        :param name: Name of the directory of the book.
        :type name: BookDirectoryName
        :returns: The directory of the book, the same one on every call.
        :rtype: SharedDirectory
        """
        if name not in self._directories:
            self._directories[name] = self._directory_type(
                root=self._files_root, name=name, sources=self._new_sources(name)
            )
        return self._directories[name]

    def sources(self, book_id: BookId) -> LocalSourceStore:
        """Return the one source store of a book.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The source store of the directory of the book, which the test names by the book.
        :rtype: LocalSourceStore
        """
        return self.open(directory_of(book_id)).sources

    def assets(self, book_id: BookId) -> LocalAssetStore:
        """Return the one asset store of a book.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The asset store of the directory of the book, which the test names by the book.
        :rtype: LocalAssetStore
        """
        return self.open(directory_of(book_id)).assets

    def _new_sources(self, name: BookDirectoryName) -> LocalSourceStore:
        """Build the source store of a book the first time its directory is handed out.

        :param name: Name of the directory of the book.
        :type name: BookDirectoryName
        :returns: A source store of the class the test chose.
        :rtype: LocalSourceStore
        """
        return self._source_store(root=self._files_root, name=name)


class BookFiles:
    """The source and a page image of imported books in the two stores, and what is left of them on disk.

    The disk checks read the layout of the local stores, which are the test adapters of the storage ports.
    """

    def __init__(self, *, root: Path) -> None:
        """Work on the stores of the directories the books of the test open.

        :param root: Local storage root both stores share.
        :type root: Path
        """
        self._root = root
        self._stored: dict[PageId, tuple[BookId, StorageKey, StorageKey]] = {}

    async def store(self, book_id: BookId, page: Page) -> None:
        """Store a source for the page's book and the full image of the page's base version, as an import would.

        :param book_id: Identifier of the book of the page.

        :type book_id: BookId

        :param page: Page whose book and image are stored.
        :type page: Page
        """
        keys = BookKeys()
        job_id, source_id = JobId(uuid4()), SourceId(uuid4())
        sources = BookDirectory.open(directory_of(book_id)).sources
        staged = await sources.stage(job_id, [upload(SOURCE_NAME)], max_bytes=MAX_SOURCE_BYTES)
        await sources.promote(job_id, source_id, names=[file.name for file in staged])
        image = keys.version_rendition(make_page_version(page_id=page.id), Rendition.FULL_JPEG)
        async with BookDirectory.open(directory_of(book_id)).assets.writable(image) as path:
            path.write_bytes(PAGE_IMAGE_CONTENT)
        self._stored[page.id] = (book_id, keys.source(source_id), image)

    def kept(self, page: Page, *, book_id: BookId | None = None) -> bool:
        """Return whether the source stored for the page's book and the page's full image are still on disk.

        :param page: Page stored earlier with ``store``.
        :type page: Page
        :param book_id: Another book that holds the same files under its own directory, such as an imported copy,
                           or None for the book the files were stored for.
        :type book_id: BookId | None
        :returns: True when both files are there with their content.
        :rtype: bool
        """
        owner, source, image = self._stored[page.id]
        # A key names a file from the directory of its book, so a copy holds the file under the same key
        directory = book_directory(self._root, owner if book_id is None else book_id)
        return (directory / source / SOURCE_NAME).is_file() and (directory / image).read_bytes() == PAGE_IMAGE_CONTENT

    def gone(self, book_id: BookId) -> bool:
        """Return whether nothing of the book, not even its directory, is left on disk.

        :param book_id: Book whose files were deleted.
        :type book_id: BookId
        :returns: True when the book's directory does not exist.
        :rtype: bool
        """
        return not book_directory(self._root, book_id).exists()
