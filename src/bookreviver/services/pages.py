"""Use cases of pages: the manifest of a book, one page, and access to the derived files of its pages."""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.keys import ProjectKeys
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from bookreviver.domain.entities import Actor, Page
    from bookreviver.domain.ids import ProjectId, StorageKey
    from bookreviver.domain.values import Slice, SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore


class PageService:
    """Pages of the acting account's projects and their derived files."""

    def __init__(self, uow: UnitOfWork, assets: AssetStore) -> None:
        self._uow = uow
        self._assets = assets

    async def manifest(self, actor: Actor, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        """Return a slice of the project's pages in book order.

        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        return await self._uow.pages.list_for_project(project_id, request)

    async def get(self, actor: Actor, project_id: ProjectId, index: int) -> Page:
        """Return one page of the project.

        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        return await self._uow.pages.get(project_id, index)

    @asynccontextmanager
    async def open_asset(self, actor: Actor, key: StorageKey) -> AsyncIterator[Path]:
        """Give a local path of the derived file at ``key`` for as long as the context is open.

        :raises NotFoundError: If the key is not inside one of the actor's projects, or nothing is stored there.
        """
        if (keys := ProjectKeys.owning(key)) is None:
            raise NotFoundError(key)
        await owned_project(self._uow.projects, actor, keys.project_id)
        async with self._assets.readable(key) as path:
            yield path
