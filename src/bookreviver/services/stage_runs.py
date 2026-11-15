"""Running the steps of a recipe on the pages of a job, for a stage run and for a preview.

The classes here and beside them form one chain, each owning the next and holding what its methods share:

- ``StageRuns`` here is the top, built once: it holds the ``StageEngine`` of ``stage_work``, which runs the steps, with
  the recipes, the stage records and the order keys, and opens the work of a job for its book.
- ``StageWork`` (``stage_work``) is the work of one job on one book: the book, its record and the control of the job,
  with the engine it belongs to. ``RecipeRun`` here is the work of a ``run-stage`` job and ``PreviewRun`` the one of a
  ``preview-step`` job.
- ``PageWork`` (``page_work``) is one stage on one page: the input of the stage. ``PageRun`` here is a page run by its
  recipe, which makes the last version the current one of the stage.
- ``StepChain`` (``step_work``) is the steps of that page, and ``StepWork`` one of them: it finds the version it made
  before, or makes it.

Each module imports the module of its owner and never the reverse, so this module, which makes the chain run, lies on
top of ``stage_work``, ``page_work``, ``step_work`` and ``spread_splits``.

A run reads the current version of the nearest earlier stage of the page, or the scan itself for the page split, and
makes one version for each step of the recipe, each reading the one before. ``PageRun`` ends a page's run by making the
last version the current one of the stage, which marks the later stages of the page stale, and by cutting its tile
pyramid. A run may stop at one step of the recipe, and the page is then recorded as stopped there. If the earlier
stage of the page is stale, or stopped short of its last step, it is run again first by its own recipe, which finds its
versions in the cache when nothing changed. ``PreviewRun`` runs the steps of a form on the previews of the images and
never changes what is current.

A step whose plugin has a half that reads the book (``BookStep``) may leave some of its parameters to the book. A run
of the whole book asks the plugin once, with the versions of the step its pages were just taken to, and keeps the
answer for every page of the recipe; a run of a page or a preview asks it for that page alone.

No transaction is held while a processor computes. A page deleted before a run ends is reported as skipped, and
nothing of the run is stored. A step on a page is a task of the job, which tells its control that the task is done
once the version is stored, outside any transaction. The control raises ``JobCancelledError`` there when the job was
cancelled, which is no failure of the page, so it passes every handler that records one.
"""

from typing import TYPE_CHECKING, override

from attrs import evolve, frozen

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import ConflictError, DomainError, NotFoundError
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.pages import Page
from bookreviver.domain.runs import RunOutcome
from bookreviver.domain.stages import PageStageKey, Stage
from bookreviver.domain.versions import VersionData, VersionScale, VersionState
from bookreviver.services.page_work import PageWork
from bookreviver.services.spread_splits import SpreadSplit
from bookreviver.services.stage_work import StageWork
from bookreviver.services.step_work import StepChain

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from datetime import datetime

    from bookreviver.core.book import Book
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import PageId, PageVersionId, RecipeId, StepId
    from bookreviver.domain.jobs import StepPreview
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_steps import PageStepState
    from bookreviver.domain.recipes import Recipe, Step
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.recipes import RecipeBook
    from bookreviver.services.spread_splits import Unsplit
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.stage_work import StageEngine
    from bookreviver.services.step_source import StepSource

NO_PREVIEW_INPUT: str = 'The page has no image to preview a step on.'
NO_STEP_TO_PREVIEW: str = 'Every step up to this one is switched off, so there is nothing to preview.'


@frozen(kw_only=True)
class RunBasis:
    """What a run of a recipe read to decide what to make, which the change that makes its result current reads again.

    :ivar input_id: Version the first step read, or None when it read a scan.
    :ivar recipe_updated_at: When the recipe the run followed was last saved.
    :ivar states: The settings and the manual edits of the steps of the stage on the page.
    :ivar undoing: The split the run undoes, or None.
    """

    input_id: PageVersionId | None
    recipe_updated_at: datetime
    states: tuple[PageStepState, ...]
    undoing: Unsplit | None


