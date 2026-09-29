"""Storage ports: the uploaded source of a book and the files derived from it.

Two stores divide the files of a project. The ``SourceStore`` keeps the upload exactly as received, and the
``AssetStore`` keeps everything derived from it, such as page images and tile pyramids. Both write once and never
replace what they store: another book needs another project, and a regenerated asset gets a new key. Local paths,
not open files, cross these ports, because the imaging libraries read and write paths.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from bookreviver.domain.ids import ProjectId, StorageKey


class IncomingFile(Protocol):
    """An uploaded file being received; FastAPI's ``UploadFile`` satisfies it.

    :ivar filename: Name the client sent for the file, possibly with a client-side path, or None.
    """

    filename: str | None

    async def read(self, size: int = -1) -> bytes:
        """Read the next part of the file.

        :param size: Largest number of bytes to read, or a negative number to read everything that is left.
        :type size: int
        :returns: The bytes read, empty once the file is exhausted.
        :rtype: bytes
        """
        ...


class SourceStore(ABC):
    """The uploaded source of each project, written once and never replaced.

    Another book needs another project, or this project deleted with everything processed from it and created again.
    """

    @abstractmethod
    async def stage(self, project_id: ProjectId, files: Sequence[IncomingFile], *, max_bytes: int) -> int:
        """Receive an upload for a project without a source, next to where its source will be.

        :param project_id: Project receiving the upload.
        :type project_id: ProjectId
        :param files: Uploaded files, read in chunks as they arrive.
        :type files: Sequence[IncomingFile]
        :param max_bytes: Largest total size of the upload in bytes.
        :type max_bytes: int
        :returns: Total size of the staged files in bytes.
        :rtype: int
        :raises ConflictError: If the project already has a source; nothing of the upload is read then.
        :raises UploadRejectedError: If the upload breaks an upload rule, such as growing past ``max_bytes``; nothing
                                     is kept then.
        """

    @abstractmethod
    async def promote(self, project_id: ProjectId) -> None:
        """Make the staged upload the project's source in one step, so the source is never half written.

        :param project_id: Project whose staged upload becomes its source.
        :type project_id: ProjectId
        :raises NotFoundError: If no upload is staged.
        :raises ConflictError: If the project already has a source, which is never replaced.
        """

    @abstractmethod
    async def discard(self, project_id: ProjectId) -> None:
        """Remove a staged upload; a project without one is not an error.

        :param project_id: Project whose staged upload is removed.
        :type project_id: ProjectId
        """

    @abstractmethod
    def staged_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give local paths of the staged files for as long as the context is open.

        :param project_id: Project whose staged upload is read.
        :type project_id: ProjectId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If no upload is staged, when the context opens.
        """

    @abstractmethod
    def source_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give local paths of the source files for as long as the context is open.

        :param project_id: Project whose source is read.
        :type project_id: ProjectId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If the project has no source, when the context opens.
        """

    @abstractmethod
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove the source and the staged upload of the project; a project without either is not an error.

        :param project_id: Project whose files are removed.
        :type project_id: ProjectId
        """


class AssetStore(ABC):
    """Derived files addressed by storage keys, such as page images and tile pyramids, each written once.

    A regenerated file gets a new key, such as the next page version, so a stored file is never replaced. Keys never
    reach the source or staged upload of a project, which belong to the ``SourceStore``.
    """

    @abstractmethod
    def writable(self, key: StorageKey) -> AbstractAsyncContextManager[Path]:
        """Give a local path to write the file or directory at ``key``, published when the context exits.

        A writer that raises publishes nothing, and readers never see a half-written file or pyramid.

        :param key: Key to store the file or directory at.
        :type key: StorageKey
        :returns: Context manager yielding the path to write, which does not exist yet.
        :rtype: AbstractAsyncContextManager[Path]
        :raises ConflictError: If something is stored at ``key``, when the context opens or when it publishes.
        :raises ValueError: If the key leaves the storage root or reaches the files of the ``SourceStore``.
        """

    @abstractmethod
    def readable(self, key: StorageKey) -> AbstractAsyncContextManager[Path]:
        """Give a local path of the stored file or directory at ``key``.

        :param key: Key the file or directory is stored at.
        :type key: StorageKey
        :returns: Context manager yielding the path, valid while the context is open.
        :rtype: AbstractAsyncContextManager[Path]
        :raises NotFoundError: If nothing is stored at the key.
        :raises ValueError: If the key leaves the storage root or reaches the files of the ``SourceStore``.
        """

    @abstractmethod
    async def delete_prefix(self, prefix: StorageKey) -> None:
        """Remove the file or directory at ``prefix`` and everything under it.

        The prefix matches whole path segments, so ``pages/1`` removes ``pages/1/full.jpg`` and keeps
        ``pages/10/full.jpg``; a missing prefix is not an error.

        :param prefix: Key of the file or directory to remove.
        :type prefix: StorageKey
        :raises ValueError: If the prefix leaves the storage root or reaches the files of the ``SourceStore``.
        """

    @abstractmethod
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove every derived file of the project; a project without any is not an error.

        The store knows where the keys of a project lie, so a caller never builds a project-wide prefix, which would
        also name the files of the ``SourceStore``. The source and the staged upload are left to
        ``SourceStore.delete_project``.

        :param project_id: Project whose derived files are removed.
        :type project_id: ProjectId
        """
