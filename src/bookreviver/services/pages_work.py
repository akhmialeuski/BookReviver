"""The pages of a book inside one transaction of a page use case: their overviews, and their places.

``PageKeeping`` is the pages of the books of a service, built once with the ports of the pages, and it opens a
``PagesWork`` in the work ``PageService`` hands to ``Book.read`` or ``Book.change``. ``PagesWork`` holds the
repositories of that transaction and the keeping it belongs to, so nothing below it is handed the repositories or the
ports from call to call. It adds the pending base version of a page that is bound to a scan, finds the leaves a newer
``pages.blank`` replaces, and owns two objects, each reaching the transaction through it:

- ``PageOverviews`` reads pages as the interface shows them, with their position, image, label and section;
- ``PagePlaces`` gives pages the order keys of a place of the book, for a page that is added or moved.

``Leaves`` of ``services/leaves.py`` stands a blank leaf in place of the scan of a page, and ``PageImages`` of
``services/page_images.py`` is the ``prepare-pages`` job that writes the images of base versions. Both belong to what
this module opens, and so lie above it, which never imports them.

The position of a page is not stored: it is computed when a page is read, from the order keys of the book. Moving a
page writes a new order key to the moved pages alone: the key lies between the key of the page next to the place and
the key of the page after it, which the repository finds while leaving the moved pages out, so the pages that stay
never change.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.errors import AnchorInsideMovedPagesError, ConflictError, NotFoundError
from bookreviver.domain.ids import PageId
from bookreviver.domain.pages import Page, PageOverview
from bookreviver.domain.pagination import Numbering
from bookreviver.domain.sides import Side
from bookreviver.domain.versions import VersionState
from bookreviver.services.page_labels import PageLabels

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from bookreviver.domain.pages import NewPage, PageAnchor
    from bookreviver.domain.sources import Scan
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.base_versions import BaseVersions
    from bookreviver.services.book_steps import BookSteps
    from bookreviver.services.stage_records import SideWatch, StageRecords
    from bookreviver.services.steps import StepRunner

ANCHOR_NOT_IN_BOOK: str = 'the page it is put next to is not a page of the book.'
PAGE_OF_BATCH: str = 'The page at index {index} of the list cannot be added: {reason}'


class PageKeeping:
    """The pages of the books of a service, which a use case opens for the work of one transaction.

    It is built once, with what every transaction of the pages and every ``prepare-pages`` job works with, and the
    ``PagesWork`` it opens, the ``Leaves`` of that and the ``PageImages`` of a job reach those through it.

    :ivar order_keys: Builder of the order keys of moved and new pages.
    :ivar base_versions: Builder of the rows of base versions.
    :ivar runner: Runner of the processors ``split.none`` and ``pages.blank``, and writer of their files.
    :ivar book_steps: Finder of the steps whose plugin reads the whole book, one of which may give the size of a leaf.
    :ivar records: Writer of the records of the stages of a page.
    :ivar labels: Keeper of the labels the pagination sections give the pages.
    """

    def __init__(
        self,
        *,
        order_keys: OrderKeys,
        base_versions: BaseVersions,
        runner: StepRunner,
        book_steps: BookSteps,
        records: StageRecords,
    ) -> None:
        """Keep the pages of the books through the ports of the application.

        :param order_keys: Builder of the order keys of moved and new pages.
        :type order_keys: OrderKeys
        :param base_versions: Builder of the rows of base versions.
        :type base_versions: BaseVersions
        :param runner: Runner of the processors ``split.none`` and ``pages.blank``, and writer of their files.
        :type runner: StepRunner
        :param book_steps: Finder of the steps whose plugin reads the whole book.
        :type book_steps: BookSteps
        :param records: Writer of the records of the stages of a page.
        :type records: StageRecords
        """
        self.order_keys = order_keys
        self.base_versions = base_versions
        self.runner = runner
        self.book_steps = book_steps
        self.records = records
        self.labels = PageLabels()

    def open(self, repos: BookRepositories) -> PagesWork:
        """Open the pages of a book for the work of one transaction.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The pages of the transaction, with their overviews and their places.
        :rtype: PagesWork
        """
        return PagesWork(self, repos)


class PagesWork:
    """The pages of a book inside one transaction of a page use case.

    :ivar keeping: What opened the pages, through which the ports of the pages are reached.
    :ivar repos: Repositories of the transaction.
    :ivar overviews: Reader of the pages as the interface shows them.
    :ivar places: Giver of the order keys of a place of the book.
    """

    def __init__(self, keeping: PageKeeping, repos: BookRepositories) -> None:
        """Open the pages of a book for the work of one transaction.

        :param keeping: What opened the pages.
        :type keeping: PageKeeping
        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        """
        self.keeping = keeping
        self.repos = repos
        self.overviews = PageOverviews(self)
        self.places = PagePlaces(self)

    def settle(self, *, leaving: Sequence[PageId] = ()) -> None:
        """Settle the pagination sections after pages were added, deleted, moved or changed.

        :param leaving: Pages that are about to be deleted, whose sections are handed on.
        :type leaving: Sequence[PageId]
        """
        self.keeping.labels.settle(self.repos, leaving=leaving)

    def numbering(self, pages: Sequence[Page]) -> Numbering:
        """Work out the labels and the sections the pagination gives pages.

        :param pages: Pages of the book.
        :type pages: Sequence[Page]
        :returns: The labels and the sections of the pages.
        :rtype: Numbering
        """
        return self.keeping.labels.numbering(self.repos, pages)

    def watching_sides(self) -> SideWatch:
        """Mark stale the pages that turn over to the other side of the book while the block changes the order.

        :returns: The block inside which pages are added, deleted or moved.
        :rtype: SideWatch
        """
        return self.keeping.records.watching_sides(self.repos)

    def found(self, page_id: PageId) -> Page | None:
        """Read a page that may have been deleted since a job looked at it.

        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page, or None when it is gone.
        :rtype: Page | None
        """
        try:
            return self.repos.pages.get(page_id)
        except NotFoundError:
            return None

    def bind_scan(self, page: Page, scan: Scan, moment: datetime) -> None:
        """Add the pending base version of a page that now shows a scan, which copies the scan for the job to write.

        :param page: The page, as it shows the scan.
        :type page: Page
        :param scan: The scan the page shows.
        :type scan: Scan
        :param moment: Time of the change.
        :type moment: datetime
        """
        self.repos.page_versions.add(
            self.keeping.base_versions.split_none(page=page, scan=scan, state=VersionState.PENDING, moment=moment)
        )

    def replaced_leaves(self) -> list[PageId]:
        """List the pages whose page order is a leaf made by a version of ``pages.blank`` that was replaced.

        :returns: The identifiers of the pages.
        :rtype: list[PageId]
        """
        leaf = self.keeping.base_versions.blank_leaf
        return [record.page_id for record in self.repos.page_stages.list_replaced(leaf.stage, leaf.ref)]


class PageOverviews:
    """Reader of the pages of a transaction as the interface shows them.

    A page shows the current version of the latest stage that has an image, and its base version while no stage has
    one. The label and the section of a page are worked out on read, from the sections and the whole book, by the walk
    that numbers the pages, so they can never disagree. Neither is stored: both change with every section and every
    page that moves, and keeping them would mean writing pages that nobody touched.

    :ivar work: The pages of the transaction, through which the repositories are reached.
    """

    def __init__(self, work: PagesWork) -> None:
        """Read the pages of a transaction.

        :param work: The pages of the transaction.
        :type work: PagesWork
        """
        self.work = work

    def of(self, pages: Sequence[Page], first_position: int, *, numbered: bool = True) -> Sequence[PageOverview]:
        """Attach positions, base versions, labels and sections to consecutive pages of a book.

        A book without sections costs no read of its pages.

        :param pages: Pages of one book in book order, without a gap.
        :type pages: Sequence[Page]
        :param first_position: Position of the first page in the book.
        :type first_position: int
        :param numbered: Whether to give every page its label and its section, which a caller that shows neither skips,
                         leaving both empty.
        :type numbered: bool
        :returns: The overviews of the pages, in the same order.
        :rtype: Sequence[PageOverview]
        """
        return self._shown(pages, range(first_position, first_position + len(pages)), numbered=numbered)

    def placed(self, pages: Sequence[Page]) -> Sequence[PageOverview]:
        """Attach positions, base versions, labels and sections to pages that may stand anywhere in a book.

        The order of the book is read once for the positions of all the pages, and the rest is read once for all of
        them, as ``of`` reads it, so the pages a batch put in many places cost no read for each place.

        :param pages: Stored pages of one book, in any order.
        :type pages: Sequence[Page]
        :returns: The overviews of the pages, in the same order.
        :rtype: Sequence[PageOverview]
        """
        positions = self.work.repos.pages.order().positions()
        return self._shown(pages, [positions[page.id] for page in pages], numbered=True)

    def _shown(self, pages: Sequence[Page], positions: Sequence[int], *, numbered: bool) -> Sequence[PageOverview]:
        """Attach the given positions, and the base versions, labels and sections read for all the pages at once.

        :param pages: Pages of one book.
        :type pages: Sequence[Page]
        :param positions: Position of each page in the book, in the order of the pages.
        :type positions: Sequence[int]
        :param numbered: Whether to give every page its label and its section.
        :type numbered: bool
        :returns: The overviews of the pages, in the same order.
        :rtype: Sequence[PageOverview]
        """
        repos = self.work.repos
        numbering = self.work.numbering(pages) if numbered and pages else Numbering(labels={}, section_ids={})
        versions = repos.page_versions.list_base_versions([page.id for page in pages])
        # The versions come earliest first, so the newest base version of a page wins
        newest = {version.page_id: version for version in versions}
        current = self._current_images(pages)
        scans = repos.scans.list_by_ids({page.scan_id for page in pages if page.scan_id is not None})
        sources = {scan.id: scan.source_id for scan in scans}
        return [
            PageOverview(
                page=page,
                position=position,
                image_version=current.get(page.id, newest.get(page.id)),
                source_id=sources.get(page.scan_id) if page.scan_id is not None else None,
                section_id=numbering.section_ids.get(page.id),
                label=numbering.labels.get(page.id, ''),
            )
            for page, position in zip(pages, positions, strict=True)
        ]

    def one(self, page: Page) -> PageOverview:
        """Read the position, base version and source of one stored page.

        :param page: Stored page of a book.
        :type page: Page
        :returns: The page with its place in the book.
        :rtype: PageOverview
        """
        [overview] = self.of([page], self.work.repos.pages.count_before(page))
        return overview

    def _current_images(self, pages: Sequence[Page]) -> Mapping[PageId, PageVersion]:
        """Find the version each page shows: the current version of the latest stage that has an image.

        A page whose stages have no current version yet, such as a leaf waiting for its image, is left out, and the
        caller falls back to its base version.

        :param pages: Pages of one book.
        :type pages: Sequence[Page]
        :returns: The version to show by page identifier.
        :rtype: Mapping[PageId, PageVersion]
        """
        repos = self.work.repos
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


class PagePlaces:
    """Giver of the order keys of a place of the book, for the pages a transaction adds or moves.

    :ivar work: The pages of the transaction, through which the repositories and the key builder are reached.
    """

    def __init__(self, work: PagesWork) -> None:
        """Place the pages of a transaction.

        :param work: The pages of the transaction.
        :type work: PagesWork
        """
        self.work = work

    def keys_at(self, anchor: PageAnchor | None, *, count: int, moving: Collection[PageId] = ()) -> Sequence[str]:
        """Make order keys for ``count`` pages that stand in a run at the anchor, or at the end of the book.

        The key of the page next to the place is found with the moving pages left out, so a page that already stands
        beside the anchor gets a key between the same neighbours it would have had if it were elsewhere.

        :param anchor: Page the run is put next to, and the side of it, or None for the end of the book.
        :type anchor: PageAnchor | None
        :param count: Number of keys.
        :type count: int
        :param moving: Pages that are about to leave their place, which do not count as neighbours.
        :type moving: Collection[PageId]
        :returns: The keys in ascending order.
        :rtype: Sequence[str]
        :raises NotFoundError: If the anchor is not a page of the book.
        :raises ConflictError: If the anchor is one of the moving pages, since the place is then not defined.
        """
        pages = self.work.repos.pages
        if anchor is None:
            lower, upper = pages.last_order_key(), None
        else:
            if anchor.page_id in moving:
                raise AnchorInsideMovedPagesError
            target = pages.get(anchor.page_id)
            neighbour = pages.neighbour_key(target.order_key, anchor.side, excluding=moving)
            lower, upper = (
                (neighbour, target.order_key) if anchor.side is Side.BEFORE else (target.order_key, neighbour)
            )
        return self.work.keeping.order_keys.spread(lower=lower, upper=upper, count=count)

    def new(self, new_page: NewPage, moment: datetime) -> Page:
        """Build the stored form of a page the user adds, at its place of the book.

        :param new_page: The page to add, with its place.
        :type new_page: NewPage
        :param moment: Time of the request, which creates and last changes the page.
        :type moment: datetime
        :returns: The page, not yet stored.
        :rtype: Page
        :raises NotFoundError: If the anchor is not a page of the book.
        """
        [key] = self.keys_at(new_page.anchor, count=1)
        return self._page(new_page, key, moment)

    def batch(self, new_pages: Sequence[NewPage], moment: datetime) -> list[Page]:
        """Build the pages of a batch with their order keys, one call of the key builder for each place.

        A page with an anchor goes to that place, and a page without one goes after the page before it in the list, or
        at the end of the book when it is the first, so it joins that page's place. The order of the book is read once
        for every place, before any page of the batch is stored, so each place lies between the pages the book had.

        :param new_pages: The pages of the batch.
        :type new_pages: Sequence[NewPage]
        :param moment: When the pages are added.
        :type moment: datetime
        :returns: The new pages, in the order listed.
        :rtype: list[Page]
        :raises ConflictError: If the anchor of a place is not a page of the book, naming the first page of the place.
        """
        places: dict[PageAnchor | None, list[int]] = {}
        place = None
        for index, new_page in enumerate(new_pages):
            place = new_page.anchor or place
            places.setdefault(place, []).append(index)

        gaps = self.work.repos.pages.order().gaps(places)
        pages: dict[int, Page] = {}
        for anchor, indexes in places.items():
            if anchor not in gaps:
                raise ConflictError(PAGE_OF_BATCH.format(index=indexes[0], reason=ANCHOR_NOT_IN_BOOK))
            lower, upper = gaps[anchor]
            keys = self.work.keeping.order_keys.spread(lower=lower, upper=upper, count=len(indexes))
            pages.update((i, self._page(new_pages[i], key, moment)) for i, key in zip(indexes, keys, strict=True))
        return [pages[index] for index in range(len(new_pages))]

    def moved(self, pages: Sequence[Page], anchor: PageAnchor) -> list[Page]:
        """Give pages new order keys that put them in a run next to the anchor page, in the order given.

        :param pages: Pages to move, in book order.
        :type pages: Sequence[Page]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :returns: The pages with their new keys and their update time, not yet stored.
        :rtype: list[Page]
        :raises NotFoundError: If the anchor is not a page of the book.
        :raises ConflictError: If the anchor is one of the pages, since the place is then not defined.
        """
        keys = self.keys_at(anchor, count=len(pages), moving={page.id for page in pages})
        moment = datetime.now(UTC)
        return [evolve(page, order_key=key, updated_at=moment) for page, key in zip(pages, keys, strict=True)]

    def move_run(self, pages: Sequence[Page], anchor: PageAnchor) -> None:
        """Put pages in a run next to the anchor page, and settle the sections.

        :param pages: Pages to move, in book order; none moves nothing.
        :type pages: Sequence[Page]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :raises NotFoundError: If the anchor is not a page of the book.
        :raises ConflictError: If the anchor is one of the pages.
        """
        moved = self.moved(pages, anchor)
        if not moved:
            return
        with self.work.watching_sides():
            self.work.repos.pages.update_many(moved)
        self.work.settle()

    @staticmethod
    def _page(new_page: NewPage, key: str, moment: datetime) -> Page:
        """Build the stored form of a page the user adds, with its order key.

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
            order_key=key,
            manual_label=new_page.label or None,
            kind=new_page.kind,
            origin=new_page.origin.page_origin,
            notes=new_page.notes,
            created_at=moment,
            updated_at=moment,
        )
