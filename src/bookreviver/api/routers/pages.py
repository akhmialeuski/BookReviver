"""The page manifest and one page of a book, addressed by the page's identifier.

The manifest lists the pages of a book in book order, with the computed position of every page and never its order
key, and a page carries the paths of its images. Pages kept out of the book are listed too, with ``included`` false,
so a client can offer to bring them back.
"""

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, Request

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import ManifestPage, ManifestParams, Pager
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.entities import PageOverview
from bookreviver.domain.ids import PageId, ProjectId
from bookreviver.services.pages import PageService

PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'
PAGE_ID_DESCRIPTION: str = 'Identifier of the page'

router = APIRouter(prefix='/projects', tags=['pages'], route_class=DishkaRoute)


@router.get('/{project_id}/pages')
async def list_pages(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    request: Request,
    params: Annotated[ManifestParams, Depends()],
    pages: FromDishka[PageService],
) -> ManifestPage[PageSchema]:
    """List the pages of a project in book order, up to a thousand at once, with the paths of their images.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param params: Page number and size from the query, the size up to a thousand.
    :type params: ManifestParams
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: One page of the manifest, whose items carry their position in the whole book.
    :rtype: ManifestPage[PageSchema]
    """
    pager = Pager[PageOverview, PageSchema](params, partial(PageSchema.from_overview, request=request))
    return pager.page(await pages.manifest(actor, project_id, pager.request))


@router.get('/{project_id}/pages/{page_id}')
async def get_page(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    page_id: Annotated[PageId, Path(description=PAGE_ID_DESCRIPTION)],
    actor: ActorDep,
    request: Request,
    pages: FromDishka[PageService],
) -> PageSchema:
    """Return one page of a project.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param page_id: Identifier of the page.
    :type page_id: PageId
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: The page with its position in the book.
    :rtype: PageSchema
    """
    return PageSchema.from_overview(await pages.get(actor, project_id, page_id), request)
