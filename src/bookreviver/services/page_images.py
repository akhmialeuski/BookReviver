"""The ``prepare-pages`` job of a book, which writes the images of base versions, and the jobs the pages queue.

A page without a scan, a placeholder bound to a scan and a leaf that stands in place of a scan are committed with a
pending base version, and no request writes an image. The job writes the files, a task for each version, and the work
is a chain of classes: ``PageImages`` is one job on one book, and ``BaseVersionWork`` one version of it, which reaches
the book, the runner and the builder of base versions through the job it belongs to.

The files of a base version are made by the processors of the roles ``ProcessorRole.SCAN_COPY`` and
``ProcessorRole.BLANK_LEAF``, which a ``StepRunner`` runs as it runs the steps of a recipe, and a base version that is
ready becomes the current version of its stage in the record of the page, so the page split and the page order stage
read like any other. The job holds no transaction while it writes files, only a short ``Book.change`` for each
version, which reads the page again before it stores anything.

``PageJobs`` queues the jobs of the pages of one book. A book has one ``prepare-pages`` job and one ``detect-content``
job at a time, so two jobs never write the files of one version, or the content of one page, together.
"""

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.errors import ConflictError, DomainError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.ids import JobId
from bookreviver.domain.jobs import ContentDetection, Job, JobEnd, JobKind, JobState
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.pages import PageOrigin
from bookreviver.domain.stages import PageStageKey
from bookreviver.domain.step_settings import BlankParam, PaperFill
from bookreviver.domain.versions import PageSize, VersionData, VersionState
from bookreviver.services.pages_work import PagesWork
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.book import Book
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.jobs import JobWork
    from bookreviver.domain.pages import Page
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.pages_work import PageParts

SCAN_NOT_CUT: str = 'The scan has no image yet. Bind it after its import has cut it.'
SCAN_DELETED: str = 'The scan of the page was deleted, so its image cannot be copied.'
PAGES_FAILED: str = '{count} of the page images could not be made. They are tried again by the next job.'
UNEXPECTED_FAILURE: str = (
    'The images of the pages could not be made because of an unexpected error. It has been logged.'
)

logger = logging.getLogger(__name__)


class PageJobs:
    """The jobs the pages of one book queue: the writing of their images and the detection of their content.

    The check of the active jobs and the put of the new one are one step of the event loop, with no ``await`` between
    them, so two requests never pass the check together.

    :ivar book: The open book whose queue the jobs are put on.
    """

    def __init__(self, book: Book) -> None:
        """Queue the jobs of the pages of a book.

        :param book: The open book.
        :type book: Book
        """
        self.book = book

    def prepare(self, *, after: JobId | None = None) -> Job | None:
        """Queue a ``prepare-pages`` job, unless one is active.

        A job that is running when a version is stored may have read its versions before it, which is why the job
        looks again when it ends and queues a job that follows it.

        :param after: The running job the new one follows, which is not counted as the active job, or None.
        :type after: JobId | None
        :returns: The job as the board holds it, queued, or None when one is queued or running already.
        :rtype: Job | None
        """
        return self._put(JobKind.PREPARE_PAGES, None, after=after)

    def detect(self, detection: ContentDetection) -> Job | None:
        """Queue a ``detect-content`` job, unless one is active.

        :param detection: The pages to detect the content of.
        :type detection: ContentDetection
        :returns: The job as the board holds it, queued, or None when one is queued or running already.
        :rtype: Job | None
        """
        return self._put(JobKind.DETECT_CONTENT, detection)

    def detect_new(self) -> None:
        """Queue the detection of the content of the pages that have none, right behind the running job.

        The detection follows from the job that made the images, so it runs right after it, ahead of the jobs the user
        queued meanwhile: a run asked for while the pages were being split finds what they show already detected, and
        so runs each page by the recipe of its kind. Nothing is queued while a detection of the book is queued or
        running, which goes over the pages it finds.
        """
        if not self._active(JobKind.DETECT_CONTENT):
            self.book.jobs.put(self._new(JobKind.DETECT_CONTENT, ContentDetection()), next_up=True)

    def _put(self, kind: JobKind, work: JobWork | None, *, after: JobId | None = None) -> Job | None:
        """Put a job on the board, unless one of its kind is active.

        :param kind: What the job does.
        :type kind: JobKind
        :param work: What the job was asked to do, as the value of its kind, or None for a job that takes nothing.
        :type work: JobWork | None
        :param after: The running job the new one follows, which is not counted as the active job of the kind, or None.
        :type after: JobId | None
        :returns: The job as the board holds it, queued, or None when a job of the kind is queued or running already.
        :rtype: Job | None
        """
        if self._active(kind, but=after):
            return None
        return self.book.jobs.put(self._new(kind, work))

    def _active(self, kind: JobKind, *, but: JobId | None = None) -> bool:
        """Tell whether a job of a kind is queued or running.

        :param kind: The kind of job.
        :type kind: JobKind
        :param but: A job that is not counted, or None.
        :type but: JobId | None
        :returns: Whether another job of the kind is active.
        :rtype: bool
        """
        return any(job.kind is kind and job.id != but for job in self.book.jobs.active())

    @staticmethod
    def _new(kind: JobKind, work: JobWork | None) -> Job:
        """Build a queued job.

        :param kind: What the job does.
        :type kind: JobKind
        :param work: What the job was asked to do, or None for a job that takes nothing.
        :type work: JobWork | None
        :returns: The job, queued as of now.
        :rtype: Job
        """
        return Job(id=JobId(uuid4()), kind=kind, work=work, created_at=datetime.now(UTC))


