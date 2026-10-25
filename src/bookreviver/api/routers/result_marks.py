"""The mark and the comment of a result, which is a version of a page.

A mark and a comment are notes of the user on a result: setting them changes no file, no identifier and no stage record,
and the answer is the version with both.
"""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Request

from bookreviver.api.access import open_book
from bookreviver.api.routers.processing import VersionPath
from bookreviver.api.schemas.processing import PageVersionSchema
from bookreviver.api.schemas.result_marks import ResultMarkForm
from bookreviver.services.book import Book
from bookreviver.services.result_marks import ResultMarksService

router = APIRouter(prefix='/books', tags=['result-marks'], route_class=DishkaRoute)


@router.put('/{book_id}/pages/{page_id}/versions/{version_id}/mark')
async def put_mark(
    book: Annotated[Book, Depends(open_book)],
    address: Annotated[VersionPath, Depends()],
    form: ResultMarkForm,
    request: Request,
    marks: FromDishka[ResultMarksService],
) -> PageVersionSchema:
    """Replace the mark and the comment of a result.

    Both are replaced together, so a body without a mark takes the mark off and an empty comment takes the comment off.
    A body equal to what is stored writes nothing. The answer is 409 for a preview.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param address: Identifiers of the book, the page and the version.
    :type address: VersionPath
    :param form: The mark and the comment.
    :type form: ResultMarkForm
    :param request: The request, whose application knows the route that serves the images.
    :type request: Request
    :param marks: Result marks service of the request.
    :type marks: ResultMarksService
    :returns: The version with its mark and comment.
    :rtype: PageVersionSchema
    """
    version = await marks.set(book, address.page_id, address.version_id, form.to_note())
    return PageVersionSchema.of(version, address.book_id, request)
