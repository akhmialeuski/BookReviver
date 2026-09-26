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
    """The uploaded source of each project, replaced only after a new upload proved readable."""

    @abstractmethod
    async def stage(self, project_id: ProjectId, files: Sequence[IncomingFile], *, max_bytes: int) -> int:
        """Receive an upload next to the current source and return its total size in bytes.

        :raises UploadRejectedError: If the upload grows past ``max_bytes``; nothing is kept then.
        """

    @abstractmethod
    async def promote(self, project_id: ProjectId) -> None:
        """Make the staged upload the project's source, replacing the previous one atomically."""

    @abstractmethod
    async def discard(self, project_id: ProjectId) -> None:
        """Remove a staged upload, leaving the current source untouched."""

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
    """Derived files addressed by storage keys, such as page images and tile pyramids."""

    @abstractmethod
    def writable(self, key: StorageKey) -> AbstractAsyncContextManager[Path]:
        """Give a local path to write the file or directory at ``key``, published when the context exits."""

    @abstractmethod
    def readable(self, key: StorageKey) -> AbstractAsyncContextManager[Path]:
        """Give a local path of the stored file or directory at ``key``.

        :raises NotFoundError: If nothing is stored at the key.
        """

    @abstractmethod
    async def delete_prefix(self, prefix: StorageKey) -> None:
        """Remove every file whose key starts with ``prefix``."""
