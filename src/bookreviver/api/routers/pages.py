"""The page manifest and one page of a book, addressed by the page's identifier.

The manifest lists the pages of a book in book order, with the computed position of every page and never its order
key, and a page carries the paths of its images. Pages kept out of the book are listed too, with ``included`` false,
so a client can offer to bring them back.
"""

from dataclasses import dataclass
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Path, Request, status

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import ManifestPage, Pager
from bookreviver.api.schemas.pages import LabelRange, PageMove, PageQuery, PageSchema, PagesMove, PageUpdate
from bookreviver.domain.entities import PageOverview
from bookreviver.domain.ids import PageId, ProjectId
from bookreviver.services.pages import PageService

PROJECT_ID_DESCRIPTION: str = 'Identifier of the project'
PAGE_ID_DESCRIPTION: str = 'Identifier of the page'

router = APIRouter(prefix='/projects', tags=['pages'], route_class=DishkaRoute)


@dataclass(frozen=True)
class PagePath:
    """The two identifiers in the address of one page, read together so a route keeps to a few parameters.

    :ivar project_id: Identifier of the project.
    :ivar page_id: Identifier of the page.
    """

    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)]
    page_id: Annotated[PageId, Path(description=PAGE_ID_DESCRIPTION)]


@router.get('/{project_id}/pages')
async def list_pages(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    actor: ActorDep,
    request: Request,
    params: Annotated[PageQuery, Depends()],
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
    :param params: Page number and size from the query, the size up to a thousand, and the filter of included pages.
    :type params: PageQuery
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: One page of the manifest, whose items carry their position in the whole book.
    :rtype: ManifestPage[PageSchema]
    """
    pager = Pager[PageOverview, PageSchema](params, partial(PageSchema.from_overview, request=request))
    return pager.page(await pages.manifest(actor, project_id, pager.request, included_only=params.included))


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


@router.post('/{project_id}/pages/{page_id}/move')
async def move_page(
    address: Annotated[PagePath, Depends()],
    body: PageMove,
    actor: ActorDep,
    request: Request,
    pages: FromDishka[PageService],
) -> PageSchema:
    """Put a page before or after another page of the book.

    The answer is 409 when the anchor is the page itself, or another request took the new place first.

    \N{FORM FEED}
    :param address: Identifiers of the project and of the page to move.
    :type address: PagePath
    :param body: The page to put it next to, and the side.
    :type body: PageMove
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: The page at its new place.
    :rtype: PageSchema
    """
    moved = await pages.move(actor, address.project_id, address.page_id, body.anchor)
    return PageSchema.from_overview(moved, request)


@router.post('/{project_id}/pages/move', status_code=status.HTTP_204_NO_CONTENT)
async def move_pages(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    body: PagesMove,
    actor: ActorDep,
    pages: FromDishka[PageService],
) -> None:
    """Put several pages in a run before or after another page, keeping the order they have in the book.

    The answer is 409 when the anchor is one of the pages. The new order reaches the browser as a ``pages-changed``
    event, so the answer has no body.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param body: The pages to move, the page to put them next to, and the side.
    :type body: PagesMove
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    """
    await pages.move_group(actor, project_id, body.page_ids, body.anchor)


@router.patch('/{project_id}/pages/{page_id}')
async def update_page(
    address: Annotated[PagePath, Depends()],
    body: Annotated[PageUpdate, Body()],
    actor: ActorDep,
    request: Request,
    pages: FromDishka[PageService],
) -> PageSchema:
    """Merge the body into the page, as JSON Merge Patch (RFC 7396) defines.

    A field left out keeps its value, and a label or notes sent as null are cleared. The kind and the inclusion cannot
    be cleared, and the order, the origin and the scan of a page are changed by other routes.

    \N{FORM FEED}
    :param address: Identifiers of the project and of the page.
    :type address: PagePath
    :param body: Fields to change, with null for a label or notes to clear.
    :type body: PageUpdate
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: The changed page.
    :rtype: PageSchema
    """
    changed = await pages.update(actor, address.project_id, address.page_id, body.to_changes())
    return PageSchema.from_overview(changed, request)


@router.post('/{project_id}/pages/labels', status_code=status.HTTP_204_NO_CONTENT)
async def number_pages(
    project_id: Annotated[ProjectId, Path(description=PROJECT_ID_DESCRIPTION)],
    body: LabelRange,
    actor: ActorDep,
    pages: FromDishka[PageService],
) -> None:
    """Write the printed numbers of a range of pages into their labels, in the style and from the number given.

    Pages kept out of the book, and pages of the kinds to skip, take no number and keep their label. The answer is 409
    when the range runs backwards. The new labels reach the browser as a ``pages-changed`` event.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param body: The range of pages, the style of the numbers, the first number and the kinds to skip.
    :type body: LabelRange
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    """
    await pages.number(actor, project_id, body.to_numbering())
