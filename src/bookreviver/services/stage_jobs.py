"""The run of a stage as a job: the pages a ``run-stage`` job goes over, each by the recipe of its kind.

A run goes by the pages of the stage and the steps of the recipe, which is the recipe of the kind of each page. A run
of the whole book leaves out the pages whose result of the stage is fresh already (``RunPlan``), so a run after an
interruption or a restart of the process makes only what is missing. The mode of the run says what it does first with
the settings and the manual edits of the pages, which it keeps unless it was asked to take them away. A recipe with a
step that leaves a field to the book, such as the page size, is run twice over: the pages are first taken as far as the
step, then its plugin is asked once what the book gives, from what the step found on all of them, and the pages are run
by it, so every page comes out of one size without a press of the button of the measure.

The work is a chain of classes. ``StageRunJob`` is one job on one book: it reads what the job goes over and plans its
tasks. ``PagesRun`` is the pages of the job with the recipe of each, which measures the book and runs the pages, and
below it ``RecipeRun`` of ``services/stage_runs.py`` runs a recipe on a page, a ``PageWork`` and a ``StepWork`` at a
time. A page that fails is recorded as failed and the job goes on to the next.
"""

import logging
from functools import partial
from typing import TYPE_CHECKING

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.runs import RunOutcome
from bookreviver.domain.spans import SpanAttribute, SpanName
from bookreviver.domain.stages import Stage
from bookreviver.services.run_plans import RunPlan
from bookreviver.services.stage_runs import RecipeRun
from bookreviver.services.worker_group import WorkerGroup

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.core.book import Book
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import PageId, RecipeId
    from bookreviver.domain.jobs import StageRun
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Recipe
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.processing_parts import ProcessingParts
    from bookreviver.services.stage_runs import StageRuntime

logger = logging.getLogger(__name__)


