"""The place an account left a book at, which the book is opened at again on any device.

FastAPI publishes a route's docstring as the description of its operation, up to a form feed. Each route docstring
puts a form feed before its reST fields, so the parameter lists stay in the code and out of the OpenAPI schema.
"""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Response, status

from bookreviver.api.book import open_book
from bookreviver.api.schemas.places import BookPlaceBody, BookPlaceSchema
from bookreviver.services.book import Book
from bookreviver.services.places import PlaceService

NO_PLACE_DESCRIPTION: str = 'The account has not worked on the book yet'

router = APIRouter(prefix='/books', tags=['places'], route_class=DishkaRoute)


@router.get(
    '/{book_id}/place',
    response_model=BookPlaceSchema,
    responses={status.HTTP_204_NO_CONTENT: {'description': NO_PLACE_DESCRIPTION}},
)
async def get_place(
    book: Annotated[Book, Depends(open_book)],
    places: FromDishka[PlaceService],
) -> BookPlaceSchema | Response:
    """Return the place the signed-in account left the book at, or 204 when it has not worked on the book yet.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param places: Place service of the request.
    :type places: PlaceService
    :returns: The place, or an empty answer.
    :rtype: BookPlaceSchema | Response
    """
    place = await places.find(book)
    if place is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return BookPlaceSchema.model_validate(place)


@router.put('/{book_id}/place')
async def put_place(
    book: Annotated[Book, Depends(open_book)],
    body: Annotated[BookPlaceBody, Body()],
    places: FromDishka[PlaceService],
) -> BookPlaceSchema:
    """Replace the place the signed-in account left the book at.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param body: Where the reader is in the book.
    :type body: BookPlaceBody
    :param places: Place service of the request.
    :type places: PlaceService
    :returns: The place as stored.
    :rtype: BookPlaceSchema
    """
    return BookPlaceSchema.model_validate(await places.save(book, body.to_place()))
