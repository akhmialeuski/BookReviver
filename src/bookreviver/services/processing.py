"""Use cases of the processing framework a request makes: recipes, runs, previews and page versions.

A stage has one recipe for each kind of page, and a page is processed by the recipe of its kind. The use cases here
change a recipe or the current version of a stage, which writes rows, commits and marks the pages they affect stale, or
start heavy
work, a run, a preview, a cut of tiles, a collection or a measure of the book, which records a job with its parameters,
queues it and answers at once, since a request handler never blocks on CPU-bound work. Neither runs a processor. The
workers do, through ``ProcessingJobs``, and the files of a step are written by ``steps.py``.

Editing a recipe does not process any page again. It marks the stage stale on every page the recipe processed, and the
user starts the run. The one recipe of each kind of a stage is kept by the database with a unique key.

Versions are not deleted when they stop being current, so going back to earlier parameters stays possible. The job
``collect-versions``, which a run queues when it ends, removes the files of the ones that are no longer current and
that nothing needs, and keeps their rows, so a version outlives its picture and a run makes the picture again under
the same identifier. Only a preview loses its row, and only once it is old.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.enums import JobKind, OrderMode, ProcessorScope, RunMode, Stage, VersionScale, VersionState
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.values import PageStageKey, Slice, StageRun, StepPreview, TileCut
from bookreviver.domain.version_chains import stage_depths, step_places, versions_of_step
from bookreviver.services.processing_parts import PROJECT_BUSY
from bookreviver.services.projects import owned_project
from bookreviver.services.run_plans import RunPlan

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Job, Page, PageStage, PageVersion, Recipe
    from bookreviver.domain.geometry import Point
    from bookreviver.domain.ids import PageId, PageVersionId, ProjectId
    from bookreviver.domain.values import ProcessorSpec, RecipeDraft, RecipeKey, SliceRequest, VersionFilter
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.services.processing_parts import ProcessingParts

NOT_CHOOSABLE: str = 'The version {version_id} cannot be made the current one: {reason}.'
NOT_READY: str = 'it is not ready'
NOT_FULL: str = 'it is a preview'
OTHER_STAGE: str = 'it belongs to another stage'
FILES_REMOVED: str = 'its picture was removed, so it has to be made again first'
NOT_REMAKABLE: str = 'The picture of the version {version_id} cannot be made again: {reason}.'
HAS_FILES: str = 'it still has its picture'
SPLITS_A_SCAN: str = 'it was made by a step that splits a scan, which only a run of the whole stage applies'
NO_IMAGE: str = 'The version {version_id} has no image.'
NEEDS_CONFIRMATION: str = (
    'The mode {mode} takes the work of {pages} pages away, which the run does only when it is confirmed.'
)
STEP_NEEDS_STAGE: str = 'The versions of a step are listed with the stage of the step.'
NO_STEP_TO_RUN_THROUGH: str = (
    'Step {index} of the recipe for {name} does not exist, or it and every step before it are switched off.'
)


class ProcessingService:
    """Recipes, runs, previews and page versions of the acting account's projects."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog, parts: ProcessingParts) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param parts: The recipes, the stage records, the job tracker and the job starter over the unit of work.
        :type parts: ProcessingParts
        """
        self._uow = uow
        self._catalogue = catalogue
        self._recipes = parts.recipes
        self._records = parts.records
        self._starter = parts.starter
        self._clock = parts.clock

    def processors(self) -> Sequence[ProcessorSpec]:
        """List what every processor of the catalogue says about itself.

        :returns: The specs, by key.
        :rtype: Sequence[ProcessorSpec]
        """
        return self._catalogue.specs()

    async def recipes(self, actor: Actor, project_id: ProjectId, stage: Stage, request: SliceRequest) -> Slice[Recipe]:
        """List the recipes of a stage, one for each kind of page, creating them the first time the stage is asked for.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The recipes of the window in the order of the kinds, and the number of all the stage's recipes.
        :rtype: Slice[Recipe]
        :raises NotFoundError: If the actor has no such project, or the stage has no recipe.
        """
        await owned_project(self._uow.projects, actor, project_id)
        recipes = await self._recipes.recipes(project_id, stage)
        return Slice(items=recipes[request.offset : request.offset + request.limit], total=len(recipes))

    async def save_recipe(self, actor: Actor, project_id: ProjectId, key: RecipeKey, draft: RecipeDraft) -> Recipe:
        """Replace the steps of a recipe of a stage, and mark the pages it processed stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The stage the request names and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :param draft: New steps, which are checked against their processors and their order.
        :type draft: RecipeDraft
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, or the project has no such recipe of the stage.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        await owned_project(self._uow.projects, actor, project_id)
        return await self._rewrite(await self._recipes.get(project_id, key.recipe_id, stage=key.stage), draft)

    async def start_run(self, actor: Actor, project_id: ProjectId, stage: Stage, run: StageRun) -> Job:
        """Record a job that runs a stage over some pages, each by the recipe of its kind, and queue it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param run: The pages and the mode, with the stage the request names.
        :type run: StageRun
        :returns: The queued job; a preview of the project that is queued or running is cancelled for it.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project or page, or the stage has no recipe.
        :raises ConflictError: If a run or a measure of the project is queued or running, or the run goes through a
                               step that the recipe of a page has no such step for, or whose steps up to it are all
                               switched off, or its mode takes the work of pages away and it is not confirmed.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._recipes.recipes(project_id, stage)
        if run.page_ids is not None:
            await self._uow.pages.list_by_ids(project_id, run.page_ids)
        if run.through_step is not None:
            planned = {recipe.id: recipe for recipe in (await self._plan(project_id, run).recipes()).values()}
            for recipe in planned.values():
                if run.through_step >= len(recipe.steps) or not recipe.indexed_steps_through(run.through_step):
                    raise ConflictError(
                        NO_STEP_TO_RUN_THROUGH.format(index=run.through_step + 1, name=recipe.kind.label)
                    )
        if run.mode is not RunMode.KEEP and not run.confirm_overwrite:
            affected = (await self._plan(project_id, run).impact()).affected
            if affected:
                raise ConflictError(NEEDS_CONFIRMATION.format(mode=run.mode.label, pages=affected))
        return await self._starter.enqueue(project_id, JobKind.RUN_STAGE, run.to_map())

    def _plan(self, project_id: ProjectId, run: StageRun) -> RunPlan:
        """Plan a run over the unit of work of the request.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param run: The run.
        :type run: StageRun
        :returns: The pages and recipes of the run.
        :rtype: RunPlan
        """
        return RunPlan(uow=self._uow, recipes=self._recipes, clock=self._clock, project_id=project_id, run=run)

    async def start_measure(self, actor: Actor, project_id: ProjectId) -> Job:
        """Record a job that measures the book and writes the medians into the normalize step, and queue it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The queued job; a preview of the project that is queued or running is cancelled for it.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project, or the Geometry stage has no recipe.
        :raises ConflictError: If a run or a measure of the project is queued or running, which may be writing the
                               versions that are measured or the recipe.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._recipes.recipes(project_id, Stage.GEOMETRY)
        return await self._starter.enqueue(project_id, JobKind.MEASURE_BOOK, {})

    async def start_preview(self, actor: Actor, project_id: ProjectId, preview: StepPreview) -> Job:
        """Record a job that previews the steps of a form on a page, and queue it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param preview: The page, the stage, the steps of the form and the step the result is wanted of.
        :type preview: StepPreview
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        :raises InvalidParametersError: If a step does not fit its processor.
        :raises ConflictError: If a run, a preview, a tile cutting, a collection or a measure of the project is queued
                               or running.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, preview.page_id)
        checked = StepPreview(
            page_id=preview.page_id,
            stage=preview.stage,
            # A preview only draws, so an order the user may still be arranging is not refused
            steps=await self._recipes.check(preview.stage, preview.steps, order=OrderMode.FREE),
            step_index=preview.step_index,
        )
        return await self._starter.enqueue(project_id, JobKind.PREVIEW_STEP, checked.to_map())

    async def page_stages(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, request: SliceRequest
    ) -> Slice[PageStage]:
        """List the current version and the state of every stage a page has been through, in pipeline order.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The records of the window and the number of all the page's records.
        :rtype: Slice[PageStage]
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        records = await self._uow.page_stages.list_for_page(page_id)
        return Slice(items=records[request.offset : request.offset + request.limit], total=len(records))

    async def choose_version(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, stage: Stage, version_id: PageVersionId
    ) -> PageStage:
        """Make a version the current one of a stage of a page, and mark the later stages stale.

        The version must be ready, made by a full run of this page in this stage. If its tile pyramid is not cut yet, a
        job is queued to cut it, since the viewer opens the current version.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The record of the stage in its new state.
        :rtype: PageStage
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page has no such
                               version.
        :raises ConflictError: If the version is not ready, is a preview, or belongs to another stage, or a run, a
                               preview, a tile cutting or a collection of the project is queued or running, which
                               may be reading or deleting the versions the choice depends on.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        if await self._starter.busy(project_id) is not None:
            raise ConflictError(PROJECT_BUSY)
        version = await self._version_of(page_id, version_id)
        if (reason := self._why_not_choosable(version, stage)) is not None:
            raise ConflictError(NOT_CHOOSABLE.format(version_id=version_id, reason=reason))
        key = PageStageKey(page_id, stage)
        previous = await self._uow.page_stages.find(key)
        changed = await self._records.set_head(
            key, head_version_id=version_id, recipe_id=None if previous is None else previous.recipe_id
        )
        await self._uow.commit()
        await self._records.announce(project_id, changed)
        if version.renditions is not None and not version.tiles_ready:
            await self._starter.enqueue_tiles(project_id, [version_id])
        return changed[0]

    async def versions(
        self,
        actor: Actor,
        project_id: ProjectId,
        page_id: PageId,
        version_filter: VersionFilter,
        request: SliceRequest,
    ) -> Slice[PageVersion]:
        """List the versions of a page, the earliest first, of one stage, step, scale and mark or of all.

        The versions of a step are the ones its place in a recipe of the stage gives: every step that is on stores one
        version that reads the one before, so a version belongs to the step whose processor made it and whose place
        among the steps that are on is the number of versions of its stage the version reads. A step that is switched
        off in every recipe has none.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_filter: The stage, the step, the scale and the mark to list, each of them or all.
        :type version_filter: VersionFilter
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The versions of the window and the number of all that match.
        :rtype: Slice[PageVersion]
        :raises NotFoundError: If the actor has no such project, the project has no such page, or no recipe of the
                               stage has the step.
        :raises InvalidParametersError: If a step is asked for without its stage.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        step_id = version_filter.step_id
        if step_id is None:
            return await self._uow.page_versions.list_for_stage(
                page_id, version_filter.stage, version_filter.scale, request, version_filter.mark
            )
        if version_filter.stage is None:
            raise InvalidParametersError(STEP_NEEDS_STAGE)
        recipes = await self._uow.recipes.list_for_stage(project_id, version_filter.stage)
        if not any(step.step_id == step_id for recipe in recipes for step in recipe.steps):
            raise NotFoundError(step_id)
        found = await self._uow.page_versions.list_for_page(page_id)
        made = versions_of_step(found, version_filter.stage, step_places(recipes, step_id), stage_depths(found))
        matching = [
            version
            for version in found
            if version.id in made
            and (version_filter.scale is None or version.scale is version_filter.scale)
            and (version_filter.mark is None or version.mark is version_filter.mark)
        ]
        return Slice(items=matching[request.offset : request.offset + request.limit], total=len(matching))

    async def version(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, version_id: PageVersionId
    ) -> PageVersion:
        """Return one version of a page.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page has no such
                               version.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        return await self._version_of(page_id, version_id)

    async def start_tiles(self, actor: Actor, project_id: ProjectId, page_id: PageId, version_id: PageVersionId) -> Job:
        """Record a job that cuts the tile pyramid of a version, which the viewer asks for when it opens one.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page has no such
                               version.
        :raises ConflictError: If the version is not ready, has no image, or is a preview, or a run, a preview, a tile
                               cutting or a collection of the project is queued or running.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        version = await self._version_of(page_id, version_id)
        if version.renditions is None or version.files_removed:
            raise ConflictError(NO_IMAGE.format(version_id=version_id))
        if version.state is not VersionState.READY or version.scale is not VersionScale.FULL:
            raise ConflictError(NOT_CHOOSABLE.format(version_id=version_id, reason=NOT_READY))
        return await self._starter.enqueue(project_id, JobKind.CUT_TILES, TileCut(version_ids=(version_id,)).to_map())

    async def start_remake(
        self, actor: Actor, project_id: ProjectId, page_id: PageId, version_id: PageVersionId
    ) -> Job:
        """Record a job that makes the picture of a version again, whose files a collection removed, and queue it.

        The job is a run of the stage over this page, with the parameters and the edit the version stored, over the
        current version of the earlier stage, and it makes the version current when it ends. It finds the version under
        the same identifier and gives its row the files again. When the earlier stage has another current version than
        the one the version was made from, the job fails with that reason instead of making another version, since a
        result that reads other pixels is not the result the user chose.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the page has no such
                               version.
        :raises ConflictError: If the version has its files, is not ready, is a preview, or is made by a step that
                               splits a scan.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        version = await self._version_of(page_id, version_id)
        if (reason := self._why_not_remakable(version)) is not None:
            raise ConflictError(NOT_REMAKABLE.format(version_id=version_id, reason=reason))
        if self._catalogue.get(version.processor.key).spec.scope is ProcessorScope.SPLIT:
            raise ConflictError(NOT_REMAKABLE.format(version_id=version_id, reason=SPLITS_A_SCAN))
        run = StageRun(stage=version.stage, page_ids=(page_id,), remake=version_id)
        return await self._starter.enqueue(project_id, JobKind.RUN_STAGE, run.to_map())

    async def start_collection(self, actor: Actor, project_id: ProjectId) -> Job:
        """Record a job that removes the files of the old versions nothing needs, and queue it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The queued job, or the collection that is queued or running already.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project.
        :raises ConflictError: If a run, a preview or a tile cutting of the project is queued or running.
        """
        await owned_project(self._uow.projects, actor, project_id)
        if (job := await self._starter.enqueue_collection(project_id)) is None:
            raise ConflictError(PROJECT_BUSY)
        return job

    async def map_to_scan(
        self,
        actor: Actor,
        project_id: ProjectId,
        page_id: PageId,
        version_id: PageVersionId,
        points: Sequence[Point],
    ) -> Sequence[Point]:
        """Map points of the image of a version back to the pixels of the scan, along the chain of its transforms.

        Each version maps a point of its output to its input, and a base version maps it to the scan, which is its
        input.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version the points lie on.
        :type version_id: PageVersionId
        :param points: Points in the pixels of the image of the version.
        :type points: Sequence[Point]
        :returns: The points in the pixels of the scan, in the same order.
        :rtype: Sequence[Point]
        :raises NotFoundError: If the actor has no such project, the project has no such page, the page has no such
                               version, or a version of the chain was deleted.
        :raises UnsupportedTransformError: If a version of the chain follows a mesh.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        version = await self._version_of(page_id, version_id)
        mapped = list(points)
        while True:
            mapped = [version.transform.to_input(point) for point in mapped]
            if version.input_id is None:
                return mapped
            version = await self._version_of(page_id, version.input_id)

    async def _rewrite(self, recipe: Recipe, draft: RecipeDraft) -> Recipe:
        """Change the name and the steps of a recipe, and mark the pages it processed stale.

        :param recipe: The recipe to change.
        :type recipe: Recipe
        :param draft: New name and steps, which are checked against their processors and their order.
        :type draft: RecipeDraft
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """
        changed = await self._recipes.rewrite(recipe, draft)
        stale = await self._records.mark_recipe_stale(recipe.id)
        await self._uow.commit()
        await self._records.announce(recipe.project_id, stale)
        return changed

    async def _page(self, project_id: ProjectId, page_id: PageId) -> Page:
        """Return a page of the project.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :returns: The page.
        :rtype: Page
        :raises NotFoundError: If the page does not exist or belongs to another project.
        """
        page = await self._uow.pages.get(page_id)
        if page.project_id != project_id:
            raise NotFoundError(page_id)
        return page

    async def _version_of(self, page_id: PageId, version_id: PageVersionId) -> PageVersion:
        """Return a version of a page.

        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the version does not exist or belongs to another page.
        """
        version = await self._uow.page_versions.get(version_id)
        if version.page_id != page_id:
            raise NotFoundError(version_id)
        return version

    @staticmethod
    def _why_not_choosable(version: PageVersion, stage: Stage) -> str | None:
        """Say why a version cannot become the current one of a stage, or None when it can.

        :param version: The version.
        :type version: PageVersion
        :param stage: The stage.
        :type stage: Stage
        :returns: The reason, or None.
        :rtype: str | None
        """
        if version.stage is not stage:
            return OTHER_STAGE
        if version.state is not VersionState.READY:
            return NOT_READY
        if version.scale is not VersionScale.FULL:
            return NOT_FULL
        if version.files_removed:
            return FILES_REMOVED
        return None

    @staticmethod
    def _why_not_remakable(version: PageVersion) -> str | None:
        """Say why the picture of a version cannot be made again, or None when it can.

        :param version: The version.
        :type version: PageVersion
        :returns: The reason, or None.
        :rtype: str | None
        """
        if version.scale is not VersionScale.FULL:
            return NOT_FULL
        if version.state is not VersionState.READY:
            return NOT_READY
        if not version.files_removed:
            return HAS_FILES
        return None
