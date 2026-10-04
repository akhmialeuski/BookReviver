"""The mark and the comment of a result, which is a version of a page, and the log of their changes.

A mark and a comment are notes of the user on a result: setting them changes no file, no identifier and no stage record,
and the answer is the version with both. The log keeps every change with its values before and after.
"""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Request
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.routers.processing import VersionPath
from bookreviver.api.schemas.processing import PageVersionSchema
from bookreviver.api.schemas.result_marks import ResultMarkChangeSchema, ResultMarkForm
from bookreviver.domain.entities import ResultMarkChange
from bookreviver.domain.values import Slice
from bookreviver.services.result_marks import ResultMarksService

router = APIRouter(prefix='/projects', tags=['result-marks'], route_class=DishkaRoute)


@router.put('/{project_id}/pages/{page_id}/versions/{version_id}/mark')
async def put_mark(
    address: Annotated[VersionPath, Depends()],
    form: ResultMarkForm,
    actor: ActorDep,
    request: Request,
    marks: FromDishka[ResultMarksService],
) -> PageVersionSchema:
    """Replace the mark and the comment of a result, and write the change to its log.

    Both are replaced together, so a body without a mark takes the mark off and an empty comment takes the comment off.
    A body equal to what is stored writes nothing. The answer is 409 for a preview.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page and the version.
    :type address: VersionPath
    :param form: The mark and the comment.
    :type form: ResultMarkForm
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param marks: Result marks service of the request.
    :type marks: ResultMarksService
    :returns: The version with its mark and comment.
    :rtype: PageVersionSchema
    """
    version = await marks.set(actor, address.project_id, address.page_id, address.version_id, form.to_note())
    return PageVersionSchema.of(version, address.project_id, request)


@router.get('/{project_id}/pages/{page_id}/versions/{version_id}/mark-changes')
async def list_mark_changes(
    address: Annotated[VersionPath, Depends()],
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    marks: FromDishka[ResultMarksService],
) -> Page[ResultMarkChangeSchema]:
    """List the changes of the mark and the comment of a result, the earliest first.

    \N{FORM FEED}
    :param address: Identifiers of the project, the page and the version.
    :type address: VersionPath
    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param marks: Result marks service of the request.
    :type marks: ResultMarksService
    :returns: One page of the log of the version.
    :rtype: Page[ResultMarkChangeSchema]
    """
    pager = Pager[ResultMarkChange, ResultMarkChangeSchema](params, ResultMarkChangeSchema.model_validate)
    found = await marks.changes(actor, address.project_id, address.page_id, address.version_id)
    window = found[pager.request.offset : pager.request.offset + pager.request.limit]
    return pager.page(Slice(items=window, total=len(found)))