class PageRun(PageWork):
    """One page run by the recipe of its kind, whose last version becomes the current one of the stage.

    :ivar recipe: Recipe of the stage the page is run by.
    :ivar steps: The steps the run goes over: the ones that are on, up to the step it stops at.
    """

    def __init__(self, run: RecipeRun, page: Page, recipe: Recipe, *, through_step_id: StepId | None = None) -> None:
        """Run a page by a recipe for a job.

        :param run: The work of the ``run-stage`` job the page belongs to.
        :type run: RecipeRun
        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe of the stage to run.
        :type recipe: Recipe
        :param through_step_id: The last step to run, or None for every step that is on.
        :type through_step_id: StepId | None
        """
        super().__init__(run, page, recipe.stage)
        self._run = run
        self._through_step_id = through_step_id
        self.recipe = recipe
        self.steps = [step for _, step in recipe.indexed_steps_through(through_step_id)]

    async def run(self, *, confirmed: bool = False) -> RunOutcome:
        """Run the steps of the recipe on the page, and make the version of the last one current.

        The right half of a split spread is made by the run of its left half, so a run of the page split leaves it out.
        A run through a step makes the steps up to it, finding those before in the cache when their inputs did not
        change, and records the page as stopped there, so the stage after it does not read the page until the rest is
        run.

        Called with no transaction open. The earlier stages are brought up to date and the steps are run before the one
        ``Book.change`` that makes the version current, which reads the page and what the run went by again. A page
        deleted meanwhile, or a run whose basis changed, is skipped and nothing of the run is made current.

        :param confirmed: Whether the user confirmed that undoing a split deletes the right half of a spread.
        :type confirmed: bool
        :returns: Whether the page was processed, failed, or skipped for lack of an image, because it was deleted, or
                  because the input, the recipe, the settings or the edits it went by changed meanwhile.
        :rtype: RunOutcome
        """
        if self.stage is Stage.PAGE_SPLIT and self.page.slot > Page.LEFT_HALF:
            return RunOutcome.SKIPPED
        try:
            if (source := await self.source(VersionScale.FULL)) is None:
                return RunOutcome.SKIPPED
            if self._run.splits.splits(self.recipe):
                outcome = await self._split(source, confirmed=confirmed)
            else:
                outcome = await self._process(source, confirmed=confirmed)
        except DomainError:
            return await self._fail()
        return await self._fail() if outcome is RunOutcome.FAILED else outcome

    async def measured(self) -> PageVersion | None:
        """Make the steps of the recipe up to the step that reads the book, and give the version that step made.

        A run of the whole book gives every page what the step found on all of them, so it takes every page as far as
        the step first. The versions this makes are the ones the run finds again. Called with no transaction open.

        :returns: The version of the step on the page, or None when the page has no image or a step failed.
        :rtype: PageVersion | None
        """
        if (fields := self.work.engine.book_fields(self.steps)) is None:
            return None
        if (source := await self.source(VersionScale.FULL)) is None:
            return None
        return await StepChain(self, self.steps[: fields.position + 1]).measure(source)

    async def _split(self, source: StepSource, *, confirmed: bool) -> RunOutcome:
        """Split the page by the recipe, which is a task of the job like any step.

        The split is the only step of its recipe and makes its versions itself, outside the chain of steps that tells
        the control after each of them, so the task is counted here.

        :param source: What the split reads.
        :type source: StepSource
        :param confirmed: Whether the user confirmed that undoing a split deletes the right half of a spread.
        :type confirmed: bool
        :returns: What the split came to.
        :rtype: RunOutcome
        :raises DomainError: If the split cannot be made.
        :raises JobCancelledError: If the job was cancelled, which stops it before its next task.
        """
        outcome = await self._run.splits.split(self.page, self.recipe, source, confirmed=confirmed)
        await self.work.control.task_done()
        return outcome

    async def _process(self, source: StepSource, *, confirmed: bool) -> RunOutcome:
        """Run the steps of the recipe on the page and make the last version current.

        What the run goes by is read before the steps run, so the change that makes the result current can tell that
        none of it changed meanwhile.

        :param source: What the first step reads.
        :type source: StepSource
        :param confirmed: Whether the user confirmed that undoing a split deletes the right half of a spread.
        :type confirmed: bool
        :returns: What the run of the page came to.
        :rtype: RunOutcome
        :raises DomainError: If a processor is missing, its parameters do not fit, or the pyramid cannot be cut.
        """
        page, recipe, splits = self.page, self.recipe, self._run.splits

        def read(repos: BookRepositories) -> RunBasis:
            """Read what the run goes by from one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The basis of the run.
            :rtype: RunBasis
            """
            return RunBasis(
                input_id=source.version_id,
                recipe_updated_at=recipe.updated_at,
                states=tuple(repos.page_step_states.list_for_page(page.id, recipe.stage)),
                undoing=splits.undoing(repos, page, recipe, confirmed=confirmed),
            )

        basis = await self.work.book.read(read)
        if (version := await self._run_steps(evolve(source, book=await self._book(source)))) is None:
            return RunOutcome.FAILED
        return await self._apply(version, basis)

    async def _run_steps(self, source: StepSource) -> PageVersion | None:
        """Make the version of each step, and cut the pyramid of the last.

        The files of the pyramid are written here, and the row of the version learns that they are cut in the change of
        ``_apply``, which makes the version current.

        :param source: What the first step reads.
        :type source: StepSource
        :returns: The version of the last step run, or None when a step failed or there is no step.
        :rtype: PageVersion | None
        :raises DomainError: If a processor is missing, its parameters do not fit, or the pyramid cannot be cut.
        """
        version = await StepChain(self, self.steps).make(source)
        if version is None or version.state is not VersionState.READY:
            return None
        if version.renditions is not None and not version.tiles_ready:
            version = await self.work.engine.runner.cut_tiles(self.work.book, version)
        return version

    async def _apply(self, version: PageVersion, basis: RunBasis) -> RunOutcome:
        """Make the version of the last step the current one of the stage, in one change, and tell the browser after it.

        Called with no transaction open, and it opens a ``Book.change`` of its own. The page and the version are read
        again, so a page deleted while the steps ran writes nothing, and the page that undoes a split is the one the
        change reads, not the one the run started with. What the run went by is read again too: a change of the input,
        of the recipe, or of the settings and the edits of the page's steps marked the stage stale while the steps ran,
        and making the version current would hide that mark, so the run is skipped and nothing is written. Whatever
        this stores is rolled back if the change fails.

        :param version: Version of the last step, with its pyramid cut.
        :type version: PageVersion
        :param basis: What the run read before it computed, including the split it undoes.
        :type basis: RunBasis
        :returns: Done when the version is current, and skipped when what the run read changed meanwhile.
        :rtype: RunOutcome
        :raises NotFoundError: If the page or the version was deleted meanwhile.
        """
        run, key = self._run, PageStageKey(self.page.id, self.stage)
        recipe, stopped_at = self.recipe, self.recipe.stopped_at(self._through_step_id)

        def apply(repos: BookRepositories) -> tuple[bool, Unsplit | None]:
            """Make the version current, unless what the run went by changed meanwhile.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: Whether the version was made current, and what undoing the split deletes, or None.
            :rtype: tuple[bool, Unsplit | None]
            :raises NotFoundError: If the page or the version was deleted meanwhile.
            """
            holds, undoing = self._basis_holds(repos, basis)
            if not holds:
                return False, None
            stored = repos.page_versions.get(version.id)
            if version.tiles_ready and not stored.tiles_ready:
                repos.page_versions.update(evolve(stored, tiles_ready=True))
            run.runs.records.set_head(
                repos, key, head_version_id=version.id, recipe_id=recipe.id, through_step_id=stopped_at
            )
            if undoing is not None:
                run.splits.unsplit(repos, undoing)
            return True, undoing

        stored, undoing = await self.work.book.change(lambda repos, _book_record: apply(repos))
        if not stored:
            return RunOutcome.SKIPPED
        if undoing is not None:
            await run.splits.finish_unsplit(undoing)
        return RunOutcome.DONE

    def _basis_holds(self, repos: BookRepositories, basis: RunBasis) -> tuple[bool, Unsplit | None]:
        """Tell whether what the run went by is still what the book holds, in the transaction of the caller.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param basis: What the run read before it computed.
        :type basis: RunBasis
        :returns: Whether nothing the run went by changed, and what undoing the split deletes, or None.
        :rtype: tuple[bool, Unsplit | None]
        :raises NotFoundError: If the page or the recipe was deleted meanwhile.
        """
        page_id, recipe = self.page.id, self.recipe
        fresh = repos.pages.get(page_id)
        current = BookContent(repos).pages.inputs(recipe.stage, [page_id]).get(page_id)
        undoing = None if basis.undoing is None else self._run.splits.undoing(repos, fresh, recipe, confirmed=True)
        holds = (
            (basis.input_id is None or (current is not None and current.id == basis.input_id))
            and repos.recipes.get(recipe.id).updated_at == basis.recipe_updated_at
            and tuple(repos.page_step_states.list_for_page(page_id, recipe.stage)) == basis.states
            and (basis.undoing is None or basis.undoing.deletes_as(undoing))
        )
        return holds, undoing

    async def _book(self, source: StepSource) -> MetadataMap:
        """Give what the book gives the step that reads it on the page: what the run worked out, or else the page's own.

        A run of the whole book asked the plugin once for all its pages. A page that is run alone asks it with its own
        version of the step, beside what the other pages hold.

        :param source: What the first step reads.
        :type source: StepSource
        :returns: The fields of the step that are by the book with their values, none when there is nothing to give.
        :rtype: MetadataMap
        """
        worked = self._run.books.get(self.recipe.id)
        return worked if worked is not None else await StepChain(self, self.steps).book_of(source)

    async def _fail(self) -> RunOutcome:
        """Record that the stage failed on the page, or that the page is gone.

        Called with no transaction open, and it opens a ``Book.change`` of its own, in which the page is read again. A
        page that was deleted while it ran has nothing to record, so the files the run left for it are discarded.

        :returns: Failed, or skipped for a page that was deleted.
        :rtype: RunOutcome
        """
        run, page_id, recipe = self._run, self.page.id, self.recipe

        def fail(repos: BookRepositories) -> None:
            """Mark the stage of the page failed.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :raises NotFoundError: If the page was deleted meanwhile.
            """
            repos.pages.get(page_id)
            run.runs.records.mark_failed(repos, page_id, recipe.stage, recipe_id=recipe.id)

        try:
            await self.work.book.change(lambda repos, _book_record: fail(repos))
        except NotFoundError:
            await self.work.engine.runner.discard(self.work.book, BookKeys.page(page_id))
            return RunOutcome.SKIPPED
        return RunOutcome.FAILED


