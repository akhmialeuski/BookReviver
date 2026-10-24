"""Local directory tree under the storage root, holding the uploads, sources and derived files of every book.

Both stores share one root, and every path under it is a storage key that ``BookKeys`` builds, joined by the
directory separator. The source store owns ``projects/<id>/incoming/<job_id>/``, where an upload is received, and
``projects/<id>/sources/<source_id>/``, where each source lives as uploaded. The asset store owns
``projects/<id>/assets/`` and refuses any key outside it, so a wrong key can never erase a source.

Nothing is written in place and nothing stored is ever replaced. The files of a source are moved from the upload
into a hidden sibling directory, which one rename then publishes as the source's directory. A derived file or
directory is written under a hidden sibling name and moved onto its key once complete. The move is a hard link for a
file and a rename for a directory, because the operating system refuses both when the target exists, which a check
followed by a write could not guarantee against a second writer. Every blocking call goes through ``anyio.Path`` or
``anyio.to_thread.run_sync``, and cleanup after a failure runs in a shielded cancel scope, so a cancelled job still
removes what it half wrote.
"""

import errno
import hashlib
import os
import shutil
import zipfile
from contextlib import asynccontextmanager
from functools import partial
from operator import itemgetter
from pathlib import Path
from typing import TYPE_CHECKING, override
from uuid import uuid4

import anyio

from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import ConflictError, InvalidArchiveError, NotFoundError, UploadRejectedError
from bookreviver.domain.keys import BookKeys, KeySegment
from bookreviver.domain.values import SourceFile, UploadPath
from bookreviver.ports.storage import AssetStore, BookArchive, BookFileStores, SourceStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from contextlib import AbstractAsyncContextManager
    from typing import BinaryIO

    from bookreviver.domain.ids import BookId, JobId, SourceId, StorageKey
    from bookreviver.ports.storage import IncomingFile

# Read size of an upload stream, large enough to keep the per-chunk overhead negligible
CHUNK_BYTES: int = 1024 * 1024
PARTIAL_ROLE: str = 'partial'
# First character of the name of a file or directory still being written, which nothing but its writer reads
HIDDEN_PREFIX: str = '.'
# The archive of a book is stored without compression, since its images and documents are compressed already
ARCHIVE_COMPRESSION: int = zipfile.ZIP_STORED
NOT_AN_ARCHIVE: str = 'The file is not a ZIP archive.'
NO_DATABASE: str = 'The archive holds no database of a book.'
OUTSIDE_THE_BOOK: str = 'The archive holds {name}, which is not a file of a book.'
# What ``rmdir`` reports for a directory that is gone or still holds another store's files
KEPT_DIRECTORY_ERRNOS: frozenset[int] = frozenset({errno.ENOENT, errno.ENOTEMPTY})


class _LocalTree:
    """The storage root both local stores share, with the paths of storage keys under it."""

    def __init__(self, *, root: Path) -> None:
        """Keep the files under ``root``.

        :param root: Storage root shared by the source store and the asset store, created on the first write.
        :type root: Path
        """
        self._root = anyio.Path(root)

    def _path(self, key: StorageKey) -> anyio.Path:
        """Return the path of a key under the root, whether or not anything is stored there.

        :param key: Storage key built by ``BookKeys``, with ``/`` separating its segments.
        :type key: StorageKey
        :returns: Path of the key under the root.
        :rtype: anyio.Path
        """
        return self._root.joinpath(*key.split(BookKeys.SEPARATOR))

    async def _remove_area(self, area: StorageKey) -> None:
        """Remove one area of a book, then the book's directory once no other area is left in it.

        :param area: Key of the area, such as ``projects/<id>/assets``.
        :type area: StorageKey
        """
        await self._remove_inside_book(self._path(area))

    @staticmethod
    async def _remove_inside_book(path: anyio.Path) -> None:
        """Remove one directory of a book, then the book's directory once nothing is left in it.

        :param path: Directory under the directory of a book, such as ``projects/<id>/assets``.
        :type path: anyio.Path
        """
        await _remove(path)
        try:
            await path.parent.rmdir()
        except OSError as error:
            if error.errno not in KEPT_DIRECTORY_ERRNOS:
                raise


