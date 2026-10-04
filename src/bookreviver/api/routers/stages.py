"""The stages of a book as a whole: the summary every stage bar draws from, and the pages of one stage for its strip.

The recipes, the runs and the previews of a stage live in the processing router. These two routes only read, and each
answers for the whole book in one request, so the interface never asks page by page.
"""

from dataclasses import dataclass
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import ManifestPage, ManifestParams, Pager
from bookreviver.api.routers.processing import StagePath
from bookreviver.api.schemas.stages import StagePageSchema, StageSummarySchema
from bookreviver.domain.ids import ProjectId, StepId
from bookreviver.domain.stage_summaries import StageRow, StageSummary
from bookreviver.services.projects import ProjectService

PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'


@dataclass(frozen=True)
class StageRowsPath(StagePath):
    """The address of the rows of a stage, with the request, whose application knows the route that serves the images.

    :ivar request: The request.
    """

    request: Request


router = APIRouter(prefix='/projects', tags=['stages'], route_class=DishkaRoute)


@router.get('/{project_id}/stages')
async def list_stages(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    projects: FromDishka[ProjectService],
) -> Page[StageSummarySchema]:
    """List the stages of a book in the order of the pipeline, each summed over the pages of the book.

    A stage is available when it is done by hand or a processor of it is installed. The counts of a stage done by hand
    are zero.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param params: Page number and size from the query; the ten stages fit one page of the default size.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param projects: Project service of the request.
    :type projects: ProjectService
    :returns: One page of the stages.
    :rtype: Page[StageSummarySchema]
    """
    pager = Pager[StageSummary, StageSummarySchema](params, StageSummarySchema.model_validate)
    return pager.page(await projects.stages(actor, project_id, pager.request))


@router.get('/{project_id}/stages/{stage}/pages')
async def list_stage_pages(
    address: Annotated[StageRowsPath, Depends()],
    params: Annotated[ManifestParams, Depends()],
    actor: ActorDep,
    projects: FromDishka[ProjectService],
    step: Annotated[StepId | None, Query(description='A step of a recipe of the stage to place every page at')] = None,
) -> ManifestPage[StagePageSchema]:
    """List the pages of a book in book order, each with where it stands in a stage and the version that is its result.

    A page the stage has not run on has the status ``not-run`` and no version. A page holds up to a thousand rows, as
    the page manifest does, so a strip of a whole book takes few requests. With ``step`` every row also says what that
    step read and made on its page and where the shape of the step comes from; a step no recipe of the stage has is a
    404.

    \N{FORM FEED}
    :param address: Identifiers of the project and the stage, and the request.
    :type address: StageRowsPath
    :param params: Page number and size from the query, the size up to a thousand.
    :type params: ManifestParams
    :param actor: The signed-in account.
    :type actor: Actor
    :param projects: Project service of the request.
    :type projects: ProjectService
    :param step: The step to place every page at, or None for the rows of the stage alone.
    :type step: StepId | None
    :returns: One page of the rows of the stage.
    :rtype: ManifestPage[StagePageSchema]
    """
    to_schema = partial(StagePageSchema.of, project_id=address.project_id, request=address.request)
    pager = Pager[StageRow, StagePageSchema](params, to_schema)
    return pager.page(await projects.stage_pages(actor, address.project_id, address.stage, pager.request, step))
