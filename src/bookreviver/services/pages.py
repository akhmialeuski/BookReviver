"""Use cases of the pages of a book: its manifest, one page by identifier, adding, moving and deleting pages.

A page is addressed by its ``PageId`` and never by its number, because its position changes when pages are moved.

``PageService`` holds the use cases, and each is the head of a chain of classes. A use case that reads or writes opens
``PagesWork`` of ``services/pages_work.py`` in the work it hands to ``Book.read`` or ``Book.change``: the pages of that
transaction, with their overviews and their places in the order of the book. ``Leaves`` of ``services/leaves.py`` stands
the blank leaves of those pages. The ``prepare-pages`` job is ``PageImages`` of ``services/page_images.py``, which
writes the images no request writes, and the jobs of the pages are queued by ``PageJobs`` of the same module.

Every change of a book is the work of one ``Book.change``, which waits for any other change of that book, and it is
announced once it committed. The labels the pagination sections give the pages are computed when the pages are read,
so a change that adds, deletes or moves pages, or changes the kind or the inclusion of one, writes no label: it settles
the sections in the same work, which hands on the sections of the pages it deletes and refuses a number a section
cannot write, and the browser reads the pages and their labels again for the change. The work is synchronous and holds
the repositories of its transaction, so the events, the files and the queue stay outside it. A job queued after a
change is queued after the transaction of the change.

A page without a scan is added in a place of the book: a placeholder has no image and waits for a scan, and a blank leaf
gets the base version of a leaf, a white image of the median size of the book's pages. Binding a scan to a placeholder
makes it a page of that scan with the base version that copies the scan. None of these writes an image in the request:
the page and its pending base version are committed, and the ``prepare-pages`` job writes the files.
"""

from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, ClassVar

from attrs import evolve, frozen

from bookreviver.domain.errors import ConflictError, NotAPlaceholderError, NotFoundError, ScanAlreadyInBookError
from bookreviver.domain.jobs import ContentDetection
from bookreviver.domain.pages import BlankFill, NewPageOrigin, Page, PageKind, PageOrigin
from bookreviver.domain.slices import Slice
from bookreviver.domain.stages import PageStageKey, Stage
from bookreviver.domain.versions import VersionState
from bookreviver.services.leaves import Leaves
from bookreviver.services.page_images import SCAN_NOT_CUT, PageImages, PageJobs
from bookreviver.services.page_labels import PageLabels
from bookreviver.services.pages_work import PageParts, PagesWork
from bookreviver.services.stage_records import StageRecords

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.core.book import Book
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.changes import PageChanges
    from bookreviver.domain.ids import PageId, ScanId, SourceId
    from bookreviver.domain.jobs import Job, JobEnd
    from bookreviver.domain.pages import NewPage, PageAnchor, PageOverview
    from bookreviver.domain.slices import SliceRequest
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.base_versions import BaseVersions
    from bookreviver.services.book_steps import BookSteps
    from bookreviver.services.books import BooksService
    from bookreviver.services.steps import StepRunner

NOT_A_CUT_PAGE: str = 'The page {page_id} is not cut from a scan, so it has no scan to keep or to replace.'
NOT_A_BLANK_PAGE: str = 'The page {page_id} is not a blank page, so no leaf can stand in place of its scan.'
DETECTION_RUNNING: str = 'The content of the pages is being detected. Wait for it to end, or cancel it.'
BATCH_TOO_LONG: str = 'A request adds at most {limit} pages. Send the rest in another request.'


@frozen(kw_only=True)
class PageImaging:
    """What writes the images of base versions.

    :ivar base_versions: Builder of the rows of base versions.
    :ivar runner: Runner of the processors ``split.none`` and ``pages.blank``, and writer of their files.
    :ivar book_steps: Finder of the steps whose plugin reads the whole book, one of which may give the size of a leaf.
    """

    base_versions: BaseVersions
    runner: StepRunner
    book_steps: BookSteps