class LocalSourceStore(_LocalTree, SourceStore):
    """Uploads in ``incoming/<job_id>/`` and sources in ``sources/<source_id>/`` of the directory of one book.

    An upload keeps the relative paths of its files under ``incoming/<job_id>/``, and a source keeps each of its files
    under the last segment of that path alone, since the folders of a directory upload end where the source starts.
    """

    def __init__(self, *, root: Path, book_id: BookId) -> None:
        """Keep the uploads and sources of one book under ``root``.

        :param root: Storage root shared by the source store and the asset store, created on the first write.
        :type root: Path
        :param book_id: The book whose uploads and sources the store holds.
        :type book_id: BookId
        """
        super().__init__(root=root)
        self._keys = BookKeys(book_id)

    @override
    async def stage(self, job_id: JobId, files: Sequence[IncomingFile], *, max_bytes: int) -> Sequence[SourceFile]:
        """Stream an upload into the job's ``incoming/<job_id>/`` directory, replacing an interrupted upload of it.

        :param job_id: Import job owning the upload.
        :type job_id: JobId
        :param files: Uploaded files, read in chunks of ``CHUNK_BYTES``.
        :type files: Sequence[IncomingFile]
        :param max_bytes: Largest total size of the upload in bytes.
        :type max_bytes: int
        :returns: Name, size and SHA-256 digest of every staged file, in upload order.
        :rtype: Sequence[SourceFile]
        :raises UploadRejectedError: If a file has no usable name or path, two paths differ only in letter case, or the
                                     upload grows past ``max_bytes``; the job's directory is removed then.
        """
        incoming = self._path(self._keys.incoming(job_id))
        # An interrupted upload of the same job may have left files behind
        await _remove(incoming)
        await incoming.mkdir(parents=True)
        try:
            return await _receive(files, directory=incoming, max_bytes=max_bytes)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await _remove(incoming)
            raise

    @override
    async def promote(self, job_id: JobId, source_id: SourceId, *, names: Sequence[str]) -> None:
        """Move the named files from ``incoming/<job_id>/`` into a hidden directory, then rename it to the source's.

        A refused promotion moves the files back, so they stay staged and nothing of the upload is lost.

        :param job_id: Import job whose upload holds the files.
        :type job_id: JobId
        :param source_id: Source the files become.
        :type source_id: SourceId
        :param names: Names of the staged files that make the source.
        :type names: Sequence[str]
        :raises NotFoundError: If the job has no staged upload, or a name is not among its staged files.
        :raises ConflictError: If ``sources/<source_id>/`` exists, which the rename refuses to replace, or two of the
                               names end in the same file name.
        :raises ValueError: If ``names`` is empty.
        """
        if not names:
            err_msg = f'Source {source_id} needs at least one file.'
            raise ValueError(err_msg)
        keys = self._keys
        # Only a name the store itself staged is accepted, so no name can climb out of the job's directory
        staged = await _staged_paths(await self._existing(keys.incoming(job_id)))
        if missing := sorted(set(names) - staged.keys()):
            err_msg = f'Import job {job_id} has staged no file named {missing}'
            raise NotFoundError(err_msg)
        stored_names = {name: UploadPath.parse(name).name for name in dict.fromkeys(names)}
        if len(set(stored_names.values())) != len(stored_names):
            err_msg = f'Source {source_id} cannot hold two files with the same name from different folders'
            raise ConflictError(err_msg)
        target = self._path(keys.source(source_id))
        conflict_message = f'Source {source_id} already has files, and the files of a source are never replaced'
        if await target.exists():
            raise ConflictError(conflict_message)
        await target.parent.mkdir(parents=True, exist_ok=True)
        hidden = target.with_name(f'{HIDDEN_PREFIX}{target.name}-{PARTIAL_ROLE}-{uuid4().hex}')
        await hidden.mkdir()
        try:
            for name, stored_name in stored_names.items():
                await anyio.Path(staged[name]).rename(hidden / stored_name)
            await _publish(hidden, target=target, conflict_message=conflict_message)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await _return_to_staging(hidden, staged={stored_names[name]: staged[name] for name in stored_names})
            raise

    @override
    async def discard(self, job_id: JobId) -> None:
        """Remove the job's ``incoming/<job_id>/`` directory, if there is one.

        :param job_id: Import job whose upload is removed.
        :type job_id: JobId
        """
        await _remove(self._path(self._keys.incoming(job_id)))

    @override
    @asynccontextmanager
    async def staged_files(self, job_id: JobId) -> AsyncIterator[Mapping[str, Path]]:
        """Give the paths of the files left in the job's ``incoming/<job_id>/`` directory and its folders.

        :param job_id: Import job whose upload is read.
        :type job_id: JobId
        :returns: Iterator yielding the path of every file by its relative name, in the order of the names, once.
        :rtype: AsyncIterator[Mapping[str, Path]]
        :raises NotFoundError: If the job has no staged upload, when the context opens.
        """
        yield await _staged_paths(await self._existing(self._keys.incoming(job_id)))

    @override
    def source_files(self, source_id: SourceId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give the paths of the files in the source's ``sources/<source_id>/`` directory.

        :param source_id: Source whose files are read.
        :type source_id: SourceId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If the source has no files, when the context opens.
        """
        return self._files(self._keys.source(source_id))

    @override
    async def delete_source(self, source_id: SourceId) -> None:
        """Remove the source's ``sources/<source_id>/`` directory, if there is one.

        :param source_id: Source whose files are removed.
        :type source_id: SourceId
        """
        await _remove(self._path(self._keys.source(source_id)))

    @override
    async def delete_all(self) -> None:
        """Remove the book's ``incoming/`` and ``sources/`` directories, then the book's directory if empty.

        The book's directory stays while ``assets/`` is in it, since only the asset store removes that.
        """
        for area in (self._keys.incoming_area, self._keys.sources_area):
            await self._remove_area(area)

    async def _existing(self, key: StorageKey) -> anyio.Path:
        """Return the directory at ``key``, which must exist.

        :param key: Key of an upload's or a source's directory.
        :type key: StorageKey
        :returns: Path of the existing directory.
        :rtype: anyio.Path
        :raises NotFoundError: If nothing is stored at the key.
        """
        directory = self._path(key)
        if not await directory.is_dir():
            err_msg = f'No files are stored at {key}'
            raise NotFoundError(err_msg)
        return directory

    @asynccontextmanager
    async def _files(self, key: StorageKey) -> AsyncIterator[Sequence[Path]]:
        """Yield the files of the directory at ``key`` in name order.

        :param key: Key of an upload's or a source's directory.
        :type key: StorageKey
        :returns: Iterator yielding the sorted paths once.
        :rtype: AsyncIterator[Sequence[Path]]
        :raises NotFoundError: If nothing is stored at the key.
        """
        directory = await self._existing(key)
        yield sorted([Path(entry) async for entry in directory.iterdir()])


class LocalAssetStore(_LocalTree, AssetStore):
    """Derived files at ``<root>/<key>`` under the ``assets/`` of one book, each written once and published whole."""

    def __init__(self, *, root: Path, book_id: BookId) -> None:
        """Keep the derived files of one book under ``root``.

        :param root: Storage root shared by the source store and the asset store, created on the first write.
        :type root: Path
        :param book_id: The book whose derived files the store holds.
        :type book_id: BookId
        """
        super().__init__(root=root)
        self._keys = BookKeys(book_id)

    @override
    @asynccontextmanager
    async def writable(self, key: StorageKey) -> AsyncIterator[Path]:
        """Hand out a hidden sibling path of the key and move what was written there onto the key on exit.

        The sibling keeps the key's suffix, so writers that infer the format from it still can. It is removed on
        every exit, so a failed or refused write leaves nothing behind.

        :param key: Key to store the file or directory at.
        :type key: StorageKey
        :returns: Iterator yielding the sibling path once.
        :rtype: AsyncIterator[Path]
        :raises ConflictError: If something is stored at ``key``, before the writer starts or when it publishes.
        :raises ValueError: If the key does not lie under ``projects/<id>/assets/``.
        """
        target = self._asset_path(key)
        conflict_message = f'Something is already stored at {key}, and stored files are never replaced'
        # Refused before the writer spends time on a pyramid that could not be published
        if await target.exists():
            raise ConflictError(conflict_message)
        await target.parent.mkdir(parents=True, exist_ok=True)
        # Hidden, unique and keeping the suffix, so writers that infer the format from it still can
        hidden = target.with_stem(f'.{target.stem}-{PARTIAL_ROLE}-{uuid4().hex}')
        try:
            yield Path(hidden)
            await _publish(hidden, target=target, conflict_message=conflict_message)
        finally:
            # Nothing is left once published; a failed or refused write leaves its partial files
            with anyio.CancelScope(shield=True):
                await _remove(hidden)

    @override
    @asynccontextmanager
    async def readable(self, key: StorageKey) -> AsyncIterator[Path]:
        """Yield the path of the stored file or directory at ``key``.

        :param key: Key the file or directory is stored at.
        :type key: StorageKey
        :returns: Iterator yielding the path once.
        :rtype: AsyncIterator[Path]
        :raises NotFoundError: If nothing is stored at the key.
        :raises ValueError: If the key does not lie under ``projects/<id>/assets/``.
        """
        path = self._asset_path(key)
        if not await path.exists():
            err_msg = f'Nothing is stored at {key}'
            raise NotFoundError(err_msg)
        yield Path(path)

    @override
    async def copy(self, source: StorageKey, target: StorageKey) -> None:
        """Copy the file or directory tree at ``source`` into a hidden sibling of ``target``, then publish it.

        :param source: Key the file or directory is stored at.
        :type source: StorageKey
        :param target: Key to store the copy at.
        :type target: StorageKey
        :raises NotFoundError: If nothing is stored at ``source``.
        :raises ConflictError: If something is stored at ``target``, before the copy starts or when it publishes.
        :raises ValueError: If either key does not lie under ``projects/<id>/assets/``.
        """
        async with self.readable(source) as origin, self.writable(target) as destination:
            await anyio.to_thread.run_sync(_copy_tree, origin, destination)

    @override
    async def delete_prefix(self, prefix: StorageKey) -> None:
        """Remove the file or directory tree at ``prefix``, if there is one.

        :param prefix: Key of the file or directory to remove, matched by whole path segments.
        :type prefix: StorageKey
        :raises ValueError: If the prefix does not lie under ``projects/<id>/assets/``.
        """
        await _remove(self._asset_path(prefix))

    @override
    async def size_of(self, prefix: StorageKey) -> int:
        """Add up the sizes of the files at ``prefix``, if there are any.

        :param prefix: Key of the file or directory to measure, matched by whole path segments.
        :type prefix: StorageKey
        :returns: The total size of the files in bytes, 0 when nothing is stored there.
        :rtype: int
        :raises ValueError: If the prefix does not lie under ``projects/<id>/assets/``.
        """
        return await anyio.to_thread.run_sync(_tree_size, Path(self._asset_path(prefix)))

    @override
    async def delete_all(self) -> None:
        """Remove the book's ``assets/`` directory, then the book's directory if empty.

        The book's directory stays while ``incoming/`` or ``sources/`` is in it, since only the source store
        removes those.
        """
        await self._remove_area(self._keys.assets_area)

    def _asset_path(self, key: StorageKey) -> anyio.Path:
        """Return the path of a key, which must name something strictly inside the ``assets/`` of the store's book.

        :param key: Storage key with ``/`` separating its segments.
        :type key: StorageKey
        :returns: Path of the key under the root.
        :rtype: anyio.Path
        :raises ValueError: If the key is not a well-formed key of the book of the store, or does not lie under its
                            ``assets/``, such as the key of an upload, of a source, of ``assets/`` itself, or of a file
                            of another book.
        """
        if BookKeys.owning(key) != self._keys or not key.startswith(f'{self._keys.assets_area}{BookKeys.SEPARATOR}'):
            err_msg = f'Storage key {key!r} does not lie under the assets directory of book {self._keys.book_id}'
            raise ValueError(err_msg)
        return self._path(key)


class LocalBookFileStores(_LocalTree, BookFileStores):
    """Opens the local source store and asset store of a book, all under one storage root."""

    @override
    def sources(self, book_id: BookId) -> LocalSourceStore:
        """Return the local store of the uploads and sources of a book.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The source store of the book.
        :rtype: LocalSourceStore
        """
        return LocalSourceStore(root=Path(self._root), book_id=book_id)

    @override
    def assets(self, book_id: BookId) -> LocalAssetStore:
        """Return the local store of the derived files of a book.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The asset store of the book.
        :rtype: LocalAssetStore
        """
        return LocalAssetStore(root=Path(self._root), book_id=book_id)

    @override
    async def discard_all_staged(self) -> None:
        """Remove the ``incoming/`` directory of every book, and the directory of a book that it leaves empty."""
        books = self._root / KeySegment.BOOKS
        if not await books.is_dir():
            return
        async for directory in books.iterdir():
            await self._remove_inside_book(directory / KeySegment.INCOMING)


async def _receive(files: Sequence[IncomingFile], *, directory: anyio.Path, max_bytes: int) -> list[SourceFile]:
    """Stream every file into ``directory`` under its relative path, hashing it on the way.

    :param files: Uploaded files, read in chunks of ``CHUNK_BYTES``.
    :type files: Sequence[IncomingFile]
    :param directory: Existing, empty directory to write the files into.
    :type directory: anyio.Path
    :param max_bytes: Largest total size of the upload in bytes.
    :type max_bytes: int
    :returns: Relative path, size and SHA-256 digest of every written file, in upload order.
    :rtype: list[SourceFile]
    :raises UploadRejectedError: If a file has no usable name, leaves its folder, shares its path with another in any
                                 letter case or lies where another file has a folder, or the total grows past
                                 ``max_bytes``.
    """
    total = 0
    received: list[SourceFile] = []
    paths: set[str] = set()
    folders: set[str] = set()
    for file in files:
        path = UploadPath.parse(file.filename or '')
        name = str(path)
        # A case-insensitive file system, such as a Windows drive, stores Page.jpg and page.jpg as one file
        folded = name.casefold()
        above = {folder.casefold() for folder in path.folders}
        # A path cannot be a file and a folder at once, whatever the letter case
        if folded in paths or folded in folders or not above.isdisjoint(paths):
            raise UploadRejectedError(UploadProblem.DUPLICATE_NAME)
        paths.add(folded)
        folders |= above
        size = 0
        digest = hashlib.sha256()
        stored = directory.joinpath(*path.segments)
        await stored.parent.mkdir(parents=True, exist_ok=True)
        async with await stored.open('wb') as target:
            while chunk := await file.read(CHUNK_BYTES):
                size += len(chunk)
                if total + size > max_bytes:
                    raise UploadRejectedError(UploadProblem.TOO_LARGE)
                digest.update(chunk)
                await target.write(chunk)
        total += size
        received.append(SourceFile(name=name, size_bytes=size, sha256=digest.hexdigest()))
    return received


class _Pieces:
    """Where ``zipfile`` writes an archive, held until the stream of the archive takes it.

    It has no ``tell``, so ``zipfile`` writes the sizes of each file after its data and never seeks back.

    :ivar _pieces: What was written since the last time the pieces were taken.
    """

    def __init__(self) -> None:
        """Start with nothing written."""
        self._pieces: list[bytes] = []

    def write(self, data: bytes, /) -> int:
        """Keep what ``zipfile`` writes.

        :param data: The bytes written.
        :type data: bytes
        :returns: How many bytes were written.
        :rtype: int
        """
        self._pieces.append(bytes(data))
        return len(data)

    def flush(self) -> None:
        """Do nothing, since the pieces are taken by the stream."""

    def close(self) -> None:
        """Do nothing, since nothing is open."""

    def take(self) -> bytes:
        """Return what was written since the last call, and forget it.

        :returns: The bytes written meanwhile, which are empty when nothing was.
        :rtype: bytes
        """
        taken = b''.join(self._pieces)
        self._pieces.clear()
        return taken


class LocalBookArchive(_LocalTree, BookArchive):
    """The archives of the books as ZIP files of the directories of the books under the local storage root.

    The archive is written as it is streamed, one piece of a file at a time, so a book of many gigabytes needs no
    temporary file and no more memory than one piece. Files still being written under a hidden name are left out.
    """

    @override
    async def export(self, book_id: BookId, database: bytes) -> AsyncIterator[bytes]:
        """Stream the archive of a book: its database first, then every file of its sources and derived files.

        :param book_id: The book.
        :type book_id: BookId
        :param database: The copy of the database of the book.
        :type database: bytes
        :returns: The pieces of the archive, in order.
        :rtype: AsyncIterator[bytes]
        """
        keys = BookKeys(book_id)
        pieces = _Pieces()
        # The archive and its entries write to memory alone, so a stream given up half way leaves nothing open
        archive = zipfile.ZipFile(pieces, mode='w', compression=ARCHIVE_COMPRESSION)
        archive.writestr(keys.relative(keys.database), database)
        yield pieces.take()
        for path, name in await anyio.to_thread.run_sync(self._files_of, keys):
            info = await anyio.to_thread.run_sync(zipfile.ZipInfo.from_file, path, name)
            entry = archive.open(info, mode='w')
            offset = 0
            while piece := await anyio.to_thread.run_sync(_piece_of, path, offset):
                entry.write(piece)
                offset += len(piece)
                yield pieces.take()
            entry.close()
            yield pieces.take()
        archive.close()
        yield pieces.take()

    @override
    async def unpack(self, book_id: BookId, archive: BinaryIO) -> None:
        """Place the content of an archive as the directory of a new book, its database first.

        :param book_id: The new book, which has no directory yet.
        :type book_id: BookId
        :param archive: The archive, a file that can be read from any position.
        :type archive: BinaryIO
        :raises InvalidArchiveError: If the file is not a ZIP archive, holds no database, or holds a file outside what
                                     the archive of a book carries.
        :raises ConflictError: If the new book has a directory already.
        """
        await anyio.to_thread.run_sync(self._unpack, BookKeys(book_id), archive)

    def _files_of(self, keys: BookKeys) -> list[tuple[Path, str]]:
        """List the files of the sources and the derived files of a book, by their names in the archive.

        :param keys: The keys of the book.
        :type keys: BookKeys
        :returns: Each file and its name relative to the directory of the book, in the order of the names.
        :rtype: list[tuple[Path, str]]
        """
        book = Path(self._path(keys.prefix))
        files = [
            (path, path.relative_to(book).as_posix())
            for area in (keys.sources_area, keys.assets_area)
            for path in Path(self._path(area)).rglob('*')
            if path.is_file() and not any(part.startswith(HIDDEN_PREFIX) for part in path.relative_to(book).parts)
        ]
        return sorted(files, key=itemgetter(1))

    def _unpack(self, keys: BookKeys, archive: BinaryIO) -> None:
        """Check every name of the archive, then write its files into the new directory of the book, database first.

        :param keys: The keys of the new book.
        :type keys: BookKeys
        :param archive: The archive, a file that can be read from any position.
        :type archive: BinaryIO
        :raises InvalidArchiveError: If the archive is refused.
        :raises ConflictError: If the new book has a directory already.
        """
        try:
            opened = zipfile.ZipFile(archive)
        except zipfile.BadZipFile as error:
            raise InvalidArchiveError(NOT_AN_ARCHIVE) from error
        with opened:
            files = [member for member in opened.infolist() if not member.is_dir()]
            placed: dict[str, StorageKey] = {}
            for member in files:
                if (key := keys.archived(member.filename)) is None:
                    raise InvalidArchiveError(OUTSIDE_THE_BOOK.format(name=member.filename))
                placed[member.filename] = key
            database = keys.relative(keys.database)
            if database not in placed:
                raise InvalidArchiveError(NO_DATABASE)
            try:
                Path(self._path(keys.prefix)).mkdir(parents=True)
            except FileExistsError as error:
                raise ConflictError(keys.book_id) from error
            for member in sorted(files, key=lambda member: member.filename != database):
                target = Path(self._path(placed[member.filename]))
                target.parent.mkdir(parents=True, exist_ok=True)
                with opened.open(member) as source, target.open('xb') as copy:
                    shutil.copyfileobj(source, copy, CHUNK_BYTES)


def _list_files(directory: Path) -> dict[str, Path]:
    """Find the files under ``directory``, however deep, by their relative path.

    :param directory: Existing directory of an upload.
    :type directory: Path
    :returns: Path of every file by its ``/`` separated path relative to ``directory``, in the order of the paths.
    :rtype: dict[str, Path]
    """
    # os.walk reads the kind of every entry with the listing, where Path.rglob asks the file system for each file, and
    # the relative name is cut once per folder, since Path.relative_to costs more than reading a book's worth of files
    found: dict[str, Path] = {}
    for current, _folders, names in os.walk(directory):
        folder = os.path.relpath(current, directory).replace(os.sep, '/')
        prefix = '' if folder == os.curdir else f'{folder}/'
        found.update({f'{prefix}{name}': Path(current, name) for name in names})
    return dict(sorted(found.items()))


async def _staged_paths(directory: anyio.Path) -> dict[str, Path]:
    """Find the files of an upload under ``directory``, however deep, in one call to a worker thread.

    A listing that waited on the file system for every entry would cost a thread hop per file, and an import lists the
    upload once per source, which is thousands of files for a book of scans.

    :param directory: Existing directory of an upload.
    :type directory: anyio.Path
    :returns: Path of every file by its ``/`` separated path relative to ``directory``, in the order of the paths.
    :rtype: dict[str, Path]
    """
    return await anyio.to_thread.run_sync(_list_files, Path(directory))


async def _return_to_staging(hidden: anyio.Path, *, staged: Mapping[str, Path]) -> None:
    """Move the files of a refused promotion back to where the upload staged them, and remove the hidden directory.

    :param hidden: The hidden directory the files were moved into, which may not have been made.
    :type hidden: anyio.Path
    :param staged: The staged path of each file, by the name it has in the hidden directory.
    :type staged: Mapping[str, Path]
    """
    if not await hidden.is_dir():
        return
    for stored_name, path in staged.items():
        if await (hidden / stored_name).exists():
            await (hidden / stored_name).rename(path)
    await hidden.rmdir()


async def _publish(staged: anyio.Path, *, target: anyio.Path, conflict_message: str) -> None:
    """Move ``staged`` onto ``target`` in one step that never replaces what is stored there.

    A file is published by a hard link, which leaves ``staged`` for the caller to remove, and a directory by a rename.

    :param staged: Completely written file or directory.
    :type staged: anyio.Path
    :param target: Path to publish it at.
    :type target: anyio.Path
    :param conflict_message: Message of the error raised when ``target`` is taken, worded for the caller's user.
    :type conflict_message: str
    :raises ConflictError: With ``conflict_message``, if something is stored at ``target``.
    """
    try:
        if await staged.is_dir():
            # The operating system refuses to rename a directory over a file or a non-empty directory
            await staged.rename(target)
        else:
            # A hard link, unlike a rename, refuses a target that exists
            await target.hardlink_to(staged)
    except (FileExistsError, NotADirectoryError) as error:
        raise ConflictError(conflict_message) from error
    except OSError as error:
        if error.errno != errno.ENOTEMPTY:
            raise
        raise ConflictError(conflict_message) from error


def _copy_tree(origin: Path, destination: Path) -> None:
    """Copy a file, or a directory with everything in it, to a path that does not exist yet.

    :param origin: Existing file or directory to copy.
    :type origin: Path
    :param destination: Path to create, whose parent exists.
    :type destination: Path
    """
    if origin.is_dir():
        shutil.copytree(origin, destination)
    else:
        shutil.copyfile(origin, destination)


def _piece_of(path: Path, offset: int) -> bytes:
    """Read one piece of a file, opening and closing it for the piece alone.

    :param path: The file.
    :type path: Path
    :param offset: Where the piece starts.
    :type offset: int
    :returns: The piece, which is empty at the end of the file.
    :rtype: bytes
    """
    with path.open('rb') as source:
        source.seek(offset)
        return source.read(CHUNK_BYTES)


def _tree_size(path: Path) -> int:
    """Add up the sizes of a file, or of every file under a directory; a missing path has none.

    :param path: File or directory to measure.
    :type path: Path
    :returns: The total size in bytes.
    :rtype: int
    """
    if path.is_file():
        return path.stat().st_size
    return sum(entry.stat().st_size for entry in path.rglob('*') if entry.is_file())


async def _remove(path: anyio.Path) -> None:
    """Delete a file or a directory tree; a missing path is not an error.

    :param path: File or directory to delete.
    :type path: anyio.Path
    """
    if await path.is_dir():
        # typeshed declares rmtree as a callable protocol, whose parameters mypy cannot carry through run_sync
        await anyio.to_thread.run_sync(partial(shutil.rmtree, path))
    else:
        await path.unlink(missing_ok=True)
