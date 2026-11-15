"""The work of the processing jobs the board runs: a run of a stage, a preview, tiles, a collection and a measure.

Each entry point takes the book of the queue the job came from, the job and the control the job reports through, does
the tasks of the job one after the other, and returns how the job ended. A task is the processing of one page by one
plugin, which is one step of a recipe on one page; a tile cutting counts a version, a collection a batch of deletions, a
measure of the book the one reading of every page's box, and the detection of content a page. The control tells the
board after each task, which is where the job learns that it was cancelled, so a cancelled job stops between two tasks
and never inside one. The parameters of a job are read into the value of its kind, and a job whose parameters are not
valid ends as failed with the reason. An error nobody expected passes to the board, which logs it and fails the job.

A run of a stage is the work of ``StageRunJob`` of ``services/stage_jobs.py``. A page that fails is recorded as
failed and the job goes on to the next, and the job succeeds when it processed at least one page. When it ends well it
queues a collection of the book's versions that are no longer current, behind itself, so their files go as soon as the
run has replaced them, without the user asking. A run of the page split that made pages is followed by the detection
of the content of the pages that have none.

A measure of the book asks the plugins that read the whole book what their steps found on every page, and writes it
into the parameters of those steps in every recipe that has one, which marks the pages of those recipes stale.

A book processes one thing at a time, so a collection never overlaps a run that may be reusing the versions it
deletes. A collection chooses the versions and hands them in batches to ``VersionClearing``, readers first, which marks
each batch failed, removes the directories and then deletes the rows with the log of their marks, in one commit. A row
is never deleted before its files, and a version whose files could not be removed stays marked with the versions it
reads, so the next collection chooses them again, and the job ends as failed with their number. Removing a directory
that is gone is no error.
"""

import logging
from itertools import batched
from typing import TYPE_CHECKING, ClassVar

from bookreviver.domain.errors import DomainError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.images import Rendition
from bookreviver.domain.jobs import JobEnd, JobState, StageRun, StepPreview, TileCut, VersionCollection
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.runs import RunOutcome
from bookreviver.domain.stages import Stage
from bookreviver.domain.version_chains import readers_first
from bookreviver.domain.versions import VersionState
from bookreviver.services.book_measure import BookMeasure
from bookreviver.services.content_detection import ContentDetector
from bookreviver.services.stage_jobs import StageRunJob
from bookreviver.services.stage_runs import PreviewRun
from bookreviver.services.version_clearing import VersionClearing

if TYPE_CHECKING:
    from bookreviver.core.book import Book
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import PageVersionId
    from bookreviver.domain.jobs import Job
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.pages import PageService
    from bookreviver.services.processing_parts import ProcessingParts
    from bookreviver.services.stage_runs import StageRuntime

NO_PAGE_PROCESSED: str = 'No page could be processed. The state of the stage of each page says why.'
NO_CONTENT_DETECTED: str = 'The content of no page could be detected. The log of the worker says why.'
VERSIONS_LEFT: str = (
    '{count} versions could not be deleted, because their files could not be removed. The log says why.'
)

logger = logging.getLogger(__name__)


