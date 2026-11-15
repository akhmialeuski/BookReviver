"""The blank leaves of a book: the leaf that stands in place of the scan of a blank page, and the leaf that is added.

A blank page cut from a scan may show a leaf in place of its scan: a white leaf, or one of the colour of the paper of
the neighbouring pages. The leaf is the version of the page order that the processor of the role
``ProcessorRole.BLANK_LEAF`` makes, of the size of the pages of the book, and it becomes the current version of the
page order, so the stages after it read the leaf. The page keeps its scan and its version, so choosing the scan again
removes the record of the page order and the page shows its scan again.

``Leaves`` belongs to the ``PagesWork`` of a transaction, through which it reaches the repositories, so it writes
through the transaction of its caller, and the caller queues the job that writes the images after it.
"""

import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from attrs import evolve

from bookreviver.domain.errors import ConflictError
from bookreviver.domain.images import ColorMode
from bookreviver.domain.pages import BlankFill, NewPageOrigin, PageKind, RecipeKind
from bookreviver.domain.slices import SliceRequest
from bookreviver.domain.stages import PageStageKey, Stage
from bookreviver.domain.versions import PageSize, VersionState
from bookreviver.services.pages_work import PAGE_OF_BATCH

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import PageVersionId
    from bookreviver.domain.pages import NewPage, Page
    from bookreviver.domain.versions import PageVersion
    from bookreviver.services.pages_work import PagesWork

NO_BOOK_IMAGE: str = (
    'No page of the book that is cut from a scan and part of the book has a recorded size, so a blank leaf has no size '
    'to take. Give the size of the leaf.'
)


