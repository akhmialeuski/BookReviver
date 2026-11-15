"""Detecting the content of the pages: what each page shows, written into the page as a proposal the user may change.

The job reads the preview of the base image of a page through the processor of the role
``ProcessorRole.CONTENT_PROBE``, which finds the pictures of the page and their colour, and writes the answer into the
page. A job that names no pages goes over the pages of the
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
from functools import partial
from itertools import batched
from typing import TYPE_CHECKING, ClassVar

from attrs import evolve

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.images import Rendition
from bookreviver.domain.jobs import ContentDetection
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.pages import ContentType, PageKind, PageOrigin
from bookreviver.domain.processors import ProcessorRole
from bookreviver.domain.versions import VersionData, VersionScale
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.core.book import Book
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import PageId
    from bookreviver.domain.jobs import Job
    from bookreviver.domain.pages import Page
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.stage_runs import StageRuntime

logger = logging.getLogger(__name__)


class ContentDetector:
    """Detects the content of the pages of one job and writes it into them.

    The work is a chain of classes: ``DetectionWork`` is one job on one book, and ``PageDetection`` one page of it,
    which reaches the book, the runner and the records through the job it belongs to.

    :ivar runner: The runner of the processor that finds the content of an image.
    :ivar processors: The processors the application can run.
    :ivar records: Writer of the stage records, which marks the stages of a page stale.
    """

    def __init__(self, *, runtime: StageRuntime, records: StageRecords) -> None:
        """Work over the ports of one job.

        :param runtime: The runner of the processor that finds the content of an image.
        :type runtime: StageRuntime
        :param records: Writer of the stage records, which marks the stages of a page stale.
        :type records: StageRecords
        """
        self.runner = runtime.runner
        self.processors = runtime.processors
        self.records = records

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
        detection = job.asked(ContentDetection)
        # A machine without OpenCV offers no such processor, and then the kind of a page is all that says what it shows
        if all(spec.role is not ProcessorRole.CONTENT_PROBE for spec in self.processors.specs()):
            return 0, 0
        return await DetectionWork(self, book, detection).run(control)


class DetectionWork:
    """One ``detect-content`` job on one book.

    :cvar KINDS: The kinds of page a job that names no pages goes over: the others are a role the user gave the page.
    :cvar WINDOW: How many pages are read from the database at once.
    :ivar detector: What the job is run with: the runner, the processors and the records.
    :ivar book: The open book of the job.
    :ivar detection: What the job was asked to detect.
    """

    KINDS: ClassVar[frozenset[PageKind]] = frozenset(
        {PageKind.TEXT, PageKind.TITLE, PageKind.PLATE, PageKind.FRONTISPIECE, PageKind.OTHER}
    )
    WINDOW: ClassVar[int] = 500

    def __init__(self, detector: ContentDetector, book: Book, detection: ContentDetection) -> None:
        """Work on the detection of a book.

        :param detector: What the job is run with.
        :type detector: ContentDetector
        :param book: The open book.
        :type book: Book
        :param detection: What the job was asked to detect.
        :type detection: ContentDetection
        """
        self.detector = detector
        self.book = book
        self.detection = detection

    @property
    def forced(self) -> bool:
        """Whether the user asked for the pages by name, which gives a page set by hand back to the detection."""
        return self.detection.page_ids is not None

    async def run(self, control: JobControl) -> tuple[int, int]:
        """Detect the content of the pages of the job, a task for each page.

        :param control: What the job reports through, and learns of its cancellation from, after each page.
        :type control: JobControl
        :returns: How many pages were read and how many could not be.
        :rtype: tuple[int, int]
        :raises NotFoundError: If the job names a page that is gone.
        :raises JobCancelledError: If the job was cancelled, which stops it between two pages.
        """
        pages = await self.book.read(self._pages)
        control.plan(len(pages))
        read = failed = 0
        for window in batched(pages, self.WINDOW, strict=False):
            bases = await self.book.read(partial(self._bases, window))
            for page in window:
                if (base := bases.get(page.id)) is not None:
                    outcome = await PageDetection(self, base).make()
                    read += outcome is not None
                    failed += outcome is None
                await control.task_done()
        return read, failed

    def _pages(self, repos: BookRepositories) -> list[Page]:
        """Choose the pages the job goes over, from one snapshot of the book.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The pages in book order that have an image to read: the ones named, or those not detected yet.
        :rtype: list[Page]
        :raises NotFoundError: If the job names a page that is gone.
        """
        if self.detection.page_ids is not None:
            return [page for page in repos.pages.list_by_ids(self.detection.page_ids) if self._readable(page)]
        return [
            in_book.page
            for in_book in BookContent(repos).pages.all()
            if self._readable(in_book.page) and in_book.page.content_type is None and in_book.page.kind in self.KINDS
        ]

    @staticmethod
    def _bases(pages: Sequence[Page], repos: BookRepositories) -> Mapping[PageId, PageVersion]:
        """Read the base version of each page whose image is ready, which is the image a page starts from.

        :param pages: Pages whose base versions are read in one query.
        :type pages: Sequence[Page]
        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The latest ready base version of each page that has one.
        :rtype: Mapping[PageId, PageVersion]
        """
        return BookContent(repos).pages.bases([page.id for page in pages])

    @staticmethod
    def _readable(page: Page) -> bool:
        """Tell whether a page has an image of a scan to read, which a placeholder and a drawn leaf have not.

        :param page: The page.
        :type page: Page
        :returns: True for a page cut from a scan whose image is the scan.
        :rtype: bool
        """
        return page.origin is PageOrigin.SCAN and not page.is_leaf


class PageDetection:
    """The detection of the content of one page of a job, read from the base image of the page.

    :ivar work: The job the page belongs to, through which the book and the runner are reached.
    :ivar base: The ready base version of the page.
    """

    def __init__(self, work: DetectionWork, base: PageVersion) -> None:
        """Work on one page of a job.

        :param work: The job the page belongs to.
        :type work: DetectionWork
        :param base: The ready base version of the page.
        :type base: PageVersion
        """
        self.work = work
        self.base = base

    async def make(self) -> bool | None:
        """Read what the base image of the page shows and write it into the page.

        :returns: None when the page could not be read, which is logged, else whether the page was written.
        :rtype: bool | None
        """
        try:
            content = await self._probe()
        except Exception:
            logger.exception('The content of page %s could not be detected', self.base.page_id)
            return None
        return await self.work.book.change(partial(self._write, content))

    async def _probe(self) -> ContentType:
        """Tell what the image of the base version shows, by the processor, on its preview.

        :returns: The content type the processor found.
        :rtype: ContentType
        :raises NotFoundError: If the processor is not installed, or a file of the version is not stored.
        :raises DomainError: If the image cannot be read.
        """
        detector, base = self.work.detector, self.base
        run = StepRun(
            processor_key=detector.processors.of_role(ProcessorRole.CONTENT_PROBE).key,
            params={},
            input_data=base.data,
            image=BookKeys.version_rendition(base, Rendition.PREVIEW),
            scale=VersionScale.PREVIEW,
        )
        async with detector.runner.execute(self.work.book, run) as result:
            [output] = result.outputs
        return ContentType(output.data[VersionData.CONTENT_TYPE])

    def _write(self, content: ContentType, repos: BookRepositories, _book_record: BookRecord) -> bool:
        """Write what was detected into the page, and mark its later stages stale when its kind changed.

        It is the work of one short ``Book.change``, which reads the page again first, so a change of another request
        that committed after the probe stays. Nothing is written when the page is gone, when the user set its content
        by hand and did not ask for the page by name, or when the page already shows the content.

        :param content: What the page was found to show.
        :type content: ContentType
        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the write does not read.
        :type _book_record: BookRecord
        :returns: Whether the content type was written.
        :rtype: bool
        """
        page_id = self.base.page_id
        try:
            before = repos.pages.get(page_id)
        except NotFoundError:
            return False
        if before.content_by_hand and not self.work.forced:
            return False
        after = evolve(before, content_type=content, content_by_hand=False, updated_at=datetime.now(UTC))
        if after == evolve(before, updated_at=after.updated_at):
            return False
        repos.pages.update(after)
        if before.recipe_kind is not after.recipe_kind:
            self.work.detector.records.mark_content_stale(repos, page_id)
        return True
