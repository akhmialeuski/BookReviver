"""Local directory tree under the storage root, holding the sources and the derived files of every project.

Both stores share one root and one layout: ``projects/<id>/incoming/`` holds an upload being received,
``projects/<id>/source/`` the book as received, and every other path under ``projects/<id>/`` belongs to derived files
addressed by storage keys. The asset store refuses keys that name or hold the two source directories, so a wrong key
cannot erase a book.

Nothing is written in place and nothing stored is ever replaced. An upload becomes ``source/`` by one rename, and a
derived file or directory is written under a hidden sibling name and moved onto its key once complete. The move is a
hard link for a file and a rename for a directory, because the operating system refuses both when the target exists,
which a check followed by a write could not guarantee against a second writer. Every blocking call goes through
``anyio.Path`` or ``asyncify``, and cleanup after a failure runs in a shielded cancel scope, so a cancelled job still
removes what it half wrote.
"""

import enum
import errno
import shutil
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import TYPE_CHECKING, override
from uuid import uuid4

import anyio
from asyncer import asyncify

from bookreviver.domain.enums import UploadProblem
from bookreviver.domain.errors import ConflictError, NotFoundError, UploadRejectedError
from bookreviver.ports.storage import AssetStore, SourceStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from contextlib import AbstractAsyncContextManager

    from bookreviver.domain.ids import ProjectId, StorageKey
    from bookreviver.ports.storage import IncomingFile

# Read size of an upload stream, large enough to keep the per-chunk overhead negligible
CHUNK_BYTES: int = 1024 * 1024
PROJECTS_DIR: str = 'projects'
# Base names a client may send that do not name a file
NAMELESS: frozenset[str] = frozenset({'', '.', '..'})
PARENT_PART: str = '..'
# Index of the directory under ``projects/<id>/`` in the parts of a storage key
PROJECT_AREA_INDEX: int = 2
PARTIAL_ROLE: str = 'partial'
SOURCE_EXISTS_MESSAGE: str = (
    'This project already has a book. Create a new project for another book, '
    'or delete this project with everything processed from it and create it again.'
)


class SourceArea(enum.StrEnum):
    """Directory of a project holding one state of its source, which only the source store touches."""

    INCOMING = 'incoming'
    SOURCE = 'source'


