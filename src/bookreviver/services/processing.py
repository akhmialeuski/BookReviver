"""Use cases of the processing framework a request makes: recipes, variants, runs, previews and page versions.

A stage is processed by a recipe, and a page by the recipe it was last processed by. The use cases here change a recipe
or the current version of a stage, which writes rows, commits and marks the pages they affect stale, or start heavy
work, a run, a preview, a cut of tiles or a collection, which records a job with its parameters, queues it and answers
at once, since a request handler never blocks on CPU-bound work. Neither runs a processor. The workers do, through
``ProcessingJobs``, and the files of a step are written by ``steps.py``.

Editing the active recipe does not process any page again. It marks the stage stale on every page the recipe processed,
and the user starts the run. Changing the active variant does the same for the pages the old active recipe processed.
The one active recipe of a stage is kept by the service in one transaction and by the database with a partial unique
index.

Versions are not deleted when they stop being current, so going back to earlier parameters is instant. The job
``collect-versions`` removes the old ones that nothing needs.
"""

import contextlib
from typing import TYPE_CHECKING

from bookreviver.domain.enums import JobKind, VersionScale, VersionState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.values import PageStageKey, Slice, StepPreview, TileCut
from bookreviver.services.processing_parts import PROJECT_BUSY
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Job, Page, PageStage, PageVersion, Recipe
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.geometry import Point
    from bookreviver.domain.ids import PageId, PageVersionId, ProjectId, RecipeId
    from bookreviver.domain.values import ProcessorSpec, RecipeKey, SliceRequest, StageRun, Step, VersionFilter
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.services.processing_parts import ProcessingParts