class PageImages:
    """One ``prepare-pages`` job on one book, and the files of its pages.

    :ivar parts: What the use cases of the pages are built with.
    :ivar book: The open book of the job.
    :ivar jobs: The jobs the pages of the book queue.
    """

    def __init__(self, parts: PageParts, book: Book) -> None:
        """Work on the images of the pages of a book.

        :param parts: What the use cases of the pages are built with.
        :type parts: PageParts
        :param book: The open book.
        :type book: Book
        """
        self.parts = parts
        self.book = book
        self.jobs = PageJobs(book)

    async def run(self, job: Job, control: JobControl) -> JobEnd:
        """Write the files of every pending or failed version, a task for each.

        The versions of the page split and the page order stages are read when the job starts, and each is written and
        stored on its own, so the viewer swaps an empty frame for the image of a page as soon as it is ready. A
        version that fails is stored as failed with its reason, the job goes on, and it ends failed if any version did,
        which the next job of the book takes up again. A job that was cancelled stops before its next version.

        When the job has made images it queues the detection of the content of the pages that have none, and when a
        version was committed while it ran, a job of its own for that version: a request that commits a version while
        a job runs finds the job active and queues none. A version that failed is not pending, so a version that
        cannot be made does not keep a job running again.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each version.
        :type control: JobControl
        :returns: How the job ended: failed with the number of versions that could not be made, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two versions.
        """
        versions = await self._waiting()
        control.plan(len(versions))
        failed = 0
        for version in versions:
            failed += not await BaseVersionWork(self, version).make()
            await control.task_done()
        if any(version.state is VersionState.PENDING for version in await self._waiting()):
            self.jobs.prepare(after=job.id)
        if versions:
            self.jobs.detect_new()
        if failed:
            return JobEnd(state=JobState.FAILED, error=PAGES_FAILED.format(count=failed))
        return JobEnd(state=JobState.SUCCEEDED)

    async def discard(self, pages: Sequence[Page]) -> None:
        """Remove every file of pages that were deleted, after the transaction that deleted them has ended.

        :param pages: The deleted pages, whose directories under ``assets/pages`` are removed.
        :type pages: Sequence[Page]
        """
        for page in pages:
            await self.book.assets.delete_prefix(BookKeys.page(page.id))

    async def _waiting(self) -> Sequence[PageVersion]:
        """Read the base versions of the book that are pending or failed.

        :returns: The versions the job has to make.
        :rtype: Sequence[PageVersion]
        """
        prepared = self.parts.base_versions.prepared_keys
        return await self.book.read(lambda repos: repos.page_versions.list_to_prepare(prepared))


