"""The page manifest and one page of a book, addressed by the page's identifier.

The manifest lists the pages of a book in book order, with the computed position of every page and never its order
key, and a page carries the paths of its images. Pages kept out of the book are listed too, with ``included`` false,
so a client can offer to bring them back.
"""

from dataclasses import dataclass
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Path, Request, Response, status

from bookreviver.api.auth import ActorDep
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import ManifestPage, Pager
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.api.schemas.pages import (
    BlankFillChange,
    ContentDetectionRequest,
    LabelRange,
    NumberedPageSchema,
    PageCreate,
    PageCreateList,
    PageMove,
    PageQuery,
    PageSchema,
    PagesMove,
    PageUpdate,
    ScanAttach,
)
from bookreviver.domain.entities import PageOverview
from bookreviver.domain.ids import BookId, PageId
from bookreviver.services.pages import PageService
from bookreviver.services.pagination import PaginationService

BOOK_ID_DESCRIPTION: str = 'Identifier of the book'
PAGE_ID_DESCRIPTION: str = 'Identifier of the page'
LOCATION_HEADER: str = 'Location'

router = APIRouter(prefix='/books', tags=['pages'], route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@dataclass(frozen=True)
class PagePath:
    """The two identifiers in the address of one page, read together so a route keeps to a few parameters.

    :ivar book_id: Identifier of the book.
    :ivar page_id: Identifier of the page.
    """

    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)]
    page_id: Annotated[PageId, Path(description=PAGE_ID_DESCRIPTION)]


class PageLinks:
    """The request and the response of a route that creates a page, which build its schema and its ``Location``."""

    def __init__(self, request: Request, response: Response) -> None:
        """Keep the request, whose application knows the routes, and the response that gets the header.

        :param request: The request.
        :type request: Request
        :param response: The response, which receives the ``Location`` header.
        :type response: Response
        """
        self._request = request
        self._response = response

    def created(self, book_id: BookId, overview: PageOverview) -> PageSchema:
        """Point to a page just created in the ``Location`` header, and return its schema.

        :param book_id: Identifier of the book the page was added to.
        :type book_id: BookId
        :param overview: The new page with its position.
        :type overview: PageOverview
        :returns: The page resource.
        :rtype: PageSchema
        """
        address = self._request.url_for(RouteName.PAGE, book_id=book_id, page_id=overview.page.id)
        self._response.headers[LOCATION_HEADER] = str(address)
        return PageSchema.from_overview(overview, self._request, book_id=book_id)


@router.get('/{book_id}/pages')
async def list_pages(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    request: Request,
    params: Annotated[PageQuery, Depends()],
    pages: FromDishka[PageService],
) -> ManifestPage[PageSchema]:
    """List the pages of a book in book order, up to a thousand at once, with the paths of their images.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
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
    schema_of = partial(PageSchema.from_overview, request=request, book_id=book_id)
    pager = Pager[PageOverview, PageSchema](params, schema_of)
    return pager.page(await pages.manifest(actor, book_id, pager.request, included_only=params.included))


@router.get('/{book_id}/pages/{page_id}', name=RouteName.PAGE)
async def get_page(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    page_id: Annotated[PageId, Path(description=PAGE_ID_DESCRIPTION)],
    actor: ActorDep,
    request: Request,
    pages: FromDishka[PageService],
) -> PageSchema:
    """Return one page of a book.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
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
    return PageSchema.from_overview(await pages.get(actor, book_id, page_id), request, book_id=book_id)


@router.post('/{book_id}/pages/{page_id}/move')
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
    :param address: Identifiers of the book and of the page to move.
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
    moved = await pages.move(actor, address.book_id, address.page_id, body.anchor)
    return PageSchema.from_overview(moved, request, book_id=address.book_id)


@router.post('/{book_id}/pages/move', status_code=status.HTTP_204_NO_CONTENT)
async def move_pages(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: PagesMove,
    actor: ActorDep,
    pages: FromDishka[PageService],
) -> None:
    """Put several pages in a run before or after another page, keeping the order they have in the book.

    The answer is 409 when the anchor is one of the pages. The new order reaches the browser as a ``book-changed``
    event, so the answer has no body.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param body: The pages to move, the page to put them next to, and the side.
    :type body: PagesMove
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    """
    await pages.move_group(actor, book_id, body.page_ids, body.anchor)


