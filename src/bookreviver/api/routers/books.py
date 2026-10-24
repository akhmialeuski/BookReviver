"""Book list, creation, description, export, import and deletion.

FastAPI publishes a route's docstring as the description of its operation, up to a form feed. Each route docstring
puts a form feed before its reST fields, so the parameter lists stay in the code and out of the OpenAPI schema.

The import of a book declares no body, as the upload of sources does: a dependency reads the form once the caller is
known to be signed in, so no archive is spooled to disk for a request that will be refused. The export streams the
archive as it is written, since a book may hold many gigabytes.
"""

from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi_pagination import Page, Params
from pydantic import ValidationError

from bookreviver.api.access import open_book, own_books
from bookreviver.api.auth import ActorDep
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import Pager
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.books import ArchiveForm, BookCreate, BookSchema, BookUpdate
from bookreviver.domain.entities import BookOverview
from bookreviver.domain.values import Slice
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


async def read_archive(request: Request, _actor: ActorDep) -> AsyncIterator[UploadFile]:
    """Read the uploaded archive of the request, once the caller is known to be signed in.

    The form is closed when the response has been sent, which removes the temporary file it spooled.

    :param request: The request, whose multipart body holds the archive.
    :type request: Request
    :param _actor: The signed-in account, resolved before the body is read.
    :type _actor: Actor
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

    def created(self, book: Book) -> None:
        """Point to a book just created in the ``Location`` header.

        :param book: The new book.
        :type book: Book
        """
        self._response.headers[LOCATION_HEADER] = str(self._request.url_for(RouteName.BOOK, book_id=book.id))


# The routes of the whole collection name no book, and those of one book take the freshness of its reads
collection = APIRouter(prefix='/books', route_class=DishkaRoute)
one_book = APIRouter(prefix='/books', route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@collection.get('')
async def list_books(
    params: Annotated[Params, Depends()],
    window: Annotated[Slice[Book], Depends(own_books)],
    stages: FromDishka[StageSummaries],
) -> Page[BookSchema]:
    """List the books of the signed-in account, the latest added first.

    \N{FORM FEED}
    :param params: Page number and size from the query.
    :type params: Params
    :param window: The books of the signed-in account the page asks for.
    :type window: Slice[Book]
    :param stages: Stage summaries of the request, which give each book its progress.
    :type stages: StageSummaries
    :returns: One page of the account's books.
    :rtype: Page[BookSchema]
    """
    pager = Pager[BookOverview, BookSchema](params, BookSchema.from_overview)
    return pager.page(Slice(items=[await stages.overview(book) for book in window.items], total=window.total))


@collection.post('', status_code=status.HTTP_201_CREATED)
async def create_book(
    body: Annotated[BookCreate, Body()],
    actor: ActorDep,
    links: Annotated[BookLinks, Depends()],
    books: FromDishka[BooksService],
    stages: FromDishka[StageSummaries],
) -> BookSchema:
    """Create a book from its description, and point to it in the ``Location`` header.

    \N{FORM FEED}
    :param body: Description of the book.
    :type body: BookCreate
    :param actor: The signed-in account, which becomes the owner.
    :type actor: Actor
    :param links: The request and response, which build the ``Location`` of the new book.
    :type links: BookLinks
    :param books: Books service of the request.
    :type books: BooksService
    :param stages: Stage summaries of the request, which give the book its progress.
    :type stages: StageSummaries
    :returns: The new book.
    :rtype: BookSchema
    """
    book = await books.create(actor, body.to_details())
    links.created(book)
    return BookSchema.from_overview(await stages.overview(book))


@one_book.get('/{book_id}', name=RouteName.BOOK)
async def get_book(
    book: Annotated[Book, Depends(open_book)],
    stages: FromDishka[StageSummaries],
) -> BookSchema:
    """Return one book of the signed-in account.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param stages: Stage summaries of the request, which give the book its progress.
    :type stages: StageSummaries
    :returns: The book.
    :rtype: BookSchema
    """
    return BookSchema.from_overview(await stages.overview(book))


@one_book.patch('/{book_id}')
async def update_book(
    book: Annotated[Book, Depends(open_book)],
    body: Annotated[BookUpdate, Body()],
    stages: FromDishka[StageSummaries],
) -> BookSchema:
    """Merge the body into the book, as JSON Merge Patch (RFC 7396) defines.

    A field left out keeps its value and a field sent as null is cleared to the value a new book has for it; the
    title cannot be cleared, and the cover must be a page of the book.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param body: Fields to change, with null for a field to clear.
    :type body: BookUpdate
    :param stages: Stage summaries of the request, which give the changed book its progress.
    :type stages: StageSummaries
    :returns: The changed book.
    :rtype: BookSchema
    """
    await book.update(body.to_changes())
    return BookSchema.from_overview(await stages.overview(book))


@one_book.get(
    '/{book_id}/archive',
    response_class=StreamingResponse,
    responses={status.HTTP_200_OK: {'content': {ARCHIVE_MEDIA_TYPE: {}}, 'description': 'The archive of the book'}},
)
async def export_book(book: Annotated[Book, Depends(open_book)]) -> StreamingResponse:
    """Download the archive of a book, which another instance imports as a book of its own.

    The archive is a ZIP file of the database of the book and every file of its sources and derived files, streamed as
    it is written and named after the title of the book.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :returns: The stream of the archive.
    :rtype: StreamingResponse
    """
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
    actor: ActorDep,
    links: Annotated[BookLinks, Depends()],
    books: FromDishka[BooksService],
    stages: FromDishka[StageSummaries],
) -> BookSchema:
    """Import the archive of a book as a new book of the signed-in account, and point to it in ``Location``.

    The book keeps its pages, versions, history, recipes and settings, and gets a new identifier, so the same archive
    can be imported twice. An archive that is not the archive of a book is refused, and so is one exported by a newer
    release.

    \N{FORM FEED}
    :param archive: The uploaded archive, read once the caller was checked.
    :type archive: UploadFile
    :param actor: The signed-in account, which becomes the owner.
    :type actor: Actor
    :param links: The request and response, which build the ``Location`` of the new book.
    :type links: BookLinks
    :param books: Books service of the request.
    :type books: BooksService
    :param stages: Stage summaries of the request, which give the book its progress.
    :type stages: StageSummaries
    :returns: The new book.
    :rtype: BookSchema
    """
    book = await books.import_archive(actor, archive.file)
    links.created(book)
    return BookSchema.from_overview(await stages.overview(book))


@one_book.delete('/{book_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(
    book: Annotated[Book, Depends(open_book)],
    books: FromDishka[BooksService],
) -> None:
    """Delete a book with its pages, jobs and files.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param books: Books service of the request.
    :type books: BooksService
    """
    await books.delete(book)


router = APIRouter(tags=['books'])
router.include_router(collection)
router.include_router(one_book)
