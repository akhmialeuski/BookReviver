"""Storage ports: the uploaded source of a book and the files derived from it."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from bookreviver.domain.ids import ProjectId, StorageKey


class IncomingFile(Protocol):
    """An uploaded file being received; FastAPI's ``UploadFile`` satisfies it."""

    filename: str | None

    async def read(self, size: int = -1) -> bytes:
        """Read up to ``size`` bytes, or everything that is left when ``size`` is negative."""
        ...


class SourceStore(ABC):
    """The uploaded source of each project, written once and never replaced.

    Another book needs another project, or this project deleted with everything processed from it and created again.
    """

    @abstractmethod
    async def stage(self, project_id: ProjectId, files: Sequence[IncomingFile], *, max_bytes: int) -> int:
        """Receive an upload for a project without a source and return its total size in bytes.

        :raises ConflictError:       If the project already has a source; nothing of the upload is read then.
        :raises UploadRejectedError: If the upload breaks an upload rule, such as growing past ``max_bytes``; nothing
                                     is kept then.
        """

    @abstractmethod
    async def promote(self, project_id: ProjectId) -> None:
        """Make the staged upload the project's source in one step, so the source is never half written.

        :raises NotFoundError: If no upload is staged.
        :raises ConflictError: If the project already has a source, which is never replaced.
        """

    @abstractmethod
    async def discard(self, project_id: ProjectId) -> None:
        """Remove a staged upload; a project without one is not an error."""

    @abstractmethod
    def staged_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give local paths of the staged files for as long as the context is open."""

    @abstractmethod
    def source_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        """Give local paths of the current source files for as long as the context is open."""

    @abstractmethod
    async def delete_project(self, project_id: ProjectId) -> None:
        """Remove every source file of the project."""


class AssetStore(ABC):
    """Derived files addressed by storage keys, such as page images and tile pyramids, each written once.

    A regenerated file gets a new key, such as the next page version, so a stored file is never replaced. Keys never
    reach the source or staged upload of a project, which belong to the ``SourceStore``.
    """

    @abstractmethod
    def writable(self, key: StorageKey) -> AbstractAsyncContextManager[Path]:
        """Give a local path to write the file or directory at ``key``, published when the context exits.

        :raises ConflictError: If something is stored at ``key``, when the context opens or when it publishes.
        """

    @abstractmethod
    def readable(self, key: StorageKey) -> AbstractAsyncContextManager[Path]:
        """Give a local path of the stored file or directory at ``key``.

        :raises NotFoundError: If nothing is stored at the key.
        """

    @abstractmethod
    async def delete_prefix(self, prefix: StorageKey) -> None:
        """Remove the file or directory at ``prefix`` and everything under it.

        The prefix matches whole path segments, so ``pages/1`` removes ``pages/1/full.jpg`` and keeps
        ``pages/10/full.jpg``; a missing prefix is not an error.
        """