class ProcessingJobs:
    """Does the work of the processing jobs for the board that runs them.

    :cvar COLLECTION_BATCH_SIZE: How many versions a collection deletes before it commits and counts a task.
    """

    COLLECTION_BATCH_SIZE: ClassVar[int] = 100

    def __init__(
        self,
        *,
        runtime: StageRuntime,
        parts: ProcessingParts,
        pages: PageService,
    ) -> None:
        """Work over the ports of one job.

        :param runtime: The runner, the processors and the size of a preview.
        :type runtime: StageRuntime
        :param parts: The recipes, the stage records and the job starter, which work on the book of each job.
        :type parts: ProcessingParts
        :param pages: The page service, which queues the detection of the content of the pages a split made.
        :type pages: PageService
        """
        self._runtime = runtime
        self._parts = parts
        self._measure = BookMeasure(recipes=parts.recipes, records=parts.records)
        self._detector = ContentDetector(runtime=runtime, records=parts.records)
        self._starter = parts.starter
        self._pages = pages

    async def run_stage(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run a ``run-stage`` job: the recipe over the pages, one page after the other.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed when no page could be processed or the job is not valid, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            outcomes = await StageRunJob(self._runtime, self._parts, book, job.asked(StageRun)).outcomes(control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        done, failed = outcomes.count(RunOutcome.DONE), outcomes.count(RunOutcome.FAILED)
        if failed and not done:
            return JobEnd(state=JobState.FAILED, error=NO_PAGE_PROCESSED)
        # The collection is put behind this job, so the book is never free between the two
        self._starter.queue_collection(book)
        if done and job.stage is Stage.PAGE_SPLIT:
            self._pages.detect_new_pages(book)
        return JobEnd(state=JobState.SUCCEEDED)

    async def preview_step(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run a ``preview-step`` job: the steps of a form on the previews of a page, up to one of them.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed with the reason when a step cannot be made, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            await self._preview(book, job, control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        return JobEnd(state=JobState.SUCCEEDED)

    async def cut_tiles(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run a ``cut-tiles`` job: the tile pyramids of some versions, each announced when it is cut.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed when its parameters are not valid, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            cut = job.asked(TileCut)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        control.plan(len(cut.version_ids))
        for version_id in cut.version_ids:
            await self._cut_version(book, version_id)
            await control.task_done()
        return JobEnd(state=JobState.SUCCEEDED)

    async def collect_versions(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run a ``collect-versions`` job: delete the old versions that nothing needs, with their files.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed with the number of versions left when some could not be deleted, or when
                  its parameters are not valid, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            left = await self._collect(book, job, control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        if left:
            return JobEnd(state=JobState.FAILED, error=VERSIONS_LEFT.format(count=left))
        return JobEnd(state=JobState.SUCCEEDED)

    async def measure_book(self, book: Book, _job: Job, control: JobControl) -> JobEnd:
        """Run a ``measure-book`` job: the median line height and page size of the book, written into its recipe.

        The measure reads the boxes the pages were placed by and writes one result, so it is one task of its own.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param _job: The running job, which the measure reads nothing of.
        :type _job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed with the reason when the book cannot be measured, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        control.plan(1)
        try:
            await self._measure.run(book)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        await control.task_done()
        return JobEnd(state=JobState.SUCCEEDED)

    async def detect_content(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run a ``detect-content`` job: what each page shows, written into the pages as a proposal.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed when no page could be read, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            read, failed = await self._detector.run(book, job, control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        if failed and not read:
            return JobEnd(state=JobState.FAILED, error=NO_CONTENT_DETECTED)
        return JobEnd(state=JobState.SUCCEEDED)

    async def _preview(self, book: Book, job: Job, control: JobControl) -> None:
        """Run the steps of a ``preview-step`` job.

        :param book: The open book the job runs on.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :raises DomainError: If the parameters are not valid, the page is gone, or a step fails.
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        preview = job.asked(StepPreview)
        book_record, page = await book.read(lambda repos: (repos.book_record.get(), repos.pages.get(preview.page_id)))
        executor = PreviewRun(book=book, book_record=book_record, runtime=self._runtime, control=control)
        await executor.run(page, preview.stage, preview.steps, preview.step_index)

    async def _cut_version(self, book: Book, version_id: PageVersionId) -> None:
        """Cut the pyramid of one version, and mark the version as cut when it is still as it was read.

        A pyramid is cut with no change open, and the version is marked in a short ``Book.change`` that reads it again.
        A version that is gone or has changed since it was read keeps no mark, and a pyramid left for a version that is
        gone is removed.

        :param book: The open book owning the version.
        :type book: Book
        :param version_id: The version, which is passed by when it is gone, not ready, cut already or without an image.
        :type version_id: PageVersionId
        """
        version = await book.read(lambda repos: repos.page_versions.find(version_id))
        if (
            version is None
            or version.state is not VersionState.READY
            or version.tiles_ready
            or version.renditions is None
        ):
            return
        cut_version = await self._runtime.runner.cut_tiles(book, version)

        def mark(repos: BookRepositories, _book_record: BookRecord) -> tuple[bool, PageVersion | None]:
            """Store the version with its pyramid cut, when it is still as it was read.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the marking does not read.
            :type _book_record: BookRecord
            :returns: Whether the version was stored, and the version as the transaction found it, which is None when
                      it is gone.
            :rtype: tuple[bool, PageVersion | None]
            """
            found = repos.page_versions.find(version_id)
            if stored := found == version:
                repos.page_versions.update(cut_version)
            return stored, found

        stored, current = await book.change(mark)
        if stored:
            await book.events.publish(PageVersionReady(version=cut_version))
        elif current is None:
            await book.assets.delete_prefix(BookKeys.version_rendition(version, Rendition.TILES))

    async def _collect(self, book: Book, job: Job, control: JobControl) -> int:
        """Delete the versions that nothing needs, batch by batch, with their files and their rows.

        The versions go readers first and a batch never holds a version and one that reads it, so no version is left
        without the input it reads. Every batch is committed by ``VersionClearing``, so a version whose files cannot be
        removed leaves the deletions of the others standing, and a batch is a task of the job.

        :param book: The open book the job runs on.
        :type book: Book
        :param job: The running job.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How many versions were left, because their files could not be removed or a version that stays reads
                  them.
        :rtype: int
        :raises DomainError: If the parameters of the job are not valid.
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        collection = job.asked(VersionCollection)
        old = await book.read(lambda repos: repos.page_versions.collectable(collection.previews_older_than))
        groups = [list(batched(group, self.COLLECTION_BATCH_SIZE, strict=False)) for group in readers_first(old)]
        control.plan(sum(len(batches) for batches in groups))
        clearing = VersionClearing(book=book)
        deleted = 0
        for batches in groups:
            for batch in batches:
                deleted += await clearing.clear(batch)
                await control.task_done()
        return len(old) - deleted