class RecipeRun(StageWork):
    """The work of a ``run-stage`` job: it runs the pages of the job, each by the recipe of its kind.

    :ivar runs: What opened the work, through which the recipes and the stage records are reached.
    :ivar splits: The splitting of a scan into the pages of a spread, and its undoing.
    :ivar books: What the book gives the step that reads it, by recipe, once a run of the whole book worked it out.
    """

    def __init__(self, runs: StageRuns, *, book: Book, book_record: BookRecord, control: JobControl) -> None:
        """Work over the ports of one job.

        :param runs: What opened the work, which holds the recipes of the book, by which an earlier stage that is stale
                     is run again, and the writer of the stage records.
        :type runs: StageRuns
        :param book: The open book owning the pages, whose transactions are opened for each version and never held
                     while a step runs.
        :type book: Book
        :param book_record: Record of the book owning the pages.
        :type book_record: BookRecord
        :param control: What the job reports through: every step of a page is a task, and the job stops between two.
        :type control: JobControl
        """
        super().__init__(engine=runs.engine, book=book, book_record=book_record, control=control)
        self.runs = runs
        self.splits = runs.splits(self)
        self.books: dict[RecipeId, MetadataMap] = {}

    async def run(
        self,
        page: Page,
        recipe: Recipe,
        *,
        confirmed: bool = False,
        through_step_id: StepId | None = None,
    ) -> RunOutcome:
        """Run the steps of the recipe on the page, and make the version of the last one current.

        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe of the stage to run.
        :type recipe: Recipe
        :param confirmed: Whether the user confirmed that undoing a split deletes the right half of a spread.
        :type confirmed: bool
        :param through_step_id: The last step to run, or None for every step that is on.
        :type through_step_id: StepId | None
        :returns: Whether the page was processed, failed, or skipped, as ``PageRun.run`` tells.
        :rtype: RunOutcome
        """
        return await PageRun(self, page, recipe, through_step_id=through_step_id).run(confirmed=confirmed)

    async def measure(self, page: Page, recipe: Recipe, *, through_step_id: StepId | None = None) -> PageVersion | None:
        """Make the steps of the recipe up to the step that reads the book, and give the version that step made.

        :param page: Page to measure.
        :type page: Page
        :param recipe: Recipe of the stage the page is run by.
        :type recipe: Recipe
        :param through_step_id: The last step the run goes over, or None for every step that is on.
        :type through_step_id: StepId | None
        :returns: The version of the step on the page, or None when the page has no image or a step failed.
        :rtype: PageVersion | None
        """
        return await PageRun(self, page, recipe, through_step_id=through_step_id).measured()

    async def settle_book(
        self, recipe: Recipe, fresh: Mapping[PageId, PageVersion], *, through_step_id: StepId | None = None
    ) -> None:
        """Work out what the book gives the step that reads it, for every page run by the recipe.

        :param recipe: Recipe of the stage the pages are run by.
        :type recipe: Recipe
        :param fresh: The versions of the step on the pages the run has just taken as far as it.
        :type fresh: Mapping[PageId, PageVersion]
        :param through_step_id: The last step the run goes over, or None for every step that is on.
        :type through_step_id: StepId | None
        """
        fields = self.engine.book_fields([step for _, step in recipe.indexed_steps_through(through_step_id)])
        if fields is not None:
            self.books[recipe.id] = await self.book.read(
                lambda repos: fields.book_step.from_book(BookContent(repos), recipe.stage, fields.params, fresh),
            )

    @override
    async def refresh(self, page: Page, stage: Stage) -> None:
        """Run an earlier stage again by the recipe of the kind of the page, which finds its versions in the cache.

        Called before the run opens any transaction, since the recipes may store the defaults and the run stores its own
        versions, each in a transaction of its own.

        :param page: Page whose earlier stage is stale.
        :type page: Page
        :param stage: The stale stage.
        :type stage: Stage
        """
        try:
            recipe = (await self.runs.recipes.for_pages(self.book, stage, [page]))[page.id]
        except NotFoundError:
            # A stage without a recipe, such as the page order, has nothing to run again
            return
        await self.run(page, recipe)