@router.post('/{book_id}/pages/blank-fill', status_code=status.HTTP_204_NO_CONTENT)
async def fill_blank_pages(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: BlankFillChange,
    actor: ActorDep,
    pages: FromDishka[PageService],
) -> None:
    """Choose the image of one or more blank pages: their scan, a white leaf or a leaf of the paper of the book.

    A leaf stands in place of the scan of a page of kind blank that is cut from a scan, has the size of the pages of
    the book and passes the stages after the page order without steps and without a mark of review. Its image is
    written by a job, so the pages show it once the job is done. The scan stays, and choosing it again, or changing the
    kind of the page, brings it back. The answer is 409 when a page is not cut from a scan, or is not blank and gets a
    leaf, or when no page of the book has a size to give the leaf, and nothing is changed then. The change reaches the
    browser as a ``book-changed`` event, so the answer has no body.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param body: The pages to change and the choice.
    :type body: BlankFillChange
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    """
    await pages.set_blank_fill(actor, book_id, body.page_ids, body.blank_fill)


@router.post('/{book_id}/pages/content-types/detect', status_code=status.HTTP_202_ACCEPTED)
async def detect_content_types(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: ContentDetectionRequest,
    actor: ActorDep,
    pages: FromDishka[PageService],
) -> JobSchema:
    """Detect what pages show in the background, and answer with the queued job.

    The job reads the preview of the image of each page, tells text from a picture and a picture in colour from one in
    black and white, and writes the answer into the page as its content type. Without ``page_ids`` it goes over the
    pages of the book that have no content type yet, which a job queued by the end of the preparation of the images
    does too, and it leaves the pages the user set by hand and the covers, the endpapers and the blank pages alone. With
    ``page_ids`` it goes over those pages whatever they have, and gives the ones the user set by hand back to the
    detection. The answer is 409 while the content of the pages of the book is being detected. The change reaches the
    browser as a ``book-changed`` event.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param body: The pages to detect, or none for those that are not detected yet.
    :type body: ContentDetectionRequest
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: The queued job.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await pages.start_detection(actor, book_id, body.page_ids))


@router.patch('/{book_id}/pages/{page_id}')
async def update_page(
    address: Annotated[PagePath, Depends()],
    body: Annotated[PageUpdate, Body()],
    actor: ActorDep,
    request: Request,
    pages: FromDishka[PageService],
) -> PageSchema:
    """Merge the body into the page, as JSON Merge Patch (RFC 7396) defines.

    A field left out keeps its value, and a label or notes sent as null are cleared. The kind, the content type and the
    inclusion cannot be cleared, and the order, the origin and the scan of a page are changed by other routes. A content
    type sent here is the user's, which the detection of the content leaves alone. A page that stops being blank shows
    its scan again in place of a leaf.

    \N{FORM FEED}
    :param address: Identifiers of the book and of the page.
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
    changed = await pages.update(actor, address.book_id, address.page_id, body.to_changes())
    return PageSchema.from_overview(changed, request, book_id=address.book_id)


@router.post('/{book_id}/pages/labels', status_code=status.HTTP_204_NO_CONTENT)
async def number_pages(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: LabelRange,
    actor: ActorDep,
    pagination: FromDishka[PaginationService],
) -> None:
    """Make the pagination sections that number a range of pages, in the style and from the number given.

    The range becomes a section of the main flow that starts at its first page, with a series that does not count for
    the kinds to skip, and a section that does not count after the range, so the numbers follow the sections from then
    on. Pages kept out of the book, and pages of the kinds to skip, take no number and keep their label. The pages the
    range numbers give up the labels written by hand. The answer is 409 when the range runs backwards. The new labels
    reach the browser as a ``book-changed`` event.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param body: The range of pages, the style of the numbers, the first number and the kinds to skip.
    :type body: LabelRange
    :param actor: The signed-in account.
    :type actor: Actor
    :param pagination: Pagination service of the request.
    :type pagination: PaginationService
    """
    await pagination.number(actor, book_id, body.to_numbering())