class LocalSourceStore(SourceStore):
    """Sources in ``projects/<id>/source/``, each written once from an upload staged in ``projects/<id>/incoming/``."""

    def __init__(self, *, root: Path) -> None:
        """Keep the sources under ``root``.

        :param root: Storage root shared with the asset store, created on the first upload.
        :type root: Path
        """
        self._root = anyio.Path(root)

    @override
    async def stage(self, project_id: ProjectId, files: Sequence[IncomingFile], *, max_bytes: int) -> int:
        """Stream an upload into the project's ``incoming/`` directory, replacing an earlier interrupted upload.

        :param project_id: Project receiving the upload.
        :type project_id: ProjectId
        :param files: Uploaded files, read in chunks of ``CHUNK_BYTES``.
        :type files: Sequence[IncomingFile]
        :param max_bytes: Largest total size of the upload in bytes.
        :type max_bytes: int
        :returns: Total size of the staged files in bytes.
        :rtype: int
        :raises ConflictError: If the project already has a source; nothing of the upload is read then.
        :raises UploadRejectedError: If a file has no usable name, two names differ only in letter case, or the upload
                                     grows past ``max_bytes``; ``incoming/`` is removed then.
        """
        # Refused before a byte is read, since an upload can be gigabytes
        if await self._area(project_id, SourceArea.SOURCE).exists():
            raise ConflictError(SOURCE_EXISTS_MESSAGE)
        incoming = self._area(project_id, SourceArea.INCOMING)
        # An interrupted upload may have left files behind
        await _remove(incoming)
        await incoming.mkdir(parents=True)
        try:
            return await _receive(files, directory=incoming, max_bytes=max_bytes)
        except BaseException:
            with anyio.CancelScope(shield=True):
                await _remove(incoming)
            raise

    @override
    async def promote(self, project_id: ProjectId) -> None:
        """Rename the project's ``incoming/`` directory to ``source/``.

        :param project_id: Project whose staged upload becomes its source.
        :type project_id: ProjectId
        :raises NotFoundError: If no upload is staged.
        :raises ConflictError: If ``source/`` exists, which the rename refuses to replace.
        """
        incoming = await self._existing_area(project_id, SourceArea.INCOMING)
        await _publish(
            incoming, target=self._area(project_id, SourceArea.SOURCE), conflict_message=SOURCE_EXISTS_MESSAGE
        )

    @override
    async def discard(self, project_id: ProjectId) -> None:
        """Remove the project's ``incoming/`` directory, if there is one.

        :param project_id: Project whose staged upload is removed.
        :type project_id: ProjectId
        """
        await _remove(self._area(project_id, SourceArea.INCOMING))

    @override
    def staged_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give the paths of the files in the project's ``incoming/`` directory.

        :param project_id: Project whose staged upload is read.
        :type project_id: ProjectId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If no upload is staged, when the context opens.
        """
        return self._files(project_id, SourceArea.INCOMING)

    @override
    def source_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give the paths of the files in the project's ``source/`` directory.

        :param project_id: Project whose source is read.
        :type project_id: ProjectId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If the project has no source, when the context opens.
        """
        return self._files(project_id, SourceArea.SOURCE)

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove the project's ``source/`` and ``incoming/`` directories, whichever exist.

        :param project_id: Project whose source files are removed.
        :type project_id: ProjectId
        """
        for area in SourceArea:
            await _remove(self._area(project_id, area))

    def _area(self, project_id: ProjectId, area: SourceArea) -> anyio.Path:
        """Return the directory of one state of the project's source, whether or not it exists.

        :param project_id: Project owning the directory.
        :type project_id: ProjectId
        :param area: State of the source the directory holds.
        :type area: SourceArea
        :returns: Path of ``projects/<id>/<area>`` under the root.
        :rtype: anyio.Path
        """
        return self._root / PROJECTS_DIR / str(project_id) / area

    async def _existing_area(self, project_id: ProjectId, area: SourceArea) -> anyio.Path:
        """Return the directory of one state of the project's source, which must exist.

        :param project_id: Project owning the directory.
        :type project_id: ProjectId
        :param area: State of the source the directory holds.
        :type area: SourceArea
        :returns: Path of the existing ``projects/<id>/<area>`` directory.
        :rtype: anyio.Path
        :raises NotFoundError: If the project has no source in that state.
        """
        directory = self._area(project_id, area)
        if not await directory.is_dir():
            err_msg = f'Project {project_id} has no {area} files'
            raise NotFoundError(err_msg)
        return directory

    @asynccontextmanager
    async def _files(self, project_id: ProjectId, area: SourceArea) -> AsyncIterator[Sequence[Path]]:
        """Yield the files of one state of the project's source in name order.

        :param project_id: Project owning the files.
        :type project_id: ProjectId
        :param area: State of the source to list.
        :type area: SourceArea
        :returns: Iterator yielding the sorted paths once.
        :rtype: AsyncIterator[Sequence[Path]]
        :raises NotFoundError: If the project has no source in that state.
        """
        directory = await self._existing_area(project_id, area)
        yield sorted([Path(entry) async for entry in directory.iterdir()])


class LocalAssetStore(AssetStore):
    """Derived files at ``<root>/<key>``, each written once and published only when written completely."""

    def __init__(self, *, root: Path) -> None:
        """Keep the derived files under ``root``.

        :param root: Storage root shared with the source store.
        :type root: Path
        """
        self._root = anyio.Path(root)

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
        :raises ValueError: If the key leaves the root or reaches the files of the source store.
        """
        target = self._path(key)
        conflict_message = f'Something is already stored at {key}, and stored files are never replaced'
        # Refused before the writer spends time on a pyramid that could not be published
        if await target.exists():
            raise ConflictError(conflict_message)
        await target.parent.mkdir(parents=True, exist_ok=True)
        # Hidden, unique and keeping the suffix, so writers that infer the format from it still can
        partial = target.with_stem(f'.{target.stem}-{PARTIAL_ROLE}-{uuid4().hex}')
        try:
            yield Path(partial)
            await _publish(partial, target=target, conflict_message=conflict_message)
        finally:
            # Nothing is left once published; a failed or refused write leaves its partial files
            with anyio.CancelScope(shield=True):
                await _remove(partial)

    @override
    @asynccontextmanager
    async def readable(self, key: StorageKey) -> AsyncIterator[Path]:
        """Yield the path of the stored file or directory at ``key``.

        :param key: Key the file or directory is stored at.
        :type key: StorageKey
        :returns: Iterator yielding the path once.
        :rtype: AsyncIterator[Path]
        :raises NotFoundError: If nothing is stored at the key.
        :raises ValueError: If the key leaves the root or reaches the files of the source store.
        """
        path = self._path(key)
        if not await path.exists():
            err_msg = f'Nothing is stored at {key}'
            raise NotFoundError(err_msg)
        yield Path(path)

    @override
    async def delete_prefix(self, prefix: StorageKey) -> None:
        """Remove the file or directory tree at ``prefix``, if there is one.

        :param prefix: Key of the file or directory to remove, matched by whole path segments.
        :type prefix: StorageKey
        :raises ValueError: If the prefix leaves the root or reaches the files of the source store.
        """
        await _remove(self._path(prefix))

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove every entry of ``projects/<id>/`` except the source directories, then the directory if it is empty.

        The directory stays while ``source/`` or ``incoming/`` is in it, since only the source store removes those.

        :param project_id: Project whose derived files are removed.
        :type project_id: ProjectId
        """
        project_dir = self._root / PROJECTS_DIR / str(project_id)
        if not await project_dir.is_dir():
            return
        for entry in [entry async for entry in project_dir.iterdir()]:
            if entry.name not in SourceArea:
                await _remove(entry)
        try:
            await project_dir.rmdir()
        except OSError as error:
            if error.errno != errno.ENOTEMPTY:
                raise

    def _path(self, key: StorageKey) -> anyio.Path:
        """Return the path of a key, which must name something strictly inside the root and outside every source.

        :param key: Storage key with ``/`` separating its segments.
        :type key: StorageKey
        :returns: Path of the key under the root.
        :rtype: anyio.Path
        :raises ValueError: If the key is empty, absolute, climbs out of the root, or names or holds the source or
                            staged upload of a project, which belong to the source store.
        """
        parts = PurePosixPath(key).parts
        if not parts or PurePosixPath(key).is_absolute() or PARENT_PART in parts:
            err_msg = f'Storage key {key!r} does not name a path inside the storage root'
            raise ValueError(err_msg)
        if parts[0] == PROJECTS_DIR and (len(parts) <= PROJECT_AREA_INDEX or parts[PROJECT_AREA_INDEX] in SourceArea):
            err_msg = f'Storage key {key!r} reaches into the files of the source store'
            raise ValueError(err_msg)
        return self._root.joinpath(*parts)


async def _receive(files: Sequence[IncomingFile], *, directory: anyio.Path, max_bytes: int) -> int:
    """Stream every file into ``directory`` under its base name.

    :param files: Uploaded files, read in chunks of ``CHUNK_BYTES``.
    :type files: Sequence[IncomingFile]
    :param directory: Existing, empty directory to write the files into.
    :type directory: anyio.Path
    :param max_bytes: Largest total size of the upload in bytes.
    :type max_bytes: int
    :returns: Total size of the written files in bytes.
    :rtype: int
    :raises UploadRejectedError: If a file has no usable name, two files share a base name in any letter case, or
                                 the total grows past ``max_bytes``.
    """
    total = 0
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
        async with await (directory / name).open('wb') as target:
            while chunk := await file.read(CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise UploadRejectedError(UploadProblem.TOO_LARGE)
                await target.write(chunk)
    return total


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