class PreviewRun(StageWork):
    """The work of a ``preview-step`` job: the steps of a form on the previews of a page, with nothing made current."""

    async def run(self, page: Page, stage: Stage, steps: Sequence[Step], step_index: int) -> PageVersion:
        """Run the steps up to one of them, each on the preview the one before made.

        Called with no transaction open. The previews are stored by the ``Book.change`` calls of ``StepWork``, and
        the page that was deleted meanwhile is reported as not found.

        :param page: Page to preview on.
        :type page: Page
        :param stage: Stage of the steps.
        :type stage: Stage
        :param steps: The steps of the form, whose parameters are not saved in a recipe. A step that is switched off is
                      left out, as in a run.
        :type steps: Sequence[Step]
        :param step_index: Index of the last step to run.
        :type step_index: int
        :returns: The preview version of the last step that is on.
        :rtype: PageVersion
        :raises NotFoundError: If the page was deleted while the steps ran.
        :raises ConflictError: If the page has no image to preview on, every step up to the index is off, or a step
                               failed.
        """
        page_work = PageWork(self, page, stage)
        source = await page_work.source(VersionScale.PREVIEW)
        if source is None:
            raise ConflictError(NO_PREVIEW_INPUT)
        enabled = [step for step in steps[: step_index + 1] if step.enabled]
        self.control.plan(self.engine.task_count(enabled))
        chain = StepChain(page_work, enabled)
        version = await chain.make(await chain.with_book(source))
        if version is None:
            raise ConflictError(NO_STEP_TO_PREVIEW)
        if version.state is not VersionState.READY:
            raise ConflictError(version.data[VersionData.ERROR])
        return version