class BaseVersionWork:
    """One base version of a ``prepare-pages`` job: its files are written and its new state is stored.

    A page deleted while the job runs takes its versions with it, so a version whose page is gone, before or after its
    files were written, is nothing to make and nothing that failed. A leaf that is not the choice of its page any
    longer is nothing to make either, and left pending it would be taken by every job, so it is deleted.

    :ivar images: The job the version belongs to, through which the book and the runner are reached.
    :ivar version: The pending or failed base version.
    """

    def __init__(self, images: PageImages, version: PageVersion) -> None:
        """Work on one base version of a job.

        :param images: The job the version belongs to.
        :type images: PageImages
        :param version: A pending or failed base version.
        :type version: PageVersion
        """
        self.images = images
        self.version = version

    async def make(self) -> bool:
        """Write the files of the version, and store its new state.

        A version that cannot be made is stored as failed with the reason in its data, and the job goes on with the
        others. The files are written outside any transaction, and the result is stored in a ``Book.change`` of its
        own.

        :returns: False when the version failed, and True when it is ready, nothing was left to make, or its page is
                  gone.
        :rtype: bool
        """
        version, book = self.version, self.images.book
        if (page := await book.read(self._page)) is None:
            return True
        # The page is read again in the work, since a request may have chosen the leaf again meanwhile
        if self._outdated(page):
            await book.change(self._drop)
            return True
        try:
            made = await self._write_files(page)
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

    def _page(self, repos: BookRepositories) -> Page | None:
        """Read the page of the version, which may have been deleted since the job read its versions.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The page, or None when it is gone.
        :rtype: Page | None
        """
        return PagesWork(self.images.parts, repos).found(self.version.page_id)

    def _outdated(self, page: Page) -> bool:
        """Tell whether the version is a leaf whose fill is not the choice of its page any longer.

        A page that got its scan back is the usual case.

        :param page: The page the version belongs to, as read.
        :type page: Page
        :returns: Whether the version is a leaf of another fill than the page has now.
        :rtype: bool
        """
        version = self.version
        return bool(
            version.processor == self.images.parts.base_versions.blank_leaf.ref
            and page.origin is PageOrigin.SCAN
            and page.blank_fill.value != version.params.get(BlankParam.FILL, PaperFill.WHITE.value)
        )

    def _drop(self, repos: BookRepositories, _book_record: BookRecord) -> bool:
        """Delete the version when its page still does not choose the leaf.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the work does not need.
        :type _book_record: BookRecord
        :returns: Whether the version was deleted.
        :rtype: bool
        """
        if (page := self._page(repos)) is None or not self._outdated(page):
            return False
        repos.page_versions.delete_many([self.version.id])
        return True

    async def _store(self, page: Page, version: PageVersion, *, current: bool) -> bool:
        """Store the new state of the version in one change, and make it the current version when it is ready.

        The work reads the page again. A page deleted while its files were written took its versions with it, so
        nothing is stored and the files are removed after the transaction. A leaf that stopped being the choice of its
        page meanwhile is not stored either, since making it current would undo that choice: it is deleted in the
        work, and its files are removed after the transaction.

        :param page: The page the version belongs to, as read before its files were written.
        :type page: Page
        :param version: The version with its new state.
        :type version: PageVersion
        :param current: Whether the version becomes the current version of its stage, which a ready version does.
        :type current: bool
        :returns: True when the version is stored, and False when its page is gone or the leaf is outdated.
        :rtype: bool
        """
        records = self.images.parts.records

        def work(repos: BookRepositories, book_record: BookRecord) -> tuple[bool, bool]:
            """Store the version unless its page is gone or no longer chooses the leaf.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it.
            :type book_record: BookRecord
            :returns: Whether the page is gone, and whether the leaf is outdated and was deleted.
            :rtype: tuple[bool, bool]
            """
            if self._page(repos) is None:
                return True, False
            if self._drop(repos, book_record):
                return False, True
            repos.page_versions.update(version)
            if current:
                records.set_head(
                    repos, PageStageKey(page.id, version.stage), head_version_id=version.id, recipe_id=None
                )
            return False, False

        book = self.images.book
        gone, outdated = await book.change(work)
        if gone:
            await self.images.discard([page])
        elif outdated:
            await book.assets.delete_prefix(BookKeys.version_directory(version))
        elif current:
            await book.events.publish(PageVersionReady(version=version))
        return not (gone or outdated)

    async def _write_files(self, page: Page) -> PageVersion:
        """Run the processor of the version and write its files: a copy of its scan, or a generated leaf.

        :param page: The page the version belongs to.
        :type page: Page
        :returns: The version as ready, with its transform, data and renditions.
        :rtype: PageVersion
        :raises ConflictError: If the scan to copy is deleted or not cut yet.
        :raises ValueError: If the version is neither a copy of a scan nor a leaf, or has no size to make.
        """
        book, runner = self.images.book, self.images.parts.runner
        book_record, run = await book.read(lambda repos: (repos.book_record.get(), self._run(repos, page)))
        async with runner.execute(book, run) as result:
            return await runner.store(
                book, self.version, result.outputs[0], policy=book_record.image_policy, tiles=True
            )

    def _run(self, repos: BookRepositories, page: Page) -> StepRun:
        """Read what the processor of the version reads: the image of its scan, or the pages a leaf takes its paper of.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param page: The page the version belongs to.
        :type page: Page
        :returns: The run of the processor.
        :rtype: StepRun
        :raises ConflictError: If the scan to copy is deleted or not cut yet.
        :raises ValueError: If the version is neither a copy of a scan nor a leaf, or has no size to make.
        """
        version, base_versions = self.version, self.images.parts.base_versions
        if version.processor == base_versions.scan_copy.ref:
            if page.scan_id is None:
                raise ConflictError(SCAN_DELETED)
            scan = repos.scans.get(page.scan_id)
            if not scan.renditions.ready:
                raise ConflictError(SCAN_NOT_CUT)
            return StepRun(
                processor_key=version.processor.key,
                params=version.params,
                input_data=scan.facts.as_data(),
                image=BookKeys.scan_rendition(scan, scan.renditions.full),
            )
        if version.processor == base_versions.blank_leaf.ref and PageSize.from_data(version.data) is not None:
            sources = repos.page_versions.list_by_ids(version.params.get(BlankParam.PAPER_FROM, []))
            return StepRun(
                processor_key=version.processor.key,
                params=version.params,
                input_data={},
                references=[
                    BookKeys.version_rendition(source, source.renditions.full)
                    for source in sources
                    if source.renditions is not None and source.renditions.ready
                ],
            )
        err_msg = f'Page version {version.id} made by {version.processor.key} has no files to write.'
        raise ValueError(err_msg)
