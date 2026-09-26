"""Project list, creation, book description and deletion."""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Path, Request, Response, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.projects import ProjectCreate, ProjectSchema, ProjectUpdate
from bookreviver.domain.entities import ProjectOverview
from bookreviver.domain.ids import ProjectId
from bookreviver.services.projects import ProjectService

LOCATION_HEADER: str = 'Location'

# Parameter and dependency aliases; FastAPI and dishka read route annotations at runtime
ProjectIdPath = Annotated[ProjectId, Path(description='Identifier of the project')]
PagingDep = Annotated[Params, Depends()]
ProjectServiceDep = FromDishka[ProjectService]
ProjectCreateBody = Annotated[ProjectCreate, Body()]
ProjectUpdateBody = Annotated[ProjectUpdate, Body()]
ProjectPage = Page[ProjectSchema]

router = APIRouter(prefix='/projects', tags=['projects'], route_class=DishkaRoute)


@router.get('')
async def list_projects(actor: ActorDep, params: PagingDep, projects: ProjectServiceDep) -> ProjectPage:
    """List the projects of the signed-in account, most recently updated first."""
    pager = Pager[ProjectOverview, ProjectSchema](params, ProjectSchema.from_overview)
    return pager.page(await projects.list(actor, pager.request))


@router.post('', status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateBody, actor: ActorDep, request: Request, response: Response, projects: ProjectServiceDep
) -> ProjectSchema:
    """Create a project from the description of its book."""
    overview = await projects.create(actor, body.to_details())
    response.headers[LOCATION_HEADER] = str(request.url_for(get_project.__name__, project_id=overview.project.id))
    return ProjectSchema.from_overview(overview)


@router.get('/{project_id}')
async def get_project(project_id: ProjectIdPath, actor: ActorDep, projects: ProjectServiceDep) -> ProjectSchema:
    """Return one project."""
    return ProjectSchema.from_overview(await projects.get(actor, project_id))


@router.patch('/{project_id}')
async def update_project(
    project_id: ProjectIdPath, body: ProjectUpdateBody, actor: ActorDep, projects: ProjectServiceDep
) -> ProjectSchema:
    """Change the fields of the book description that the body carries."""
    return ProjectSchema.from_overview(await projects.update_details(actor, project_id, body.to_changes()))


@router.delete('/{project_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: ProjectIdPath, actor: ActorDep, projects: ProjectServiceDep) -> None:
    """Delete a project with its pages, jobs and files."""
    await projects.delete(actor, project_id)
