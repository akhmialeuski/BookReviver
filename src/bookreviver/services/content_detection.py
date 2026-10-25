"""Detecting the content of the pages: what each page shows, written into the page as a proposal the user may change.

The job reads the preview of the base image of a page through the processor ``pages.content``, which finds the pictures
of the page and their colour, and writes the answer into the page. A job that names no pages goes over the pages of the
book that have an image and no content type yet, so a page that was detected, or that the user set by hand, is not read
again, and the covers, the endpapers and the blank pages are left out, since a page the user gave such a role to is not
a page of text or of pictures to be told apart. A job that names pages goes over those and only those, whatever they
have and whatever their kind, and a page the user had set by hand is given back to the detection.

Detection never writes over a change it did not read. A page the user changed while the job was working on it is read
again, and a type the user set meanwhile stays. A page whose kind changed has its stages after the page order marked
stale, since the recipe that made their versions is not the one the page now meets.
A page that cannot be read fails for itself and the job goes on.
"""

import logging
from datetime import UTC, datetime
from itertools import batched
from typing import TYPE_CHECKING, ClassVar

from attrs import evolve

from bookreviver.domain.enums import (
    ContentType,
    PageKind,
    PageOrigin,
    Rendition,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import ContentDetection
from bookreviver.services.books import book_pages
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import BookRecord, Job, Page, PageVersion
    from bookreviver.domain.ids import PageId
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.book import Book
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.stage_runs import StageRuntime

PROBE_KEY: str = 'pages.content'
logger = logging.getLogger(__name__)


class ContentDetector:
    """Detects the content of the pages of one job and writes it into them."""

    # The kinds of page a job that names no pages goes over: the others are a role the user gave the page
    KINDS: ClassVar[frozenset[PageKind]] = frozenset(
        {PageKind.TEXT, PageKind.TITLE, PageKind.PLATE, PageKind.FRONTISPIECE, PageKind.OTHER}
    )
    # How many pages are read from the database at once
    WINDOW: ClassVar[int] = 500

    def __init__(self, *, runtime: StageRuntime, records: StageRecords) -> None:
        """Work over the ports of one job.

        :param runtime: The runner of the processor that finds the content of an image.
        :type runtime: StageRuntime
        :param records: Writer of the stage records, which marks the stages of a page stale.
        :type records: StageRecords
        """
        self._runner = runtime.runner
        self._processors = runtime.processors
        self._records = records

    async def run(self, book: Book, job: Job, control: JobControl) -> tuple[int, int]:
        """Detect the content of the pages of a running job, a task for each page.

        Each page is written in a ``Book.change`` of its own, so what a page was given stays when the job is cancelled.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each page.
        :type control: JobControl
        :returns: How many pages were read and how many could not be, none of them when the processor is not installed.
        :rtype: tuple[int, int]
        :raises InvalidParametersError: If the parameters of the job are not those of a ``detect-content`` job.
        :raises NotFoundError: If the book of the job is gone, or the job names a page that is gone.
        :raises JobCancelledError: If the job was cancelled, which stops it between two pages.
        """
        detection = ContentDetection.from_map(job.params)
        # A machine without OpenCV offers no such processor, and then the kind of a page is all that says what it shows
        if all(spec.key != PROBE_KEY for spec in self._processors.specs()):
            return 0, 0
        pages = await self._pages(book, detection)
        control.plan(len(pages))
        read = failed = 0
        for window in batched(pages, self.WINDOW, strict=False):
            bases = await self._bases(book, window)
            for page in window:
                if (base := bases.get(page.id)) is not None:
                    outcome = await self._detect(book, page.id, base, forced=detection.page_ids is not None)
                    read += outcome is not None
                    failed += outcome is None
                await control.task_done()
        return read, failed

    async def _detect(self, book: Book, page_id: PageId, base: PageVersion, *, forced: bool) -> bool | None:
        """Read what the base image of one page shows and write it into the page.

        :param book: The open book owning the page.
        :type book: Book
        :param page_id: The page.
        :type page_id: PageId
        :param base: The ready base version of the page.
        :type base: PageVersion
        :param forced: Whether the user asked for this page by name, which gives a page set by hand back to detection.
        :type forced: bool
        :returns: None when the page could not be read, which is logged, else whether the page was written.
        :rtype: bool | None
        """
        try:
            content = await self._probe(book, base)
        except Exception:
            logger.exception('The content of page %s could not be detected', page_id)
            return None
        return await self._write(book, page_id, content, forced=forced)

    async def _pages(self, book: Book, detection: ContentDetection) -> Sequence[Page]:
        """Choose the pages a job goes over.

        :param book: The open book owning the pages.
        :type book: Book
        :param detection: What the job was asked to detect.
        :type detection: ContentDetection
        :returns: The pages in book order that have an image to read: the ones named, or those not detected yet.
        :rtype: Sequence[Page]
        :raises NotFoundError: If the job names a page that is gone.
        """

        def work(repos: BookRepositories) -> list[Page]:
            """Read the pages of the job from one snapshot of the book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The pages in book order that have an image to read.
            :rtype: list[Page]
            :raises NotFoundError: If the job names a page that is gone.
            """
            if detection.page_ids is not None:
                return [page for page in repos.pages.list_by_ids(detection.page_ids) if self._readable(page)]
            return [
                page
                for page in book_pages(repos.pages)
                if self._readable(page) and page.content_type is None and page.kind in self.KINDS
            ]

        return await book.read(work)

    @staticmethod
    def _readable(page: Page) -> bool:
        """Tell whether a page has an image of a scan to read, which a placeholder and a drawn leaf have not.

        :param page: The page.
        :type page: Page
        :returns: True for a page cut from a scan whose image is the scan.
        :rtype: bool
        """
        return page.origin is PageOrigin.SCAN and not page.is_leaf

    async def _bases(self, book: Book, pages: Sequence[Page]) -> dict[PageId, PageVersion]:
        """Read the base version of each page whose image is ready, which is the image a page starts from.

        :param book: The open book owning the pages.
        :type book: Book
        :param pages: Pages whose base versions are read in one query.
        :type pages: Sequence[Page]
        :returns: The latest ready base version of each page that has one.
        :rtype: dict[PageId, PageVersion]
        """
        versions = await book.read(lambda repos: repos.page_versions.list_base_versions([page.id for page in pages]))
        # The versions come earliest first, so the latest of a page that was cut again is the one that stays
        return {
            version.page_id: version
            for version in versions
            if version.state is VersionState.READY and version.renditions is not None and version.renditions.ready
        }

    async def _probe(self, book: Book, base: PageVersion) -> ContentType:
        """Tell what the image of a base version shows, by the processor, on its preview.

        :param book: The open book owning the page.
        :type book: Book
        :param base: The ready base version of the page.
        :type base: PageVersion
        :returns: The content type the processor found.
        :rtype: ContentType
        :raises NotFoundError: If the processor is not installed, or a file of the version is not stored.
        :raises DomainError: If the image cannot be read.
        """
        run = StepRun(
            processor_key=PROBE_KEY,
            params={},
            input_data=base.data,
            image=BookKeys.version_rendition(base, Rendition.PREVIEW),
            scale=VersionScale.PREVIEW,
        )
        async with self._runner.execute(book, run) as result:
            [output] = result.outputs
        return ContentType(output.data[VersionData.CONTENT_TYPE])

    async def _write(self, book: Book, page_id: PageId, content: ContentType, *, forced: bool) -> bool:
        """Write what was detected into the page in one short ``Book.change``, which reads the page again first.

        The page is read inside the transaction, so a change of another request that committed after the probe stays.
        Nothing is written when the page is gone, when the user set its content by hand and did not ask for the page by
        name, or when the page already shows the content.

        :param book: The open book owning the page.
        :type book: Book
        :param page_id: Page that was read.
        :type page_id: PageId
        :param content: What the page was found to show.
        :type content: ContentType
        :param forced: Whether the user asked for this page by name, which gives a page set by hand back to detection.
        :type forced: bool
        :returns: True when the page was written.
        :rtype: bool
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> bool:
            """Write the content type into the page, and mark its later stages stale when its kind changed.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the write does not read.
            :type _book_record: BookRecord
            :returns: Whether the content type was written.
            :rtype: bool
            """
            try:
                before = repos.pages.get(page_id)
            except NotFoundError:
                return False
            if before.content_by_hand and not forced:
                return False
            after = evolve(before, content_type=content, content_by_hand=False, updated_at=datetime.now(UTC))
            if after == evolve(before, updated_at=after.updated_at):
                return False
            repos.pages.update(after)
            if before.recipe_kind is not after.recipe_kind:
                self._records.mark_content_stale(repos, page_id)
            return True

        return await book.change(work)
