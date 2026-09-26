"""Storage fakes and seeding for the tests of the projects feature."""

import shutil
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, override

from dishka import Provider, Scope, provide

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.errors import NotFoundError
from bookreviver.ports.storage import AssetStore, SourceStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Page, Project
    from bookreviver.domain.ids import ProjectId, StorageKey
    from bookreviver.ports.storage import IncomingFile

UNUSED_BY_FEATURE: str = 'The projects feature never stages or reads sources.'


class FakeSourceStore(SourceStore):
    """Records which projects had their source deleted."""

    def __init__(self) -> None:
        self.deleted: list[ProjectId] = []

    @override
    async def stage(self, project_id: ProjectId, files: Sequence[IncomingFile], *, max_bytes: int) -> int:
        raise NotImplementedError(UNUSED_BY_FEATURE)

    @override
    async def promote(self, project_id: ProjectId) -> None:
        raise NotImplementedError(UNUSED_BY_FEATURE)

    @override
    async def discard(self, project_id: ProjectId) -> None:
        raise NotImplementedError(UNUSED_BY_FEATURE)

    @override
    def staged_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        raise NotImplementedError(UNUSED_BY_FEATURE)

    @override
    def source_files(self, project_id: ProjectId) -> AbstractAsyncContextManager[Sequence[Path]]:
        raise NotImplementedError(UNUSED_BY_FEATURE)

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        self.deleted.append(project_id)


class FakeAssetStore(AssetStore):
    """Derived files kept as real files under a temporary directory, one file per key."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def put(self, key: StorageKey, content: bytes) -> None:
        """Store ``content`` at ``key``, as an import would."""
        target = self._root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def exists(self, key: StorageKey) -> bool:
        """Whether a file or directory is stored at ``key``."""
        return (self._root / key).exists()

    @override
    @asynccontextmanager
    async def writable(self, key: StorageKey) -> AsyncIterator[Path]:
        target = self._root / key
        target.parent.mkdir(parents=True, exist_ok=True)
        yield target

    @override
    @asynccontextmanager
    async def readable(self, key: StorageKey) -> AsyncIterator[Path]:
        if not self.exists(key):
            raise NotFoundError(key)
        yield self._root / key

    @override
    async def delete_prefix(self, prefix: StorageKey) -> None:
        for path in [
            path for path in self._root.rglob('*') if path.relative_to(self._root).as_posix().startswith(prefix)
        ]:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)


class FakeStorageProvider(Provider):
    """Overrides the storage adapters of the application with the given fakes."""

    scope = Scope.APP

    def __init__(self, sources: FakeSourceStore, assets: FakeAssetStore) -> None:
        super().__init__()
        self._sources = sources
        self._assets = assets

    @provide(override=True)
    def sources(self) -> SourceStore:
        """Return the source store fake."""
        return self._sources

    @provide(override=True)
    def assets(self) -> AssetStore:
        """Return the asset store fake."""
        return self._assets


async def commit_project(database: InMemoryDatabase, project: Project, *pages: Page) -> None:
    """Commit a project and its pages, as an earlier request would have."""
    uow = InMemoryUnitOfWork(database)
    await uow.projects.add(project)
    await uow.pages.replace_for_project(project.id, pages)
    await uow.commit()
