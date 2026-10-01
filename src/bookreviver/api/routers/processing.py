"""Recipes, variants, runs, previews, the stages of a page and its versions.

A stage is processed by a recipe, and the stage has one active recipe and any number of variants. Editing the active
recipe or switching to a variant marks the pages it processed stale and runs nothing. A run and a preview are jobs:
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
    HeadChoice,
    PageStageSchema,
    PageVersionSchema,
    RecipeBody,
    RecipeSchema,
    StageRunBody,
    StepPreviewBody,
    VersionQuery,
)
from bookreviver.domain.entities import PageStage, PageVersion, Recipe
from bookreviver.domain.enums import Stage
from bookreviver.domain.ids import PageId, PageVersionId, ProjectId, RecipeId
from bookreviver.domain.values import RecipeKey
from bookreviver.services.processing import ProcessingService

PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'
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
class VariantPath:
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
    page_id: Annotated[PageId, Path(description='Identifier of the page')]
    stage: Annotated[Stage, Path(description=STAGE_DESCRIPTION)]


@dataclass(frozen=True)
class VersionPath:
    """The identifiers in the address of one version of a page of a project.

    :ivar project_id: Identifier of the project.
    :ivar page_id: Identifier of the page.
    :ivar version_id: Identifier of the version.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    page_id: Annotated[PageId, Path(description='Identifier of the page')]
    version_id: Annotated[PageVersionId, Path(description=VERSION_ID_DESCRIPTION, pattern=r'^[0-9a-f]{16}$')]


@router.get('/{project_id}/stages/{stage}/recipe')
async def get_recipe(
    address: Annotated[StagePath, Depends()], actor: ActorDep, processing: FromDishka[ProcessingService]
) -> RecipeSchema:
    """Return the active recipe of a stage, which a project creates the first time the stage is asked for.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The active recipe.
    :rtype: RecipeSchema
    """
    return RecipeSchema.model_validate(await processing.recipe(actor, address.project_id, address.stage))


@router.put('/{project_id}/stages/{stage}/recipe')
async def put_recipe(
    address: Annotated[StagePath, Depends()],
    body: RecipeBody,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> RecipeSchema:
    """Replace the name and the steps of the active recipe, which marks the pages it processed stale.

    No page is processed again by this request; the stage is run by ``POST .../run``. A step whose processor is unknown
    or of another stage, or whose parameters do not fit, answers 422.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The name and the steps.
    :type body: RecipeBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The recipe as stored, with the defaults of each processor filled in.
    :rtype: RecipeSchema
    """
    saved = await processing.save_recipe(actor, address.project_id, address.stage, body.name, body.to_steps())
    return RecipeSchema.model_validate(saved)


@router.get('/{project_id}/stages/{stage}/variants')
async def list_variants(
    address: Annotated[StagePath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> Page[RecipeSchema]:
    """List the recipes of a stage, the active one first and then the variants, oldest first.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: One page of the recipes of the stage.
    :rtype: Page[RecipeSchema]
    """
    pager = Pager[Recipe, RecipeSchema](params, RecipeSchema.model_validate)
    return pager.page(await processing.variants(actor, address.project_id, address.stage, pager.request))


@router.post('/{project_id}/stages/{stage}/variants', status_code=status.HTTP_201_CREATED)
async def create_variant(
    address: Annotated[StagePath, Depends()],
    body: RecipeBody,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> RecipeSchema:
    """Add a variant of a stage, which is not active until it is activated.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The name and the steps.
    :type body: RecipeBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The variant as stored.
    :rtype: RecipeSchema
    """
    variant = await processing.add_variant(actor, address.project_id, address.stage, body.name, body.to_steps())
    return RecipeSchema.model_validate(variant)


@router.put('/{project_id}/stages/{stage}/variants/{recipe_id}')
async def put_variant(
    address: Annotated[VariantPath, Depends()],
    body: RecipeBody,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> RecipeSchema:
    """Replace the name and the steps of a recipe of a stage, which marks the pages it processed stale.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage and the recipe.
    :type address: VariantPath
    :param body: The name and the steps.
    :type body: RecipeBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The recipe as stored.
    :rtype: RecipeSchema
    """
    saved = await processing.save_variant(
        actor, address.project_id, RecipeKey(address.stage, address.recipe_id), body.name, body.to_steps()
    )
    return RecipeSchema.model_validate(saved)


@router.post('/{project_id}/stages/{stage}/variants/{recipe_id}/activate')
async def activate_variant(
    address: Annotated[VariantPath, Depends()], actor: ActorDep, processing: FromDishka[ProcessingService]
) -> RecipeSchema:
    """Make a variant the active recipe of its stage, which marks the pages the old one processed stale.

    \N{FORM FEED}
    :param address: Identifiers of the project, the stage and the recipe.
    :type address: VariantPath
    :param actor: The signed-in account.
    :type actor: Actor
    :param processing: Processing service of the request.
    :type processing: ProcessingService
    :returns: The recipe as active.
    :rtype: RecipeSchema
    """
    activated = await processing.activate(actor, address.project_id, address.stage, address.recipe_id)
    return RecipeSchema.model_validate(activated)


@router.post('/{project_id}/stages/{stage}/run', status_code=status.HTTP_202_ACCEPTED)
async def run_stage(
    address: Annotated[StagePath, Depends()],
    body: StageRunBody,
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> JobSchema:
    """Run a stage over some pages by a recipe, in the background, and answer with the queued job.

    The job makes a version for every step on every page and reports one step of progress for each page. A project runs
    one stage at a time, and another run while one is queued or running answers 409.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage.
    :type address: StagePath
    :param body: The recipe, or the active one, and the pages, or every page with an image.
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
    """List the versions of a page, the earliest first, of one stage and one scale or of all.

    \N{FORM FEED}
    :param address: Identifiers of the project and of the page.
    :type address: PagePath
    :param query: Page number and size, and the stage and the scale to list.
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


@router.post('/{project_id}/versions/collect', status_code=status.HTTP_202_ACCEPTED)
async def collect_versions(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    processing: FromDishka[ProcessingService],
) -> JobSchema:
    """Delete the old versions nothing needs, in the background, and answer with the queued job.

    A version goes when it is not current, not in the chain of inputs of a current version, not a base version, and
    older than the retention period of its scale. A collection that is queued or running already is the answer.

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