class StageRuns:
    """Opens the work of the jobs that run the steps of recipes on the pages of a book, the same for every job.

    It is the top of the chain of a run: the container builds it once, and a job asks it for the work of its book, a
    ``RecipeRun`` or a ``PreviewRun``, which belongs to the engine and reaches the recipes and the records through it.

    :ivar engine: What runs the steps: the runner, the processors, the tracer and the bounds of a run.
    :ivar recipes: The recipes of the book, by which a run goes and an earlier stage that is stale is run again.
    :ivar records: Writer of the stage records.
    :ivar order_keys: Builder of the order key of the right half of a spread, which a split inserts after the left.
    """

    def __init__(
        self, *, engine: StageEngine, recipes: RecipeBook, records: StageRecords, order_keys: OrderKeys
    ) -> None:
        """Open the work of jobs through one engine.

        :param engine: What runs the steps.
        :type engine: StageEngine
        :param recipes: The recipes of the book.
        :type recipes: RecipeBook
        :param records: Writer of the stage records.
        :type records: StageRecords
        :param order_keys: Builder of the order key of the right half of a spread.
        :type order_keys: OrderKeys
        """
        self.engine = engine
        self.recipes = recipes
        self.records = records
        self.order_keys = order_keys

    def recipe_run(self, book: Book, book_record: BookRecord, control: JobControl) -> RecipeRun:
        """Open the work of a ``run-stage`` job on a book.

        :param book: The open book of the job.
        :type book: Book
        :param book_record: Record of the book.
        :type book_record: BookRecord
        :param control: What the job reports through.
        :type control: JobControl
        :returns: The work, which runs the pages of the job by their recipes.
        :rtype: RecipeRun
        """
        return RecipeRun(self, book=book, book_record=book_record, control=control)

    async def preview(self, book: Book, preview: StepPreview, control: JobControl) -> PageVersion:
        """Run a ``preview-step`` job: the steps of a form on the previews of a page, up to one of them.

        :param book: The open book of the job.
        :type book: Book
        :param preview: What the job was asked to preview.
        :type preview: StepPreview
        :param control: What the job reports through.
        :type control: JobControl
        :returns: The preview version of the last step that is on.
        :rtype: PageVersion
        :raises NotFoundError: If the page is gone, before or while the steps ran.
        :raises ConflictError: If the page has no image to preview on, every step up to the index is off, or a step
                               failed.
        """
        book_record, page = await book.read(lambda repos: (repos.book_record.get(), repos.pages.get(preview.page_id)))
        work = PreviewRun(engine=self.engine, book=book, book_record=book_record, control=control)
        return await work.run(page, preview.stage, preview.steps, preview.step_index)

    def splits(self, work: StageWork) -> SpreadSplit:
        """Open the splitting of scans into the pages of a spread for the work of a job.

        :param work: The work of the job.
        :type work: StageWork
        :returns: The splitting, which writes the stage records and gives a new right half its order key.
        :rtype: SpreadSplit
        """
        return SpreadSplit(work, order_keys=self.order_keys, records=self.records)
