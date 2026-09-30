"""Use cases of the pages of a book: its manifest in book order, one page by identifier and the files of its images.

A page is addressed by its ``PageId`` and never by its number, because its position changes when pages are moved. The
position is not stored: the service computes it when it reads a page, from the order keys of the project, and returns
it in a ``PageOverview`` together with the base version whose renditions show the page. The order key itself stays
inside the persistence adapter's ordering, so no client ever sees it.

A page shows its base version because no later version is recorded yet. When the processing stages write versions of
their own, the overview will name the current version of the stage the viewer asks for.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from bookreviver.domain.entities import PageOverview
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Slice
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from bookreviver.domain.entities import Actor, Page
    from bookreviver.domain.ids import PageId, ProjectId, StorageKey
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore


class PageService:
    """Pages of the acting account's projects, addressed by identifier, and access to their derived files."""

    def __init__(self, *, uow: UnitOfWork, assets: AssetStore) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, from which pages and versions are read.
        :type uow: UnitOfWork
        :param assets: Store of the derived files, whose files a viewer reads by key.
        :type assets: AssetStore
        """
        self._uow = uow
        self._assets = assets

    async def manifest(self, actor: Actor, project_id: ProjectId, request: SliceRequest) -> Slice[PageOverview]:
        """Return a window of the project's pages in book order, every page with its position and base version.

        Pages kept out of the book are listed too, and count in the positions, so the client can offer to bring them
        back.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The pages of the window and the number of all the project's pages.
        :rtype: Slice[PageOverview]
        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        window = await self._uow.pages.list_for_project(project_id, request)
        return Slice(items=await self._overviews(window.items, request.offset), total=window.total)

    async def get(self, actor: Actor, project_id: ProjectId, page_id: PageId) -> PageOverview:
        """Return one page of the project with its position and base version.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page, its position in the book and its base version.
        :rtype: PageOverview
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        page = await self._uow.pages.get(page_id)
        # A page of another project is reported like a missing one, so no identifier can be probed across projects
        if page.project_id != project_id:
            raise NotFoundError(page_id)
        [overview] = await self._overviews([page], await self._uow.pages.count_before(page))
        return overview

    @asynccontextmanager
    async def open_asset(self, actor: Actor, key: StorageKey) -> AsyncIterator[Path]:
        """Give a local path of the derived file at ``key`` for as long as the context is open.

        The owner is checked before the store is asked for anything, so a key inside another account's project and a
        key with no file behind it are told apart by nobody. A key under the project's ``incoming/`` or ``sources/``
        belongs to no derived file and is reported as not found without a look at the store.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param key: Storage key of a derived file, such as one taken from the address of an image.
        :type key: StorageKey
        :returns: Context manager yielding the path of the file, valid while the context is open.
        :rtype: AsyncIterator[Path]
        :raises NotFoundError: If the key is not under the ``assets/`` of one of the actor's projects, or nothing is
                               stored at it.
        """
        if (keys := ProjectKeys.owning(key)) is None:
            raise NotFoundError(key)
        await owned_project(self._uow.projects, actor, keys.project_id)
        async with self._assets.readable(key) as path:
            yield path

    async def _overviews(self, pages: Sequence[Page], first_position: int) -> Sequence[PageOverview]:
        """Attach positions and base versions to consecutive pages of a book, reading the versions in one call.

        :param pages: Pages of one project in book order, without a gap.
        :type pages: Sequence[Page]
        :param first_position: Position of the first page in the book.
        :type first_position: int
        :returns: The overviews of the pages, in the same order.
        :rtype: Sequence[PageOverview]
        """
        versions = await self._uow.page_versions.list_base_versions([page.id for page in pages])
        # The versions come earliest first, so the newest base version of a page wins
        newest = {version.page_id: version for version in versions}
        return [
            PageOverview(page=page, position=first_position + index, base_version=newest.get(page.id))
            for index, page in enumerate(pages)
        ]
