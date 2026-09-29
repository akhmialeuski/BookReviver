"""Local directory tree under the storage root, holding the sources and the derived files of every project.

Nothing is written in place and nothing stored is ever replaced: an upload lands in ``incoming/`` and becomes
``source/`` by one rename, and a derived file or directory is written under a hidden sibling name and moved onto its
key once complete, in a step the operating system refuses when the key is already taken.
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
    """Directory of a project holding one state of its source."""

    INCOMING = 'incoming'
    SOURCE = 'source'


class LocalSourceStore(SourceStore):
    """Sources in ``projects/<id>/source/``, each written once from an upload staged in ``projects/<id>/incoming/``."""

    def __init__(self, *, root: Path) -> None:
        self._root = anyio.Path(root)

    @override
    async def stage(self, project_id: ProjectId, files: Sequence[IncomingFile], *, max_bytes: int) -> int:
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
        incoming = await self._existing_area(project_id, SourceArea.INCOMING)
        await _publish(
            incoming, target=self._area(project_id, SourceArea.SOURCE), conflict_message=SOURCE_EXISTS_MESSAGE
        )

    @override
    async def discard(self, project_id: ProjectId) -> None:
        await _remove(self._area(project_id, SourceArea.INCOMING))

    @override
    def staged_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        return self._files(project_id, SourceArea.INCOMING)

    @override
    def source_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        return self._files(project_id, SourceArea.SOURCE)

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        for area in SourceArea:
            await _remove(self._area(project_id, area))

    def _area(self, project_id: ProjectId, area: SourceArea) -> anyio.Path:
        """Return the directory of one state of the project's source."""
        return self._root / PROJECTS_DIR / str(project_id) / area

    async def _existing_area(self, project_id: ProjectId, area: SourceArea) -> anyio.Path:
        """Return the directory of one state of the project's source, which must exist.

        :raises NotFoundError: If the project has no source in that state.
        """
        directory = self._area(project_id, area)
        if not await directory.is_dir():
            err_msg = f'Project {project_id} has no {area} files'
            raise NotFoundError(err_msg)
        return directory

    @asynccontextmanager
    async def _files(self, project_id: ProjectId, area: SourceArea) -> AsyncIterator[Sequence[Path]]:
        """Yield the files of one state of the project's source in name order."""
        directory = await self._existing_area(project_id, area)
        yield sorted([Path(entry) async for entry in directory.iterdir()])


class LocalAssetStore(AssetStore):
    """Derived files at ``<root>/<key>``, each written once and published only when written completely."""

    def __init__(self, *, root: Path) -> None:
        self._root = anyio.Path(root)

    @override
    @asynccontextmanager
    async def writable(self, key: StorageKey) -> AsyncIterator[Path]:
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
        path = self._path(key)
        if not await path.exists():
            err_msg = f'Nothing is stored at {key}'
            raise NotFoundError(err_msg)
        yield Path(path)

    @override
    async def delete_prefix(self, prefix: StorageKey) -> None:
        await _remove(self._path(prefix))

    def _path(self, key: StorageKey) -> anyio.Path:
        """Return the path of a key, which must name something strictly inside the root and outside every source.

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
    """Stream every file into ``directory`` under its base name and return the total size in bytes.

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
    """Delete a file or a directory tree; a missing path is not an error."""
    if await path.is_dir():
        # typeshed declares rmtree as a callable protocol, whose parameters mypy cannot carry through asyncify
        await asyncify(partial(shutil.rmtree, path))()
    else:
        await path.unlink(missing_ok=True)
