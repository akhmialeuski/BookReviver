"""Use cases of the pages of a book: its manifest, one page by identifier, moving pages, and the files of their images.

A page is addressed by its ``PageId`` and never by its number, because its position changes when pages are moved. The
position is not stored: the service computes it when it reads a page, from the order keys of the project, and returns
it in a ``PageOverview`` together with the base version whose renditions show the page. The order key itself stays
inside the persistence adapter's ordering, so no client ever sees it.

Moving a page writes a new order key to the moved pages alone: the key lies between the key of the page next to the
place and the key of the page after it, which the repository finds while leaving the moved pages out, so the pages that
stay never change. A group of pages, or every page of one source, is placed as one run that keeps its order in the
book. Every change commits first and publishes one ``PagesChanged`` event after, naming all the pages it touched. A
change that adds, deletes or moves pages, or changes the kind or the inclusion of one, also recomputes the labels the
pagination sections give the pages, in its own transaction, and announces the pages renumbered as a second
``PagesChanged``.

A blank page cut from a scan may show a leaf in place of its scan: a white leaf, or one of the colour of the paper of
the neighbouring pages. The leaf is the version of the page order that ``pages.blank`` makes, of the size of the pages
of the book, and it becomes the current version of the page order, so the stages after it read the leaf. The page keeps
its scan and its version, so choosing the scan again removes the record of the page order and the page shows its scan
again. Changing the kind of the page does the same. A change of the choice is written in one transaction for all the
pages, and the leaves are written by the ``prepare-pages`` job.

A page without a scan is added in a place of the book: a placeholder has no image and waits for a scan, and a blank leaf
gets the base version ``pages.blank``, a white image of the median size of the book's pages. Binding a scan to a
placeholder makes it a page of that scan with the base version ``split.none``. None of these writes an image in the
request: the page and its pending base version are committed, and the ``prepare-pages`` job writes the files, which
``prepare_images`` does for every pending or failed version of the project. A failed version keeps its reason in its
data, and the next job takes it with the new ones, so no route repeats it.

A page shows its base version because no later version is recorded yet. When the processing stages write versions of
their own, the overview will name the current version of the stage the viewer asks for.

The files of a base version are made by the processors ``split.none`` and ``pages.blank``, which a ``StepRunner`` runs,
as it runs the steps of a recipe, and a base version that is ready becomes the current version of its stage in the
record of the page, so the page split and the page order stage read like any other.
"""

import logging
import statistics
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, ClassVar
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import Job, Page, PageOverview
from bookreviver.domain.enums import (
    BlankFill,
    BlankParam,
    ColorMode,
    JobKind,
    JobState,
    NewPageOrigin,
    NormalizeParam,
    PageChange,
    PageKind,
    PageOrigin,
    PaperFill,
    Side,
    Stage,
    VersionData,
    VersionState,
)
from bookreviver.domain.errors import (
    AnchorInsideMovedPagesError,
    ConcurrentChangeError,
    ConflictError,
    DomainError,
    NotAPlaceholderError,
    NotFoundError,
    ScanAlreadyInBookError,
)
from bookreviver.domain.events import JobChanged, PagesChanged, PageVersionReady
from bookreviver.domain.ids import JobId, PageId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageSize, PageStageKey, Progress, Slice, SliceRequest
from bookreviver.services.base_versions import PAGES_BLANK, SPLIT_NONE, BaseVersions
from bookreviver.services.book_measure import NORMALIZE_KEY
from bookreviver.services.page_labels import PageLabels
from bookreviver.services.projects import owned_project
from bookreviver.services.stage_records import StageRecords
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from bookreviver.domain.changes import PageChanges
    from bookreviver.domain.entities import Actor, PageStage, PageVersion
    from bookreviver.domain.ids import PageVersionId, ProjectId, ScanId, SourceId, StorageKey
    from bookreviver.domain.values import NewPage, PageAnchor
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
    from bookreviver.ports.storage import AssetStore
    from bookreviver.services.steps import StepRunner

# The processors whose pending and failed versions the ``prepare-pages`` job writes the files of, which are the ones a
# page is given when it is made. A version of another processor of the same stages, such as a half of a split spread,
# belongs to the run that makes it
PREPARED_PROCESSORS: frozenset[str] = frozenset({SPLIT_NONE.key, PAGES_BLANK.key})
NOT_A_CUT_PAGE: str = 'The page {page_id} is not cut from a scan, so it has no scan to keep or to replace.'
NOT_A_BLANK_PAGE: str = 'The page {page_id} is not a blank page, so no leaf can stand in place of its scan.'
NO_BOOK_IMAGE: str = (
    'No page of the book that is cut from a scan and part of the book has a recorded size, so a blank leaf has no size '
    'to take. Give the size of the leaf.'
)
SCAN_NOT_CUT: str = 'The scan has no image yet. Bind it after its import has cut it.'
SCAN_DELETED: str = 'The scan of the page was deleted, so its image cannot be copied.'
PAGES_FAILED: str = '{count} of the page images could not be made. They are tried again by the next job.'
NOT_QUEUED: str = 'The images of the pages could not be queued. They are made by the next job.'
BATCH_TOO_LONG: str = 'A request adds at most {limit} pages. Send the rest in another request.'
PAGE_OF_BATCH: str = 'The page at index {index} of the list cannot be added: {reason}'
ANCHOR_NOT_IN_BOOK: str = 'the page it is put next to is not a page of the book.'
UNEXPECTED_FAILURE: str = (
    'The images of the pages could not be made because of an unexpected error. It has been logged.'
)

