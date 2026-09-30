"""Local directory tree under the storage root, holding the uploads, sources and derived files of every project.

Both stores share one root, and every path under it is a storage key that ``ProjectKeys`` builds, joined by the
directory separator. The source store owns ``projects/<id>/incoming/<job_id>/``, where an upload is received, and
``projects/<id>/sources/<source_id>/``, where each source lives as uploaded. The asset store owns
``projects/<id>/assets/`` and refuses any key outside it, so a wrong key can never erase a source.

Nothing is written in place and nothing stored is ever replaced. The files of a source are moved from the upload
into a hidden sibling directory, which one rename then publishes as the source's directory. A derived file or
directory is written under a hidden sibling name and moved onto its key once complete. The move is a hard link for a
file and a rename for a directory, because the operating system refuses both when the target exists, which a check
followed by a write could not guarantee against a second writer. Every blocking call goes through ``anyio.Path`` or
``asyncify``, and cleanup after a failure runs in a shielded cancel scope, so a cancelled job still removes what it
half wrote.
"""

import errno
import hashlib
import shutil
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, override
from uuid import uuid4

import anyio
from asyncer import asyncify

from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import ConflictError, NotFoundError, UploadRejectedError
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import SourceFile
from bookreviver.ports.storage import AssetStore, SourceStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from contextlib import AbstractAsyncContextManager

    from bookreviver.domain.ids import JobId, ProjectId, SourceId, StorageKey
    from bookreviver.ports.storage import IncomingFile