class PageService:
    """Pages of the acting account's books, addressed by identifier, their order, and access to their files.

    :cvar MAX_PAGES_PER_BATCH: Most pages one call of ``add_many`` adds, which bounds the rows of one transaction.
    """

    MAX_PAGES_PER_BATCH: ClassVar[int] = 1000

    def __init__(self, *, books: BooksService, order_keys: OrderKeys, imaging: PageImaging) -> None:
        """Work over the ports of one request or job.

        :param books: Books service of the request or job, which opens the books of the library the passes of the
                      start go over.
        :type books: BooksService
        :param order_keys: Builder of the order keys of moved and new pages.
        :type order_keys: OrderKeys
        :param imaging: The builder of base versions and the runner of their processors.
        :type imaging: PageImaging
        """
        self._books = books
        self._parts = PageParts(
            order_keys=order_keys,
            base_versions=imaging.base_versions,
            runner=imaging.runner,
            book_steps=imaging.book_steps,
            records=StageRecords(),
            labels=PageLabels(),
        )

    async def manifest(self, book: Book, request: SliceRequest, *, included_only: bool = False) -> Slice[PageOverview]:
        """Return a window of the book's pages in book order, every page with its position and base version.

        Pages kept out of the book are listed too, and count in the positions, so the client can offer to bring them
        back. With ``included_only`` they are left out of the listing and of the numbering, so the positions are those
        of the book a viewer shows.

        :param book: The open book.
        :type book: Book
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book.
        :type included_only: bool
        :returns: The pages of the window and the number of the pages listed.
        :rtype: Slice[PageOverview]
        """

        def work(repos: BookRepositories) -> Slice[PageOverview]:
            """Read the window of pages with the position and the base version of each.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The pages of the window and the number of the pages listed.
            :rtype: Slice[PageOverview]
            """
            window = repos.pages.list_window(request, included_only=included_only)
            overviews = self._pages(repos).overviews.of(window.items, request.offset)
            return Slice(items=overviews, total=window.total)

        return await book.read(work)

    async def get(self, book: Book, page_id: PageId) -> PageOverview:
        """Return one page of the book with its position and base version.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page, its position in the book and its base version.
        :rtype: PageOverview
        :raises NotFoundError: If the book has no such page.
        """

        def work(repos: BookRepositories) -> PageOverview:
            """Read the page of the book with its position and base version.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The page, its position in the book and its base version.
            :rtype: PageOverview
            :raises NotFoundError: If the book has no such page.
            """
            return self._pages(repos).overviews.one(repos.pages.get(page_id))

        return await book.read(work)

    async def update(self, book: Book, page_id: PageId, changes: PageChanges) -> PageOverview:
        """Change the printed number, the kind, the inclusion or the notes of a page, which writes its one row.

        A page that stops being blank shows its scan again in place of a leaf, so the record of its page order is
        removed and the stages after it become stale.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param changes: New values for the fields to change, None keeping a field.
        :type changes: PageChanges
        :returns: The changed page with its position.
        :rtype: PageOverview
        :raises NotFoundError: If the book has no such page.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> PageOverview:
            """Write the changed page, mark the stages its change makes stale, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :returns: The changed page with its position.
            :rtype: PageOverview
            :raises NotFoundError: If the book has no such page.
            """
            pages, records = self._pages(repos), self._parts.records
            before = repos.pages.get(page_id)
            page = changes.apply_to(before)
            changed = repos.pages.update(evolve(page, updated_at=datetime.now(UTC)))
            # A page that stops being blank gets its scan back in place of the leaf, so the leaf is no longer current
            if before.blank_fill is not BlankFill.SCAN and page.blank_fill is BlankFill.SCAN:
                records.clear(repos, PageStageKey(page_id, Stage.PAGE_ORDER))
            # The kind of the page decides which recipe of the stages after the page order processes it
            if before.recipe_kind is not page.recipe_kind:
                records.mark_content_stale(repos, page_id)
            # A page takes the values its group has for a step, so joining or leaving a group changes what it runs with
            if before.group_label != page.group_label:
                records.mark_group_stale(repos, page_id, {before.group_label, page.group_label})
            # A label, a kind or an inclusion changes what the sections give the pages, the page itself included
            if any(field is not None for field in (changes.label, changes.kind, changes.included)):
                pages.settle()
            return pages.overviews.one(changed)

        return await book.change(work)

    async def start_detection(self, book: Book, page_ids: Sequence[PageId] | None) -> Job:
        """Record a job that detects what some pages show, and queue it.

        The job writes the content type it finds into the pages it goes over, which gives a page the user set by hand
        back to the detection.

        :param book: The open book.
        :type book: Book
        :param page_ids: Pages to detect, or None for the pages that have no content type yet.
        :type page_ids: Sequence[PageId] | None
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the book has no such page.
        :raises ConflictError: If the content of the pages of the book is being detected already.
        """

        def check(repos: BookRepositories) -> None:
            """Require the pages to be pages of the book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :raises NotFoundError: If the book has no such page.
            """
            if page_ids is not None:
                repos.pages.list_by_ids(page_ids)

        await book.read(check)
        detection = ContentDetection(page_ids=None if page_ids is None else tuple(page_ids))
        if (job := PageJobs(book).detect(detection)) is None:
            raise ConflictError(DETECTION_RUNNING)
        return job

    def detect_new_pages(self, book: Book) -> None:
        """Queue the detection of the content of the pages that have none, which a job asks for when images are made.

        The detection follows from the job that made the images, so it runs right after it, ahead of the jobs the user
        queued meanwhile: a run asked for while the pages were being split finds what they show already detected, and
        so runs each page by the recipe of its kind. Nothing is queued while a detection of the book is queued or
        running, which goes over the pages it finds.

        :param book: The open book.
        :type book: Book
        """
        PageJobs(book).detect_new()

    async def add(self, book: Book, new_page: NewPage) -> PageOverview:
        """Add a placeholder or a blank leaf at a place of the book, or at its end.

        The pages after it move down, and those that turn over to the other side of the book, odd or even, are marked
        stale where a side has values.

        A placeholder has no image. A blank leaf gets the pending base version ``pages.blank``, of the size given or of
        the median of the pages of the book, whose white image the ``prepare-pages`` job writes. The request makes no
        image itself.

        :param book: The open book.
        :type book: Book
        :param new_page: The page to add, with its place and for a blank leaf perhaps its size.
        :type new_page: NewPage
        :returns: The new page with its position.
        :rtype: PageOverview
        :raises NotFoundError: If the book has no such anchor.
        :raises ConflictError: If a blank leaf has no size given and no page of the book has one to take the median of.
        """
        blank = new_page.origin is NewPageOrigin.BLANK

        def work(repos: BookRepositories, book_record: BookRecord) -> PageOverview:
            """Store the page and, for a blank leaf, its pending base version, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it.
            :type book_record: BookRecord
            :returns: The new page with its position.
            :rtype: PageOverview
            :raises NotFoundError: If the book has no such anchor.
            :raises ConflictError: If a blank leaf has no size given and no page of the book has one to take.
            """
            pages = self._pages(repos)
            leaves = self._leaves(pages)
            size = (new_page.size or leaves.size()) if blank else None
            page = pages.places.new(new_page, datetime.now(UTC))
            with pages.watching_sides():
                repos.pages.add(page)
            if size is not None:
                repos.page_versions.add(leaves.added(page, size, book_record))
            pages.settle()
            return pages.overviews.one(page)

        overview = await book.change(work)
        if blank:
            PageJobs(book).prepare()
        return overview

    async def add_many(self, book: Book, new_pages: Sequence[NewPage]) -> list[PageOverview]:
        """Add placeholders and blank leaves in one transaction, or add none of them.

        A page with an anchor goes to that place, and a page without one goes after the page before it in the list, or
        at the end of the book when it is the first. The pages that stand in one place are given their keys by one
        call of the key builder, in the order listed. The order of the book is read once for all the places before the
        pages are stored, and once for the positions of all the new pages after, so the book is never read for each
        place or each page. Two places that lie in the same gap between neighbours are not told apart, and the second
        is refused by the unique key of the order. The median size of the book is read once, for all the blank leaves
        without a size.

        :param book: The open book.
        :type book: Book
        :param new_pages: The pages to add, in the order they are to stand in where they share a place.
        :type new_pages: Sequence[NewPage]
        :returns: The new pages with their positions, in the order listed.
        :rtype: list[PageOverview]
        :raises ConflictError: If there are more pages than ``MAX_PAGES_PER_BATCH``, if the anchor of a page is not a
                               page of the book, or if a blank leaf has no size and no page of the book has one to
                               take the median of. The message of the last two names the index of the page.
        """
        if len(new_pages) > self.MAX_PAGES_PER_BATCH:
            raise ConflictError(BATCH_TOO_LONG.format(limit=self.MAX_PAGES_PER_BATCH))

        def work(repos: BookRepositories, book_record: BookRecord) -> list[PageOverview]:
            """Store the pages and the pending base versions of the blank leaves, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it.
            :type book_record: BookRecord
            :returns: The new pages with their positions in the order listed, none when no page was given.
            :rtype: list[PageOverview]
            :raises ConflictError: If the anchor of a page is not a page of the book, or a blank leaf has no size and
                                   no page of the book has one to take the median of.
            """
            if not new_pages:
                return []
            pages = self._pages(repos)
            leaves = self._leaves(pages)
            median = leaves.size_for(new_pages)
            batch = pages.places.batch(new_pages, datetime.now(UTC))
            with pages.watching_sides():
                repos.pages.add_many(batch)

            added = []
            for page, new_page in zip(batch, new_pages, strict=True):
                # The median was read above for every blank leaf without a size, so a leaf always has one
                if new_page.origin is NewPageOrigin.BLANK and (size := new_page.size or median) is not None:
                    added.append(leaves.added(page, size, book_record))
            repos.page_versions.add_many(added)

            pages.settle()
            return list(pages.overviews.placed(batch))

        overviews = await book.change(work)
        if any(new_page.origin is NewPageOrigin.BLANK for new_page in new_pages):
            PageJobs(book).prepare()
        return overviews

    async def set_blank_fill(self, book: Book, page_ids: Collection[PageId], fill: BlankFill) -> None:
        """Choose what the image of blank pages cut from a scan is: the scan, a white leaf or the paper of the book.

        A leaf is the pending version of the page order that ``pages.blank`` makes, of the size of the pages of the
        book, and the ``prepare-pages`` job writes it, after which it becomes the current version of the page order and
        the stages after it read it. A leaf of the paper takes the colour of the nearest pages of text. Choosing the
        scan removes the record of the page order, so the page shows its scan again and runs through the stages as it
        did, and a leaf already made for the page is found again when it is chosen again. A page that has the choice
        already is left as it is, and when every page has it nothing is written. All the pages change in one
        transaction, or none of them.

        :param book: The open book.
        :type book: Book
        :param page_ids: Pages to change, each at most once.
        :type page_ids: Collection[PageId]
        :param fill: What the image of the pages becomes.
        :type fill: BlankFill
        :raises NotFoundError: If the book lacks one of the pages.
        :raises ConflictError: If a page is not cut from a scan, or a leaf is chosen for a page that is not blank, or no
                               page of the book has a recorded size to give the leaf.
        """

        def work(repos: BookRepositories, book_record: BookRecord) -> bool:
            """Write the choice of every page that changes it, and give effect to it.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it.
            :type book_record: BookRecord
            :returns: Whether a leaf waits for the ``prepare-pages`` job, which none does when every page has the
                      choice already.
            :rtype: bool
            :raises NotFoundError: If the book lacks one of the pages.
            :raises ConflictError: If a page is not cut from a scan, or a leaf is chosen for a page that is not blank,
                                   or no page of the book has a recorded size to give the leaf.
            """
            found = {page.id: page for page in repos.pages.list_by_ids(page_ids)}
            if missing := next((page_id for page_id in page_ids if page_id not in found), None):
                raise NotFoundError(missing)
            for page in found.values():
                if page.origin is not PageOrigin.SCAN or (fill is BlankFill.SCAN and page.scan_id is None):
                    raise ConflictError(NOT_A_CUT_PAGE.format(page_id=page.id))
                if fill is not BlankFill.SCAN and page.kind is not PageKind.BLANK:
                    raise ConflictError(NOT_A_BLANK_PAGE.format(page_id=page.id))
            changing = [page for page in found.values() if page.blank_fill is not fill]
            if not changing:
                return False

            moment = datetime.now(UTC)
            changed = [evolve(page, blank_fill=fill, updated_at=moment) for page in changing]
            repos.pages.update_many(changed)
            return self._leaves(self._pages(repos)).stand(changed, book_record)

        # The job is queued in a ``change`` of its own, so after the transaction that wrote the leaves
        if await book.change(work):
            PageJobs(book).prepare()

    async def remake_outdated_leaves(self) -> None:
        """Make again the leaves whose version of ``pages.blank`` was replaced, by the path that made them.

        A leaf is identified by the version of the processor too, so a leaf made by an older version is another version
        than the one the current processor makes. Every page order record of every book of the library whose current
        version is such a leaf is read, whatever its state, and its page gets the leaf of the current version as a
        pending version, which the ``prepare-pages`` job writes and makes the current version of the stage, which is
        fresh then. A page that has the pending leaf already keeps it. The start queues no job, since the board is empty
        and no job begins before the user asks for one, so the pending leaf waits for the next ``prepare-pages`` job,
        which a change of a page or a run queues, and that job takes every pending version of the book.

        The records of each book are read in a transaction of their own, and the book is written in a ``Book.change``
        of its own, which reads its pages again. It is a pass of the start, before any request is served.
        """

        def read_pages(repos: BookRepositories) -> list[PageId]:
            """List the pages whose page order is a replaced leaf.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The identifiers of the pages of the book.
            :rtype: list[PageId]
            """
            leaf = self._parts.base_versions.blank_leaf
            return [record.page_id for record in repos.page_stages.list_replaced(leaf.stage, leaf.ref)]

        def work(page_ids: Sequence[PageId], repos: BookRepositories, book_record: BookRecord) -> None:
            """Give the pages of one book that no longer show their scan the leaf their choice says.

            :param page_ids: Identifiers of the pages whose page order was a replaced leaf.
            :type page_ids: Sequence[PageId]
            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it.
            :type book_record: BookRecord
            :raises ConflictError: If no page of the book has a recorded size to give a leaf.
            """
            pages = [page for page in repos.pages.list_by_ids(page_ids) if page.blank_fill is not BlankFill.SCAN]
            self._leaves(self._pages(repos)).stand(pages, book_record)

        for book in await self._books.listed():
            page_ids = await book.read(read_pages)
            if not page_ids:
                continue
            await book.change(partial(work, page_ids))

    async def delete(self, book: Book, page_id: PageId) -> None:
        """Delete a page with its versions, and then its files, leaving its scan and the scan's source.

        The pages after it move up, and those that turn over to the other side of the book, odd or even, are marked
        stale where a side has values.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :raises NotFoundError: If the book has no such page.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> Page:
            """Renumber the pages without the page, and delete it.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :returns: The deleted page.
            :rtype: Page
            :raises NotFoundError: If the book has no such page.
            """
            pages = self._pages(repos)
            page = repos.pages.get(page_id)
            pages.settle(leaving=[page.id])
            with pages.watching_sides():
                repos.pages.delete(page.id)
            return page

        page = await book.change(work)
        await PageImages(self._parts, book).discard([page])

    async def attach_scan(
        self, book: Book, page_id: PageId, scan_id: ScanId, *, take_over: bool = False
    ) -> PageOverview:
        """Bind a scan to a placeholder, which becomes a page of that scan with the pending base version ``split.none``.

        The fields given to the placeholder stay, and its label is the scan's own label when it had none. A scan that
        another page shows already, such as the page the import made of it, is refused unless ``take_over`` is set,
        which deletes that page with its versions in the same transaction and its files after it. The request copies no
        image: the ``prepare-pages`` job does.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the placeholder.
        :type page_id: PageId
        :param scan_id: Identifier of the scan to bind.
        :type scan_id: ScanId
        :param take_over: Whether to take the scan from the pages that show it, deleting them.
        :type take_over: bool
        :returns: The page, which now shows the scan.
        :rtype: PageOverview
        :raises NotFoundError: If the book has no such page or scan.
        :raises NotAPlaceholderError: If the page is not a placeholder.
        :raises ScanAlreadyInBookError: If another page shows the scan and ``take_over`` is not set, the error naming
                                        those pages by their printed number or position.
        :raises ConflictError: If the scan is not cut yet.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> tuple[PageOverview, Sequence[Page]]:
            """Delete the pages that show the scan when asked to, and bind the scan to the placeholder.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :returns: The page that shows the scan now, and the pages that were deleted for it.
            :rtype: tuple[PageOverview, Sequence[Page]]
            :raises NotFoundError: If the book has no such page or scan.
            :raises NotAPlaceholderError: If the page is not a placeholder.
            :raises ScanAlreadyInBookError: If another page shows the scan and ``take_over`` is not set.
            :raises ConflictError: If the scan is not cut yet.
            """
            pages = self._pages(repos)
            placeholder = repos.pages.get(page_id)
            if placeholder.origin is not PageOrigin.PLACEHOLDER:
                raise NotAPlaceholderError
            scan = repos.scans.get(scan_id)
            if not scan.renditions.ready:
                raise ConflictError(SCAN_NOT_CUT)
            taken = repos.pages.list_for_scan(scan_id)
            if taken and not take_over:
                labels = pages.numbering(taken).labels
                raise ScanAlreadyInBookError(
                    [
                        f'p. {labels[page.id]}' if labels[page.id] else f'position {repos.pages.count_before(page) + 1}'
                        for page in taken
                    ]
                )
            if taken:
                pages.settle(leaving=[page.id for page in taken])
            with pages.watching_sides():
                for page in taken:
                    repos.pages.delete(page.id)
            moment = datetime.now(UTC)
            # The label the source printed on the scan becomes a manual label of a page its sections leave unnumbered
            shown = pages.numbering([placeholder]).labels[placeholder.id]
            page = evolve(
                placeholder,
                origin=PageOrigin.SCAN,
                scan_id=scan_id,
                slot=Page.WHOLE_SCAN,
                manual_label=placeholder.manual_label or (None if shown else scan.source_label or None),
                updated_at=moment,
            )
            repos.pages.update(page)
            repos.page_versions.add(
                self._parts.base_versions.split_none(page=page, scan=scan, state=VersionState.PENDING, moment=moment)
            )
            return pages.overviews.one(page), taken

        overview, taken = await book.change(work)
        await PageImages(self._parts, book).discard(taken)
        PageJobs(book).prepare()
        return overview

    async def prepare_images(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run a ``prepare-pages`` job: write the files of every pending or failed version, a task for each.

        The versions of the page split and the page order stages are read when the job starts, and each is written and
        stored on its own, so the viewer swaps an empty frame for the image of a page as soon as it is ready. A
        version that fails is stored as failed with its reason, the job goes on, and it ends failed if any version did,
        which the next job of the book takes up again. A job that was cancelled stops before its next version.

        The job holds no transaction while it writes files, only a short ``Book.change`` for each version, which reads
        the page again before it stores anything. When it has made images it queues the detection of the content of
        the pages that have none, and when a version was committed while it ran, a job of its own for that version. It
        is the entry point of a job.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each version.
        :type control: JobControl
        :returns: How the job ended: failed with the number of versions that could not be made, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two versions.
        """
        return await PageImages(self._parts, book).run(job, control)

    async def move(self, book: Book, page_id: PageId, anchor: PageAnchor) -> PageOverview:
        """Put one page before or after another, and renumber the pages whose number the sections now give differently.

        The pages that turn over to the other side of the book, odd or even, are marked stale where a side has values.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page to move.
        :type page_id: PageId
        :param anchor: Page the moved page is put next to, and the side of it.
        :type anchor: PageAnchor
        :returns: The page at its new place.
        :rtype: PageOverview
        :raises NotFoundError: If the book has no such page or anchor.
        :raises ConflictError: If the anchor is the page itself.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> PageOverview:
            """Give the page its new place, and renumber the pages.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :returns: The page at its new place.
            :rtype: PageOverview
            :raises NotFoundError: If the book has no such page or anchor.
            :raises ConflictError: If the anchor is the page itself.
            """
            pages = self._pages(repos)
            with pages.watching_sides():
                [moved] = pages.places.moved([repos.pages.get(page_id)], anchor)
                repos.pages.update(moved)
            pages.settle()
            return pages.overviews.one(moved)

        return await book.change(work)

    async def move_group(self, book: Book, page_ids: Collection[PageId], anchor: PageAnchor) -> None:
        """Put several pages in a run before or after another page, keeping the order they have in the book.

        The pages that turn over to the other side of the book, odd or even, are marked stale where a side has values.

        :param book: The open book.
        :type book: Book
        :param page_ids: Identifiers of the pages to move.
        :type page_ids: Collection[PageId]
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :raises NotFoundError: If the book lacks a page or the anchor.
        :raises ConflictError: If the anchor is one of the pages.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Put the pages in a run next to the anchor.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :raises NotFoundError: If the book lacks a page or the anchor.
            :raises ConflictError: If the anchor is one of the pages.
            """
            self._pages(repos).places.move_run(repos.pages.list_by_ids(page_ids), anchor)

        await book.change(work)

    async def move_source(self, book: Book, source_id: SourceId, anchor: PageAnchor) -> None:
        """Put every page whose scan belongs to a source in a run before or after another page.

        A source that has no page left in the book is not an error and moves nothing.

        :param book: The open book.
        :type book: Book
        :param source_id: Identifier of the source whose pages are moved.
        :type source_id: SourceId
        :param anchor: Page the run is put next to, and the side of it.
        :type anchor: PageAnchor
        :raises NotFoundError: If the book has no such source or anchor.
        :raises ConflictError: If the anchor is a page of the source.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Put the pages of the source in a run next to the anchor.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :raises NotFoundError: If the book has no such source or anchor.
            :raises ConflictError: If the anchor is a page of the source.
            """
            repos.sources.get(source_id)
            self._pages(repos).places.move_run(repos.pages.list_for_source(source_id), anchor)

        await book.change(work)

    def _pages(self, repos: BookRepositories) -> PagesWork:
        """Open the pages of a book for the work of one transaction.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The pages of the transaction, with their overviews, their places and their leaves.
        :rtype: PagesWork
        """
        return PagesWork(self._parts, repos)

    @staticmethod
    def _leaves(pages: PagesWork) -> Leaves:
        """Give the blank leaves of the pages of one transaction.

        :param pages: The pages of the transaction.
        :type pages: PagesWork
        :returns: What stands a blank leaf in place of the scan of a page, writing through the transaction of the pages.
        :rtype: Leaves
        """
        return Leaves(pages)
