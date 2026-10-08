"""The work of the processing jobs a worker takes from the queue: a run of a stage, a preview, tiles and a collection.

Each entry point reads its job back, moves it to running, does its work and leaves it in a final state, as every job
that reports its progress does, through ``JobTracker``. The parameters of a job are read into the value of its kind, and
a job whose parameters are not valid fails with the reason.

A run goes by the pages of the stage and the steps of the recipe, which is the recipe of the kind of each page. The mode
of the run
says what it does first with the settings and the manual edits of the pages (``RunPlan``), which it keeps unless it was
asked to take them away. A recipe whose normalize step leaves the page size to the book is run twice over: the pages
are first taken as far as the step to find their content boxes, then the size of the book is worked out once from all
of them, and the pages are run by it, so every page comes out of one size without a press of the button of the measure.
The run reads the current version of the nearest earlier stage of a page, finds the versions it made before by their
identifier, makes only what is new, and makes the last version the current one of the stage. A page that fails is
recorded as failed and the job goes on to the next, and the job succeeds when it processed at least one page. When it
ends it queues a collection of the project's versions that are no longer current, so their files go as soon as the run
has replaced them, without the user asking. The collection is stored in the same commit as the end of the run, so the
project is never free between the two.

A measure of the book reads the content boxes the normalize step recorded on every page and writes the parameters of
that step of every Geometry recipe, which marks the pages of those recipes stale.

A project processes one thing at a time, so a collection never overlaps a run that may be reusing the versions it
clears. A collection chooses the versions, marks them failed so that none can be chosen or reused any more, removes
their directories and then settles their rows: a preview loses its row, and a version of a full run keeps it with the
time its files were removed, so its parameters, data and edit hash outlive its image. A collection that stops on the
way leaves the versions marked, which are old and read by nothing that stays, so the next collection chooses them
again and finishes the work. Removing a directory that is gone is no error.

A run may be asked to make a version again whose files were removed (``StageRun.remake``), which goes through the same
steps with the parameters the versions of its chain stored.
"""

import logging
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import JobState, RunOutcome, VersionData, VersionScale, VersionState
from bookreviver.domain.errors import DomainError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import StageRun, StepPreview, TileCut, VersionCollection
from bookreviver.services.book_measure import BookMeasure
from bookreviver.services.content_detection import ContentDetector
from bookreviver.services.run_plans import RunPlan
from bookreviver.services.stage_runs import PreviewRun, RecipeRun

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from bookreviver.domain.entities import Job, Page, Recipe
    from bookreviver.domain.ids import JobId, PageId, RecipeId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore
    from bookreviver.services.book_measure import BlockMeasure
    from bookreviver.services.processing_parts import ProcessingParts
    from bookreviver.services.stage_runs import StageRuntime

NO_PAGE_PROCESSED: str = 'No page could be processed. The state of the stage of each page says why.'
NO_CONTENT_DETECTED: str = 'The content of no page could be detected. The log of the worker says why.'
UNEXPECTED_FAILURE: str = 'The job stopped because of an unexpected error. It has been logged.'
BEING_COLLECTED: str = 'The version is being deleted.'

logger = logging.getLogger(__name__)


