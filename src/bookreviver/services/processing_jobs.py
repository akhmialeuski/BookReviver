"""The work of the processing jobs a worker takes from the queue: a run of a stage, a preview, tiles and a collection.

Each entry point reads its job back, moves it to running, does its work and leaves it in a final state, as every job
that reports its progress does, through ``JobTracker``. The parameters of a job are read into the value of its kind, and
a job whose parameters are not valid fails with the reason.

A run goes by the pages of the stage and the steps of the recipe. It reads the current version of the nearest earlier
stage of a page, finds the versions it made before by their identifier, makes only what is new, and makes the last
version the current one of the stage. A page that fails is recorded as failed and the job goes on to the next, and the
job succeeds when it processed at least one page. When it ends it queues a collection of the project's old versions, so
old versions go by their age without the user asking.

A project processes one thing at a time, so a collection never overlaps a run that may be reusing the versions it
deletes. A collection chooses the versions, marks them failed so that none can be chosen or reused any more, removes
their directories and then deletes their rows. A collection that stops on the way leaves the versions marked, which are
old and read by nothing that stays, so the next collection chooses them again and finishes the work. Removing a
directory that is gone is no error.
"""

import logging
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import JobState, PageOrigin, RunOutcome, VersionData, VersionState
from bookreviver.domain.errors import DomainError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import SliceRequest, StageRun, StepPreview, TileCut, VersionCollection
from bookreviver.services.stage_runs import PreviewRun, RecipeRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Job, Page, Recipe
    from bookreviver.domain.ids import JobId, ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import AssetStore
    from bookreviver.services.processing_parts import ProcessingParts
    from bookreviver.services.stage_runs import StageRuntime

NO_PAGE_PROCESSED: str = 'No page could be processed. The state of the stage of each page says why.'
UNEXPECTED_FAILURE: str = 'The job stopped because of an unexpected error. It has been logged.'
BEING_COLLECTED: str = 'The version is being deleted.'
# How many pages a run reads from the book at a time
PAGE_WINDOW: int = 1_000

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
        self._tracker = parts.tracker
        self._starter = parts.starter

    async def run_stage(self, job_id: JobId) -> None:
        """Run a ``run-stage`` job: the recipe over the pages, one page after the other.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        if (job := await self._tracker.start(job_id)) is None:
            return
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
                return
            done, failed, total = outcomes
            if failed and not done:
                await self._tracker.finish(job, JobState.FAILED, error=NO_PAGE_PROCESSED, total=total)
            else:
                await self._tracker.finish(job, JobState.SUCCEEDED, total=total)
            await self._starter.enqueue_collection(job.project_id)

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
        recipe = (
            await self._recipes.active(project.id, run.stage)
            if run.recipe_id is None
            else await self._recipes.get(project.id, run.recipe_id, stage=run.stage)
        )
        pages = await self._pages_to_run(project.id, run)
        executor = RecipeRun(
            project=project, uow=self._uow, runtime=self._runtime, recipes=self._recipes, records=self._records
        )
        outcomes: list[RunOutcome] = []
        for page in pages:
            if (saved := await self._tracker.advance(job, done=len(outcomes), total=len(pages))) is None:
                return None
            job = saved
            outcomes.append(await self._run_page(executor, page, recipe, confirmed=run.confirm_unsplit))
        return outcomes.count(RunOutcome.DONE), outcomes.count(RunOutcome.FAILED), len(pages)

    async def _run_page(self, executor: RecipeRun, page: Page, recipe: Recipe, *, confirmed: bool) -> RunOutcome:
        """Run the recipe on one page, so that whatever goes wrong on it fails the page and not the job.

        :param executor: Runner of recipes of the project.
        :type executor: RecipeRun
        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe to run.
        :type recipe: Recipe
        :param confirmed: Whether the user confirmed that undoing a split deletes the right half of a spread.
        :type confirmed: bool
        :returns: What the run came to.
        :rtype: RunOutcome
        """
        try:
            return await executor.run(page, recipe, confirmed=confirmed)
        except Exception:
            logger.exception('The stage %s failed on page %s', recipe.stage, page.id)
            await self._uow.rollback()
            record = await self._records.mark_failed(page.id, recipe.stage, recipe_id=recipe.id)
            await self._uow.commit()
            await self._records.announce(page.project_id, [record])
            return RunOutcome.FAILED

    async def _pages_to_run(self, project_id: ProjectId, run: StageRun) -> Sequence[Page]:
        """Choose the pages a run goes over: the ones it names, or every page of the book that has an image.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param run: What the job was asked to run.
        :type run: StageRun
        :returns: The pages in book order, without the placeholders, which have no image.
        :rtype: Sequence[Page]
        """
        if run.page_ids is not None:
            pages = list(await self._uow.pages.list_by_ids(project_id, run.page_ids))
        else:
            pages = []
            while True:
                window = await self._uow.pages.list_for_project(
                    project_id, SliceRequest(offset=len(pages), limit=PAGE_WINDOW)
                )
                pages.extend(window.items)
                if len(pages) >= window.total or not window.items:
                    break
        return [page for page in pages if page.origin is not PageOrigin.PLACEHOLDER]

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
        """Delete the old versions that nothing needs: mark them, remove their directories, and delete their rows.

        :param job: The running job.
        :type job: Job
        :returns: The number of versions deleted, or None when the job was cancelled before it deleted anything.
        :rtype: int | None
        :raises DomainError: If the parameters of the job are not valid.
        """
        collection = VersionCollection.from_map(job.params)
        old = await self._uow.page_versions.collectable(
            job.project_id, collection.older_than, collection.previews_older_than
        )
        if await self._tracker.advance(job, done=0, total=len(old)) is None:
            return None
        for version in old:
            marked = evolve(
                version, state=VersionState.FAILED, data={**version.data, VersionData.ERROR: BEING_COLLECTED}
            )
            await self._uow.page_versions.update(marked)
        await self._uow.commit()
        keys = ProjectKeys(job.project_id)
        for version in old:
            await self._assets.delete_prefix(keys.version_directory(version))
        await self._uow.page_versions.delete_many([version.id for version in old])
        await self._uow.commit()
        return len(old)
