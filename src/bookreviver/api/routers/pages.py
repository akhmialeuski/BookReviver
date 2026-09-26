"""Page manifest and page facts for the viewer."""

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, Request
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.api.schemas.types import PageIndex
from bookreviver.domain.entities import Page as DomainPage
from bookreviver.domain.ids import ProjectId
from bookreviver.services.pages import PageService

# Parameter and dependency aliases; FastAPI and dishka read route annotations at runtime
ProjectIdPath = Annotated[ProjectId, Path(description='Identifier of the project')]
PageIndexPath = Annotated[PageIndex, Path()]
PagingDep = Annotated[Params, Depends()]
PageServiceDep = FromDishka[PageService]
PageManifest = Page[PageSchema]

router = APIRouter(prefix='/projects', tags=['pages'], route_class=DishkaRoute)


@router.get('/{project_id}/pages')
async def list_pages(
    project_id: ProjectIdPath, actor: ActorDep, request: Request, params: PagingDep, pages: PageServiceDep
) -> PageManifest:
    """List the pages of a project in book order, with the addresses of their images."""
    pager = Pager[DomainPage, PageSchema](params, partial(PageSchema.from_page, request=request))
    return pager.page(await pages.manifest(actor, project_id, pager.request))


@router.get('/{project_id}/pages/{index}')
async def get_page(
    project_id: ProjectIdPath, index: PageIndexPath, actor: ActorDep, request: Request, pages: PageServiceDep
) -> PageSchema:
    """Return one page of a project."""
    return PageSchema.from_page(await pages.get(actor, project_id, index), request)
