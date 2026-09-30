"""Project list, creation, book description and deletion.

FastAPI publishes a route's docstring as the description of its operation, up to a form feed. Each route docstring
puts a form feed before its reST fields, so the parameter lists stay in the code and out of the OpenAPI schema.
"""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Path, Request, Response, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.projects import ProjectCreate, ProjectSchema, ProjectUpdate
from bookreviver.domain.entities import ProjectOverview
from bookreviver.domain.ids import ProjectId
from bookreviver.services.projects import ProjectService

LOCATION_HEADER: str = 'Location'
PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'

router = APIRouter(prefix='/projects', tags=['projects'], route_class=DishkaRoute)


@router.get('')
async def list_projects(
    actor: ActorDep, params: Annotated[Params, Depends()], projects: FromDishka[ProjectService]
) -> Page[ProjectSchema]:
    """List the projects of the signed-in account, most recently updated first.

    \N{FORM FEED}
    :param actor: The signed-in account.
    :type actor: Actor
    :param params: Page number and size from the query.
    :type params: Params
    :param projects: Project service of the request.
    :type projects: ProjectService
    :returns: One page of the account's projects.
    :rtype: Page[ProjectSchema]
    """
    pager = Pager[ProjectOverview, ProjectSchema](params, ProjectSchema.from_overview)
    return pager.page(await projects.list(actor, pager.request))


@router.post('', status_code=status.HTTP_201_CREATED)
async def create_project(
    body: Annotated[ProjectCreate, Body()],
    actor: ActorDep,
    request: Request,
    response: Response,
    projects: FromDishka[ProjectService],
) -> ProjectSchema:
    """Create a project from the description of its book, and point to it in the ``Location`` header.

    \N{FORM FEED}
    :param body: Description of the book.
    :type body: ProjectCreate
    :param actor: The signed-in account, which becomes the owner.
    :type actor: Actor
    :param request: The request, whose URL the new project's address is built from.
    :type request: Request
    :param response: The response, which receives the ``Location`` header.
    :type response: Response
    :param projects: Project service of the request.
    :type projects: ProjectService
    :returns: The new project.
    :rtype: ProjectSchema
    """
    overview = await projects.create(actor, body.to_details())
    response.headers[LOCATION_HEADER] = str(request.url_for(RouteName.PROJECT, project_id=overview.project.id))
    return ProjectSchema.from_overview(overview)


@router.get('/{project_id}', name=RouteName.PROJECT)
async def get_project(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    projects: FromDishka[ProjectService],
) -> ProjectSchema:
    """Return one project of the signed-in account.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param projects: Project service of the request.
    :type projects: ProjectService
    :returns: The project.
    :rtype: ProjectSchema
    """
    return ProjectSchema.from_overview(await projects.get(actor, project_id))


@router.patch('/{project_id}')
async def update_project(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    body: Annotated[ProjectUpdate, Body()],
    actor: ActorDep,
    projects: FromDishka[ProjectService],
) -> ProjectSchema:
    """Merge the body into the project, as JSON Merge Patch (RFC 7396) defines.

    A field left out keeps its value and a field sent as null is cleared to the value a new project has for it; the
    title cannot be cleared, and the cover must be a page of the project.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param body: Fields to change, with null for a field to clear.
    :type body: ProjectUpdate
    :param actor: The signed-in account.
    :type actor: Actor
    :param projects: Project service of the request.
    :type projects: ProjectService
    :returns: The changed project.
    :rtype: ProjectSchema
    """
    return ProjectSchema.from_overview(await projects.update(actor, project_id, body.to_changes()))


@router.delete('/{project_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    projects: FromDishka[ProjectService],
) -> None:
    """Delete a project with its pages, jobs and files.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param projects: Project service of the request.
    :type projects: ProjectService
    """
    await projects.delete(actor, project_id)
