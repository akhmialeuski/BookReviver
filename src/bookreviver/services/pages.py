"""Use cases of the pages of a book: its manifest, one page by identifier, moving pages, and the files of their images.

A page is addressed by its ``PageId`` and never by its number, because its position changes when pages are moved. The
position is not stored: the service computes it when it reads a page, from the order keys of the project, and returns
it in a ``PageOverview`` together with the base version whose renditions show the page. The order key itself stays
inside the persistence adapter's ordering, so no client ever sees it.

Moving a page writes a new order key to the moved pages alone: the key lies between the key of the page next to the
place and the key of the page after it, which the repository finds while leaving the moved pages out, so the pages that
stay never change. A group of pages, or every page of one source, is placed as one run that keeps its order in the
book. Every change commits first and publishes one ``PagesChanged`` event after, naming all the pages it touched.

A page shows its base version because no later version is recorded yet. When the processing stages write versions of
their own, the overview will name the current version of the stage the viewer asks for.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.entities import PageOverview
from bookreviver.domain.enums import PageChange, Side
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PagesChanged
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Slice
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Sequence
    from pathlib import Path

    from bookreviver.domain.changes import PageChanges
    from bookreviver.domain.entities import Actor, Page
    from bookreviver.domain.ids import PageId, ProjectId, SourceId, StorageKey
    from bookreviver.domain.values import PageAnchor, PageNumbering, SliceRequest
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher
    from bookreviver.ports.storage import AssetStore


class PageService:
    """Pages of the acting account's projects, addressed by identifier, their order, and access to their files."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        assets: AssetStore,
        order_keys: OrderKeys,
        publisher: EventPublisher,
        clock: Clock,
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, from which pages and versions are read and whose commit ends every
                    use case that changes pages.
        :type uow: UnitOfWork
        :param assets: Store of the derived files, whose files a viewer reads by key.
        :type assets: AssetStore
        :param order_keys: Builder of the order keys of moved pages.
        :type order_keys: OrderKeys
        :param publisher: Publisher of the events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the pages a use case changes.
        :type clock: Clock
        """
        self._uow = uow
        self._assets = assets
        self._order_keys = order_keys
        self._publisher = publisher
        self._clock = clock

    async def manifest(
        self, actor: Actor, project_id: ProjectId, request: SliceRequest, *, included_only: bool = False
    ) -> Slice[PageOverview]:
        """Return a window of the project's pages in book order, every page with its position and base version.

        Pages kept out of the book are listed too, and count in the positions, so the client can offer to bring them
        back. With ``included_only`` they are left out of the listing and of the numbering, so the positions are those
        of the book a viewer shows.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book.
        :type included_only: bool
        :returns: The pages of the window and the number of the pages listed.
        :rtype: Slice[PageOverview]
        :raises NotFoundError: If the actor has no such project.
        """
        await owned_project(self._uow.projects, actor, project_id)
        window = await self._uow.pages.list_for_project(project_id, request, included_only=included_only)
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
        return await self._overview(await self._page(project_id, page_id))

    async def update(self, actor: Actor, project_id: ProjectId, page_id: PageId, changes: PageChanges) -> PageOverview:
        """Change the printed number, the kind, the inclusion or the notes of a page, which writes its one row.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param changes: New values for the fields to change, None keeping a field.
        :type changes: PageChanges
        :returns: The changed page with its position.
        :rtype: PageOverview
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        page = changes.apply_to(await self._page(project_id, page_id))
        changed = evolve(page, updated_at=self._clock.now())
        await self._uow.pages.update(changed)
        overview = await self._overview(changed)
        await self._finish(project_id, [changed], PageChange.EDITED)
        return overview

    async def number(self, actor: Actor, project_id: ProjectId, numbering: PageNumbering) -> None:
        """Write the printed numbers of a range of pages into their labels.

        The numbers count the pages of the range that are part of the book and not of a skipped kind, from the start of
        the numbering, and the pages left out keep their label, as do the pages outside the range. Only the pages whose
        label changes are written.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param numbering: The range, the style and the first number.
        :type numbering: PageNumbering
        :raises NotFoundError: If the actor has no such project, or the project lacks the first or the last page.
        :raises ConflictError: If the range runs backwards, or a number does not fit the style, such as 4000 in Roman
                               numerals.
        """
        await owned_project(self._uow.projects, actor, project_id)
        first = await self._page(project_id, numbering.first_page_id)
        last = await self._page(project_id, numbering.last_page_id)
        if first.order_key.encode() > last.order_key.encode():
            raise ConflictError(numbering.first_page_id, numbering.last_page_id)
        pages = await self._uow.pages.list_range(project_id, first.order_key, last.order_key)
        counted = [page for page in pages if page.included and page.kind not in numbering.skip_kinds]
        moment = self._clock.now()
        try:
            labels = [numbering.label(number) for number in range(numbering.start, numbering.start + len(counted))]
        except ValueError as error:
            raise ConflictError(str(error)) from error
        changed = [
            evolve(page, label=label, updated_at=moment)
            for page, label in zip(counted, labels, strict=True)
            if page.label != label
        ]
        if not changed:
            return
        await self._uow.pages.update_many(changed)
        await self._finish(project_id, changed, PageChange.EDITED)

    async def move(self, actor: Actor, project_id: ProjectId, page_id: PageId, anchor: PageAnchor) -> PageOverview:
        """Put one page before or after another, which writes one row and renumbers nothing.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page to move.
        :type page_id: PageId
        :param anchor: Page the moved page is put next to, and the side of it.
        :type anchor: PageAnchor
        :returns: The page at its new place.
        :rtype: PageOverview
        :raises NotFoundError: If the actor has no such project, or the project has no such page or anchor.
        :raises ConflictError: If the anchor is the page itself, or another request took the new place first.
        """
        await owned_project(self._uow.projects, actor, project_id)
        [moved] = await self._place(project_id, [await self._page(project_id, page_id)], anchor)
        await self._uow.pages.update(moved)
        overview = await self._overview(moved)
        await self._finish(project_id, [moved], PageChange.MOVED)
        return overview

    async def move_group(
        self, actor: Actor, project_id: ProjectId, page_ids: Collection[PageId], anchor: PageAnchor
    ) -> None:
        """Put several pages in a run before or after another page, keeping the order they have in the book.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_ids: Identifiers of the pages to move.
        :type page_ids: Collection[PageId]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :raises NotFoundError: If the actor has no such project, or the project lacks a page or the anchor.
        :raises ConflictError: If the anchor is one of the pages, or another request took a new place first.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._move_run(project_id, await self._uow.pages.list_by_ids(project_id, page_ids), anchor)

    async def move_source(self, actor: Actor, project_id: ProjectId, source_id: SourceId, anchor: PageAnchor) -> None:
        """Put every page whose scan belongs to a source in a run before or after another page.

        A source that has no page left in the book is not an error and moves nothing.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param source_id: Identifier of the source whose pages are moved.
        :type source_id: SourceId
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :raises NotFoundError: If the actor has no such project, or the project has no such source or anchor.
        :raises ConflictError: If the anchor is a page of the source, or another request took a new place first.
        """
        await owned_project(self._uow.projects, actor, project_id)
        # A source of another project is reported like a missing one, as a page of another project is
        if (await self._uow.sources.get(source_id)).project_id != project_id:
            raise NotFoundError(source_id)
        await self._move_run(project_id, await self._uow.pages.list_for_source(project_id, source_id), anchor)

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
        scans = await self._uow.scans.list_by_ids({page.scan_id for page in pages if page.scan_id is not None})
        sources = {scan.id: scan.source_id for scan in scans}
        return [
            PageOverview(
                page=page,
                position=first_position + index,
                base_version=newest.get(page.id),
                source_id=sources.get(page.scan_id) if page.scan_id is not None else None,
            )
            for index, page in enumerate(pages)
        ]

    async def _overview(self, page: Page) -> PageOverview:
        """Read the position, base version and source of one stored page.

        :param page: Stored page of a project.
        :type page: Page
        :returns: The page with its place in the book.
        :rtype: PageOverview
        """
        [overview] = await self._overviews([page], await self._uow.pages.count_before(page))
        return overview

    async def _page(self, project_id: ProjectId, page_id: PageId) -> Page:
        """Read a page of the project.

        :param project_id: Identifier of the project the page must belong to.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page.
        :rtype: Page
        :raises NotFoundError: If there is no such page, or it belongs to another project, which is reported like a
                               missing one so no identifier can be probed across projects.
        """
        page = await self._uow.pages.get(page_id)
        if page.project_id != project_id:
            raise NotFoundError(page_id)
        return page

    async def _place(self, project_id: ProjectId, pages: Sequence[Page], anchor: PageAnchor) -> list[Page]:
        """Give pages new order keys that put them in a run next to the anchor page, in the order given.

        The key of the page next to the place is found with the moved pages left out, so a page that already stands
        beside the anchor gets a key between the same neighbours it would have had if it were elsewhere.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param pages: Pages to move, in book order.
        :type pages: Sequence[Page]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :returns: The pages with their new keys and their update time.
        :rtype: list[Page]
        :raises NotFoundError: If the anchor is not a page of the project.
        :raises ConflictError: If the anchor is one of the pages, since the place is then not defined.
        """
        moving = {page.id for page in pages}
        if anchor.page_id in moving:
            raise ConflictError(anchor.page_id)
        target = await self._page(project_id, anchor.page_id)
        neighbour = await self._uow.pages.neighbour_key(project_id, target.order_key, anchor.side, excluding=moving)
        lower, upper = (neighbour, target.order_key) if anchor.side is Side.BEFORE else (target.order_key, neighbour)
        keys = self._order_keys.spread(lower=lower, upper=upper, count=len(pages))
        moment = self._clock.now()
        return [evolve(page, order_key=key, updated_at=moment) for page, key in zip(pages, keys, strict=True)]

    async def _move_run(self, project_id: ProjectId, pages: Sequence[Page], anchor: PageAnchor) -> None:
        """Put pages in a run next to the anchor page, commit, and publish the move.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param pages: Pages to move, in book order; none moves nothing and publishes nothing.
        :type pages: Sequence[Page]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :raises NotFoundError: If the anchor is not a page of the project.
        :raises ConflictError: If the anchor is one of the pages, or another request took a new place first.
        """
        moved = await self._place(project_id, pages, anchor)
        if not moved:
            return
        await self._uow.pages.update_many(moved)
        await self._finish(project_id, moved, PageChange.MOVED)

    async def _finish(self, project_id: ProjectId, pages: Sequence[Page], change: PageChange) -> None:
        """Commit what a use case wrote, and only then tell the browser which pages changed.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param pages: Pages the use case touched.
        :type pages: Sequence[Page]
        :param change: What was done to them.
        :type change: PageChange
        :raises ConflictError: If the database refuses the commit.
        """
        await self._uow.commit()
        await self._publisher.publish(
            PagesChanged(project_id=project_id, page_ids=[page.id for page in pages], change=change)
        )
