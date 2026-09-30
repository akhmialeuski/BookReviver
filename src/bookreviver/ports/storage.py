"""Storage ports: the uploads and sources of a book, and the files derived from them.

Two stores divide the files of a project by prefix, as ``ProjectKeys`` lays them out. The ``SourceStore`` owns
``incoming/`` and ``sources/``: an upload is received into the directory of the import job receiving it, and every
source the upload holds is promoted into a directory of its own, named by its ``SourceId``. A project gathers any
number of sources of any kind that way, and deleting one source leaves the others. The ``AssetStore`` owns
``assets/``, where everything derived from the sources lives, such as the renditions of scans and page versions.

Both stores write once and never replace what they store: a source is immutable once promoted, and a regenerated
asset gets a new key. Each store deletes everything of a project it holds, so deleting a project is one call to
each. Local paths, not open files, cross these ports, because the imaging libraries read and write paths.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from bookreviver.domain.ids import JobId, ProjectId, SourceId, StorageKey
    from bookreviver.domain.values import SourceFile


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
    """The uploads being received and the sources of each project, every source written once and never replaced."""

    @abstractmethod
    async def stage(
        self, project_id: ProjectId, job_id: JobId, files: Sequence[IncomingFile], *, max_bytes: int
    ) -> Sequence[SourceFile]:
        """Receive the upload of an import job, replacing whatever an interrupted upload of the same job left.

        The sources the project already has do not matter: another upload adds sources beside them.

        :param project_id: Project receiving the upload.
        :type project_id: ProjectId
        :param job_id: Import job owning the upload.
        :type job_id: JobId
        :param files: Uploaded files, read in chunks as they arrive.
        :type files: Sequence[IncomingFile]
        :param max_bytes: Largest total size of the upload in bytes.
        :type max_bytes: int
        :returns: Name, size and SHA-256 digest of every staged file, in upload order.
        :rtype: Sequence[SourceFile]
        :raises UploadRejectedError: If the upload breaks an upload rule, such as growing past ``max_bytes``; nothing
                                     of it is kept then.
        """

    @abstractmethod
    async def promote(self, project_id: ProjectId, job_id: JobId, source_id: SourceId, *, names: Sequence[str]) -> None:
        """Move the staged files of one source into the source's own directory in one step.

        The source is never seen half written, and the files it takes are no longer staged afterwards.

        :param project_id: Project owning the upload and the source.
        :type project_id: ProjectId
        :param job_id: Import job whose upload holds the files.
        :type job_id: JobId
        :param source_id: Source the files become.
        :type source_id: SourceId
        :param names: Names of the staged files that make the source, as ``stage`` reported them.
        :type names: Sequence[str]
        :raises NotFoundError: If the job has no staged upload, or a name is not among its staged files.
        :raises ConflictError: If the source already has files, which are never replaced.
        :raises ValueError: If ``names`` is empty.
        """

    @abstractmethod
    async def discard(self, project_id: ProjectId, job_id: JobId) -> None:
        """Remove what is left of the upload of an import job; a job without one is not an error.

        :param project_id: Project owning the upload.
        :type project_id: ProjectId
        :param job_id: Import job whose upload is removed.
        :type job_id: JobId
        """

    @abstractmethod
    def staged_files(self, project_id: ProjectId, job_id: JobId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give local paths of the files an import job has staged and not promoted, while the context is open.

        :param project_id: Project owning the upload.
        :type project_id: ProjectId
        :param job_id: Import job whose upload is read.
        :type job_id: JobId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If the job has no staged upload, when the context opens.
        """

    @abstractmethod
    def source_files(self, project_id: ProjectId, source_id: SourceId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give local paths of the files of one source, while the context is open.

        :param project_id: Project owning the source.
        :type project_id: ProjectId
        :param source_id: Source whose files are read.
        :type source_id: SourceId
        :returns: Context manager yielding the paths in name order.
        :rtype: AbstractAsyncContextManager[Sequence[Path]]
        :raises NotFoundError: If the source has no files, when the context opens.
        """

    @abstractmethod
    async def delete_source(self, project_id: ProjectId, source_id: SourceId) -> None:
        """Remove the files of one source; a source without files is not an error.

        The renditions of its scans are derived files, which ``AssetStore.delete_prefix`` removes.

        :param project_id: Project owning the source.
        :type project_id: ProjectId
        :param source_id: Source whose files are removed.
        :type source_id: SourceId
        """

    @abstractmethod
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove every source and every staged upload of the project; a project without either is not an error.

        :param project_id: Project whose files are removed.
        :type project_id: ProjectId
        """


class AssetStore(ABC):
    """Derived files addressed by storage keys, such as the renditions of scans and page versions, each written once.

    A regenerated file gets a new key, such as the next renditions version of a scan, so a stored file is never
    replaced. Every key lies under ``projects/<id>/assets/``, as ``ProjectKeys`` builds it, so no key reaches the
    uploads and sources of a project, which belong to the ``SourceStore``.
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
        :raises ValueError: If the key does not lie under ``projects/<id>/assets/``.
        """

    @abstractmethod
    def readable(self, key: StorageKey) -> AbstractAsyncContextManager[Path]:
        """Give a local path of the stored file or directory at ``key``.

        :param key: Key the file or directory is stored at.
        :type key: StorageKey
        :returns: Context manager yielding the path, valid while the context is open.
        :rtype: AbstractAsyncContextManager[Path]
        :raises NotFoundError: If nothing is stored at the key.
        :raises ValueError: If the key does not lie under ``projects/<id>/assets/``.
        """

    @abstractmethod
    async def copy(self, source: StorageKey, target: StorageKey) -> None:
        """Store a copy of the file or directory at ``source`` at ``target``, published whole like a written file.

        The copy stands on its own, so deleting either one leaves the other, which is how a page keeps its image when
        the scan it was cut from is deleted.

        :param source: Key the file or directory is stored at.
        :type source: StorageKey
        :param target: Key to store the copy at.
        :type target: StorageKey
        :raises NotFoundError: If nothing is stored at ``source``.
        :raises ConflictError: If something is stored at ``target``, which is never replaced.
        :raises ValueError: If either key does not lie under ``projects/<id>/assets/``.
        """

    @abstractmethod
    async def delete_prefix(self, prefix: StorageKey) -> None:
        """Remove the file or directory at ``prefix`` and everything under it.

        The prefix matches whole path segments, so removing ``assets/scans/<source_id>/1`` keeps
        ``assets/scans/<source_id>/10``; a missing prefix is not an error.

        :param prefix: Key of the file or directory to remove.
        :type prefix: StorageKey
        :raises ValueError: If the prefix does not lie under ``projects/<id>/assets/``.
        """

    @abstractmethod
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove every derived file of the project; a project without any is not an error.

        The store removes the whole ``assets/`` directory of the project, which no ``delete_prefix`` may name. The
        uploads and sources are left to ``SourceStore.delete_project``.

        :param project_id: Project whose derived files are removed.
        :type project_id: ProjectId
        """