@router.post('/{book_id}/pages/labels/preview')
async def preview_page_numbers(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: LabelRange,
    actor: ActorDep,
    pagination: FromDishka[PaginationService],
) -> list[NumberedPageSchema]:
    """Return the labels a numbering of the same body would write, and write nothing.

    The labels come from the sections the numbering would make, so the interface shows exactly what saving stores. The
    answer is 409 when the range runs backwards.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param body: The range of pages, the style of the numbers, the first number and the kinds to skip.
    :type body: LabelRange
    :param actor: The signed-in account.
    :type actor: Actor
    :param pagination: Pagination service of the request.
    :type pagination: PaginationService
    :returns: Every page the numbering counts, in book order, with the label it would get.
    :rtype: list[NumberedPageSchema]
    """
    numbered = await pagination.preview_numbers(actor, book_id, body.to_numbering())
    return [NumberedPageSchema.model_validate(page) for page in numbered]


@router.post('/{book_id}/pages', status_code=status.HTTP_201_CREATED)
async def create_page(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: Annotated[PageCreate, Body()],
    actor: ActorDep,
    links: Annotated[PageLinks, Depends()],
    pages: FromDishka[PageService],
) -> PageSchema:
    """Add a placeholder or a blank leaf to the book, and point to the page in the ``Location`` header.

    A placeholder has no image. A blank leaf has the median size of the book's pages unless its size is given, and
    its white image is written by a job, so its images appear once the job is done. The answer is 409 for a blank
    leaf without a size when no page of the book has an image to take the median of.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param body: The kind and origin of the page, its place, and the size of a blank leaf.
    :type body: PageCreate
    :param actor: The signed-in account.
    :type actor: Actor
    :param links: The request and response, which build the page and its ``Location``.
    :type links: PageLinks
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: The new page.
    :rtype: PageSchema
    """
    return links.created(book_id, await pages.add(actor, book_id, body.to_new_page()))


@router.post('/{book_id}/pages/batch', status_code=status.HTTP_201_CREATED)
async def create_pages(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: Annotated[PageCreateList, Body()],
    actor: ActorDep,
    request: Request,
    pages: FromDishka[PageService],
) -> list[PageSchema]:
    """Add several placeholders or blank leaves in one transaction, up to a thousand at once.

    Each page is placed as in ``POST /pages``. A page without a place stands after the page before it in the list, and
    the first one at the end of the book. All pages are added or none is, and one ``book-changed`` event follows. The
    answer is 422 for a page whose body is invalid, with its index in the list in the location of the error, and 409
    for a page whose place is no page of the book or a blank leaf without a size in a book with no page to take the
    median of, with its index in the detail.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param body: The pages to add, in the order they stand in where they share a place.
    :type body: PageCreateList
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: The new pages, in the order of the request.
    :rtype: list[PageSchema]
    """
    added = await pages.add_many(actor, book_id, [page.to_new_page() for page in body])
    return [PageSchema.from_overview(overview, request, book_id=book_id) for overview in added]


@router.delete('/{book_id}/pages/{page_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_page(address: Annotated[PagePath, Depends()], actor: ActorDep, pages: FromDishka[PageService]) -> None:
    """Delete a page with its versions and files, leaving its scan and the source of the scan.

    \N{FORM FEED}
    :param address: Identifiers of the book and of the page.
    :type address: PagePath
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    """
    await pages.delete(actor, address.book_id, address.page_id)


@router.put('/{book_id}/pages/{page_id}/scan')
async def attach_scan(
    address: Annotated[PagePath, Depends()],
    body: ScanAttach,
    actor: ActorDep,
    request: Request,
    pages: FromDishka[PageService],
) -> PageSchema:
    """Bind a scan to a placeholder, which becomes a page of that scan once a job has copied its image.

    The answer is 409 for a page that is not a placeholder, a scan that is not cut yet, and a scan another page shows,
    unless ``take_over`` is set, which deletes that page. A scan of another book is a 404.

    \N{FORM FEED}
    :param address: Identifiers of the book and of the placeholder.
    :type address: PagePath
    :param body: The scan to bind, and whether to take it from the page that shows it.
    :type body: ScanAttach
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param pages: Page service of the request.
    :type pages: PageService
    :returns: The page, which has no images until the job is done.
    :rtype: PageSchema
    """
    bound = await pages.attach_scan(actor, address.book_id, address.page_id, body.scan_id, take_over=body.take_over)
    return PageSchema.from_overview(bound, request, book_id=address.book_id)
