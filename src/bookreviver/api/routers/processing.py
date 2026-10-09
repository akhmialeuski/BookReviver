"""Recipes, runs, previews, the stages of a page and its versions.

A stage has one recipe for each kind of page, and a page is processed by the recipe of its kind. Editing a recipe marks
the pages it processed stale and runs nothing. A run and a preview are jobs:
the route records the job and answers 202 with it, and the result reaches the browser as events, since a request
handler never blocks on CPU-bound work. A page keeps every version it was ever given, a stage points at the current
one, and the user may make another ready version the current one.
"""

from dataclasses import dataclass
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, Request, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.pages import PagePath
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.api.schemas.processing import (
    CollectionReportSchema,
    HeadChoice,
    PageStageSchema,
    PageVersionSchema,
    RecipeBody,
    RecipeSchema,
    RunImpactSchema,
    StageRunBody,
    StepPreviewBody,
    VersionQuery,
)
from bookreviver.domain.entities import PageStage, PageVersion, Recipe
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, PageVersionId, ProjectId, RecipeId
from bookreviver.domain.values import RecipeKey
from bookreviver.services.processing import ProcessingService
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipe_profiles import RecipeProfiles
from bookreviver.services.run_plans import RunImpactService

PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'
PAGE_ID_DESCRIPTION: str = 'Identifier of the page'
STAGE_DESCRIPTION: str = 'Stage of the pipeline'
RECIPE_ID_DESCRIPTION: str = 'Identifier of the recipe'
VERSION_ID_DESCRIPTION: str = 'Identifier of the page version'

router = APIRouter(prefix='/projects', tags=['processing'], route_class=DishkaRoute)


@dataclass(frozen=True)
class StagePath:
    """The identifiers in the address of one stage of a project, read together so a route keeps to a few parameters.

    :ivar project_id: Identifier of the project.
    :ivar stage: The stage.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    stage: Annotated[Stage, Path(description=STAGE_DESCRIPTION)]


@dataclass(frozen=True)
class RecipePath:
    """The identifiers in the address of one recipe of a stage of a project.

    :ivar project_id: Identifier of the project.
    :ivar stage: The stage.
    :ivar recipe_id: Identifier of the recipe.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    stage: Annotated[Stage, Path(description=STAGE_DESCRIPTION)]
    recipe_id: Annotated[RecipeId, Path(description=RECIPE_ID_DESCRIPTION)]


@dataclass(frozen=True)
class PageStagePath:
    """The identifiers in the address of one stage of one page of a project.

    :ivar project_id: Identifier of the project.
    :ivar page_id: Identifier of the page.
    :ivar stage: The stage.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    page_id: Annotated[PageId, Path(description=PAGE_ID_DESCRIPTION)]
    stage: Annotated[Stage, Path(description=STAGE_DESCRIPTION)]


@dataclass(frozen=True)
class VersionPath:
    """The identifiers in the address of one version of a page of a project.

    :ivar project_id: Identifier of the project.
    :ivar page_id: Identifier of the page.
    :ivar version_id: Identifier of the version.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    page_id: Annotated[PageId, Path(description=PAGE_ID_DESCRIPTION)]
    version_id: Annotated[PageVersionId, Path(description=VERSION_ID_DESCRIPTION, pattern=r'^[0-9a-f]{16}$')]