class ProcessingJobs:
    """Does the work of the processing jobs for the worker that took them."""

    def __init__(self, *, uow: UnitOfWork, assets: AssetStore, runtime: StageRuntime, parts: ProcessingParts) -> None:
        """Work over the ports of one job.

        :param uow: Unit of work of the job, which the work commits as it goes.
        :type uow: UnitOfWork
        :param assets: Store of the derived files, from which a collection removes directories.
        :type assets: AssetStore
        :param runtime: The runner, the catalogue, the publisher, the clock and the size of a preview.
        :type runtime: StageRuntime
        :param parts: The recipes, the stage records, the job tracker and the job starter over the unit of work.
        :type parts: ProcessingParts
        """
        self._uow = uow
        self._assets = assets
        self._runtime = runtime
        self._recipes = parts.recipes
        self._records = parts.records
        self._clock = parts.clock
        self._measure = BookMeasure(uow=uow, recipes=parts.recipes, records=parts.records)
        self._detector = ContentDetector(uow=uow, runtime=runtime, records=parts.records, tracker=parts.tracker)
        self._tracker = parts.tracker
        self._starter = parts.starter

    async def run_stage(self, job_id: JobId) -> Job | None:
        """Run a ``run-stage`` job: the recipe over the pages, one page after the other.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job when the run made at least one page, which the worker follows with the detection of the
                  content of new pages after the page split, or None when it made none or did not run.
        :rtype: Job | None
        :raises NotFoundError: If there is no such job.
        """
        if (job := await self._tracker.start(job_id)) is None:
            return None
        try:
            outcomes = await self._run_pages(job)
        except DomainError as error:
            await self._uow.rollback()
            await self._tracker.finish(job, JobState.FAILED, error=str(error))
        except Exception:
            logger.exception('The run-stage job %s stopped', job_id)
            await self._tracker.finish(job, JobState.FAILED, error=UNEXPECTED_FAILURE)
        else:
            if outcomes is None:
                return None
            done, failed, total = outcomes
            no_page = bool(failed) and not done
            # The collection is stored with the end of the run, so the project is never free in between
            await self._tracker.finish(
                job,
                JobState.FAILED if no_page else JobState.SUCCEEDED,
                error=NO_PAGE_PROCESSED if no_page else '',
                total=total,
                follow_up=self._starter.new_collection(job.project_id),
            )
            return job if done else None
        return None

    async def preview_step(self, job_id: JobId) -> None:
        """Run a ``preview-step`` job: the steps of a form on the previews of a page, up to one of them.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        if (job := await self._tracker.start(job_id)) is None:
            return
        try:
            await self._preview(job)
        except DomainError as error:
            await self._uow.rollback()
            await self._tracker.finish(job, JobState.FAILED, error=str(error))
        except Exception:
            logger.exception('The preview-step job %s stopped', job_id)
            await self._tracker.finish(job, JobState.FAILED, error=UNEXPECTED_FAILURE)
        else:
            await self._tracker.finish(job, JobState.SUCCEEDED, total=1)

    async def cut_tiles(self, job_id: JobId) -> None:
        """Run a ``cut-tiles`` job: the tile pyramids of some versions, each announced when it is cut.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        if (job := await self._tracker.start(job_id)) is None:
            return
        try:
            total = await self._cut(job)
        except DomainError as error:
            await self._uow.rollback()
            await self._tracker.finish(job, JobState.FAILED, error=str(error))
        except Exception:
            logger.exception('The cut-tiles job %s stopped', job_id)
            await self._tracker.finish(job, JobState.FAILED, error=UNEXPECTED_FAILURE)
        else:
            if total is not None:
                await self._tracker.finish(job, JobState.SUCCEEDED, total=total)

    async def collect_versions(self, job_id: JobId) -> None:
        """Run a ``collect-versions`` job: delete the old versions that nothing needs, and then their directories.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        if (job := await self._tracker.start(job_id)) is None:
            return
        try:
            total = await self._collect(job)
        except DomainError as error:
            await self._uow.rollback()
            await self._tracker.finish(job, JobState.FAILED, error=str(error))
        except Exception:
            logger.exception('The collect-versions job %s stopped', job_id)
            await self._tracker.finish(job, JobState.FAILED, error=UNEXPECTED_FAILURE)
        else:
            if total is not None:
                await self._tracker.finish(job, JobState.SUCCEEDED, total=total)

    async def measure_book(self, job_id: JobId) -> None:
        """Run a ``measure-book`` job: the median line height and page size of the book, written into its recipe.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        if (job := await self._tracker.start(job_id)) is None:
            return
        try:
            total = await self._measure.run(job.project_id)
        except DomainError as error:
            await self._uow.rollback()
            await self._tracker.finish(job, JobState.FAILED, error=str(error))
        except Exception:
            logger.exception('The measure-book job %s stopped', job_id)
            await self._tracker.finish(job, JobState.FAILED, error=UNEXPECTED_FAILURE)
        else:
            await self._tracker.finish(job, JobState.SUCCEEDED, total=total)

    async def detect_content(self, job_id: JobId) -> None:
        """Run a ``detect-content`` job: what each page shows, written into the pages as a proposal.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        if (job := await self._tracker.start(job_id)) is None:
            return
        try:
            outcome = await self._detector.run(job)
        except DomainError as error:
            await self._uow.rollback()
            await self._tracker.finish(job, JobState.FAILED, error=str(error))
        except Exception:
            logger.exception('The detect-content job %s stopped', job_id)
            await self._tracker.finish(job, JobState.FAILED, error=UNEXPECTED_FAILURE)
        else:
            if outcome is None:
                return
            read, failed = outcome
            no_page = bool(failed) and not read
            await self._tracker.finish(
                job,
                JobState.FAILED if no_page else JobState.SUCCEEDED,
                error=NO_CONTENT_DETECTED if no_page else '',
                total=read + failed,
            )

    async def _run_pages(self, job: Job) -> tuple[int, int, int] | None:
        """Run the recipe of a ``run-stage`` job over its pages, recording the progress as it goes.

        :param job: The running job.
        :type job: Job
        :returns: How many pages were processed, how many failed and how many the job went through, or None when the job
                  was cancelled.
        :rtype: tuple[int, int, int] | None
        :raises DomainError: If the parameters of the job are not valid, or the recipe or a page it names is gone.
        """
        run = StageRun.from_map(job.params)
        project = await self._uow.projects.get(job.project_id)
        plan = RunPlan(uow=self._uow, recipes=self._recipes, clock=self._clock, project_id=project.id, run=run)
        pages = await plan.pages()
        executor = RecipeRun(
            project=project, uow=self._uow, runtime=self._runtime, recipes=self._recipes, records=self._records
        )
        if run.remake is not None:
            # A page without an image, such as a placeholder, has no version to make again
            if not pages:
                return 0, 1, 1
            if await self._tracker.advance(job, done=0, total=1) is None:
                return None
            outcome = await executor.remake(pages[0], run.remake)
            made = outcome is RunOutcome.DONE
            return int(made), int(not made), 1
        recipes = await plan.recipes()
        # The work the mode takes away goes first, as one batch, so one undo gives it back on every page
        await plan.apply_mode()
        measured = [page for page in pages if executor.wants_book(recipes[page.id], through_step=run.through_step)]
        if measured:
            if (saved := await self._measure_book(job, executor, measured, recipes, run)) is None:
                return None
            job = saved
        total = len(pages) + len(measured)
        outcomes: list[RunOutcome] = []
        for page in pages:
            if (saved := await self._tracker.advance(job, done=len(measured) + len(outcomes), total=total)) is None:
                return None
            job = saved
            outcomes.append(await self._run_page(executor, page, recipes[page.id], run))
        return outcomes.count(RunOutcome.DONE), outcomes.count(RunOutcome.FAILED), len(pages)

    async def _measure_book(
        self, job: Job, executor: RecipeRun, pages: Sequence[Page], recipes: Mapping[PageId, Recipe], run: StageRun
    ) -> Job | None:
        """Take the pages as far as the normalize step, and work out the page size of the book from their content boxes.

        A page that fails on the way is left for the run to fail with its reason, and has no box.

        :param job: The running job.
        :type job: Job
        :param executor: Runner of recipes of the project, which holds the size it works out for the pages of a recipe.
        :type executor: RecipeRun
        :param pages: The pages whose recipe leaves a field of the normalize step to the book.
        :type pages: Sequence[Page]
        :param recipes: The recipe of each page.
        :type recipes: Mapping[PageId, Recipe]
        :param run: What the job was asked to run, whose last step the pages are taken as far as.
        :type run: StageRun
        :returns: The job as the progress left it, or None when it was cancelled.
        :rtype: Job | None
        """
        boxes: dict[RecipeId, dict[PageId, BlockMeasure]] = {}
        # The measure is a step of progress for each page it goes over, and the run another for each page
        total = len(recipes) + len(pages)
        for done, page in enumerate(pages):
            if (saved := await self._tracker.advance(job, done=done, total=total)) is None:
                return None
            job = saved
            try:
                box = await executor.measure(page, recipes[page.id], through_step=run.through_step)
            except Exception:
                logger.exception('The content box of page %s could not be measured', page.id)
                await self._uow.rollback()
                box = None
            if box is not None:
                boxes.setdefault(recipes[page.id].id, {})[page.id] = box
        for recipe in {recipes[page.id].id: recipes[page.id] for page in pages}.values():
            await executor.settle_book(recipe, boxes.get(recipe.id, {}), through_step=run.through_step)
        return job

    async def _run_page(self, executor: RecipeRun, page: Page, recipe: Recipe, run: StageRun) -> RunOutcome:
        """Run the recipe on one page, so that whatever goes wrong on it fails the page and not the job.

        :param executor: Runner of recipes of the project.
        :type executor: RecipeRun
        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe to run.
        :type recipe: Recipe
        :param run: What the job was asked to run, whose confirmation and last step apply to the page.
        :type run: StageRun
        :returns: What the run came to.
        :rtype: RunOutcome
        """
        try:
            return await executor.run(page, recipe, confirmed=run.confirm_unsplit, through_step=run.through_step)
        except Exception:
            logger.exception('The stage %s failed on page %s', recipe.stage, page.id)
            await self._uow.rollback()
            record = await self._records.mark_failed(page.id, recipe.stage, recipe_id=recipe.id)
            await self._uow.commit()
            await self._records.announce(page.project_id, [record])
            return RunOutcome.FAILED

    async def _preview(self, job: Job) -> None:
        """Run the steps of a ``preview-step`` job.

        :param job: The running job.
        :type job: Job
        :raises DomainError: If the parameters are not valid, the page is gone, or a step fails.
        """
        preview = StepPreview.from_map(job.params)
        project = await self._uow.projects.get(job.project_id)
        page = await self._uow.pages.get(preview.page_id)
        executor = PreviewRun(project=project, uow=self._uow, runtime=self._runtime)
        await executor.run(page, preview.stage, preview.steps, preview.step_index)

    async def _cut(self, job: Job) -> int | None:
        """Cut the pyramids of a ``cut-tiles`` job.

        :param job: The running job.
        :type job: Job
        :returns: The number of versions the job went through, or None when it was cancelled.
        :rtype: int | None
        :raises DomainError: If the parameters of the job are not valid.
        """
        cut = TileCut.from_map(job.params)
        keys = ProjectKeys(job.project_id)
        for done, version_id in enumerate(cut.version_ids):
            if (saved := await self._tracker.advance(job, done=done, total=len(cut.version_ids))) is None:
                return None
            job = saved
            version = await self._uow.page_versions.find(version_id)
            if version is None or version.state is not VersionState.READY or version.tiles_ready:
                continue
            if version.renditions is None:
                continue
            cut_version = await self._runtime.runner.cut_tiles(keys, version)
            await self._uow.page_versions.update(cut_version)
            await self._uow.commit()
            await self._runtime.publisher.publish(PageVersionReady(project_id=job.project_id, version=cut_version))
        return len(cut.version_ids)

    async def _collect(self, job: Job) -> int | None:
        """Clear the versions that nothing needs: mark them, remove their directories, then settle their rows.

        A preview loses its row. A version of a full run keeps its row, with its parameters, data and edit hash, and
        loses its files, which a run makes again under the same identifier. The mark is a failed state that nothing can
        choose or reuse, and the row is given back its own state only after its directory is gone, together with the
        time the files were removed.

        :param job: The running job.
        :type job: Job
        :returns: The number of versions cleared, or None when the job was cancelled before it cleared anything.
        :rtype: int | None
        :raises DomainError: If the parameters of the job are not valid.
        """
        collection = VersionCollection.from_map(job.params)
        old = await self._uow.page_versions.collectable(job.project_id, collection.previews_older_than)
        if await self._tracker.advance(job, done=0, total=len(old)) is None:
            return None
        for version in old:
            if version.state is VersionState.READY:
                marked = evolve(
                    version, state=VersionState.FAILED, data={**version.data, VersionData.ERROR: BEING_COLLECTED}
                )
                await self._uow.page_versions.update(marked)
        await self._uow.commit()
        keys = ProjectKeys(job.project_id)
        for version in old:
            await self._assets.delete_prefix(keys.version_directory(version))
        await self._uow.page_versions.delete_many([v.id for v in old if v.scale is VersionScale.PREVIEW])
        removed_at = self._runtime.clock.now()
        for version in old:
            if version.scale is VersionScale.FULL:
                # A version marked by an earlier collection that stopped is READY again once its files are gone
                own_mark = version.data.get(VersionData.ERROR) == BEING_COLLECTED
                await self._uow.page_versions.update(
                    evolve(
                        version,
                        state=VersionState.READY if own_mark else version.state,
                        data={k: v for k, v in version.data.items() if not (own_mark and k == VersionData.ERROR)},
                        renditions=None if version.renditions is None else evolve(version.renditions, ready=False),
                        tiles_ready=False,
                        files_removed_at=removed_at,
                    )
                )
        await self._uow.commit()
        return len(old)
