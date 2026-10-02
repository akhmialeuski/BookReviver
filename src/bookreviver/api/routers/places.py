"""The place an account left a book at, which the book is opened at again on any device.

FastAPI publishes a route's docstring as the description of its operation, up to a form feed. Each route docstring
puts a form feed before its reST fields, so the parameter lists stay in the code and out of the OpenAPI schema.
"""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Path, Response, status

from bookreviver.api.auth import ActorDep
from bookreviver.api.schemas.places import BookPlaceBody, BookPlaceSchema
from bookreviver.domain.ids import ProjectId
from bookreviver.services.places import PlaceService

PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'
NO_PLACE_DESCRIPTION: str = 'The account has not worked on the book yet'

router = APIRouter(prefix='/projects', tags=['places'], route_class=DishkaRoute)


@router.get(
    '/{project_id}/place',
    response_model=BookPlaceSchema,
    responses={status.HTTP_204_NO_CONTENT: {'description': NO_PLACE_DESCRIPTION}},
)
async def get_place(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    places: FromDishka[PlaceService],
) -> BookPlaceSchema | Response:
    """Return the place the signed-in account left the book at, or 204 when it has not worked on the book yet.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param places: Place service of the request.
    :type places: PlaceService
    :returns: The place, or an empty answer.
    :rtype: BookPlaceSchema | Response
    """
    place = await places.find(actor, project_id)
    if place is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return BookPlaceSchema.model_validate(place)


@router.put('/{project_id}/place')
async def put_place(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    body: Annotated[BookPlaceBody, Body()],
    actor: ActorDep,
    places: FromDishka[PlaceService],
) -> BookPlaceSchema:
    """Replace the place the signed-in account left the book at.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param body: Where the reader is in the book.
    :type body: BookPlaceBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param places: Place service of the request.
    :type places: PlaceService
    :returns: The place as stored.
    :rtype: BookPlaceSchema
    """
    return BookPlaceSchema.model_validate(await places.save(actor, project_id, body.to_place()))
