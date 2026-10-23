"""The sources a book was assembled from and their scans, which are read-only records of where the book came from.

Uploading files, which creates sources, is the import route. A source is deleted here, with its files and its scans,
and the pages of the book stay with their own images. That is also how a user gets out of an import that keeps failing
on one scan: the source is deleted by hand.

Every file of an upload is a source with a status, and the rejected and skipped ones are the report of the import. The
list shows the imported sources unless it is asked for the others, and the report is closed by deleting them all.
"""

from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi_pagination import Page, Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.pages import PageAnchorBody
from bookreviver.api.schemas.sources import ScanQuery, ScanSchema, SourceSchema
from bookreviver.domain.entities import Scan, Source
from bookreviver.domain.enums import SourceStatus
from bookreviver.domain.ids import BookId, SourceId
from bookreviver.services.pages import PageService
from bookreviver.services.sources import SourceService

BOOK_ID_DESCRIPTION: str = 'Identifier of the book'
SOURCE_ID_DESCRIPTION: str = 'Identifier of the source'
STATUS_DESCRIPTION: str = 'List the files in these statuses; the imported sources by default'
# The statuses the list shows when it is asked for none, which are the sources of the book and not the import report
IMPORTED_ONLY: frozenset[SourceStatus] = frozenset({SourceStatus.IMPORTED})

router = APIRouter(prefix='/books', tags=['sources'], route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


# The statuses are a parameter of their own beside the page parameters, not a field of a query model: a list field of a
# model taken with ``Depends()`` is read as a body, and a model taken with ``Query()`` beside the page parameters is
# published as one opaque object
@router.get('/{book_id}/sources')
async def list_sources(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    params: Annotated[Params, Depends()],
    sources: FromDishka[SourceService],
    status: Annotated[frozenset[SourceStatus], Query(description=STATUS_DESCRIPTION)] = IMPORTED_ONLY,
) -> Page[SourceSchema]:
    """List the sources of a book in the order they were recorded, the imported ones unless ``status`` says more.

    The rejected and skipped files are the report of an import: the files no check let through, with their reasons, and
    the files a cancelled import never reached.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param params: Page number and size.
    :type params: Params
    :param sources: Source service of the request.
    :type sources: SourceService
    :param status: Statuses of the files to list.
    :type status: frozenset[SourceStatus]
    :returns: One page of the book's files in the statuses.
    :rtype: Page[SourceSchema]
    """
    pager = Pager[Source, SourceSchema](params, SourceSchema.model_validate)
    return pager.page(await sources.list(actor, book_id, pager.request, statuses=status))


@router.delete('/{book_id}/sources/unimported', status_code=status.HTTP_204_NO_CONTENT)
async def delete_unimported_sources(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    sources: FromDishka[SourceService],
) -> None:
    """Delete the rejected and skipped files of a book, which is closing the report of an import.

    The imported sources stay. The book answers 409 while it imports.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param sources: Source service of the request.
    :type sources: SourceService
    """
    await sources.delete_unimported(actor, book_id)


@router.get('/{book_id}/sources/{source_id}')
async def get_source(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    source_id: Annotated[SourceId, Path(description=SOURCE_ID_DESCRIPTION)],
    actor: ActorDep,
    sources: FromDishka[SourceService],
) -> SourceSchema:
    """Return one source of a book.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param source_id: Identifier of the source.
    :type source_id: SourceId
    :param actor: The signed-in account.
    :type actor: Actor
    :param sources: Source service of the request.
    :type sources: SourceService
    :returns: The source.
    :rtype: SourceSchema
    """
    return SourceSchema.model_validate(await sources.get(actor, book_id, source_id))


@router.delete('/{book_id}/sources/{source_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    source_id: Annotated[SourceId, Path(description=SOURCE_ID_DESCRIPTION)],
    actor: ActorDep,
    sources: FromDishka[SourceService],
) -> None:
    """Delete a source with its files and its scans, leaving the pages of the book with their own images.

    The pages made from the source lose only the reference to their scan. The book answers 409 while it imports.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param source_id: Identifier of the source.
    :type source_id: SourceId
    :param actor: The signed-in account.
    :type actor: Actor
    :param sources: Source service of the request.
    :type sources: SourceService
    """
    await sources.delete(actor, book_id, source_id)


@router.get('/{book_id}/scans')
async def list_scans(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    request: Request,
    query: Annotated[ScanQuery, Depends()],
    sources: FromDishka[SourceService],
) -> Page[ScanSchema]:
    """List the scans of a book source by source, or of one source, with the paths of their images once cut.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param query: Page number and size, and the source to list the scans of, if any.
    :type query: ScanQuery
    :param sources: Source service of the request.
    :type sources: SourceService
    :returns: One page of scans.
    :rtype: Page[ScanSchema]
    """
    pager = Pager[Scan, ScanSchema](query, partial(ScanSchema.from_scan, request=request, book_id=book_id))
    return pager.page(await sources.scans(actor, book_id, pager.request, source_id=query.source_id))


@router.post('/{book_id}/sources/{source_id}/pages/move', status_code=status.HTTP_204_NO_CONTENT)
async def move_source_pages(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    source_id: Annotated[SourceId, Path(description=SOURCE_ID_DESCRIPTION)],
    body: PageAnchorBody,
    actor: ActorDep,
    pages: FromDishka[PageService],
) -> None:
    """Put every page of one source in a run before or after a page, which places a missing part of the book at once.

    The answer is 409 when the anchor is a page of the source. The new order reaches the browser as a ``book-changed``
    event, so the answer has no body.

    \N{FORM FEED}
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param source_id: Identifier of the source whose pages are moved.
    :type source_id: SourceId
    :param body: The page to put them next to, and the side.
    :type body: PageAnchorBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param pages: Page service of the request.
    :type pages: PageService
    """
    await pages.move_source(actor, book_id, source_id, body.anchor)