class Leaves:
    """The blank leaves of the pages of one transaction.

    :cvar PAPER_KINDS: Kinds of page whose paper a leaf may take, which are the pages printed on the paper of the book.
    :cvar PAPER_RADIUS: How many pages on each side of a leaf are looked at for pages to take the paper from.
    :cvar PAPER_PAGES: Most pages a leaf takes its paper from, the nearest ones.
    :ivar work: The pages of the transaction, through which the repositories are reached.
    """

    PAPER_KINDS: ClassVar[frozenset[PageKind]] = frozenset({PageKind.TEXT, PageKind.TITLE})
    PAPER_RADIUS: ClassVar[int] = 10
    PAPER_PAGES: ClassVar[int] = 6

    def __init__(self, work: PagesWork) -> None:
        """Stand the leaves of a transaction.

        :param work: The pages of the transaction.
        :type work: PagesWork
        """
        self.work = work

    def size(self) -> PageSize:
        """Return the size of a blank leaf that stands level with the pages of the book.

        Once the book is measured, its pages are the size that a step of the Geometry recipe for blank pages brings
        every page to, which is the one size of the book that the measure writes into every Geometry recipe, and a
        leaf has that size. Before that it is the median size of the pages cut from a scan.

        :returns: The size of the pages of the book, with the median resolution.
        :rtype: PageSize
        :raises ConflictError: If no page of the book has a recorded size yet.
        """
        median = self._median_size()
        recipes = self.work.repos.recipes.list_for_stage(Stage.GEOMETRY)
        recipe = next((recipe for recipe in recipes if recipe.kind is RecipeKind.BLANK), None)
        for step in () if recipe is None else recipe.enabled_steps:
            book_step = self.work.keeping.book_steps.of(step)
            if book_step is not None and (size := book_step.page_size(step.params)) is not None:
                return evolve(median, width_px=size[0], height_px=size[1])
        return median

    def size_for(self, new_pages: Sequence[NewPage]) -> PageSize | None:
        """Read the size of the leaves of the book once, when a blank leaf of a batch has no size of its own.

        :param new_pages: The pages of the batch.
        :type new_pages: Sequence[NewPage]
        :returns: The size, or None when every blank leaf has a size.
        :rtype: PageSize | None
        :raises ConflictError: If no page of the book has a size to take the median of, naming the first leaf without
                               one.
        """
        sizeless = (i for i, page in enumerate(new_pages) if page.origin is NewPageOrigin.BLANK and page.size is None)
        if (lacking := next(sizeless, None)) is None:
            return None
        try:
            return self.size()
        except ConflictError as error:
            raise ConflictError(PAGE_OF_BATCH.format(index=lacking, reason=error)) from error

    def added(self, page: Page, size: PageSize, book_record: BookRecord) -> PageVersion:
        """Build the pending base version of a blank leaf the user adds.

        :param page: The new page, whose creation time is the time of the version.
        :type page: Page
        :param size: Size of the leaf.
        :type size: PageSize
        :param book_record: The record of the book, which names the format of its bilevel images.
        :type book_record: BookRecord
        :returns: The version, not yet stored.
        :rtype: PageVersion
        """
        full = book_record.image_policy.full_format(ColorMode.BILEVEL)
        return self.work.keeping.base_versions.blank(page=page, size=size, full=full, moment=page.created_at)

    def stand(self, pages: Sequence[Page], book_record: BookRecord) -> bool:
        """Give pages the image their choice says, and tell whether a job must write leaves that are not made yet.

        A page that shows the scan loses the record of its page order, so its scan is current again. A page that shows
        a leaf gets the pending version of the leaf, or the version that was made already, which is current again
        without the job.

        :param pages: The pages, each with the choice to give effect to as its ``blank_fill``.
        :type pages: Sequence[Page]
        :param book_record: The record of the book owning the pages.
        :type book_record: BookRecord
        :returns: Whether a leaf waits for the ``prepare-pages`` job.
        :rtype: bool
        :raises ConflictError: If a leaf is chosen and no page of the book has a recorded size to give it.
        """
        repos, keeping = self.work.repos, self.work.keeping
        moment = datetime.now(UTC)
        leaves = any(page.blank_fill is not BlankFill.SCAN for page in pages)
        size = self.size() if leaves else None
        full = book_record.image_policy.full_format(ColorMode.BILEVEL)
        pending = False
        for page in pages:
            key = PageStageKey(page.id, Stage.PAGE_ORDER)
            if size is None or page.blank_fill is BlankFill.SCAN:
                keeping.records.clear(repos, key)
                continue
            sources = self._paper_sources(page) if page.blank_fill is BlankFill.PAPER else None
            leaf = keeping.base_versions.blank(page=page, size=size, full=full, moment=moment, paper_from=sources)
            made = repos.page_versions.find(leaf.id)
            if made is not None and made.state is VersionState.READY:
                # The same leaf was made before, so it is current again without the job
                keeping.records.set_head(repos, key, head_version_id=made.id, recipe_id=None)
                continue
            if made is None:
                repos.page_versions.add(leaf)
            pending = True
        return pending

    def _median_size(self) -> PageSize:
        """Return the median size of the pages of the book that are cut from a scan.

        The width and the height are the medians of those of the included pages cut from a scan, taken apart, and the
        resolution is the median of theirs, or unknown when no page has one. The median does not follow one fold-out
        map or one cropped scan.

        :returns: The median size.
        :rtype: PageSize
        :raises ConflictError: If no page of the book has a recorded size yet.
        """
        if not (sizes := self.work.repos.page_versions.base_sizes()):
            raise ConflictError(NO_BOOK_IMAGE)
        resolutions = [size.dpi for size in sizes if size.dpi is not None]
        return PageSize(
            width_px=round(statistics.median(size.width_px for size in sizes)),
            height_px=round(statistics.median(size.height_px for size in sizes)),
            dpi=round(statistics.median(resolutions), 1) if resolutions else None,
        )

    def _paper_sources(self, page: Page) -> list[PageVersionId]:
        """Find the versions of the pages nearest to a leaf whose paper the leaf takes the colour of.

        The pages looked at are those of text in a window round the leaf: pages cut from a scan and part of the book,
        which are not leaves themselves and have an image that is ready. The nearest ones are taken, and a leaf with
        none has none to take, which makes it white.

        :param page: The page that becomes a leaf.
        :type page: Page
        :returns: The identifiers of the versions the pages show.
        :rtype: list[PageVersionId]
        """
        pages = self.work.repos.pages
        position = pages.count_before(page)
        first = max(0, position - self.PAPER_RADIUS)
        window = pages.list_window(SliceRequest(offset=first, limit=position - first + self.PAPER_RADIUS + 1))
        near = sorted(
            (
                overview
                for overview in self.work.overviews.of(window.items, first, numbered=False)
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
