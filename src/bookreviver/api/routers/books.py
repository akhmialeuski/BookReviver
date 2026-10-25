"""Book list, creation, description, export, import and deletion.

FastAPI publishes a route's docstring as the description of its operation, up to a form feed. Each route docstring
puts a form feed before its reST fields, so the parameter lists stay in the code and out of the OpenAPI schema.

The import of a book declares no body, as the upload of sources does: a dependency reads the form after the guard of
the router has refused a caller who is not signed in, so no archive is spooled to disk for a request that will be
refused. The export streams the archive as it is written, since a book may hold many gigabytes.
"""

from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Path, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi_pagination import Page, Params
from pydantic import ValidationError

from bookreviver.api.access import BOOK_ID_DESCRIPTION, AccessDep
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import slice_request
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.books import ArchiveForm, BookCreate, BookSchema, BookUpdate
from bookreviver.domain.ids import BookId
from bookreviver.services.book import Book
from bookreviver.services.books import BooksService
from bookreviver.services.stage_summaries import StageSummaries

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LOCATION_HEADER: str = 'Location'
ARCHIVE_MEDIA_TYPE: str = 'application/zip'
MULTIPART_MEDIA_TYPE: str = 'multipart/form-data'
ARCHIVE_FIELD: str = 'archive'
CONTENT_DISPOSITION_HEADER: str = 'Content-Disposition'
# The name of the file of an archive: a plain one for clients that read no other, and the title of the book in UTF-8
ARCHIVE_DISPOSITION: str = 'attachment; filename="book.zip"; filename*=UTF-8\'\'{title}.zip'


async def read_archive(request: Request) -> AsyncIterator[UploadFile]:
    """Read the uploaded archive of the request, once the guard of the router has known the caller to be signed in.

    The form is closed when the response has been sent, which removes the temporary file it spooled.

    :param request: The request, whose multipart body holds the archive.
    :type request: Request
    :returns: Iterator yielding once the uploaded archive, and closing the form afterwards.
    :rtype: AsyncIterator[UploadFile]
    :raises RequestValidationError: If the form holds no archive.
    """
    form = await request.form(max_files=1)
    # try/finally rather than a context manager around the yield, because FastAPI drives this generator
    try:
        try:
            yield ArchiveForm.model_validate({ARCHIVE_FIELD: form.get(ARCHIVE_FIELD)}).archive
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
    finally:
        await form.close()


class BookLinks:
    """The request and the response of a route that creates a book, which build its ``Location``."""

    def __init__(self, request: Request, response: Response) -> None:
        """Keep the request, whose application knows the routes, and the response that gets the header.

        :param request: The request.
        :type request: Request
        :param response: The response, which receives the ``Location`` header.
        :type response: Response
        """
        self._request = request
        self._response = response

    def created(self, book_id: BookId) -> None:
        """Point to a book just created in the ``Location`` header.

        :param book_id: Identifier of the new book.
        :type book_id: BookId
        """
        self._response.headers[LOCATION_HEADER] = str(self._request.url_for(RouteName.BOOK, book_id=book_id))


