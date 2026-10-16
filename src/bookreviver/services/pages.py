"""Use cases of the pages of a book: its manifest, one page by identifier, moving pages, and the files of their images.

A page is addressed by its ``PageId`` and never by its number, because its position changes when pages are moved. The
position is not stored: the service computes it when it reads a page, from the order keys of the project, and returns
it in a ``PageOverview`` together with the base version whose renditions show the page. The order key itself stays
inside the persistence adapter's ordering, so no client ever sees it.

Moving a page writes a new order key to the moved pages alone: the key lies between the key of the page next to the
place and the key of the page after it, which the repository finds while leaving the moved pages out, so the pages that
stay never change. A group of pages, or every page of one source, is placed as one run that keeps its order in the
book. Every change of a book is the work of one ``change_book``, which waits for any other change of that book, and
publishes one ``PagesChanged`` event after the transaction, naming all the pages it touched. A change that adds,
deletes or moves pages, or changes the kind or the inclusion of one, also recomputes the labels the pagination sections
give the pages, in the same work, and announces the pages renumbered as a second ``PagesChanged``. The work is
synchronous and holds the repositories of its transaction, so the events, the files and the queue stay outside it. A
job, such as the one that writes the images of the pages, reads and computes outside any transaction, and stores its
result in a short ``change_book`` that reads again what the result depends on. A job queued after a change is queued in
a ``change`` of its own, after the transaction of the change.

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
from functools import partial
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
    RecipeKind,
    Side,
    Stage,
    VersionData,
    VersionState,
)
from bookreviver.domain.errors import (
    AnchorInsideMovedPagesError,
    ConflictError,
    DomainError,
    NotAPlaceholderError,
    NotFoundError,
    ScanAlreadyInBookError,
)
from bookreviver.domain.events import JobChanged, PagesChanged, PageVersionReady
from bookreviver.domain.ids import JobId, PageId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.pagination import Pagination
from bookreviver.domain.values import ContentDetection, PageSize, PageStageKey, Progress, Slice, SliceRequest
from bookreviver.services.base_versions import PAGES_BLANK, SPLIT_NONE, BaseVersions
from bookreviver.services.book_measure import NORMALIZE_KEY
from bookreviver.services.page_labels import PageLabels
from bookreviver.services.projects import book_pages, owned_page, owned_project, require_owner
from bookreviver.services.stage_records import StageRecords
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from bookreviver.domain.changes import PageChanges
    from bookreviver.domain.entities import Actor, PageStage, PageVersion, Project
    from bookreviver.domain.ids import PageVersionId, PaginationSectionId, ProjectId, ScanId, SourceId, StorageKey
    from bookreviver.domain.values import MetadataMap, NewPage, PageAnchor
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import Repositories, UnitOfWork
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
DETECTION_NOT_QUEUED: str = 'The detection of the content of the pages could not be queued. Ask for it again.'
DETECTION_RUNNING: str = 'The content of the pages is being detected. Wait for it to end, or cancel it.'
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


@frozen(kw_only=True)
class StoodLeaves:
    """What giving pages the image their choice says changed, which the use case announces after its transaction.

    :ivar pages: The pages whose choice was given effect to.
    :ivar records: The stage records that changed.
    :ivar pending: Whether a leaf waits for the ``prepare-pages`` job.
    """

    pages: Sequence[Page]
    records: Sequence[PageStage]
    pending: bool


class PageService:
    """Pages of the acting account's projects, addressed by identifier, their order, and access to their files.

    :cvar MAX_PAGES_PER_BATCH: Most pages one call of ``add_many`` adds, which bounds the rows of one transaction.
    :cvar PAPER_KINDS: Kinds of page whose paper a leaf may take, which are the pages printed on the paper of the book.
    :cvar PAPER_RADIUS: How many pages on each side of a leaf are looked at for pages to take the paper from.
    :cvar PAPER_PAGES: Most pages a leaf takes its paper from, the nearest ones.
    """

    MAX_PAGES_PER_BATCH: ClassVar[int] = 1000
    PAPER_KINDS: ClassVar[frozenset[PageKind]] = frozenset({PageKind.TEXT, PageKind.TITLE})
    PAPER_RADIUS: ClassVar[int] = 10
    PAPER_PAGES: ClassVar[int] = 6

    def __init__(self, *, uow: UnitOfWork, assets: AssetStore, runtime: PageRuntime, imaging: PageImaging) -> None:
        """Work over the ports of one request or job.

        :param uow: Unit of work of the request, whose ``read`` serves the pages and versions and whose ``change_book``
                    ends every use case that changes pages.
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
        self._records = StageRecords(publisher=runtime.publisher, clock=runtime.clock)
        self._labels = PageLabels(publisher=runtime.publisher, clock=runtime.clock)

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

        def work(repos: Repositories) -> Slice[PageOverview]:
            """Read the window of pages with the position and the base version of each.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The pages of the window and the number of the pages listed.
            :rtype: Slice[PageOverview]
            :raises NotFoundError: If the actor has no such project.
            """
            owned_project(repos.projects, actor, project_id)
            window = repos.pages.list_for_project(project_id, request, included_only=included_only)
            return Slice(items=self._overviews(repos, window.items, request.offset), total=window.total)

        return await self._uow.read(work)

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

        def work(repos: Repositories) -> PageOverview:
            """Read the page of the actor's project with its position and base version.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The page, its position in the book and its base version.
            :rtype: PageOverview
            :raises NotFoundError: If the actor has no such project, or the project has no such page.
            """
            return self._overview(repos, owned_page(repos, actor, project_id, page_id))

        return await self._uow.read(work)

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
        """

        def work(repos: Repositories, project: Project) -> tuple[PageOverview, list[PageStage]]:
            """Write the changed page, mark the stages its change makes stale, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The changed page with its position, and the stage records that became stale.
            :rtype: tuple[PageOverview, list[PageStage]]
            :raises NotFoundError: If the actor has no such project, or the project has no such page.
            """
            require_owner(project, actor)
            before = self._page(repos, project_id, page_id)
            page = changes.apply_to(before)
            changed = repos.pages.update(evolve(page, updated_at=self._clock.now()))
            # A page that stops being blank gets its scan back in place of the leaf, so the leaf is no longer current
            records = (
                self._records.clear(repos, PageStageKey(page_id, Stage.PAGE_ORDER))
                if before.blank_fill is not BlankFill.SCAN and page.blank_fill is BlankFill.SCAN
                else []
            )
            # The kind of the page decides which recipe of the stages after the page order processes it
            if before.recipe_kind is not page.recipe_kind:
                records.extend(self._records.mark_content_stale(repos, page_id))
            # A page takes the values its group has for a step, so joining or leaving a group changes what it runs with
            if before.group_label != page.group_label:
                records.extend(
                    self._records.mark_group_stale(repos, project_id, page_id, {before.group_label, page.group_label})
                )
            # A label, a kind or an inclusion changes what the sections give the pages, the page itself included
            if any(field is not None for field in (changes.label, changes.kind, changes.included)) and (
                self._labels.recompute(repos, project_id)
            ):
                changed = repos.pages.get(page_id)
            return self._overview(repos, changed), records

        overview, records = await self._uow.change_book(project_id, work)
        await self._announce(project_id, [overview.page], PageChange.EDITED)
        await self._records.announce(project_id, records)
        return overview

    async def start_detection(self, actor: Actor, project_id: ProjectId, page_ids: Sequence[PageId] | None) -> Job:
        """Record a job that detects what some pages show, and queue it.

        The job writes the content type it finds into the pages it goes over, which gives a page the user set by hand
        back to the detection.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_ids: Pages to detect, or None for the pages that have no content type yet.
        :type page_ids: Sequence[PageId] | None
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        :raises ConflictError: If the content of the pages of the project is being detected already.
        """

        def check(repos: Repositories) -> None:
            """Require the project to be the actor's and the pages to be its own.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :raises NotFoundError: If the actor has no such project, or the project has no such page.
            """
            owned_project(repos.projects, actor, project_id)
            if page_ids is not None:
                repos.pages.list_by_ids(project_id, page_ids)

        await self._uow.read(check)
        detection = ContentDetection(page_ids=None if page_ids is None else tuple(page_ids))
        if (job := await self._enqueue(project_id, JobKind.DETECT_CONTENT, detection.to_map())) is None:
            raise ConflictError(DETECTION_RUNNING)
        return job

    async def detect_new_pages(self, project_id: ProjectId) -> None:
        """Queue the detection of the content of the pages that have none, which a worker asks for when images are made.

        Nothing is queued while a detection of the project is queued or running, which goes over the pages it finds.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        """
        await self._enqueue(project_id, JobKind.DETECT_CONTENT, ContentDetection().to_map())

    async def add(self, actor: Actor, project_id: ProjectId, new_page: NewPage) -> PageOverview:
        """Add a placeholder or a blank leaf at a place of the book, or at its end.

        The pages after it move down, and those that turn over to the other side of the book, odd or even, are marked
        stale where a side has values.

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
        blank = new_page.origin is NewPageOrigin.BLANK

        def work(repos: Repositories, project: Project) -> tuple[PageOverview, list[PageStage]]:
            """Store the page and, for a blank leaf, its pending base version, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The new page with its position, and the stage records of the pages that turned over to the other
                      side of the book.
            :rtype: tuple[PageOverview, list[PageStage]]
            :raises NotFoundError: If the actor has no such project, or the project has no such anchor.
            :raises ConflictError: If a blank leaf has no size given and no page of the book has one to take.
            """
            require_owner(project, actor)
            size = (new_page.size or self._leaf_size(repos, project_id)) if blank else None
            [key] = self._keys_at(repos, project_id, new_page.anchor, count=1)
            moment = self._clock.now()
            page = self._new_page(project_id, new_page, key, moment)
            with self._records.watching_sides(repos, project_id) as watch:
                repos.pages.add(page)
            if size is not None:
                full = project.image_policy.full_format(ColorMode.BILEVEL)
                repos.page_versions.add(BaseVersions.blank(page=page, size=size, full=full, moment=moment))
            if self._labels.recompute(repos, project_id):
                page = repos.pages.get(page.id)
            return self._overview(repos, page), watch.turned

        overview, turned = await self._uow.change_book(project_id, work)
        await self._announce(project_id, [overview.page], PageChange.ADDED)
        await self._records.announce(project_id, turned)
        if blank:
            await self._enqueue(project_id, JobKind.PREPARE_PAGES)
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

        def work(repos: Repositories, project: Project) -> tuple[list[PageOverview], list[PageStage]] | None:
            """Store the pages and the pending base versions of the blank leaves, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The new pages with their positions in the order listed, and the stage records of the pages that
                      turned over to the other side of the book, or None when no page was given.
            :rtype: tuple[list[PageOverview], list[PageStage]] | None
            :raises NotFoundError: If the actor has no such project.
            :raises ConflictError: If the anchor of a page is not a page of the project, or a blank leaf has no size and
                                   no page of the book has one to take the median of.
            """
            require_owner(project, actor)
            if not new_pages:
                return None

            median = None
            sizeless = (
                i for i, page in enumerate(new_pages) if page.origin is NewPageOrigin.BLANK and page.size is None
            )
            if (lacking := next(sizeless, None)) is not None:
                try:
                    median = self._leaf_size(repos, project_id)
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
                    keys = self._keys_at(repos, project_id, anchor, count=len(indexes))
                except NotFoundError as error:
                    raise ConflictError(PAGE_OF_BATCH.format(index=indexes[0], reason=ANCHOR_NOT_IN_BOOK)) from error
                pages.update(
                    (i, self._new_page(project_id, new_pages[i], key, moment))
                    for i, key in zip(indexes, keys, strict=True)
                )
            added = [pages[index] for index in range(len(new_pages))]
            with self._records.watching_sides(repos, project_id) as watch:
                repos.pages.add_many(added)

            full = project.image_policy.full_format(ColorMode.BILEVEL)
            leaves = []
            for page, new_page in zip(added, new_pages, strict=True):
                # The median was read above for every blank leaf without a size, so a leaf always has one
                if new_page.origin is NewPageOrigin.BLANK and (size := new_page.size or median) is not None:
                    leaves.append(BaseVersions.blank(page=page, size=size, full=full, moment=moment))
            repos.page_versions.add_many(leaves)

            # The new pages take the numbers of their sections, which the overviews show
            if self._labels.recompute(repos, project_id):
                stored = {page.id: page for page in repos.pages.list_by_ids(project_id, [p.id for p in added])}
                pages = {index: stored[page.id] for index, page in enumerate(added)}
            overviews: dict[PageId, PageOverview] = {}
            for indexes in places.values():
                run = [pages[index] for index in indexes]
                overviews.update((o.page.id, o) for o in self._overviews(repos, run, repos.pages.count_before(run[0])))
            return [overviews[page.id] for page in added], watch.turned

        added = await self._uow.change_book(project_id, work)
        if added is None:
            return []
        overviews, turned = added
        await self._announce(project_id, [overview.page for overview in overviews], PageChange.ADDED)
        await self._records.announce(project_id, turned)
        if any(new_page.origin is NewPageOrigin.BLANK for new_page in new_pages):
            await self._enqueue(project_id, JobKind.PREPARE_PAGES)
        return overviews

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
        """

        def work(repos: Repositories, project: Project) -> StoodLeaves | None:
            """Write the choice of every page that changes it, and give effect to it.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The pages that changed with the records and the pending leaves, or None when every page has the
                      choice already.
            :rtype: StoodLeaves | None
            :raises NotFoundError: If the actor has no such project, or the project lacks one of the pages.
            :raises ConflictError: If a page is not cut from a scan, or a leaf is chosen for a page that is not blank,
                                   or no page of the book has a recorded size to give the leaf.
            """
            require_owner(project, actor)
            found = {page.id: page for page in repos.pages.list_by_ids(project_id, page_ids)}
            if missing := next((page_id for page_id in page_ids if page_id not in found), None):
                raise NotFoundError(missing)
            for page in found.values():
                if page.origin is not PageOrigin.SCAN or (fill is BlankFill.SCAN and page.scan_id is None):
                    raise ConflictError(NOT_A_CUT_PAGE.format(page_id=page.id))
                if fill is not BlankFill.SCAN and page.kind is not PageKind.BLANK:
                    raise ConflictError(NOT_A_BLANK_PAGE.format(page_id=page.id))
            changing = [page for page in found.values() if page.blank_fill is not fill]
            if not changing:
                return None

            moment = self._clock.now()
            changed = [evolve(page, blank_fill=fill, updated_at=moment) for page in changing]
            repos.pages.update_many(changed)
            return self._stand_leaves(repos, project, changed)

        if (stood := await self._uow.change_book(project_id, work)) is not None:
            await self._announce_leaves(project_id, stood)

    async def remake_outdated_leaves(self) -> None:
        """Make again the leaves whose version of ``pages.blank`` was replaced, by the path that made them.

        A leaf is identified by the version of the processor too, so a leaf made by an older version is another version
        than the one the current processor makes. Every page order record of every book whose current version is such a
        leaf is read, whatever its state, and its page gets the leaf of the current version as a pending version, which
        the ``prepare-pages`` job writes and makes the current version of the stage, which is fresh then. A page that
        has the pending leaf already keeps it, and the job is queued again, so a start that ended before the job ran is
        finished by the next one.

        The records are read in a transaction of their own, and each book is written in a ``change_book`` of its own,
        which reads its pages again. It is a pass of the start, before any request is served.
        """

        def read_projects(repos: Repositories) -> dict[ProjectId, list[PageId]]:
            """Group the pages whose page order is a replaced leaf by their projects.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The identifiers of the pages by the project that owns them.
            :rtype: dict[ProjectId, list[PageId]]
            """
            by_project: dict[ProjectId, list[PageId]] = {}
            for record in repos.page_stages.list_replaced(Stage.PAGE_ORDER, PAGES_BLANK):
                page = repos.pages.get(record.page_id)
                by_project.setdefault(page.project_id, []).append(page.id)
            return by_project

        def work(page_ids: Sequence[PageId], repos: Repositories, project: Project) -> StoodLeaves:
            """Give the pages of one book that no longer show their scan the leaf their choice says.

            :param page_ids: Identifiers of the pages whose page order was a replaced leaf.
            :type page_ids: Sequence[PageId]
            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The pages that got a leaf, the records that changed, and whether a leaf is pending.
            :rtype: StoodLeaves
            :raises ConflictError: If no page of the book has a recorded size to give a leaf.
            """
            pages = [
                page for page in repos.pages.list_by_ids(project.id, page_ids) if page.blank_fill is not BlankFill.SCAN
            ]
            return self._stand_leaves(repos, project, pages)

        for project_id, page_ids in (await self._uow.read(read_projects)).items():
            stood = await self._uow.change_book(project_id, partial(work, page_ids))
            if stood.pages:
                await self._announce_leaves(project_id, stood)

    def _stand_leaves(self, repos: Repositories, project: Project, pages: Sequence[Page]) -> StoodLeaves:
        """Give pages the image their choice says, and tell whether a job must write leaves that are not made yet.

        A page that shows the scan loses the record of its page order, so its scan is current again. A page that shows
        a leaf gets the pending version of the leaf, or the version that was made already, which is current again
        without the job. It writes through the repositories of the work of its caller, so the caller announces the
        result and queues the job after its transaction, which ``_announce_leaves`` does.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project: The project owning the pages.
        :type project: Project
        :param pages: The pages, each with the choice to give effect to as its ``blank_fill``.
        :type pages: Sequence[Page]
        :returns: The pages, the stage records that changed, and whether a leaf is pending.
        :rtype: StoodLeaves
        :raises ConflictError: If a leaf is chosen and no page of the book has a recorded size to give it.
        """
        moment = self._clock.now()
        leaves = any(page.blank_fill is not BlankFill.SCAN for page in pages)
        size = self._leaf_size(repos, project.id) if leaves else None
        full = project.image_policy.full_format(ColorMode.BILEVEL)
        records: list[PageStage] = []
        pending = False
        for page in pages:
            key = PageStageKey(page.id, Stage.PAGE_ORDER)
            if size is None or page.blank_fill is BlankFill.SCAN:
                records.extend(self._records.clear(repos, key))
                continue
            sources = self._paper_sources(repos, page) if page.blank_fill is BlankFill.PAPER else None
            leaf = BaseVersions.blank(page=page, size=size, full=full, moment=moment, paper_from=sources)
            made = repos.page_versions.find(leaf.id)
            if made is not None and made.state is VersionState.READY:
                # The same leaf was made before, so it is current again without the job
                records.extend(self._records.set_head(repos, key, head_version_id=made.id, recipe_id=None))
                continue
            if made is None:
                repos.page_versions.add(leaf)
            pending = True
        return StoodLeaves(pages=pages, records=records, pending=pending)

    async def _announce_leaves(self, project_id: ProjectId, stood: StoodLeaves) -> None:
        """Announce the pages whose image choice changed, and queue the job that writes the leaves that are pending.

        It queues the job in a ``change`` of its own, so it is called after the transaction that wrote the leaves.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stood: What ``_stand_leaves`` returned.
        :type stood: StoodLeaves
        """
        await self._announce(project_id, stood.pages, PageChange.EDITED)
        await self._records.announce(project_id, stood.records)
        if stood.pending:
            await self._enqueue(project_id, JobKind.PREPARE_PAGES)

    async def delete(self, actor: Actor, project_id: ProjectId, page_id: PageId) -> None:
        """Delete a page with its versions, and then its files, leaving its scan and the scan's source.

        The pages after it move up, and those that turn over to the other side of the book, odd or even, are marked
        stale where a side has values.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """

        def work(repos: Repositories, project: Project) -> tuple[Page, list[PageStage]]:
            """Renumber the pages without the page, and delete it.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The deleted page, and the stage records of the pages that turned over to the other side of the
                      book.
            :rtype: tuple[Page, list[PageStage]]
            :raises NotFoundError: If the actor has no such project, or the project has no such page.
            """
            require_owner(project, actor)
            page = self._page(repos, project_id, page_id)
            self._labels.recompute(repos, project_id, leaving=[page.id])
            with self._records.watching_sides(repos, project_id) as watch:
                repos.pages.delete(page.id)
            return page, watch.turned

        page, turned = await self._uow.change_book(project_id, work)
        await self._discard_files(project_id, [page])
        await self._announce(project_id, [page], PageChange.REMOVED)
        await self._records.announce(project_id, turned)

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
        """

        def work(repos: Repositories, project: Project) -> tuple[PageOverview, Sequence[Page], list[PageStage]]:
            """Delete the pages that show the scan when asked to, and bind the scan to the placeholder.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The page that shows the scan now, the pages that were deleted for it, and the stage records of the
                      pages that turned over to the other side of the book.
            :rtype: tuple[PageOverview, Sequence[Page], list[PageStage]]
            :raises NotFoundError: If the actor has no such project, or the project has no such page or scan.
            :raises NotAPlaceholderError: If the page is not a placeholder.
            :raises ScanAlreadyInBookError: If another page shows the scan and ``take_over`` is not set.
            :raises ConflictError: If the scan is not cut yet.
            """
            require_owner(project, actor)
            placeholder = self._page(repos, project_id, page_id)
            if placeholder.origin is not PageOrigin.PLACEHOLDER:
                raise NotAPlaceholderError
            scan = repos.scans.get(scan_id)
            # A scan of another project is reported like a missing one, as a page of another project is
            if scan.project_id != project_id:
                raise NotFoundError(scan_id)
            if not scan.renditions.ready:
                raise ConflictError(SCAN_NOT_CUT)
            taken = repos.pages.list_for_scan(scan_id)
            if taken and not take_over:
                raise ScanAlreadyInBookError(
                    [
                        f'p. {page.label}' if page.label else f'position {repos.pages.count_before(page) + 1}'
                        for page in taken
                    ]
                )
            if taken:
                # Renumbering may write the placeholder too, so it is read again for the write below
                self._labels.recompute(repos, project_id, leaving=[page.id for page in taken])
                placeholder = self._page(repos, project_id, page_id)
            with self._records.watching_sides(repos, project_id) as watch:
                for page in taken:
                    repos.pages.delete(page.id)
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
            repos.pages.update(page)
            repos.page_versions.add(
                BaseVersions.split_none(page=page, scan=scan, state=VersionState.PENDING, moment=moment)
            )
            return self._overview(repos, page), taken, watch.turned

        overview, taken, turned = await self._uow.change_book(project_id, work)
        await self._discard_files(project_id, taken)
        if taken:
            await self._announce(project_id, taken, PageChange.REMOVED)
        await self._announce(project_id, [overview.page], PageChange.EDITED)
        await self._records.announce(project_id, turned)
        await self._enqueue(project_id, JobKind.PREPARE_PAGES)
        return overview

    async def prepare_images(self, job_id: JobId) -> Job | None:
        """Run a ``prepare-pages`` job a worker took from the queue: write the files of every pending or failed version.

        The versions of the page split and the page order stages are read when the job starts, and each is written and
        stored on its own, so the viewer swaps an empty frame for the image of a page as soon as it is ready. A
        version that fails is stored as failed with its reason, the job goes on, and it ends failed if any version did,
        which the next job of the project takes up again. A job that was cancelled stops before its next version.

        The job holds no transaction while it writes files. It opens a ``change`` for each write of the job row, and a
        short ``change_book`` for each version, which reads the page again before it stores anything. It is the entry
        point of a job.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it ended, or None when this run did not take the job through its pages: it was over
                  already, another delivery took it, it stopped on an error, or it was cancelled.
        :rtype: Job | None
        :raises NotFoundError: If there is no such job.
        """
        job = await self._uow.read(lambda repos: repos.jobs.get(job_id))
        if job.state.is_final:
            return None
        if job.state is JobState.QUEUED:
            started = evolve(job, state=JobState.RUNNING, started_at=self._clock.now())
            running = await self._update_job(started, expected=JobState.QUEUED)
            if running is None:
                # The job left the queue since it was read: another delivery started it, or it was cancelled
                return None
            job = running
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))
        try:
            outcome = await self._prepare_all(job)
        except Exception:
            # A job left running would keep the project from ever queueing another, so it ends failed whatever
            # went wrong
            logger.exception('The prepare-pages job %s stopped', job_id)
            await self._conclude(job, JobState.FAILED, UNEXPECTED_FAILURE)
            return None
        if outcome is None:
            return None
        job, failed, total = outcome
        if failed:
            await self._conclude(job, JobState.FAILED, PAGES_FAILED.format(count=failed), total=total)
        else:
            await self._conclude(job, JobState.SUCCEEDED, '', total=total)
        await self._follow_up(job.project_id)
        return job

    async def _prepare_all(self, job: Job) -> tuple[Job, int, int] | None:
        """Prepare every pending and failed version of the job's project, recording the progress as it goes.

        :param job: The running job.
        :type job: Job
        :returns: The job as last stored, the number of versions that failed and the number the job went through, or
                  None when the job was cancelled, which stops it before its next version.
        :rtype: tuple[Job, int, int] | None
        """
        versions = await self._uow.read(
            lambda repos: repos.page_versions.list_to_prepare(job.project_id, PREPARED_PROCESSORS)
        )
        failed = 0
        for done, version in enumerate(versions):
            progress = Progress(done=done, total=len(versions))
            # The write is guarded by the state, so it is also the check that the job was not cancelled
            saved = await self._update_job(evolve(job, progress=progress), expected=JobState.RUNNING)
            if saved is None:
                return None
            job = saved
            failed += not await self._prepare(version)
        return job, failed, len(versions)

    async def _conclude(self, job: Job, state: JobState, error: str, *, total: int | None = None) -> None:
        """Store the final state of a running job, unless it was cancelled meanwhile, and announce it.

        It writes in a ``change`` of its own.

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
        if (stored := await self._update_job(final, expected=JobState.RUNNING)) is not None:
            await self._publisher.publish(JobChanged(project_id=stored.project_id, job=stored))

    async def _update_job(self, job: Job, *, expected: JobState) -> Job | None:
        """Store the new state of a job in a ``change`` of its own, only while the stored job is in the expected state.

        :param job: The job with its new state.
        :type job: Job
        :param expected: The state the stored job must be in for the write to happen.
        :type expected: JobState
        :returns: The job as stored, or None when its stored state is another, which means it left that state since it
                  was read, as a cancelled job did.
        :rtype: Job | None
        :raises NotFoundError: If the job is not stored.
        """
        return await self._uow.change(lambda repos: repos.jobs.update_if_state(job, expected=(expected,)))

    async def move(self, actor: Actor, project_id: ProjectId, page_id: PageId, anchor: PageAnchor) -> PageOverview:
        """Put one page before or after another, and renumber the pages whose number the sections now give differently.

        The pages that turn over to the other side of the book, odd or even, are marked stale where a side has values.

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
        :raises ConflictError: If the anchor is the page itself.
        """

        def work(repos: Repositories, project: Project) -> tuple[PageOverview, list[PageStage]]:
            """Give the page its new place, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The page at its new place, and the stage records of the pages that turned over to the other side
                      of the book.
            :rtype: tuple[PageOverview, list[PageStage]]
            :raises NotFoundError: If the actor has no such project, or the project has no such page or anchor.
            :raises ConflictError: If the anchor is the page itself.
            """
            require_owner(project, actor)
            with self._records.watching_sides(repos, project_id) as watch:
                [moved] = self._place(repos, project_id, [self._page(repos, project_id, page_id)], anchor)
                repos.pages.update(moved)
            if self._labels.recompute(repos, project_id):
                moved = repos.pages.get(moved.id)
            return self._overview(repos, moved), watch.turned

        overview, turned = await self._uow.change_book(project_id, work)
        await self._announce(project_id, [overview.page], PageChange.MOVED)
        await self._records.announce(project_id, turned)
        return overview

    async def move_group(
        self, actor: Actor, project_id: ProjectId, page_ids: Collection[PageId], anchor: PageAnchor
    ) -> None:
        """Put several pages in a run before or after another page, keeping the order they have in the book.

        The pages that turn over to the other side of the book, odd or even, are marked stale where a side has values.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_ids: Identifiers of the pages to move.
        :type page_ids: Collection[PageId]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :raises NotFoundError: If the actor has no such project, or the project lacks a page or the anchor.
        :raises ConflictError: If the anchor is one of the pages.
        """

        def work(repos: Repositories, project: Project) -> tuple[Sequence[Page], list[PageStage]]:
            """Put the pages in a run next to the anchor.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The pages with their new keys, and the stage records of the pages that turned over to the other
                      side of the book.
            :rtype: tuple[Sequence[Page], list[PageStage]]
            :raises NotFoundError: If the actor has no such project, or the project lacks a page or the anchor.
            :raises ConflictError: If the anchor is one of the pages.
            """
            require_owner(project, actor)
            return self._move_run(repos, project_id, repos.pages.list_by_ids(project_id, page_ids), anchor)

        moved, turned = await self._uow.change_book(project_id, work)
        if moved:
            await self._announce(project_id, moved, PageChange.MOVED)
        await self._records.announce(project_id, turned)

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
        :raises ConflictError: If the anchor is a page of the source.
        """

        def work(repos: Repositories, project: Project) -> tuple[Sequence[Page], list[PageStage]]:
            """Put the pages of the source in a run next to the anchor.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param project: The project as the change finds it.
            :type project: Project
            :returns: The pages with their new keys, and the stage records of the pages that turned over to the other
                      side of the book.
            :rtype: tuple[Sequence[Page], list[PageStage]]
            :raises NotFoundError: If the actor has no such project, or the project has no such source or anchor.
            :raises ConflictError: If the anchor is a page of the source.
            """
            require_owner(project, actor)
            # A source of another project is reported like a missing one, as a page of another project is
            if repos.sources.get(source_id).project_id != project_id:
                raise NotFoundError(source_id)
            return self._move_run(repos, project_id, repos.pages.list_for_source(project_id, source_id), anchor)

        moved, turned = await self._uow.change_book(project_id, work)
        if moved:
            await self._announce(project_id, moved, PageChange.MOVED)
        await self._records.announce(project_id, turned)

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
        await self._uow.read(lambda repos: owned_project(repos.projects, actor, keys.project_id))
        async with self._assets.readable(key) as path:
            yield path

    def _overviews(
        self, repos: Repositories, pages: Sequence[Page], first_position: int, *, with_sections: bool = True
    ) -> Sequence[PageOverview]:
        """Attach positions and base versions to consecutive pages of a book, reading the versions in one call.

        The section of a page is worked out on read, from the sections and the whole book, by the walk that numbers the
        pages, so it can never disagree with their labels. It is not stored: the section that governs a page changes
        with every section and every page that moves, and keeping it would mean writing pages that no label changes.
        A book without sections costs no read of its pages.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param pages: Pages of one project in book order, without a gap.
        :type pages: Sequence[Page]
        :param first_position: Position of the first page in the book.
        :type first_position: int
        :param with_sections: Whether to name the section of every page, which a caller that never shows it skips.
        :type with_sections: bool
        :returns: The overviews of the pages, in the same order.
        :rtype: Sequence[PageOverview]
        """
        section_ids: dict[PageId, PaginationSectionId | None] = {}
        if with_sections and pages:
            section_ids = self._section_ids(repos, pages[0].project_id)
        versions = repos.page_versions.list_base_versions([page.id for page in pages])
        # The versions come earliest first, so the newest base version of a page wins
        newest = {version.page_id: version for version in versions}
        current = self._current_images(repos, pages)
        scans = repos.scans.list_by_ids({page.scan_id for page in pages if page.scan_id is not None})
        sources = {scan.id: scan.source_id for scan in scans}
        return [
            PageOverview(
                page=page,
                position=first_position + index,
                image_version=current.get(page.id, newest.get(page.id)),
                source_id=sources.get(page.scan_id) if page.scan_id is not None else None,
                section_id=section_ids.get(page.id),
            )
            for index, page in enumerate(pages)
        ]

    def _section_ids(self, repos: Repositories, project_id: ProjectId) -> dict[PageId, PaginationSectionId | None]:
        """Name the section that governs each page of a book.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The governing section by page identifier, empty for a book without sections.
        :rtype: dict[PageId, PaginationSectionId | None]
        """
        sections = repos.pagination_sections.list_for_project(project_id)
        if not sections:
            return {}
        return Pagination(sections, book_pages(repos.pages, project_id)).section_ids()

    def _current_images(self, repos: Repositories, pages: Sequence[Page]) -> Mapping[PageId, PageVersion]:
        """Find the version each page shows: the current version of the latest stage that has an image.

        A page whose stages have no current version yet, such as a leaf waiting for its image, is left out, and the
        caller falls back to its base version.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param pages: Pages of one project.
        :type pages: Sequence[Page]
        :returns: The version to show by page identifier.
        :rtype: Mapping[PageId, PageVersion]
        """
        records = repos.page_stages.list_for_pages([page.id for page in pages])
        head_ids = {record.head_version_id for record in records if record.head_version_id is not None}
        heads = {version.id: version for version in repos.page_versions.list_by_ids(head_ids)}
        shown: dict[PageId, PageVersion] = {}
        # The records come in the order of the stages, so the latest stage that has an image wins
        for record in records:
            head = None if record.head_version_id is None else heads.get(record.head_version_id)
            if head is not None and head.renditions is not None and head.renditions.ready:
                shown[record.page_id] = head
        return shown

    def _overview(self, repos: Repositories, page: Page) -> PageOverview:
        """Read the position, base version and source of one stored page.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param page: Stored page of a project.
        :type page: Page
        :returns: The page with its place in the book.
        :rtype: PageOverview
        """
        [overview] = self._overviews(repos, [page], repos.pages.count_before(page))
        return overview

    @staticmethod
    def _page(repos: Repositories, project_id: ProjectId, page_id: PageId) -> Page:
        """Read a page of the project.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project_id: Identifier of the project the page must belong to.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page.
        :rtype: Page
        :raises NotFoundError: If there is no such page, or it belongs to another project, which is reported like a
                               missing one so no identifier can be probed across projects.
        """
        page = repos.pages.get(page_id)
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

    def _place(
        self, repos: Repositories, project_id: ProjectId, pages: Sequence[Page], anchor: PageAnchor
    ) -> list[Page]:
        """Give pages new order keys that put them in a run next to the anchor page, in the order given.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
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
        keys = self._keys_at(repos, project_id, anchor, count=len(pages), moving={page.id for page in pages})
        moment = self._clock.now()
        return [evolve(page, order_key=key, updated_at=moment) for page, key in zip(pages, keys, strict=True)]

    def _keys_at(
        self,
        repos: Repositories,
        project_id: ProjectId,
        anchor: PageAnchor | None,
        *,
        count: int,
        moving: Collection[PageId] = (),
    ) -> Sequence[str]:
        """Make order keys for ``count`` pages that stand in a run at the anchor, or at the end of the book.

        The key of the page next to the place is found with the moving pages left out, so a page that already stands
        beside the anchor gets a key between the same neighbours it would have had if it were elsewhere.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
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
            lower, upper = repos.pages.last_order_key(project_id), None
        else:
            if anchor.page_id in moving:
                raise AnchorInsideMovedPagesError
            target = self._page(repos, project_id, anchor.page_id)
            neighbour = repos.pages.neighbour_key(project_id, target.order_key, anchor.side, excluding=moving)
            lower, upper = (
                (neighbour, target.order_key) if anchor.side is Side.BEFORE else (target.order_key, neighbour)
            )
        return self._order_keys.spread(lower=lower, upper=upper, count=count)

    def _move_run(
        self, repos: Repositories, project_id: ProjectId, pages: Sequence[Page], anchor: PageAnchor
    ) -> tuple[Sequence[Page], list[PageStage]]:
        """Put pages in a run next to the anchor page, in the work of the ``change_book`` of the caller.

        It opens no transaction, so the caller announces the move after its transaction has ended.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param pages: Pages to move, in book order; none moves nothing.
        :type pages: Sequence[Page]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :returns: The pages with their new keys, none when no page was given, and the stage records that became stale
                  because a page turned to the other side of the book.
        :rtype: tuple[Sequence[Page], list[PageStage]]
        :raises NotFoundError: If the anchor is not a page of the project.
        :raises ConflictError: If the anchor is one of the pages.
        """
        moved = self._place(repos, project_id, pages, anchor)
        if not moved:
            return moved, []
        with self._records.watching_sides(repos, project_id) as watch:
            repos.pages.update_many(moved)
        self._labels.recompute(repos, project_id)
        return moved, watch.turned

    async def _announce(self, project_id: ProjectId, pages: Sequence[Page], change: PageChange) -> None:
        """Tell the browser which pages changed, which it reads the manifest again for, after the transaction.

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
        """Remove every file of pages that were deleted, after the transaction that deleted them has ended.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param pages: The deleted pages, whose directories under ``assets/pages`` are removed.
        :type pages: Sequence[Page]
        """
        keys = ProjectKeys(project_id)
        for page in pages:
            await self._assets.delete_prefix(keys.page(page.id))

    async def _enqueue(self, project_id: ProjectId, kind: JobKind, params: MetadataMap | None = None) -> Job | None:
        """Queue a ``prepare-pages`` or a ``detect-content`` job for the project, unless one of its kind is active.

        A project has one job of each of the kinds at a time, so two jobs never write the files of one version, or the
        content of one page, together. The check of the active jobs and the insert of the new one are the work of one
        ``change``, which waits for any other, so two requests never pass the check together. A job that is running when
        a version is stored may have read its versions before it, which is why the job looks again when it ends, in
        ``_follow_up``.

        The page is stored already, so a queue that refuses the job does not fail the request: the job is stored
        as failed in a ``change`` of its own, announced, and logged, which frees the project for the next job, and the
        version stays pending until that job takes it.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param kind: What the job does, ``prepare-pages`` or ``detect-content``.
        :type kind: JobKind
        :param params: What the job was asked to do, in the form its value class writes, or None for a job without.
        :type params: MetadataMap | None
        :returns: The job as stored, queued or failed, or None when a job of the kind is queued or running already.
        :rtype: Job | None
        """
        job = Job(
            id=JobId(uuid4()), project_id=project_id, kind=kind, params=params or {}, created_at=self._clock.now()
        )

        def place(repos: Repositories) -> bool:
            """Store the job unless a job of its kind is active.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: Whether the job was stored.
            :rtype: bool
            """
            active = repos.jobs.list_for_project(project_id, JobState.active())
            if any(active_job.kind is kind for active_job in active):
                return False
            repos.jobs.add(job)
            return True

        if not await self._uow.change(place):
            return None
        await self._publisher.publish(JobChanged(project_id=project_id, job=job))
        try:
            await self._queue.enqueue(job)
        except Exception:
            logger.exception('The %s job %s could not be queued', kind, job.id)
            error = NOT_QUEUED if kind is JobKind.PREPARE_PAGES else DETECTION_NOT_QUEUED
            failed = evolve(job, state=JobState.FAILED, error=error, finished_at=self._clock.now())
            stored = await self._update_job(failed, expected=JobState.QUEUED)
            if stored is not None:
                await self._publisher.publish(JobChanged(project_id=project_id, job=stored))
                return stored
        return job

    async def _follow_up(self, project_id: ProjectId) -> None:
        """Queue another job when a version was committed while the job that just ended was running.

        A request that commits a version while a job runs finds the job active and queues none, and the job may have
        read its versions before that one. Here the job has ended, so a pending version that is left was committed
        after the job read its versions, and it gets a job of its own. A version that failed is not pending, so a
        version that cannot be made does not keep a job running again.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        """
        waiting = await self._uow.read(
            lambda repos: repos.page_versions.list_to_prepare(project_id, PREPARED_PROCESSORS)
        )
        if any(version.state is VersionState.PENDING for version in waiting):
            await self._enqueue(project_id, JobKind.PREPARE_PAGES)

    def _median_size(self, repos: Repositories, project_id: ProjectId) -> PageSize:
        """Return the size of a blank leaf that stands level with the pages of the book.

        The width and the height are the medians of those of the included pages cut from a scan, taken apart, and the
        resolution is the median of theirs, or unknown when no page has one. The median does not follow one fold-out
        map or one cropped scan.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The median size.
        :rtype: PageSize
        :raises ConflictError: If no page of the book has a recorded size yet.
        """
        if not (sizes := repos.page_versions.base_sizes(project_id)):
            raise ConflictError(NO_BOOK_IMAGE)
        resolutions = [size.dpi for size in sizes if size.dpi is not None]
        return PageSize(
            width_px=round(statistics.median(size.width_px for size in sizes)),
            height_px=round(statistics.median(size.height_px for size in sizes)),
            dpi=round(statistics.median(resolutions), 1) if resolutions else None,
        )

    def _leaf_size(self, repos: Repositories, project_id: ProjectId) -> PageSize:
        """Return the size of a blank leaf that stands level with the pages of the book.

        Once the book is measured, its pages are the size the normalize step of the Geometry recipe for blank pages
        gives them, which is the one size of the book that the measure writes into every Geometry recipe, and a leaf
        has that size. Before that it is the median size of the pages cut from a scan.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The size of the pages of the book, with the median resolution.
        :rtype: PageSize
        :raises ConflictError: If no page of the book has a recorded size yet.
        """
        median = self._median_size(repos, project_id)
        recipes = repos.recipes.list_for_stage(project_id, Stage.GEOMETRY)
        recipe = next((recipe for recipe in recipes if recipe.kind is RecipeKind.BLANK), None)
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

    def _paper_sources(self, repos: Repositories, page: Page) -> list[PageVersionId]:
        """Find the versions of the pages nearest to a leaf whose paper the leaf takes the colour of.

        The pages looked at are those of text in a window round the leaf: pages cut from a scan and part of the book,
        which are not leaves themselves and have an image that is ready. The nearest ones are taken, and a leaf with
        none has none to take, which makes it white.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param page: The page that becomes a leaf.
        :type page: Page
        :returns: The identifiers of the versions the pages show.
        :rtype: list[PageVersionId]
        """
        position = repos.pages.count_before(page)
        first = max(0, position - self.PAPER_RADIUS)
        window = repos.pages.list_for_project(
            page.project_id, SliceRequest(offset=first, limit=position - first + self.PAPER_RADIUS + 1)
        )
        near = sorted(
            (
                overview
                for overview in self._overviews(repos, window.items, first, with_sections=False)
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
        removed and the job goes on. The files are written outside any transaction, and the result is stored by
        ``_store`` in a ``change_book`` of its own.

        :param version: A pending or failed base version.
        :type version: PageVersion
        :returns: False when the version failed, and True when it is ready, nothing was left to make, or its page is
                  gone.
        :rtype: bool
        """
        if (page := await self._uow.read(lambda repos: self._found(repos, version.page_id))) is None:
            return True
        # A leaf that is not the choice of its page any longer is nothing to make, and left pending it would be taken
        # by every job. The page is read again in the work, since a request may have chosen the leaf again meanwhile
        if self._outdated_leaf(version, page):

            def drop(repos: Repositories, _project: Project) -> None:
                """Delete the version when its page still does not choose the leaf.

                :param repos: Repositories of the transaction.
                :type repos: Repositories
                :param _project: The project as the change finds it, which the work does not need.
                :type _project: Project
                """
                if (current := self._found(repos, page.id)) is not None and self._outdated_leaf(version, current):
                    repos.page_versions.delete_many([version.id])

            await self._uow.change_book(page.project_id, drop)
            return True
        try:
            made = await self._write_files(version, page)
        except DomainError as error:
            reason = str(error)
        except Exception:
            logger.exception('The files of page version %s could not be made', version.id)
            reason = UNEXPECTED_FAILURE
        else:
            await self._store(page, made, current=True)
            return True
        failed = evolve(version, data={**version.data, VersionData.ERROR: reason}, state=VersionState.FAILED)
        return not await self._store(page, failed, current=False)

    async def _store(self, page: Page, version: PageVersion, *, current: bool) -> bool:
        """Store the new state of a version of a page in one change, and make it the current version when it is ready.

        The work reads the page again. A page deleted while its files were written took its versions with it, so
        nothing is stored and the files are removed after the transaction. A leaf that stopped being the choice of its
        page meanwhile is not stored either, since making it current would undo that choice. It is deleted in the work
        as ``_prepare`` deletes such a leaf, since a leaf left pending would be taken by every job, and its files are
        removed after the transaction.

        :param page: The page the version belongs to, as read before its files were written.
        :type page: Page
        :param version: The version with its new state.
        :type version: PageVersion
        :param current: Whether the version becomes the current version of its stage, which a ready version does.
        :type current: bool
        :returns: True when the version is stored, and False when its page is gone or the leaf is outdated.
        :rtype: bool
        """

        def work(repos: Repositories, _project: Project) -> tuple[bool, bool, Sequence[PageStage]]:
            """Store the version unless its page is gone or no longer chooses the leaf.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :param _project: The project as the change finds it, which the work does not need.
            :type _project: Project
            :returns: Whether the page is gone, whether the leaf is outdated and was deleted, and the stage records that
                      changed.
            :rtype: tuple[bool, bool, Sequence[PageStage]]
            """
            if (now := self._found(repos, page.id)) is None:
                return True, False, ()
            if self._outdated_leaf(version, now):
                repos.page_versions.delete_many([version.id])
                return False, True, ()
            repos.page_versions.update(version)
            changed = (
                self._records.set_head(
                    repos, PageStageKey(page.id, version.stage), head_version_id=version.id, recipe_id=None
                )
                if current
                else ()
            )
            return False, False, changed

        gone, outdated, changed = await self._uow.change_book(page.project_id, work)
        if gone:
            await self._discard_files(page.project_id, [page])
        elif outdated:
            await self._assets.delete_prefix(ProjectKeys(page.project_id).version_directory(version))
        elif current:
            await self._publisher.publish(PageVersionReady(project_id=page.project_id, version=version))
            await self._records.announce(page.project_id, changed)
        return not (gone or outdated)

    @staticmethod
    def _found(repos: Repositories, page_id: PageId) -> Page | None:
        """Read a page that may have been deleted since a job looked at it.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: Repositories
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page, or None when it is gone.
        :rtype: Page | None
        """
        try:
            return repos.pages.get(page_id)
        except NotFoundError:
            return None

    @staticmethod
    def _outdated_leaf(version: PageVersion, page: Page) -> bool:
        """Tell whether a version is a leaf whose fill is not the choice of its page any longer.

        A page that got its scan back is the usual case.

        :param version: A base version.
        :type version: PageVersion
        :param page: The page the version belongs to, as read.
        :type page: Page
        :returns: Whether the version is a leaf of another fill than the page has now.
        :rtype: bool
        """
        return bool(
            version.processor == PAGES_BLANK
            and page.origin is PageOrigin.SCAN
            and page.blank_fill.value != version.params.get(BlankParam.FILL, PaperFill.WHITE.value)
        )

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

        def read(repos: Repositories) -> tuple[Project, ProjectKeys, StepRun]:
            """Read the project and what the processor of the version reads, in one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The project, its keys, and the run of the processor.
            :rtype: tuple[Project, ProjectKeys, StepRun]
            :raises ConflictError: If the scan to copy is deleted or not cut yet.
            :raises ValueError: If the version is neither ``split.none`` nor ``pages.blank``, or has no size to make.
            """
            project = repos.projects.get(page.project_id)
            keys = ProjectKeys(project.id)
            if version.processor == SPLIT_NONE:
                if page.scan_id is None:
                    raise ConflictError(SCAN_DELETED)
                scan = repos.scans.get(page.scan_id)
                if not scan.renditions.ready:
                    raise ConflictError(SCAN_NOT_CUT)
                run = StepRun(
                    processor_key=SPLIT_NONE.key,
                    params=version.params,
                    input_data=scan.facts.as_data(),
                    image=keys.scan_rendition(scan, scan.renditions.full),
                )
            elif version.processor == PAGES_BLANK and PageSize.from_data(version.data) is not None:
                sources = repos.page_versions.list_by_ids(version.params.get(BlankParam.PAPER_FROM, []))
                run = StepRun(
                    processor_key=PAGES_BLANK.key,
                    params=version.params,
                    input_data={},
                    references=[
                        keys.version_rendition(source, source.renditions.full)
                        for source in sources
                        if source.renditions is not None and source.renditions.ready
                    ],
                )
            else:
                err_msg = f'Page version {version.id} made by {version.processor.key} has no files to write.'
                raise ValueError(err_msg)
            return project, keys, run

        project, keys, run = await self._uow.read(read)
        async with self._runner.execute(run) as result:
            return await self._runner.store(keys, version, result.outputs[0], policy=project.image_policy, tiles=True)