class StageRunJob:
    """One ``run-stage`` job on one book.

    :ivar runtime: The runner, the processors and how many pages are processed together.
    :ivar parts: The recipes, the stage records and the job starter.
    :ivar book: The open book of the job.
    :ivar run: What the job was asked to run.
    """

    def __init__(self, runtime: StageRuntime, parts: ProcessingParts, book: Book, run: StageRun) -> None:
        """Work on a run of a stage of a book.

        :param runtime: The runner, the processors and how many pages are processed together.
        :type runtime: StageRuntime
        :param parts: The recipes, the stage records and the job starter.
        :type parts: ProcessingParts
        :param book: The open book.
        :type book: Book
        :param run: What the job was asked to run.
        :type run: StageRun
        """
        self.runtime = runtime
        self.parts = parts
        self.book = book
        self.run = run

    async def outcomes(self, control: JobControl) -> list[RunOutcome]:
        """Run the recipe of the job over its pages, a task for each step of each page.

        :param control: What the job reports through.
        :type control: JobControl
        :returns: What the run came to on each page it went over, in book order.
        :rtype: list[RunOutcome]
        :raises DomainError: If the recipe or a page the job names is gone.
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        run, recipes_of_book = self.run, self.parts.recipes
        book_record = await self.book.read(lambda repos: BookContent(repos).record)
        plan = RunPlan(recipes=recipes_of_book, book=self.book, run=run)
        pages = await plan.pages()
        executor = RecipeRun(
            book=self.book, book_record=book_record, runtime=self.runtime, control=control, parts=self.parts
        )
        recipes = await plan.recipes()
        # The work the mode takes away goes first, as one batch, so one undo gives it back on every page
        await plan.apply_mode()
        control.plan(
            sum(
                executor.task_count([step for _, step in recipes[page.id].indexed_steps_through(run.through_step_id)])
                for page in pages
            )
        )
        pages_run = PagesRun(self, executor, recipes)
        await pages_run.measure_book(
            [
                page
                for page in pages
                if recipes_of_book.leaves_to_book(recipes[page.id], through_step_id=run.through_step_id)
            ]
        )
        # A split puts the right half of a spread into the book right after the left, at a place it reads from the
        # pages round it, so the pages of the page split are cut one after the other
        workers = WorkerGroup(1 if run.stage is Stage.PAGE_SPLIT else self.runtime.parallel_pages)
        return await workers.run(pages, pages_run.page)


class PagesRun:
    """The pages of a ``run-stage`` job with the recipe of each, which are measured and run.

    :ivar job: The job the pages belong to, through which the book and what was asked are reached.
    :ivar executor: Runner of recipes of the book, which holds what the book gives the pages of a recipe.
    :ivar recipes: The recipe of each page.
    """

    def __init__(self, job: StageRunJob, executor: RecipeRun, recipes: Mapping[PageId, Recipe]) -> None:
        """Run the pages of a job.

        :param job: The job the pages belong to.
        :type job: StageRunJob
        :param executor: Runner of recipes of the book.
        :type executor: RecipeRun
        :param recipes: The recipe of each page.
        :type recipes: Mapping[PageId, Recipe]
        """
        self.job = job
        self.executor = executor
        self.recipes = recipes

    async def measure_book(self, pages: Sequence[Page]) -> None:
        """Take the pages as far as the step that reads the book, and ask its plugin what the book gives the pages.

        A page that fails on the way is left for the run to fail with its reason, and is not read. Every step of the
        pass is a task of the job, which the executor counts as it makes the versions.

        :param pages: The pages whose recipe has a step that leaves a field to the book, none of which measures
                      nothing.
        :type pages: Sequence[Page]
        :raises JobCancelledError: If the job was cancelled, which stops it between two tasks.
        """
        if not pages:
            return
        recipes, through_step_id = self.recipes, self.job.run.through_step_id
        made: dict[RecipeId, dict[PageId, PageVersion]] = {}
        versions = await WorkerGroup(self.job.runtime.parallel_pages).run(pages, self._measured)
        for page, version in zip(pages, versions, strict=True):
            if version is not None:
                made.setdefault(recipes[page.id].id, {})[page.id] = version
        for recipe in {recipes[page.id].id: recipes[page.id] for page in pages}.values():
            await self.executor.settle_book(recipe, made.get(recipe.id, {}), through_step_id=through_step_id)

    async def page(self, page: Page) -> RunOutcome:
        """Run the recipe on one page, so that whatever goes wrong on it fails the page and not the job.

        A cancellation is no fault of the page: ``JobCancelledError`` is no ``Exception``, so it passes this handler and
        the page keeps the state it had.

        :param page: Page to process.
        :type page: Page
        :returns: What the run came to. A page that was deleted while its stage ran is failed without a record, since
                  there is no page to hold one. The outcome is also set on the ``run.page`` span of the page.
        :rtype: RunOutcome
        """
        run, recipe = self.job.run, self.recipes[page.id]
        attributes = {SpanAttribute.PAGE: str(page.id), SpanAttribute.STAGE: recipe.stage}
        with self.job.runtime.tracer.span(SpanName.RUN_PAGE, attributes) as span:
            try:
                outcome = await self.executor.run(
                    page, recipe, confirmed=run.confirm_unsplit, through_step_id=run.through_step_id
                )
            except Exception:
                logger.exception('The stage %s failed on page %s', recipe.stage, page.id)
                await self.job.book.change(partial(self._fail, page))
                outcome = RunOutcome.FAILED
            span.set_attribute(SpanAttribute.RUN_OUTCOME, outcome)
            return outcome

    async def _measured(self, page: Page) -> PageVersion | None:
        """Take one page as far as the step that reads the book, so that a fault of the page fails only the page.

        :param page: The page.
        :type page: Page
        :returns: The version of the step on the page, or None when the page could not be taken that far.
        :rtype: PageVersion | None
        """
        try:
            return await self.executor.measure(
                page, self.recipes[page.id], through_step_id=self.job.run.through_step_id
            )
        except Exception:
            logger.exception('Page %s could not be taken as far as the step that reads the book', page.id)
            return None

    def _fail(self, page: Page, repos: BookRepositories, _book_record: BookRecord) -> None:
        """Record that the stage failed on a page, unless the page was deleted meanwhile.

        :param page: The page the stage failed on.
        :type page: Page
        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the record does not read.
        :type _book_record: BookRecord
        """
        recipe = self.recipes[page.id]
        try:
            repos.pages.get(page.id)
        except NotFoundError:
            logger.warning('Page %s was deleted while its stage %s ran', page.id, recipe.stage)
            return
        self.job.parts.records.mark_failed(repos, page.id, recipe.stage, recipe_id=recipe.id)