NOT_CHOOSABLE: str = 'The version {version_id} cannot be made the current one: {reason}.'
NOT_READY: str = 'it is not ready'
NOT_FULL: str = 'it is a preview'
OTHER_STAGE: str = 'it belongs to another stage'
NO_IMAGE: str = 'The version {version_id} has no image.'


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

    def processors(self) -> Sequence[ProcessorSpec]:
        """List what every processor of the catalogue says about itself.

        :returns: The specs, by key.
        :rtype: Sequence[ProcessorSpec]
        """
        return self._catalogue.specs()

    async def recipe(self, actor: Actor, project_id: ProjectId, stage: Stage) -> Recipe:
        """Return the active recipe of a stage, creating the recipes the stage starts with the first time.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, or the stage has no recipe.
        """
        await owned_project(self._uow.projects, actor, project_id)
        return await self._recipes.active(project_id, stage)

    async def save_recipe(
        self, actor: Actor, project_id: ProjectId, stage: Stage, name: str, steps: Sequence[Step]
    ) -> Recipe:
        """Replace the name and the steps of the active recipe, and mark the pages it processed stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param name: New name of the recipe.
        :type name: str
        :param steps: New steps, which are checked against their processors.
        :type steps: Sequence[Step]
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, or the stage has no recipe.
        :raises InvalidParametersError: If a step does not fit its processor.
        """
        await owned_project(self._uow.projects, actor, project_id)
        return await self._rewrite(await self._recipes.active(project_id, stage), name, steps)

    async def variants(self, actor: Actor, project_id: ProjectId, stage: Stage, request: SliceRequest) -> Slice[Recipe]:
        """List the recipes of a stage, the active one first and then the variants by creation.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The recipes of the window and the number of all the stage's recipes.
        :rtype: Slice[Recipe]
        :raises NotFoundError: If the actor has no such project, or the stage has no recipe.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._recipes.active(project_id, stage)
        recipes = await self._uow.recipes.list_for_stage(project_id, stage)
        return Slice(items=recipes[request.offset : request.offset + request.limit], total=len(recipes))

    async def add_variant(
        self, actor: Actor, project_id: ProjectId, stage: Stage, name: str, steps: Sequence[Step]
    ) -> Recipe:
        """Add a recipe of a stage that is not active, to try on some pages or to compare with the active one.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param name: Name of the variant.
        :type name: str
        :param steps: Steps of the variant, which are checked against their processors.
        :type steps: Sequence[Step]
        :returns: The variant as stored.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, or the stage has no recipe.
        :raises InvalidParametersError: If a step does not fit its processor.
        """
        await owned_project(self._uow.projects, actor, project_id)
        variant = await self._recipes.add_variant(project_id, stage, name, steps)
        await self._uow.commit()
        return variant

    async def save_variant(
        self, actor: Actor, project_id: ProjectId, key: RecipeKey, name: str, steps: Sequence[Step]
    ) -> Recipe:
        """Replace the name and the steps of a recipe of a stage, and mark the pages it processed stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The stage the request names and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :param name: New name of the recipe.
        :type name: str
        :param steps: New steps, which are checked against their processors.
        :type steps: Sequence[Step]
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, or the project has no such recipe of the stage.
        :raises InvalidParametersError: If a step does not fit its processor.
        """
        await owned_project(self._uow.projects, actor, project_id)
        return await self._rewrite(await self._recipes.get(project_id, key.recipe_id, stage=key.stage), name, steps)

    async def activate(self, actor: Actor, project_id: ProjectId, stage: Stage, recipe_id: RecipeId) -> Recipe:
        """Make a variant the active recipe of its stage, and mark the pages the old active recipe processed stale.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param recipe_id: Identifier of the variant.
        :type recipe_id: RecipeId
        :returns: The recipe as active.
        :rtype: Recipe
        :raises NotFoundError: If the actor has no such project, or the project has no such recipe of the stage.
        """
        await owned_project(self._uow.projects, actor, project_id)
        chosen = await self._recipes.get(project_id, recipe_id, stage=stage)
        if chosen.active:
            return chosen
        previous = await self._recipes.active(project_id, stage)
        activated = await self._recipes.switch_active(previous, chosen)
        stale = await self._stale_for(previous.id)
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return activated

    async def start_run(self, actor: Actor, project_id: ProjectId, stage: Stage, run: StageRun) -> Job:
        """Record a job that runs a stage over some pages by a recipe, and queue it.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :param run: The recipe and the pages, with the stage the request names.
        :type run: StageRun
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project, or the project has no such recipe, stage recipe or
                               page.
        :raises ConflictError: If a run, a preview, a tile cutting or a collection of the project is queued or running.
        """
        await owned_project(self._uow.projects, actor, project_id)
        if run.recipe_id is None:
            await self._recipes.active(project_id, stage)
        else:
            await self._recipes.get(project_id, run.recipe_id, stage=stage)
        if run.page_ids is not None:
            await self._uow.pages.list_by_ids(project_id, run.page_ids)
        return await self._starter.enqueue(project_id, JobKind.RUN_STAGE, run.to_map())

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
        :raises ConflictError: If a run, a preview, a tile cutting or a collection of the project is queued or running.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, preview.page_id)
        checked = StepPreview(
            page_id=preview.page_id,
            stage=preview.stage,
            steps=await self._recipes.check(preview.stage, preview.steps),
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
        previous = await self._uow.page_stages.find(PageStageKey(page_id, stage))
        changed = await self._records.set_head(
            page_id, stage, head_version_id=version_id, recipe_id=None if previous is None else previous.recipe_id
        )
        await self._uow.commit()
        await self._records.announce(project_id, changed)
        if version.renditions is not None and not version.tiles_ready:
            await self._queue_tiles_of_choice(project_id, version_id)
        return changed[0]

    async def unpin(self, actor: Actor, project_id: ProjectId, page_id: PageId, stage: Stage) -> PageStage:
        """Take the recipe pinned to a stage of a page off it, so a run without a recipe chooses by the rules again.

        The result of the stage stays and is marked stale, since the rules may choose another recipe. A stage that is
        not pinned is returned as it is.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :returns: The record of the stage in its new state.
        :rtype: PageStage
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the stage has not run
                               on the page.
        :raises ConflictError: If a run, a preview, a tile cutting or a collection of the project is queued or running,
                               which may be writing the record.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        if await self._starter.busy(project_id) is not None:
            raise ConflictError(PROJECT_BUSY)
        key = PageStageKey(page_id, stage)
        changed = await self._records.unpin(page_id, stage)
        await self._uow.commit()
        await self._records.announce(project_id, changed)
        return changed[0] if changed else await self._uow.page_stages.get(key)

    async def _queue_tiles_of_choice(self, project_id: ProjectId, version_id: PageVersionId) -> None:
        """Queue the cutting of the pyramid of a version that was just made current.

        The choice is committed already, so a job of another request that took the project in the meantime does not
        undo it. The viewer asks for the pyramid of a version that has none when it opens it, so it is cut then.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        """
        with contextlib.suppress(ConflictError):
            await self._starter.enqueue(project_id, JobKind.CUT_TILES, TileCut(version_ids=(version_id,)).to_map())

    async def versions(
        self,
        actor: Actor,
        project_id: ProjectId,
        page_id: PageId,
        version_filter: VersionFilter,
        request: SliceRequest,
    ) -> Slice[PageVersion]:
        """List the versions of a page, the earliest first, of one stage and one scale or of all.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_filter: The stage and the scale to list, each of them or all.
        :type version_filter: VersionFilter
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The versions of the window and the number of all that match.
        :rtype: Slice[PageVersion]
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_project(self._uow.projects, actor, project_id)
        await self._page(project_id, page_id)
        return await self._uow.page_versions.list_for_stage(
            page_id, version_filter.stage, version_filter.scale, request
        )

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
        if version.renditions is None:
            raise ConflictError(NO_IMAGE.format(version_id=version_id))
        if version.state is not VersionState.READY or version.scale is not VersionScale.FULL:
            raise ConflictError(NOT_CHOOSABLE.format(version_id=version_id, reason=NOT_READY))
        return await self._starter.enqueue(project_id, JobKind.CUT_TILES, TileCut(version_ids=(version_id,)).to_map())

    async def start_collection(self, actor: Actor, project_id: ProjectId) -> Job:
        """Record a job that deletes the old versions nothing needs, and queue it.

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

    async def _rewrite(self, recipe: Recipe, name: str, steps: Sequence[Step]) -> Recipe:
        """Change the name and the steps of a recipe, and mark the pages it processed stale.

        :param recipe: The recipe to change.
        :type recipe: Recipe
        :param name: New name.
        :type name: str
        :param steps: New steps, which are checked against their processors.
        :type steps: Sequence[Step]
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises InvalidParametersError: If a step does not fit its processor.
        """
        changed = await self._recipes.rewrite(recipe, name, steps)
        stale = await self._stale_for(recipe.id)
        await self._uow.commit()
        await self._records.announce(recipe.project_id, stale)
        return changed

    async def _stale_for(self, recipe_id: RecipeId) -> list[PageStage]:
        """Mark the stage of every page a recipe processed stale.

        :param recipe_id: Recipe that changed or stopped being active.
        :type recipe_id: RecipeId
        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        stale: list[PageStage] = []
        for record in await self._uow.page_stages.list_for_recipe(recipe_id):
            stale.extend(await self._records.mark_stale(record.page_id, record.stage))
        return stale

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
        return None