# Read size of an upload stream, large enough to keep the per-chunk overhead negligible
CHUNK_BYTES: int = 1024 * 1024
# Base names a client may send that do not name a file
NAMELESS: frozenset[str] = frozenset({'', '.', '..'})
PARTIAL_ROLE: str = 'partial'
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

        :param key: Storage key built by ``ProjectKeys``, with ``/`` separating its segments.
        :type key: StorageKey
        :returns: Path of the key under the root.
        :rtype: anyio.Path
        """
        return self._root.joinpath(*key.split(ProjectKeys.SEPARATOR))

    async def _remove_area(self, area: StorageKey) -> None:
        """Remove one area of a project, then the project's directory once no other area is left in it.

        :param area: Key of the area, such as ``projects/<id>/assets``.
        :type area: StorageKey
        """
        path = self._path(area)
        await _remove(path)
        try:
            await path.parent.rmdir()
        except OSError as error:
            if error.errno not in KEPT_DIRECTORY_ERRNOS:
                raise


class LocalSourceStore(_LocalTree, SourceStore):
    """Uploads in ``incoming/<job_id>/`` and sources in ``sources/<source_id>/`` of each project's directory."""

    @override
    async def stage(
        self, project_id: ProjectId, job_id: JobId, files: Sequence[IncomingFile], *, max_bytes: int
    ) -> Sequence[SourceFile]:
        """Stream an upload into the job's ``incoming/<job_id>/`` directory, replacing an interrupted upload of it.

        :param project_id: Project receiving the upload.
        :type project_id: ProjectId
        :param job_id: Import job owning the upload.
        :type job_id: JobId
        :param files: Uploaded files, read in chunks of ``CHUNK_BYTES``.
        :type files: Sequence[IncomingFile]
        :param max_bytes: Largest total size of the upload in bytes.
        :type max_bytes: int
        :returns: Name, size and SHA-256 digest of every staged file, in upload order.
        :rtype: Sequence[SourceFile]
        :raises UploadRejectedError: If a file has no usable name, two names differ only in letter case, or the upload
                                     grows past ``max_bytes``; the job's directory is removed then.
        """
        incoming = self._path(ProjectKeys(project_id).incoming(job_id))
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
    async def promote(self, project_id: ProjectId, job_id: JobId, source_id: SourceId, *, names: Sequence[str]) -> None:
        """Move the named files from ``incoming/<job_id>/`` into a hidden directory, then rename it to the source's.

        A refused promotion moves the files back, so they stay staged and nothing of the upload is lost.

        :param project_id: Project owning the upload and the source.
        :type project_id: ProjectId
        :param job_id: Import job whose upload holds the files.
        :type job_id: JobId
        :param source_id: Source the files become.
        :type source_id: SourceId
        :param names: Names of the staged files that make the source.
        :type names: Sequence[str]
        :raises NotFoundError: If the job has no staged upload, or a name is not among its staged files.
        :raises ConflictError: If ``sources/<source_id>/`` exists, which the rename refuses to replace.
        :raises ValueError: If ``names`` is empty.
        """
        if not names:
            err_msg = f'Source {source_id} needs at least one file.'
            raise ValueError(err_msg)
        keys = ProjectKeys(project_id)
        incoming = await self._existing(keys.incoming(job_id))
        # Only a name the store itself staged is accepted, so no name can climb out of the job's directory
        staged = {entry.name async for entry in incoming.iterdir()}
        if missing := sorted(set(names) - staged):
            err_msg = f'Import job {job_id} has staged no file named {missing}'
            raise NotFoundError(err_msg)
        target = self._path(keys.source(source_id))
        conflict_message = f'Source {source_id} already has files, and the files of a source are never replaced'
        if await target.exists():
            raise ConflictError(conflict_message)
        await target.parent.mkdir(parents=True, exist_ok=True)
        hidden = target.with_name(f'.{target.name}-{PARTIAL_ROLE}-{uuid4().hex}')
        await hidden.mkdir()
        try:
            for name in dict.fromkeys(names):
                await (incoming / name).rename(hidden / name)
            await _publish(hidden, target=target, conflict_message=conflict_message)
        except BaseException:
            with anyio.CancelScope(shield=True):
                if await hidden.is_dir():
                    for entry in [entry async for entry in hidden.iterdir()]:
                        await entry.rename(incoming / entry.name)
                    await hidden.rmdir()
            raise

    @override
    async def discard(self, project_id: ProjectId, job_id: JobId) -> None:
        """Remove the job's ``incoming/<job_id>/`` directory, if there is one.

        :param project_id: Project owning the upload.
        :type project_id: ProjectId
        :param job_id: Import job whose upload is removed.
        :type job_id: JobId
        """
        await _remove(self._path(ProjectKeys(project_id).incoming(job_id)))

    @override
    def staged_files(self, project_id: ProjectId, job_id: JobId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give the paths of the files left in the job's ``incoming/<job_id>/`` directory.

        :param project_id: Project owning the upload.
        :type project_id: ProjectId
        :param job_id: Import job whose upload is read.
        :type job_id: JobId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If the job has no staged upload, when the context opens.
        """
        return self._files(ProjectKeys(project_id).incoming(job_id))

    @override
    def source_files(self, project_id: ProjectId, source_id: SourceId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give the paths of the files in the source's ``sources/<source_id>/`` directory.

        :param project_id: Project owning the source.
        :type project_id: ProjectId
        :param source_id: Source whose files are read.
        :type source_id: SourceId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If the source has no files, when the context opens.
        """
        return self._files(ProjectKeys(project_id).source(source_id))

    @override
    async def delete_source(self, project_id: ProjectId, source_id: SourceId) -> None:
        """Remove the source's ``sources/<source_id>/`` directory, if there is one.

        :param project_id: Project owning the source.
        :type project_id: ProjectId
        :param source_id: Source whose files are removed.
        :type source_id: SourceId
        """
        await _remove(self._path(ProjectKeys(project_id).source(source_id)))

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove the project's ``incoming/`` and ``sources/`` directories, then the project's directory if empty.

        The project's directory stays while ``assets/`` is in it, since only the asset store removes that.

        :param project_id: Project whose uploads and sources are removed.
        :type project_id: ProjectId
        """
        keys = ProjectKeys(project_id)
        for area in (keys.incoming_area, keys.sources_area):
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
    """Derived files at ``<root>/<key>`` under each project's ``assets/``, each written once and published whole."""

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
            await asyncify(_copy_tree)(origin, destination)

    @override
    async def delete_prefix(self, prefix: StorageKey) -> None:
        """Remove the file or directory tree at ``prefix``, if there is one.

        :param prefix: Key of the file or directory to remove, matched by whole path segments.
        :type prefix: StorageKey
        :raises ValueError: If the prefix does not lie under ``projects/<id>/assets/``.
        """
        await _remove(self._asset_path(prefix))

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove the project's ``assets/`` directory, then the project's directory if empty.

        The project's directory stays while ``incoming/`` or ``sources/`` is in it, since only the source store
        removes those.

        :param project_id: Project whose derived files are removed.
        :type project_id: ProjectId
        """
        await self._remove_area(ProjectKeys(project_id).assets_area)

    def _asset_path(self, key: StorageKey) -> anyio.Path:
        """Return the path of a key, which must name something strictly inside the ``assets/`` of a project.

        :param key: Storage key with ``/`` separating its segments.
        :type key: StorageKey
        :returns: Path of the key under the root.
        :rtype: anyio.Path
        :raises ValueError: If the key is not a well-formed key of a project, or does not lie under its ``assets/``,
                            such as the key of an upload, of a source, or of ``assets/`` itself.
        """
        keys = ProjectKeys.owning(key)
        if keys is None or not key.startswith(f'{keys.assets_area}{ProjectKeys.SEPARATOR}'):
            err_msg = f'Storage key {key!r} does not lie under the assets directory of a project'
            raise ValueError(err_msg)
        return self._path(key)


async def _receive(files: Sequence[IncomingFile], *, directory: anyio.Path, max_bytes: int) -> list[SourceFile]:
    """Stream every file into ``directory`` under its base name, hashing it on the way.

    :param files: Uploaded files, read in chunks of ``CHUNK_BYTES``.
    :type files: Sequence[IncomingFile]
    :param directory: Existing, empty directory to write the files into.
    :type directory: anyio.Path
    :param max_bytes: Largest total size of the upload in bytes.
    :type max_bytes: int
    :returns: Name, size and SHA-256 digest of every written file, in upload order.
    :rtype: list[SourceFile]
    :raises UploadRejectedError: If a file has no usable name, two files share a base name in any letter case, or
                                 the total grows past ``max_bytes``.
    """
    total = 0
    received: list[SourceFile] = []
    names: set[str] = set()
    for file in files:
        # A browser may send a client-side path, with either separator
        name = PureWindowsPath(file.filename or '').name
        if name in NAMELESS:
            raise UploadRejectedError(UploadProblem.EMPTY_NAME)
        # A case-insensitive file system, such as a Windows drive, stores Page.jpg and page.jpg as one file
        folded = name.casefold()
        if folded in names:
            raise UploadRejectedError(UploadProblem.DUPLICATE_NAME)
        names.add(folded)
        size = 0
        digest = hashlib.sha256()
        async with await (directory / name).open('wb') as target:
            while chunk := await file.read(CHUNK_BYTES):
                size += len(chunk)
                if total + size > max_bytes:
                    raise UploadRejectedError(UploadProblem.TOO_LARGE)
                digest.update(chunk)
                await target.write(chunk)
        total += size
        received.append(SourceFile(name=name, size_bytes=size, sha256=digest.hexdigest()))
    return received


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


async def _remove(path: anyio.Path) -> None:
    """Delete a file or a directory tree; a missing path is not an error.

    :param path: File or directory to delete.
    :type path: anyio.Path
    """
    if await path.is_dir():
        # typeshed declares rmtree as a callable protocol, whose parameters mypy cannot carry through asyncify
        await asyncify(partial(shutil.rmtree, path))()
    else:
        await path.unlink(missing_ok=True)