logger = logging.getLogger(__name__)


@frozen(kw_only=True)
class PageRuntime:
    """What a page use case reports through and orders by.

    :ivar publisher: Publisher of the events the browser follows.
    :ivar clock: Clock stamping the pages a use case changes.
    :ivar order_keys: Builder of the order keys of moved and new pages.
    :ivar queue: Queue handing the ``prepare-pages`` job to the workers.
    """

    publisher: EventPublisher
    clock: Clock
    order_keys: OrderKeys
    queue: JobQueue


@frozen(kw_only=True)
class PageImaging:
    """What writes the images of base versions.

    :ivar base_versions: Builder of the rows of base versions.
    :ivar runner: Runner of the processors ``split.none`` and ``pages.blank``, and writer of their files.
    """

    base_versions: BaseVersions
    runner: StepRunner


class PageService:
    """Pages of the acting account's projects, addressed by identifier, their order, and access to their files.

    :cvar PAGE_WRITE_ATTEMPTS: How many times a change of one page is read, applied and written when another request
                               keeps changing the page in between.
    :cvar MAX_PAGES_PER_BATCH: Most pages one call of ``add_many`` adds, which bounds the rows of one transaction.
    :cvar PAPER_KINDS: Kinds of page whose paper a leaf may take, which are the pages printed on the paper of the book.
    :cvar PAPER_RADIUS: How many pages on each side of a leaf are looked at for pages to take the paper from.
    :cvar PAPER_PAGES: Most pages a leaf takes its paper from, the nearest ones.
    """

    PAGE_WRITE_ATTEMPTS: ClassVar[int] = 3
    MAX_PAGES_PER_BATCH: ClassVar[int] = 1000
    PAPER_KINDS: ClassVar[frozenset[PageKind]] = frozenset({PageKind.TEXT, PageKind.TITLE})
    PAPER_RADIUS: ClassVar[int] = 10
    PAPER_PAGES: ClassVar[int] = 6

    def __init__(self, *, uow: UnitOfWork, assets: AssetStore, runtime: PageRuntime, imaging: PageImaging) -> None:
        """Work over the ports of one request or job.

        :param uow: Unit of work of the request, from which pages and versions are read and whose commit ends every
                    use case that changes pages.
        :type uow: UnitOfWork
        :param assets: Store of the derived files, whose files a viewer reads by key.
        :type assets: AssetStore
        :param runtime: The publisher, the clock, the order keys and the job queue.
        :type runtime: PageRuntime
        :param imaging: The builder of base versions and the runner of their processors.
        :type imaging: PageImaging
        """
        self._uow = uow
        self._assets = assets
        self._order_keys = runtime.order_keys
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._queue = runtime.queue
        self._base_versions = imaging.base_versions
        self._runner = imaging.runner
        self._records = StageRecords(uow=uow, publisher=runtime.publisher, clock=runtime.clock)
        self._labels = PageLabels(uow=uow, publisher=runtime.publisher, clock=runtime.clock)

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

        A page that stops being blank shows its scan again in place of a leaf, so the record of its page order is
        removed and the stages after it become stale.

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
        :raises ConcurrentChangeError: If another request kept changing the page on every one of the attempts.
        """
        await owned_project(self._uow.projects, actor, project_id)
        # The changes set only the fields they carry, so applying them again to the page another request just wrote
        # keeps both edits. The rollback drops the stale read, and the next attempt reads the page afresh
        for _ in range(self.PAGE_WRITE_ATTEMPTS - 1):
            try:
                return await self._write_page(project_id, page_id, changes)
            except ConcurrentChangeError:
                await self._uow.rollback()
        return await self._write_page(project_id, page_id, changes)

    async def _write_page(self, project_id: ProjectId, page_id: PageId, changes: PageChanges) -> PageOverview:
        """Read a page, apply the changes to it, and write and commit it, once.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param changes: New values for the fields to change, None keeping a field.
        :type changes: PageChanges
        :returns: The changed page with its position.
        :rtype: PageOverview
        :raises NotFoundError: If the project has no such page.
        :raises ConcurrentChangeError: If another request changed the page after it was read.
        """
        before = await self._page(project_id, page_id)
        page = changes.apply_to(before)
        changed = await self._uow.pages.update(evolve(page, updated_at=self._clock.now()))
        # A page that stops being blank gets its scan back in place of the leaf, so the leaf is no longer current
        records = (
            await self._records.clear(PageStageKey(page_id, Stage.PAGE_ORDER))
            if before.blank_fill is not BlankFill.SCAN and page.blank_fill is BlankFill.SCAN
            else []
        )
        # A label, a kind or an inclusion changes what the sections give the pages, the page itself included
        if any(field is not None for field in (changes.label, changes.kind, changes.included)) and (
            await self._labels.recompute(project_id)
        ):
            changed = await self._uow.pages.get(page_id)
        overview = await self._overview(changed)
        await self._finish(project_id, [changed], PageChange.EDITED)
        await self._records.announce(project_id, records)
        return overview

    async def add(self, actor: Actor, project_id: ProjectId, new_page: NewPage) -> PageOverview:
        """Add a placeholder or a blank leaf at a place of the book, or at its end.

        A placeholder has no image. A blank leaf gets the pending base version ``pages.blank``, of the size given or of
        the median of the pages of the book, whose white image the ``prepare-pages`` job writes. The request makes no
        image itself.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param new_page: The page to add, with its place and for a blank leaf perhaps its size.
        :type new_page: NewPage
        :returns: The new page with its position.
        :rtype: PageOverview
        :raises NotFoundError: If the actor has no such project, or the project has no such anchor.
        :raises ConflictError: If a blank leaf has no size given and no page of the book has one to take the median of.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        blank = new_page.origin is NewPageOrigin.BLANK
        size = (new_page.size or await self._leaf_size(project_id)) if blank else None
        [key] = await self._keys_at(project_id, new_page.anchor, count=1)
        moment = self._clock.now()
        page = self._new_page(project_id, new_page, key, moment)
        await self._uow.pages.add(page)
        if size is not None:
            full = project.image_policy.full_format(ColorMode.BILEVEL)
            await self._uow.page_versions.add(BaseVersions.blank(page=page, size=size, full=full, moment=moment))
        if await self._labels.recompute(project_id):
            page = await self._uow.pages.get(page.id)
        overview = await self._overview(page)
        await self._finish(project_id, [page], PageChange.ADDED)
        if blank:
            await self._enqueue_prepare(project_id)
        return overview

    async def add_many(self, actor: Actor, project_id: ProjectId, new_pages: Sequence[NewPage]) -> list[PageOverview]:
        """Add placeholders and blank leaves in one transaction, announced by one event, or add none of them.

        A page with an anchor goes to that place, and a page without one goes after the page before it in the list, or
        at the end of the book when it is the first. The pages that stand in one place are given their keys by one
        call of the key builder, in the order listed, so the book is read once for each place and never for each page.
        Two places that lie in the same gap between neighbours are not told apart, and the second is refused by the
        unique key of the order. The median size of the book is read once, for all the blank leaves without a size.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param new_pages: The pages to add, in the order they are to stand in where they share a place.
        :type new_pages: Sequence[NewPage]
        :returns: The new pages with their positions, in the order listed.
        :rtype: list[PageOverview]
        :raises NotFoundError: If the actor has no such project.
        :raises ConflictError: If there are more pages than ``MAX_PAGES_PER_BATCH``, if the anchor of a page is not a
                               page of the project, or if a blank leaf has no size and no page of the book has one to
                               take the median of. The message of the last two names the index of the page.
        """
        if len(new_pages) > self.MAX_PAGES_PER_BATCH:
            raise ConflictError(BATCH_TOO_LONG.format(limit=self.MAX_PAGES_PER_BATCH))
        project = await owned_project(self._uow.projects, actor, project_id)
        if not new_pages:
            return []

        median = None
        sizeless = (i for i, page in enumerate(new_pages) if page.origin is NewPageOrigin.BLANK and page.size is None)
        if (lacking := next(sizeless, None)) is not None:
            try:
                median = await self._leaf_size(project_id)
            except ConflictError as error:
                raise ConflictError(PAGE_OF_BATCH.format(index=lacking, reason=error)) from error

        # A page without an anchor stands after the page before it, so it joins that page's place
        places: dict[PageAnchor | None, list[int]] = {}
        place = None
        for index, new_page in enumerate(new_pages):
            place = new_page.anchor or place
            places.setdefault(place, []).append(index)

        moment = self._clock.now()
        pages: dict[int, Page] = {}
        for anchor, indexes in places.items():
            try:
                keys = await self._keys_at(project_id, anchor, count=len(indexes))
            except NotFoundError as error:
                raise ConflictError(PAGE_OF_BATCH.format(index=indexes[0], reason=ANCHOR_NOT_IN_BOOK)) from error
            pages.update(
                (i, self._new_page(project_id, new_pages[i], key, moment)) for i, key in zip(indexes, keys, strict=True)
            )
        added = [pages[index] for index in range(len(new_pages))]
        await self._uow.pages.add_many(added)

        full = project.image_policy.full_format(ColorMode.BILEVEL)
        leaves = []
        for page, new_page in zip(added, new_pages, strict=True):
            # The median was read above for every blank leaf without a size, so a leaf always has one
            if new_page.origin is NewPageOrigin.BLANK and (size := new_page.size or median) is not None:
                leaves.append(BaseVersions.blank(page=page, size=size, full=full, moment=moment))
        await self._uow.page_versions.add_many(leaves)

        # The new pages take the numbers of their sections, which the overviews show
        if await self._labels.recompute(project_id):
            stored = {page.id: page for page in await self._uow.pages.list_by_ids(project_id, [p.id for p in added])}
            pages = {index: stored[page.id] for index, page in enumerate(added)}
        overviews: dict[PageId, PageOverview] = {}
        for indexes in places.values():
            run = [pages[index] for index in indexes]
            overviews.update(
                (o.page.id, o) for o in await self._overviews(run, await self._uow.pages.count_before(run[0]))
            )
        await self._finish(project_id, added, PageChange.ADDED)
        if leaves:
            await self._enqueue_prepare(project_id)
        return [overviews[page.id] for page in added]

    async def set_blank_fill(
        self, actor: Actor, project_id: ProjectId, page_ids: Collection[PageId], fill: BlankFill
    ) -> None:
        """Choose what the image of blank pages cut from a scan is: the scan, a white leaf or the paper of the book.

        A leaf is the pending version of the page order that ``pages.blank`` makes, of the size of the pages of the
        book, and the ``prepare-pages`` job writes it, after which it becomes the current version of the page order and
        the stages after it read it. A leaf of the paper takes the colour of the nearest pages of text. Choosing the
        scan removes the record of the page order, so the page shows its scan again and runs through the stages as it
        did, and a leaf already made for the page is found again when it is chosen again. A page that has the choice
        already is left as it is, and when every page has it nothing is written. All the pages change in one
        transaction, or none of them.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_ids: Pages to change, each at most once.
        :type page_ids: Collection[PageId]
        :param fill: What the image of the pages becomes.
        :type fill: BlankFill
        :raises NotFoundError: If the actor has no such project, or the project lacks one of the pages.
        :raises ConflictError: If a page is not cut from a scan, or a leaf is chosen for a page that is not blank, or no
                               page of the book has a recorded size to give the leaf.
        :raises ConcurrentChangeError: If another request changed one of the pages meanwhile, which changes none.
        """
        project = await owned_project(self._uow.projects, actor, project_id)
        found = {page.id: page for page in await self._uow.pages.list_by_ids(project_id, page_ids)}
        if missing := next((page_id for page_id in page_ids if page_id not in found), None):
            raise NotFoundError(missing)
        for page in found.values():
            if page.origin is not PageOrigin.SCAN or (fill is BlankFill.SCAN and page.scan_id is None):
                raise ConflictError(NOT_A_CUT_PAGE.format(page_id=page.id))
            if fill is not BlankFill.SCAN and page.kind is not PageKind.BLANK:
                raise ConflictError(NOT_A_BLANK_PAGE.format(page_id=page.id))
        changing = [page for page in found.values() if page.blank_fill is not fill]
        if not changing:
            return

        moment = self._clock.now()
        size = None if fill is BlankFill.SCAN else await self._leaf_size(project_id)
        full = project.image_policy.full_format(ColorMode.BILEVEL)
        await self._uow.pages.update_many([evolve(page, blank_fill=fill, updated_at=moment) for page in changing])
        records: list[PageStage] = []
        pending = False
        for page in changing:
            key = PageStageKey(page.id, Stage.PAGE_ORDER)
            if size is None:
                records.extend(await self._records.clear(key))
                continue
            sources = await self._paper_sources(page) if fill is BlankFill.PAPER else None
            leaf = BaseVersions.blank(page=page, size=size, full=full, moment=moment, paper_from=sources)
            made = await self._uow.page_versions.find(leaf.id)
            if made is not None and made.state is VersionState.READY and not made.files_removed:
                # The same leaf was made before, so it is current again without the job
                records.extend(await self._records.set_head(key, head_version_id=made.id, recipe_id=None))
                continue
            if made is None:
                await self._uow.page_versions.add(leaf)
            pending = True
        await self._finish(project_id, changing, PageChange.EDITED)
        await self._records.announce(project_id, records)
        if pending:
            await self._enqueue_prepare(project_id)

    async def delete(self, actor: Actor, project_id: ProjectId, page_id: PageId) -> None:
        """Delete a page with its versions, and then its files, leaving its scan and the scan's source.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        page = await self._page(project_id, page_id)
        await self._labels.recompute(project_id, leaving=[page.id])
        await self._uow.pages.delete(page.id)
        await self._uow.commit()
        await self._discard_files(project_id, [page])
        await self._announce(project_id, [page], PageChange.REMOVED)

    async def attach_scan(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, scan_id: ScanId, *, take_over: bool = False
    ) -> PageOverview:
        """Bind a scan to a placeholder, which becomes a page of that scan with the pending base version ``split.none``.

        The fields given to the placeholder stay, and its label is the scan's own label when it had none. A scan that
        another page shows already, such as the page the import made of it, is refused unless ``take_over`` is set,
        which deletes that page with its versions in the same transaction and its files after it. The request copies no
        image: the ``prepare-pages`` job does.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the placeholder.
        :type page_id: PageId
        :param scan_id: Identifier of the scan to bind.
        :type scan_id: ScanId
        :param take_over: Whether to take the scan from the pages that show it, deleting them.
        :type take_over: bool
        :returns: The page, which now shows the scan.
        :rtype: PageOverview
        :raises NotFoundError: If the actor has no such project, or the project has no such page or scan.
        :raises NotAPlaceholderError: If the page is not a placeholder.
        :raises ScanAlreadyInBookError: If another page shows the scan and ``take_over`` is not set, the error naming
                                        those pages by their printed number or position.
        :raises ConflictError: If the scan is not cut yet.
        :raises ConcurrentChangeError: If another request kept changing the page on every one of the attempts.
        """
        await owned_project(self._uow.projects, actor, project_id)
        # A change of the placeholder that is still being written, such as its printed number, commits between the read
        # and the write below. The rollback drops the stale read, and the next attempt reads the page afresh
        for _ in range(self.PAGE_WRITE_ATTEMPTS - 1):
            try:
                return await self._bind_scan(project_id, page_id, scan_id, take_over=take_over)
            except ConcurrentChangeError:
                await self._uow.rollback()
        return await self._bind_scan(project_id, page_id, scan_id, take_over=take_over)

    async def _bind_scan(
        self, project_id: ProjectId, page_id: PageId, scan_id: ScanId, *, take_over: bool
    ) -> PageOverview:
        """Read a placeholder and a scan, bind them and commit, once.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the placeholder.
        :type page_id: PageId
        :param scan_id: Identifier of the scan to bind.
        :type scan_id: ScanId
        :param take_over: Whether to take the scan from the pages that show it, deleting them.
        :type take_over: bool
        :returns: The page, which now shows the scan.
        :rtype: PageOverview
        :raises NotFoundError: If the project has no such page or scan.
        :raises NotAPlaceholderError: If the page is not a placeholder.
        :raises ScanAlreadyInBookError: If another page shows the scan and ``take_over`` is not set.
        :raises ConflictError: If the scan is not cut yet.
        :raises ConcurrentChangeError: If another request changed the page after it was read.
        """
        placeholder = await self._page(project_id, page_id)
        if placeholder.origin is not PageOrigin.PLACEHOLDER:
            raise NotAPlaceholderError
        scan = await self._uow.scans.get(scan_id)
        # A scan of another project is reported like a missing one, as a page of another project is
        if scan.project_id != project_id:
            raise NotFoundError(scan_id)
        if not scan.renditions.ready:
            raise ConflictError(SCAN_NOT_CUT)
        taken = await self._uow.pages.list_for_scan(scan_id)
        if taken and not take_over:
            raise ScanAlreadyInBookError(
                [
                    f'p. {page.label}' if page.label else f'position {await self._uow.pages.count_before(page) + 1}'
                    for page in taken
                ]
            )
        if taken:
            # Renumbering may write the placeholder too, so it is read again for the write below
            await self._labels.recompute(project_id, leaving=[page.id for page in taken])
            placeholder = await self._page(project_id, page_id)
        for page in taken:
            await self._uow.pages.delete(page.id)
        moment = self._clock.now()
        # The label of the scan is the one its source printed, so it is an exception of the sections, like one typed
        page = evolve(
            placeholder,
            origin=PageOrigin.SCAN,
            scan_id=scan_id,
            slot=Page.WHOLE_SCAN,
            label=placeholder.label or scan.source_label,
            label_manual=placeholder.label_manual or (not placeholder.label and bool(scan.source_label)),
            updated_at=moment,
        )
        await self._uow.pages.update(page)
        await self._uow.page_versions.add(
            BaseVersions.split_none(page=page, scan=scan, state=VersionState.PENDING, moment=moment)
        )
        overview = await self._overview(page)
        await self._uow.commit()
        await self._discard_files(project_id, taken)
        if taken:
            await self._announce(project_id, taken, PageChange.REMOVED)
        await self._announce(project_id, [page], PageChange.EDITED)
        await self._enqueue_prepare(project_id)
        return overview

    async def prepare_images(self, job_id: JobId) -> None:
        """Run a ``prepare-pages`` job a worker took from the queue: write the files of every pending or failed version.

        The versions of the page split and the page order stages are read when the job starts, and each is written and
        committed on its own, so the viewer swaps an empty frame for the image of a page as soon as it is ready. A
        version that fails is stored as failed with its reason, the job goes on, and it ends failed if any version did,
        which the next job of the project takes up again. A job that was cancelled stops before its next version.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        job = await self._uow.jobs.get(job_id)
        if job.state.is_final:
            return
        if job.state is JobState.QUEUED:
            started = evolve(job, state=JobState.RUNNING, started_at=self._clock.now())
            if (running := await self._uow.jobs.update_if_state(started, expected=(JobState.QUEUED,))) is None:
                # The job left the queue since it was read: another delivery started it, or it was cancelled
                await self._uow.rollback()
                return
            job = running
            await self._uow.commit()
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))
        try:
            outcome = await self._prepare_all(job)
        except Exception:
            # A job left running would keep the project from ever queueing another, so it ends failed whatever
            # went wrong
            logger.exception('The prepare-pages job %s stopped', job_id)
            await self._uow.rollback()
            await self._conclude(job, JobState.FAILED, UNEXPECTED_FAILURE)
            return
        if outcome is None:
            return
        job, failed, total = outcome
        if failed:
            await self._conclude(job, JobState.FAILED, PAGES_FAILED.format(count=failed), total=total)
        else:
            await self._conclude(job, JobState.SUCCEEDED, '', total=total)
        await self._follow_up(job.project_id)

    async def _prepare_all(self, job: Job) -> tuple[Job, int, int] | None:
        """Prepare every pending and failed version of the job's project, recording the progress as it goes.

        :param job: The running job.
        :type job: Job
        :returns: The job as last stored, the number of versions that failed and the number the job went through, or
                  None when the job was cancelled, which stops it before its next version.
        :rtype: tuple[Job, int, int] | None
        """
        versions = await self._uow.page_versions.list_to_prepare(job.project_id, PREPARED_PROCESSORS)
        failed = 0
        for done, version in enumerate(versions):
            progress = Progress(done=done, total=len(versions))
            # The write is guarded by the state, so it is also the check that the job was not cancelled
            saved = await self._uow.jobs.update_if_state(evolve(job, progress=progress), expected=(JobState.RUNNING,))
            if saved is None:
                await self._uow.rollback()
                return None
            await self._uow.commit()
            job = saved
            failed += not await self._prepare(version)
        return job, failed, len(versions)

    async def _conclude(self, job: Job, state: JobState, error: str, *, total: int | None = None) -> None:
        """Store the final state of a running job, unless it was cancelled meanwhile, and announce it.

        :param job: The job as last stored by this run.
        :type job: Job
        :param state: The final state.
        :type state: JobState
        :param error: Why the job failed, or empty.
        :type error: str
        :param total: Number of versions the job went through, which becomes its complete progress, or None to keep the
                      progress it has.
        :type total: int | None
        """
        progress = job.progress if total is None else Progress(done=total, total=total)
        final = evolve(job, state=state, error=error, progress=progress, finished_at=self._clock.now())
        stored = await self._uow.jobs.update_if_state(final, expected=(JobState.RUNNING,))
        await self._uow.commit()
        if stored is not None:
            await self._publisher.publish(JobChanged(project_id=stored.project_id, job=stored))

    async def move(self, actor: Actor, project_id: ProjectId, page_id: PageId, anchor: PageAnchor) -> PageOverview:
        """Put one page before or after another, and renumber the pages whose number the sections now give differently.

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
        :raises ConcurrentChangeError: If another request changed the page meanwhile.
        """
        await owned_project(self._uow.projects, actor, project_id)
        [moved] = await self._place(project_id, [await self._page(project_id, page_id)], anchor)
        await self._uow.pages.update(moved)
        if await self._labels.recompute(project_id):
            moved = await self._uow.pages.get(moved.id)
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
        :raises ConcurrentChangeError: If another request changed one of the pages meanwhile, which moves none.
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
        :raises ConcurrentChangeError: If another request changed one of the pages meanwhile, which moves none.
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
        current = await self._current_images(pages)
        scans = await self._uow.scans.list_by_ids({page.scan_id for page in pages if page.scan_id is not None})
        sources = {scan.id: scan.source_id for scan in scans}
        return [
            PageOverview(
                page=page,
                position=first_position + index,
                image_version=current.get(page.id, newest.get(page.id)),
                source_id=sources.get(page.scan_id) if page.scan_id is not None else None,
            )
            for index, page in enumerate(pages)
        ]

    async def _current_images(self, pages: Sequence[Page]) -> Mapping[PageId, PageVersion]:
        """Find the version each page shows: the current version of the latest stage that has an image.

        A page whose stages have no current version yet, such as a leaf waiting for its image, is left out, and the
        caller falls back to its base version.

        :param pages: Pages of one project.
        :type pages: Sequence[Page]
        :returns: The version to show by page identifier.
        :rtype: Mapping[PageId, PageVersion]
        """
        records = await self._uow.page_stages.list_for_pages([page.id for page in pages])
        head_ids = {record.head_version_id for record in records if record.head_version_id is not None}
        heads = {version.id: version for version in await self._uow.page_versions.list_by_ids(head_ids)}
        shown: dict[PageId, PageVersion] = {}
        # The records come in the order of the stages, so the latest stage that has an image wins
        for record in records:
            head = None if record.head_version_id is None else heads.get(record.head_version_id)
            if head is not None and head.renditions is not None and head.renditions.ready:
                shown[record.page_id] = head
        return shown

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

    def _new_page(self, project_id: ProjectId, new_page: NewPage, key: str, moment: datetime) -> Page:
        """Build the stored form of a page the user adds, with its order key.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param new_page: The page to add.
        :type new_page: NewPage
        :param key: Order key that puts the page in its place.
        :type key: str
        :param moment: Time of the request, which creates and last changes the page.
        :type moment: datetime
        :returns: The page, not yet stored.
        :rtype: Page
        """
        return Page(
            id=PageId(uuid4()),
            project_id=project_id,
            order_key=key,
            label=new_page.label,
            label_manual=bool(new_page.label),
            kind=new_page.kind,
            origin=new_page.origin.page_origin,
            notes=new_page.notes,
            created_at=moment,
            updated_at=moment,
        )

    async def _place(self, project_id: ProjectId, pages: Sequence[Page], anchor: PageAnchor) -> list[Page]:
        """Give pages new order keys that put them in a run next to the anchor page, in the order given.

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
        keys = await self._keys_at(project_id, anchor, count=len(pages), moving={page.id for page in pages})
        moment = self._clock.now()
        return [evolve(page, order_key=key, updated_at=moment) for page, key in zip(pages, keys, strict=True)]

    async def _keys_at(
        self, project_id: ProjectId, anchor: PageAnchor | None, *, count: int, moving: Collection[PageId] = ()
    ) -> Sequence[str]:
        """Make order keys for ``count`` pages that stand in a run at the anchor, or at the end of the book.

        The key of the page next to the place is found with the moving pages left out, so a page that already stands
        beside the anchor gets a key between the same neighbours it would have had if it were elsewhere.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param anchor: Page the run is put next to, and the side of it, or None for the end of the book.
        :type anchor: PageAnchor | None
        :param count: Number of keys.
        :type count: int
        :param moving: Pages that are about to leave their place, which do not count as neighbours.
        :type moving: Collection[PageId]
        :returns: The keys in ascending order.
        :rtype: Sequence[str]
        :raises NotFoundError: If the anchor is not a page of the project.
        :raises ConflictError: If the anchor is one of the moving pages, since the place is then not defined.
        """
        if anchor is None:
            lower, upper = await self._uow.pages.last_order_key(project_id), None
        else:
            if anchor.page_id in moving:
                raise AnchorInsideMovedPagesError
            target = await self._page(project_id, anchor.page_id)
            neighbour = await self._uow.pages.neighbour_key(project_id, target.order_key, anchor.side, excluding=moving)
            lower, upper = (
                (neighbour, target.order_key) if anchor.side is Side.BEFORE else (target.order_key, neighbour)
            )
        return self._order_keys.spread(lower=lower, upper=upper, count=count)

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
        await self._labels.recompute(project_id)
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
        await self._announce(project_id, pages, change)

    async def _announce(self, project_id: ProjectId, pages: Sequence[Page], change: PageChange) -> None:
        """Tell the browser which pages changed, which it reads the manifest again for.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param pages: Pages the change touched.
        :type pages: Sequence[Page]
        :param change: What was done to them.
        :type change: PageChange
        """
        await self._publisher.publish(
            PagesChanged(project_id=project_id, page_ids=[page.id for page in pages], change=change)
        )
        await self._labels.announce(project_id)

    async def _discard_files(self, project_id: ProjectId, pages: Sequence[Page]) -> None:
        """Remove every file of pages that were deleted, after the transaction that deleted them committed.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param pages: The deleted pages, whose directories under ``assets/pages`` are removed.
        :type pages: Sequence[Page]
        """
        keys = ProjectKeys(project_id)
        for page in pages:
            await self._assets.delete_prefix(keys.page(page.id))

    async def _enqueue_prepare(self, project_id: ProjectId) -> None:
        """Queue a ``prepare-pages`` job for the project, unless one is queued or running already.

        A project has one such job at a time, so two jobs never write the files of one version together. The check is
        a read and the insert a second step, so the unique index of the database decides when two requests pass the
        check together, and the one that loses finds the job of the other. A job that is running when a version is
        committed may have read its versions before it, which is why the job looks again when it ends, in
        ``_follow_up``.

        The page is committed already, so a queue that refuses the job does not fail the request: the job is stored
        as failed, announced, and logged, which frees the project for the next job, and the version stays pending
        until that job takes it.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        """
        active = await self._uow.jobs.list_for_project(project_id, JobState.active())
        if any(job.kind is JobKind.PREPARE_PAGES for job in active):
            return
        job = Job(id=JobId(uuid4()), project_id=project_id, kind=JobKind.PREPARE_PAGES, created_at=self._clock.now())
        try:
            await self._uow.jobs.add(job)
            await self._uow.commit()
        except ConflictError:
            await self._uow.rollback()
            return
        await self._publisher.publish(JobChanged(project_id=project_id, job=job))
        try:
            await self._queue.enqueue(job)
        except Exception:
            logger.exception('The prepare-pages job %s could not be queued', job.id)
            failed = evolve(job, state=JobState.FAILED, error=NOT_QUEUED, finished_at=self._clock.now())
            stored = await self._uow.jobs.update_if_state(failed, expected=(JobState.QUEUED,))
            await self._uow.commit()
            if stored is not None:
                await self._publisher.publish(JobChanged(project_id=project_id, job=stored))

    async def _follow_up(self, project_id: ProjectId) -> None:
        """Queue another job when a version was committed while the job that just ended was running.

        A request that commits a version while a job runs finds the job active and queues none, and the job may have
        read its versions before that one. Here the job has ended, so a pending version that is left was committed
        after the job read its versions, and it gets a job of its own. A version that failed is not pending, so a
        version that cannot be made does not keep a job running again.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        """
        waiting = await self._uow.page_versions.list_to_prepare(project_id, PREPARED_PROCESSORS)
        if any(version.state is VersionState.PENDING for version in waiting):
            await self._enqueue_prepare(project_id)

    async def _median_size(self, project_id: ProjectId) -> PageSize:
        """Return the size of a blank leaf that stands level with the pages of the book.

        The width and the height are the medians of those of the included pages cut from a scan, taken apart, and the
        resolution is the median of theirs, or unknown when no page has one. The median does not follow one fold-out
        map or one cropped scan.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The median size.
        :rtype: PageSize
        :raises ConflictError: If no page of the book has a recorded size yet.
        """
        if not (sizes := await self._uow.page_versions.base_sizes(project_id)):
            raise ConflictError(NO_BOOK_IMAGE)
        resolutions = [size.dpi for size in sizes if size.dpi is not None]
        return PageSize(
            width_px=round(statistics.median(size.width_px for size in sizes)),
            height_px=round(statistics.median(size.height_px for size in sizes)),
            dpi=round(statistics.median(resolutions), 1) if resolutions else None,
        )

    async def _leaf_size(self, project_id: ProjectId) -> PageSize:
        """Return the size of a blank leaf that stands level with the pages of the book.

        Once the book is measured, its pages are the size the active normalize step of the Geometry stage gives them,
        and a leaf has that size. Before that it is the median size of the pages cut from a scan.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The size of the pages of the book, with the median resolution.
        :rtype: PageSize
        :raises ConflictError: If no page of the book has a recorded size yet.
        """
        median = await self._median_size(project_id)
        recipe = await self._uow.recipes.find_active(project_id, Stage.GEOMETRY)
        step = (
            None
            if recipe is None
            else next((step for step in recipe.steps if step.enabled and step.processor_key == NORMALIZE_KEY), None)
        )
        if step is None:
            return median
        width, height = (step.params.get(name) for name in (NormalizeParam.PAGE_WIDTH, NormalizeParam.PAGE_HEIGHT))
        if isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0:
            return evolve(median, width_px=width, height_px=height)
        return median

    async def _paper_sources(self, page: Page) -> list[PageVersionId]:
        """Find the versions of the pages nearest to a leaf whose paper the leaf takes the colour of.

        The pages looked at are those of text in a window round the leaf: pages cut from a scan and part of the book,
        which are not leaves themselves and have an image that is ready. The nearest ones are taken, and a leaf with
        none has none to take, which makes it white.

        :param page: The page that becomes a leaf.
        :type page: Page
        :returns: The identifiers of the versions the pages show.
        :rtype: list[PageVersionId]
        """
        position = await self._uow.pages.count_before(page)
        first = max(0, position - self.PAPER_RADIUS)
        window = await self._uow.pages.list_for_project(
            page.project_id, SliceRequest(offset=first, limit=position - first + self.PAPER_RADIUS + 1)
        )
        near = sorted(
            (
                overview
                for overview in await self._overviews(window.items, first)
                if overview.page.id != page.id
                and overview.page.included
                and not overview.page.is_leaf
                and overview.page.kind in self.PAPER_KINDS
                and overview.image_version is not None
                and overview.image_version.renditions is not None
                and overview.image_version.renditions.ready
            ),
            key=lambda overview: abs(overview.position - position),
        )
        return [overview.image_version.id for overview in near[: self.PAPER_PAGES] if overview.image_version]

    async def _prepare(self, version: PageVersion) -> bool:
        """Write the files of one pending or failed version, and store its new state.

        A version that cannot be made is stored as failed with the reason in its data, and the job goes on with the
        others. A page deleted while the job runs takes its versions with it, so a version whose page is gone, before
        or after its files were written, is nothing to make and nothing that failed: the files written for it are
        removed and the job goes on.

        :param version: A pending or failed base version.
        :type version: PageVersion
        :returns: False when the version failed, and True when it is ready or its page is gone.
        :rtype: bool
        """
        try:
            page = await self._uow.pages.get(version.page_id)
        except NotFoundError:
            return True
        # A leaf whose fill is not the choice of its page any longer, such as one of a page that got its scan back, is
        # nothing to make, and left pending it would be taken by every job
        if (
            version.processor == PAGES_BLANK
            and page.origin is PageOrigin.SCAN
            and page.blank_fill.value != version.params.get(BlankParam.FILL, PaperFill.WHITE.value)
        ):
            await self._uow.page_versions.delete_many([version.id])
            await self._uow.commit()
            return True
        try:
            made = await self._write_files(version, page)
        except DomainError as error:
            reason = str(error)
        except Exception:
            logger.exception('The files of page version %s could not be made', version.id)
            reason = UNEXPECTED_FAILURE
        else:
            if await self._store(page, made):
                changed = await self._records.set_head(
                    PageStageKey(page.id, made.stage), head_version_id=made.id, recipe_id=None
                )
                await self._uow.commit()
                await self._publisher.publish(PageVersionReady(project_id=page.project_id, version=made))
                await self._records.announce(page.project_id, changed)
            return True
        failed = evolve(version, data={**version.data, VersionData.ERROR: reason}, state=VersionState.FAILED)
        return not await self._store(page, failed)

    async def _store(self, page: Page, version: PageVersion) -> bool:
        """Store the new state of a version of a page, unless the page was deleted while its files were written.

        :param page: The page the version belongs to, as read before its files were written.
        :type page: Page
        :param version: The version with its new state.
        :type version: PageVersion
        :returns: True when the version is stored, and False when its page is gone, whose files are removed then.
        :rtype: bool
        """
        try:
            await self._uow.page_versions.update(version)
        except NotFoundError:
            await self._uow.rollback()
            await self._discard_files(page.project_id, [page])
            return False
        await self._uow.commit()
        return True

    async def _write_files(self, version: PageVersion, page: Page) -> PageVersion:
        """Run the processor of a base version and write its files: a copy of its scan, or a generated white leaf.

        :param version: A pending or failed base version.
        :type version: PageVersion
        :param page: The page the version belongs to.
        :type page: Page
        :returns: The version as ready, with its transform, data and renditions.
        :rtype: PageVersion
        :raises ConflictError: If the scan to copy is deleted or not cut yet.
        :raises ValueError: If the version is neither ``split.none`` nor ``pages.blank``, or has no size to make.
        """
        project = await self._uow.projects.get(page.project_id)
        keys = ProjectKeys(project.id)
        if version.processor == SPLIT_NONE:
            if page.scan_id is None:
                raise ConflictError(SCAN_DELETED)
            scan = await self._uow.scans.get(page.scan_id)
            if not scan.renditions.ready:
                raise ConflictError(SCAN_NOT_CUT)
            run = StepRun(
                processor_key=SPLIT_NONE.key,
                params=version.params,
                input_data=scan.facts.as_data(),
                image=keys.scan_rendition(scan, scan.renditions.full),
            )
        elif version.processor == PAGES_BLANK and PageSize.from_data(version.data) is not None:
            sources = await self._uow.page_versions.list_by_ids(version.params.get(BlankParam.PAPER_FROM, []))
            run = StepRun(
                processor_key=PAGES_BLANK.key,
                params=version.params,
                input_data={},
                references=[
                    keys.version_rendition(source, source.renditions.full)
                    for source in sources
                    if source.renditions is not None and source.renditions.ready and not source.files_removed
                ],
            )
        else:
            err_msg = f'Page version {version.id} made by {version.processor.key} has no files to write.'
            raise ValueError(err_msg)
        async with self._runner.execute(run) as result:
            return await self._runner.store(keys, version, result.outputs[0], policy=project.image_policy, tiles=True)