@router.get('/{project_id}/stages/{stage}/recipes')
async def list_recipes(
    address: Annotated[StagePath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
    order: FromDishka[RecipeOrder],
) -> Page[RecipeSchema]:
    """List the recipes of a stage, one for each kind of page, which a project creates the first time it is asked.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :param order: Finder of the steps that stand off the place their processors ask for.
    :type order: RecipeOrder
    :returns: One page of the recipes of the stage, in the order of the kinds.
    :rtype: Page[RecipeSchema]
    """
    pager = Pager[Recipe, RecipeSchema](params, lambda recipe: RecipeSchema.of(recipe, order.issues(recipe.steps)))
    return pager.page(await processing.recipes(actor, address.project_id, address.stage, pager.request))


@router.put('/{project_id}/stages/{stage}/recipes/{recipe_id}')
async def put_recipe(
    address: Annotated[RecipePath, Depends()],
    body: RecipeBody,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
    order: FromDishka[RecipeOrder],
) -> RecipeSchema:
    """Replace the steps of a recipe of a stage, which marks the pages it processed stale.

    No page is processed again by this request; the stage is run by ``POST .../run``. A step whose processor is unknown
    or of another stage, or whose parameters do not fit, answers 422, and so does a step that stands where it cannot
    work, unless the body asks for the free order. A step that stands off its usual place is saved and named in the
    answer.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage and the recipe.
    :type address: RecipePath
    :param body: The steps and the order to keep.
    :type body: RecipeBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :param order: Finder of the steps that stand off the place their processors ask for.
    :type order: RecipeOrder
    :returns: The recipe as stored, with the defaults of each processor filled in, and the steps that are out of their
              place.
    :rtype: RecipeSchema
    """
    saved = await processing.save_recipe(
        actor, address.project_id, RecipeKey(address.stage, address.recipe_id), body.to_draft()
    )
    return RecipeSchema.of(saved, order.issues(saved.steps))


@router.post('/{project_id}/stages/{stage}/recipes/{recipe_id}/reset')
async def reset_recipe(
    address: Annotated[RecipePath, Depends()],
    actor: ActorDep,
    profiles: FromDishka[RecipeProfiles],
    order: FromDishka[RecipeOrder],
) -> RecipeSchema:
    """Put the steps a stage starts with back into a recipe, which marks the pages it processed stale.

    The steps are those of the account's default profile for the stage when the recipe is the one of text pages and the
    account has a usable profile, and otherwise those of the built-in template of the kind. The recipe keeps its
    identifier and its kind, and every step in it is new. The answer is 404 for a stage that has no steps by default.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage and the recipe.
    :type address: RecipePath
    :param actor: The signed-in account.
    :type actor: Actor
    :param profiles: Recipe profile service of the request, which also puts the default steps back.
    :type profiles: RecipeProfiles
    :param order: Finder of the steps that stand off the place their processors ask for.
    :type order: RecipeOrder
    :returns: The recipe as stored, with the steps that are out of their place.
    :rtype: RecipeSchema
    """
    reset = await profiles.reset(actor, address.project_id, RecipeKey(address.stage, address.recipe_id))
    return RecipeSchema.of(reset, order.issues(reset.steps))


@router.post('/{project_id}/stages/{stage}/run', status_code=status.HTTP_202_ACCEPTED)
async def run_stage(
    address: Annotated[StagePath, Depends()],
    body: StageRunBody,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> JobSchema:
    """Run a stage over some pages, each by the recipe of its kind, in the background, and answer with the queued job.

    The job makes a version for every step on every page and reports one step of progress for each page. A project runs
    one stage at a time, and another run while one is queued or running answers 409. A run keeps the settings the pages
    changed for its steps and the manual edits they read. A mode that takes the edits or the settings away answers 409
    until the body confirms it, when it would take them from any page.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The pages, or every page with an image, and the mode.
    :type body: StageRunBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The queued job.
    :rtype: JobSchema
    """
    job = await processing.start_run(actor, address.project_id, address.stage, body.to_run(address.stage))
    return JobSchema.model_validate(job)


@router.post('/{project_id}/stages/{stage}/run-impact')
async def run_impact(
    address: Annotated[StagePath, Depends()],
    body: StageRunBody,
    actor: ActorDep,
    impact: FromDishka[RunImpactService],
) -> RunImpactSchema:
    """Count the pages a run would take work from, so the user can confirm the run before it is sent.

    The body is the one of the run. A run that keeps the settings and edits of the pages takes work from none. A run
    that drops the work of the pages takes the manual edits of the steps it goes over and the fields the pages changed
    for them. Nothing is written and nothing is queued.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The run, with its mode.
    :type body: StageRunBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param impact: Run impact service of the request.
    :type impact: RunImpactService
    :returns: The pages the run goes over and the pages that lose work to its mode.
    :rtype: RunImpactSchema
    """
    counted = await impact.impact(actor, address.project_id, body.to_run(address.stage))
    return RunImpactSchema.model_validate(counted)


@router.post('/{project_id}/stages/{stage}/preview', status_code=status.HTTP_202_ACCEPTED)
async def preview_step(
    address: Annotated[StagePath, Depends()],
    body: StepPreviewBody,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> JobSchema:
    """Preview the steps of a form on one page, in the background, and answer with the queued job.

    The result is a version of the preview scale, announced as ``page-version-ready``, which never becomes the current
    version of the stage.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The page, the steps as the form has them, and the step whose result is wanted.
    :type body: StepPreviewBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The queued job.
    :rtype: JobSchema
    """
    job = await processing.start_preview(actor, address.project_id, body.to_preview(address.stage))
    return JobSchema.model_validate(job)


@router.post('/{project_id}/stages/geometry/measure', status_code=status.HTTP_202_ACCEPTED)
async def measure_book(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> JobSchema:
    """Measure the book in the background, and answer with the queued job.

    The job reads the line height and the frame of the block of text that ``geometry.crop`` recorded on every page, and
    writes the median line height and a page size of the median block with its margins into the parameters of the
    ``geometry.normalize`` step of every Geometry recipe, which marks the pages of those recipes stale. A preview of the
    project is cancelled, and a run, a tile cutting or a collection that is queued or running answers 409.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The queued job.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await processing.start_measure(actor, project_id))


@router.get('/{project_id}/pages/{page_id}/stages')
async def list_page_stages(
    address: Annotated[PagePath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> Page[PageStageSchema]:
    """List the stages a page has been through, with the current version and the state of each, in pipeline order.

    \N{FORM FEED}
    :param address: Identifiers of the project and of the page.
    :type address: PagePath
    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: One page of the stage records.
    :rtype: Page[PageStageSchema]
    """
    pager = Pager[PageStage, PageStageSchema](params, PageStageSchema.of)
    return pager.page(await processing.page_stages(actor, address.project_id, address.page_id, pager.request))


@router.put('/{project_id}/pages/{page_id}/stages/{stage}')
async def choose_version(
    address: Annotated[PageStagePath, Depends()],
    body: HeadChoice,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> PageStageSchema:
    """Make a version the current one of a stage of a page, which marks the later stages of the page stale.

    The version must be ready and made by a full run of this page in this stage. The answer is 409 for one that is not.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page and the stage.
    :type address: PageStagePath
    :param body: The version to make current.
    :type body: HeadChoice
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The record of the stage in its new state.
    :rtype: PageStageSchema
    """
    chosen = await processing.choose_version(actor, address.project_id, address.page_id, address.stage, body.version_id)
    return PageStageSchema.of(chosen)


@router.get('/{project_id}/pages/{page_id}/versions')
async def list_versions(
    address: Annotated[PagePath, Depends()],
    query: Annotated[VersionQuery, Depends()],
    actor: ActorDep,
    request: Request,
    processing: FromDishka[ProcessingService],
) -> Page[PageVersionSchema]:
    """List the versions of a page, the earliest first, of one stage, step, scale and mark or of all.

    With ``step`` the list holds the results of that step of the stage on the page, which needs ``stage``; a step no
    recipe of the stage has is a 404. With ``mark`` it holds the versions carrying that mark only.

    \N{FORM FEED}
    :param address: Identifiers of the project and of the page.
    :type address: PagePath
    :param query: Page number and size, and the stage, the step, the scale and the mark to list.
    :type query: VersionQuery
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: One page of the versions, each with the paths of its images once they are written.
    :rtype: Page[PageVersionSchema]
    """

    def to_schema(version: PageVersion) -> PageVersionSchema:
        """Build the schema of one version of this project.

        :param version: The version.
        :type version: PageVersion
        :returns: The schema.
        :rtype: PageVersionSchema
        """
        return PageVersionSchema.of(version, address.project_id, request)

    pager = Pager[PageVersion, PageVersionSchema](query, to_schema)
    found = await processing.versions(actor, address.project_id, address.page_id, query.to_filter(), pager.request)
    return pager.page(found)


@router.get('/{project_id}/pages/{page_id}/versions/{version_id}')
async def get_version(
    address: Annotated[VersionPath, Depends()],
    actor: ActorDep,
    request: Request,
    processing: FromDishka[ProcessingService],
) -> PageVersionSchema:
    """Return one version of a page, with its provenance, its transform and the paths of its images.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page and the version.
    :type address: VersionPath
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The version.
    :rtype: PageVersionSchema
    """
    version = await processing.version(actor, address.project_id, address.page_id, address.version_id)
    return PageVersionSchema.of(version, address.project_id, request)


@router.post('/{project_id}/pages/{page_id}/versions/{version_id}/tiles', status_code=status.HTTP_202_ACCEPTED)
async def cut_version_tiles(
    address: Annotated[VersionPath, Depends()], actor: ActorDep, processing: FromDishka[ProcessingService]
) -> JobSchema:
    """Cut the tile pyramid of a version in the background, which a viewer asks for when a version has none.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page and the version.
    :type address: VersionPath
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The queued job; its end is announced as ``page-version-ready``.
    :rtype: JobSchema
    """
    job = await processing.start_tiles(actor, address.project_id, address.page_id, address.version_id)
    return JobSchema.model_validate(job)


@router.get('/{project_id}/versions/collectable')
async def collectable_versions(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> CollectionReportSchema:
    """Count the versions a collection would delete now and the space their files take, and change nothing.

    The versions are the ones ``POST /projects/{project_id}/versions/collect`` deletes, chosen by the same rule.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The number of versions and the bytes their files take.
    :rtype: CollectionReportSchema
    """
    return CollectionReportSchema.model_validate(await processing.collection_report(actor, project_id))


@router.post('/{project_id}/versions/collect', status_code=status.HTTP_202_ACCEPTED)
async def collect_versions(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> JobSchema:
    """Delete the versions nothing needs, in the background, and answer with the queued job.

    A version goes when it is not current, not in the chain of inputs of a current version, not a base version, not
    marked Good and without a comment, and, for a preview, older than its retention period. It goes with its files, its
    row and the log of its marks. A collection that is queued or running already is the answer. A run queues one by
    itself when it ends.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The queued job.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await processing.start_collection(actor, project_id))
