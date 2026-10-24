"""The work of the processing jobs the board runs: a run of a stage, a preview, tiles, a collection and a measure.

Each entry point takes its job and the control the job reports through, does the tasks of the job one after the other,
and returns how the job ended. A task is the processing of one page by one plugin, which is one step of a recipe on one
page; a tile cutting counts a version, a collection a batch of deletions, a measure of the book the one reading of
every page's box, and the detection of content a page. The control tells the board after each task, which is where the
job learns that it was cancelled, so a cancelled job stops between two tasks and never inside one. The parameters of a
job are read into the value of its kind, and a job whose parameters are not valid ends as failed with the reason. An
error nobody expected passes to the board, which logs it and fails the job.

A run goes by the pages of the stage and the steps of the recipe, which is the recipe of the kind of each page. A run of
the whole book leaves out the pages whose result of the stage is fresh already (``RunPlan``), so a run after an
interruption or a restart of the process makes only what is missing. The mode of the run says what it does first with
the settings and the manual edits of the pages, which it keeps unless it was asked to take them away. A recipe whose
normalize step leaves the page size to the book is run twice over: the pages are first taken as far as the step to find
their content boxes, then the size of the book is worked out once from all of them, and the pages are run by it, so
every page comes out of one size without a press of the button of the measure. The run reads the current version of the
nearest earlier stage of a page, finds the versions it made before by their identifier, makes only what is new, and
makes the last version the current one of the stage. A page that fails is recorded as failed and the job goes on to the
next, and the job succeeds when it processed at least one page. When it ends well it queues a collection of the
book's versions that are no longer current, behind itself, so their files go as soon as the run has replaced them,
without the user asking. A run of the page split that made pages is followed by the detection of the content of the
pages that have none.

A measure of the book reads the content boxes the normalize step recorded on every page and writes the parameters of
that step of every Geometry recipe, which marks the pages of those recipes stale.

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

from bookreviver.domain.enums import JobState, Rendition, RunOutcome, SpanAttribute, SpanName, Stage, VersionState
from bookreviver.domain.errors import DomainError, NotFoundError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import JobEnd, StageRun, StepPreview, TileCut, VersionCollection
from bookreviver.domain.version_chains import readers_first
from bookreviver.services.book_measure import BookMeasure, leaves_to_the_book
from bookreviver.services.content_detection import ContentDetector
from bookreviver.services.run_plans import RunPlan
from bookreviver.services.stage_runs import PreviewRun, RecipeRun
from bookreviver.services.version_clearing import VersionClearing

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.domain.entities import BookRecord, Job, Page, PageVersion, Recipe
    from bookreviver.domain.ids import PageId, PageVersionId, RecipeId
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.book import Book
    from bookreviver.services.book_measure import BlockMeasure
    from bookreviver.services.books import BooksService
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
        books: BooksService,
        runtime: StageRuntime,
        parts: ProcessingParts,
        pages: PageService,
    ) -> None:
        """Work over the ports of one job.

        :param books: Books service of the job, which opens the book the job runs on, whose blocks hold the work as it
                      goes.
        :type books: BooksService
        :param runtime: The runner, the catalogue, the publisher, the clock and the size of a preview.
        :type runtime: StageRuntime
        :param parts: The recipes, the stage records and the job starter over the unit of work.
        :type parts: ProcessingParts
        :param pages: The page service, which queues the detection of the content of the pages a split made.
        :type pages: PageService
        """
        self._books = books
        self._runtime = runtime
        self._parts = parts
        self._recipes = parts.recipes
        self._records = parts.records
        self._clock = parts.clock
        self._measure = BookMeasure(recipes=parts.recipes, records=parts.records)
        self._detector = ContentDetector(books=books, runtime=runtime, records=parts.records)
        self._starter = parts.starter
        self._pages = pages

    async def run_stage(self, job: Job, control: JobControl) -> JobEnd:
        """Run a ``run-stage`` job: the recipe over the pages, one page after the other.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed when no page could be processed or the job is not valid, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            outcomes = await self._run_pages(job, control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        done, failed = outcomes.count(RunOutcome.DONE), outcomes.count(RunOutcome.FAILED)
        if failed and not done:
            return JobEnd(state=JobState.FAILED, error=NO_PAGE_PROCESSED)
        # The collection is put behind this job, so the book is never free between the two
        self._starter.queue_collection(job.book_id)
        if done and job.stage is Stage.PAGE_SPLIT:
            self._pages.detect_new_pages(job.book_id)
        return JobEnd(state=JobState.SUCCEEDED)

    async def preview_step(self, job: Job, control: JobControl) -> JobEnd:
        """Run a ``preview-step`` job: the steps of a form on the previews of a page, up to one of them.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed with the reason when a step cannot be made, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            await self._preview(job, control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        return JobEnd(state=JobState.SUCCEEDED)

    async def cut_tiles(self, job: Job, control: JobControl) -> JobEnd:
        """Run a ``cut-tiles`` job: the tile pyramids of some versions, each announced when it is cut.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed when its parameters are not valid, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            cut = TileCut.from_map(job.params)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        book = await self._books.open(job.book_id)
        control.plan(len(cut.version_ids))
        for version_id in cut.version_ids:
            await self._cut_version(book, version_id)
            await control.task_done()
        return JobEnd(state=JobState.SUCCEEDED)

    async def collect_versions(self, job: Job, control: JobControl) -> JobEnd:
        """Run a ``collect-versions`` job: delete the old versions that nothing needs, with their files.

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
            left = await self._collect(job, control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        if left:
            return JobEnd(state=JobState.FAILED, error=VERSIONS_LEFT.format(count=left))
        return JobEnd(state=JobState.SUCCEEDED)

    async def measure_book(self, job: Job, control: JobControl) -> JobEnd:
        """Run a ``measure-book`` job: the median line height and page size of the book, written into its recipe.

        The measure reads the boxes the pages were placed by and writes one result, so it is one task of its own.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed with the reason when the book cannot be measured, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        control.plan(1)
        try:
            await self._measure.run(await self._books.open(job.book_id))
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        await control.task_done()
        return JobEnd(state=JobState.SUCCEEDED)

    async def detect_content(self, job: Job, control: JobControl) -> JobEnd:
        """Run a ``detect-content`` job: what each page shows, written into the pages as a proposal.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through, and learns of its cancellation from, after each task.
        :type control: JobControl
        :returns: How the job ended: failed when no page could be read, else succeeded.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        try:
            read, failed = await self._detector.run(job, control)
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        if failed and not read:
            return JobEnd(state=JobState.FAILED, error=NO_CONTENT_DETECTED)
        return JobEnd(state=JobState.SUCCEEDED)

    async def _run_pages(self, job: Job, control: JobControl) -> list[RunOutcome]:
        """Run the recipe of a ``run-stage`` job over its pages, a task for each step of each page.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: What the run came to on each page it went over, in book order.
        :rtype: list[RunOutcome]
        :raises DomainError: If the parameters of the job are not valid, or the recipe or a page it names is gone.
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        run = StageRun.from_map(job.params)
        book = await self._books.open(job.book_id)
        book_record = await book.read(lambda repos: repos.book_record.get())
        plan = RunPlan(recipes=self._recipes, clock=self._clock, book=book, run=run)
        pages = await plan.pages()
        executor = RecipeRun(
            book=book, book_record=book_record, runtime=self._runtime, control=control, parts=self._parts
        )
        recipes = await plan.recipes()
        # The work the mode takes away goes first, as one batch, so one undo gives it back on every page
        await plan.apply_mode()
        control.plan(
            sum(
                executor.task_count([step for _, step in recipes[page.id].indexed_steps_through(run.through_step)])
                for page in pages
            )
        )
        measured = [page for page in pages if leaves_to_the_book(recipes[page.id], through_step=run.through_step)]
        if measured:
            await self._measure_book(executor, measured, recipes, run)
        return [await self._run_page(book, executor, page, recipes[page.id], run) for page in pages]

    async def _measure_book(
        self, executor: RecipeRun, pages: Sequence[Page], recipes: Mapping[PageId, Recipe], run: StageRun
    ) -> None:
        """Take the pages as far as the normalize step, and work out the page size of the book from their content boxes.

        A page that fails on the way is left for the run to fail with its reason, and has no box. Every step of the
        pass is a task of the job, which the executor counts as it makes the versions.

        :param executor: Runner of recipes of the book, which holds the size it works out for the pages of a recipe.
        :type executor: RecipeRun
        :param pages: The pages whose recipe leaves a field of the normalize step to the book.
        :type pages: Sequence[Page]
        :param recipes: The recipe of each page.
        :type recipes: Mapping[PageId, Recipe]
        :param run: What the job was asked to run, whose last step the pages are taken as far as.
        :type run: StageRun
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        boxes: dict[RecipeId, dict[PageId, BlockMeasure]] = {}
        for page in pages:
            try:
                box = await executor.measure(page, recipes[page.id], through_step=run.through_step)
            except Exception:
                logger.exception('The content box of page %s could not be measured', page.id)
                box = None
            if box is not None:
                boxes.setdefault(recipes[page.id].id, {})[page.id] = box
        for recipe in {recipes[page.id].id: recipes[page.id] for page in pages}.values():
            await executor.settle_book(recipe, boxes.get(recipe.id, {}), through_step=run.through_step)

    async def _run_page(self, book: Book, executor: RecipeRun, page: Page, recipe: Recipe, run: StageRun) -> RunOutcome:
        """Run the recipe on one page, so that whatever goes wrong on it fails the page and not the job.

        A cancellation is no fault of the page: ``JobCancelledError`` is no ``Exception``, so it passes this handler and
        the page keeps the state it had.

        :param book: The open book of the job.
        :type book: Book
        :param executor: Runner of recipes of the book.
        :type executor: RecipeRun
        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe to run.
        :type recipe: Recipe
        :param run: What the job was asked to run, whose confirmation and last step apply to the page.
        :type run: StageRun
        :returns: What the run came to. A page that was deleted while its stage ran is failed without a record, since
                  there is no page to hold one. The outcome is also set on the ``run.page`` span of the page.
        :rtype: RunOutcome
        """
        attributes = {SpanAttribute.PAGE: str(page.id), SpanAttribute.STAGE: recipe.stage}
        with self._runtime.tracer.span(SpanName.RUN_PAGE, attributes) as span:
            try:
                outcome = await executor.run(page, recipe, confirmed=run.confirm_unsplit, through_step=run.through_step)
            except Exception:
                logger.exception('The stage %s failed on page %s', recipe.stage, page.id)

                def fail(repos: BookRepositories, _book_record: BookRecord) -> None:
                    """Record that the stage failed on the page, unless the page was deleted meanwhile.

                    :param repos: Repositories of the transaction.
                    :type repos: BookRepositories
                    :param _book_record: The record of the book as the change finds it, which the record does not read.
                    :type _book_record: BookRecord
                    """
                    try:
                        repos.pages.get(page.id)
                    except NotFoundError:
                        logger.warning('Page %s was deleted while its stage %s ran', page.id, recipe.stage)
                        return
                    self._records.mark_failed(repos, page.id, recipe.stage, recipe_id=recipe.id)

                await book.change(fail)
                outcome = RunOutcome.FAILED
            span.set_attribute(SpanAttribute.RUN_OUTCOME, outcome)
            return outcome

    async def _preview(self, job: Job, control: JobControl) -> None:
        """Run the steps of a ``preview-step`` job.

        :param job: The running job.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :raises DomainError: If the parameters are not valid, the page is gone, or a step fails.
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        preview = StepPreview.from_map(job.params)
        book = await self._books.open(job.book_id)
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
        keys = BookKeys()
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
            await self._runtime.publisher.publish(PageVersionReady(book_id=book.id, version=cut_version))
        elif current is None:
            await book.assets.delete_prefix(keys.version_rendition(version, Rendition.TILES))

    async def _collect(self, job: Job, control: JobControl) -> int:
        """Delete the versions that nothing needs, batch by batch, with their files and their rows.

        The versions go readers first and a batch never holds a version and one that reads it, so no version is left
        without the input it reads. Every batch is committed by ``VersionClearing``, so a version whose files cannot be
        removed leaves the deletions of the others standing, and a batch is a task of the job.

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
        collection = VersionCollection.from_map(job.params)
        book = await self._books.open(job.book_id)
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