# The routes of the whole collection name no book, and those of one book take the freshness of its reads
collection = APIRouter(prefix='/books', route_class=DishkaRoute)
one_book = APIRouter(prefix='/books', route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@collection.get('')
async def list_books(
    access: AccessDep, params: Annotated[Params, Depends()], stages: FromDishka[StageSummaries]
) -> Page[BookSchema]:
    """List the books of the signed-in account, the latest added first.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which lists its books.
    :type access: Access
    :param params: Page number and size from the query.
    :type params: Params
    :param stages: Stage summaries of the request, which give each book its progress.
    :type stages: StageSummaries
    :returns: One page of the account's books.
    :rtype: Page[BookSchema]
    """
    window = await access.books(slice_request(params))
    schemas = [
        BookSchema.from_overview(book_id=entry.book_id, overview=await stages.overview(Book(entry.directory)))
        for entry in window.items
    ]
    return Page[BookSchema].create(schemas, params, total=window.total)


@collection.post('', status_code=status.HTTP_201_CREATED)
async def create_book(
    body: Annotated[BookCreate, Body()],
    access: AccessDep,
    links: Annotated[BookLinks, Depends()],
    books: FromDishka[BooksService],
) -> BookId:
    """Create a book from its description, and point to it in the ``Location`` header.

    \N{FORM FEED}
    :param body: Description of the book.
    :type body: BookCreate
    :param access: Access of the signed-in account, which becomes the owner.
    :type access: Access
    :param links: The request and response, which build the ``Location`` of the new book.
    :type links: BookLinks
    :param books: Books service of the request.
    :type books: BooksService
    :returns: The identifier of the new book.
    :rtype: BookId
    """
    book_id = await books.create(access.actor, body.to_details())
    links.created(book_id)
    return book_id


@one_book.get('/{book_id}', name=RouteName.BOOK)
async def get_book(
    access: AccessDep,
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    stages: FromDishka[StageSummaries],
) -> BookSchema:
    """Return one book of the signed-in account.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which opens the book the path names.
    :type access: Access
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param stages: Stage summaries of the request, which give the book its progress.
    :type stages: StageSummaries
    :returns: The book.
    :rtype: BookSchema
    """
    book = await access.book(book_id)
    return BookSchema.from_overview(book_id=book_id, overview=await stages.overview(book))


@one_book.patch('/{book_id}', status_code=status.HTTP_204_NO_CONTENT)
async def update_book(
    access: AccessDep,
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: Annotated[BookUpdate, Body()],
) -> None:
    """Merge the body into the book, as JSON Merge Patch (RFC 7396) defines.

    A field left out keeps its value and a field sent as null is cleared to the value a new book has for it; the
    title cannot be cleared, and the cover must be a page of the book. The answer has no body: the changed book
    is announced on the event stream of the book and read from there.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which opens the book the path names.
    :type access: Access
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param body: Fields to change, with null for a field to clear.
    :type body: BookUpdate
    """
    book = await access.book(book_id)
    await book.update(body.to_changes())


@one_book.get(
    '/{book_id}/archive',
    response_class=StreamingResponse,
    responses={status.HTTP_200_OK: {'content': {ARCHIVE_MEDIA_TYPE: {}}, 'description': 'The archive of the book'}},
)
async def export_book(
    access: AccessDep, book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)]
) -> StreamingResponse:
    """Download the archive of a book, which another instance imports as a book of its own.

    The archive is a ZIP file of the database of the book and every file of its sources and derived files, streamed as
    it is written and named after the title of the book.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which opens the book the path names.
    :type access: Access
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :returns: The stream of the archive.
    :rtype: StreamingResponse
    """
    book = await access.book(book_id)
    archived = await book.export()
    disposition = ARCHIVE_DISPOSITION.format(title=quote(archived.title, safe=''))
    return StreamingResponse(
        archived.pieces, media_type=ARCHIVE_MEDIA_TYPE, headers={CONTENT_DISPOSITION_HEADER: disposition}
    )


@collection.post(
    '/archive',
    status_code=status.HTTP_201_CREATED,
    openapi_extra={
        'requestBody': {
            'required': True,
            'content': {MULTIPART_MEDIA_TYPE: {'schema': ArchiveForm.model_json_schema()}},
        }
    },
)
async def import_book(
    archive: Annotated[UploadFile, Depends(read_archive)],
    access: AccessDep,
    links: Annotated[BookLinks, Depends()],
    books: FromDishka[BooksService],
) -> BookId:
    """Import the archive of a book as a new book of the signed-in account, and point to it in ``Location``.

    The book keeps its pages, versions, history, recipes and settings, and gets a new identifier, so the same archive
    can be imported twice. An archive that is not the archive of a book is refused, and so is one exported by a newer
    release.

    \N{FORM FEED}
    :param archive: The uploaded archive, read once the caller was checked.
    :type archive: UploadFile
    :param access: Access of the signed-in account, which becomes the owner.
    :type access: Access
    :param links: The request and response, which build the ``Location`` of the new book.
    :type links: BookLinks
    :param books: Books service of the request.
    :type books: BooksService
    :returns: The identifier of the new book.
    :rtype: BookId
    """
    book_id = await books.import_archive(access.actor, archive.file)
    links.created(book_id)
    return book_id


@one_book.delete('/{book_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(
    access: AccessDep,
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    books: FromDishka[BooksService],
) -> None:
    """Delete a book with its pages, jobs and files.

    \N{FORM FEED}
    :param access: Access of the signed-in account, which opens the book the path names.
    :type access: Access
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param books: Books service of the request.
    :type books: BooksService
    """
    await books.delete(book_id=book_id, book=await access.book(book_id))


router = APIRouter(tags=['books'])
router.include_router(collection)
router.include_router(one_book)
