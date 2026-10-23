"""Project list, creation, book description, export, import and deletion.

FastAPI publishes a route's docstring as the description of its operation, up to a form feed. Each route docstring
puts a form feed before its reST fields, so the parameter lists stay in the code and out of the OpenAPI schema.

The import of a book declares no body, as the upload of sources does: a dependency reads the form once the caller is
known to be signed in, so no archive is spooled to disk for a request that will be refused. The export streams the
archive as it is written, since a book may hold many gigabytes.
"""

from typing import TYPE_CHECKING, Annotated
from urllib.parse import quote

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Body, Depends, Path, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import StreamingResponse
from fastapi_pagination import Page, Params
from pydantic import ValidationError

from bookreviver.api.auth import ActorDep
from bookreviver.api.freshness import fresh_book
from bookreviver.api.pagination import Pager
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.books import ArchiveForm, BookCreate, BookSchema, BookUpdate
from bookreviver.domain.entities import BookOverview
from bookreviver.domain.ids import BookId
from bookreviver.services.books import BooksService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LOCATION_HEADER: str = 'Location'
BOOK_ID_DESCRIPTION: str = 'Identifier of the book'
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


router = APIRouter(prefix='/books', tags=['books'], route_class=DishkaRoute, dependencies=[Depends(fresh_book)])


@router.get('')
async def list_books(
    actor: ActorDep, params: Annotated[Params, Depends()], books: FromDishka[BooksService]
) -> Page[BookSchema]:
    """List the projects of the signed-in account, the latest added first.

    \N{FORM FEED}
    :param actor: The signed-in account.
    :type actor: Actor
    :param params: Page number and size from the query.
    :type params: Params
    :param books: Project service of the request.
    :type books: BooksService
    :returns: One page of the account's projects.
    :rtype: Page[BookSchema]
    """
    pager = Pager[BookOverview, BookSchema](params, BookSchema.from_overview)
    return pager.page(await books.list(actor, pager.request))


@router.post('', status_code=status.HTTP_201_CREATED)
async def create_book(
    body: Annotated[BookCreate, Body()],
    actor: ActorDep,
    request: Request,
    response: Response,
    books: FromDishka[BooksService],
) -> BookSchema:
    """Create a project from the description of its book, and point to it in the ``Location`` header.

    \N{FORM FEED}
    :param body: Description of the book.
    :type body: BookCreate
    :param actor: The signed-in account, which becomes the owner.
    :type actor: Actor
    :param request: The request, whose URL the new project's address is built from.
    :type request: Request
    :param response: The response, which receives the ``Location`` header.
    :type response: Response
    :param books: Project service of the request.
    :type books: BooksService
    :returns: The new project.
    :rtype: BookSchema
    """
    overview = await books.create(actor, body.to_details())
    response.headers[LOCATION_HEADER] = str(request.url_for(RouteName.BOOK, book_id=overview.book_record.id))
    return BookSchema.from_overview(overview)


@router.get('/{book_id}', name=RouteName.BOOK)
async def get_book(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    books: FromDishka[BooksService],
) -> BookSchema:
    """Return one project of the signed-in account.

    \N{FORM FEED}
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param books: Project service of the request.
    :type books: BooksService
    :returns: The project.
    :rtype: BookSchema
    """
    return BookSchema.from_overview(await books.get(actor, book_id))


@router.patch('/{book_id}')
async def update_book(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    body: Annotated[BookUpdate, Body()],
    actor: ActorDep,
    books: FromDishka[BooksService],
) -> BookSchema:
    """Merge the body into the project, as JSON Merge Patch (RFC 7396) defines.

    A field left out keeps its value and a field sent as null is cleared to the value a new project has for it; the
    title cannot be cleared, and the cover must be a page of the project.

    \N{FORM FEED}
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param body: Fields to change, with null for a field to clear.
    :type body: BookUpdate
    :param actor: The signed-in account.
    :type actor: Actor
    :param books: Project service of the request.
    :type books: BooksService
    :returns: The changed project.
    :rtype: BookSchema
    """
    return BookSchema.from_overview(await books.update(actor, book_id, body.to_changes()))


@router.get(
    '/{book_id}/archive',
    response_class=StreamingResponse,
    responses={status.HTTP_200_OK: {'content': {ARCHIVE_MEDIA_TYPE: {}}, 'description': 'The archive of the book'}},
)
async def export_book(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    books: FromDishka[BooksService],
) -> StreamingResponse:
    """Download the archive of a book, which another instance imports as a book of its own.

    The archive is a ZIP file of the database of the book and every file of its sources and derived files, streamed as
    it is written and named after the title of the book.

    \N{FORM FEED}
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param books: Project service of the request.
    :type books: BooksService
    :returns: The stream of the archive.
    :rtype: StreamingResponse
    """
    archived = await books.export(actor, book_id)
    disposition = ARCHIVE_DISPOSITION.format(title=quote(archived.title, safe=''))
    return StreamingResponse(
        archived.pieces, media_type=ARCHIVE_MEDIA_TYPE, headers={CONTENT_DISPOSITION_HEADER: disposition}
    )


@router.post(
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
    request: Request,
    response: Response,
    books: FromDishka[BooksService],
) -> BookSchema:
    """Import the archive of a book as a new project of the signed-in account, and point to it in ``Location``.

    The book keeps its pages, versions, history, recipes and settings, and gets a new identifier, so the same archive
    can be imported twice. An archive that is not the archive of a book is refused, and so is one exported by a newer
    release.

    \N{FORM FEED}
    :param archive: The uploaded archive, read once the caller was checked.
    :type archive: UploadFile
    :param actor: The signed-in account, which becomes the owner.
    :type actor: Actor
    :param request: The request, whose URL the new project's address is built from.
    :type request: Request
    :param response: The response, which receives the ``Location`` header.
    :type response: Response
    :param books: Project service of the request.
    :type books: BooksService
    :returns: The new project.
    :rtype: BookSchema
    """
    overview = await books.import_archive(actor, archive.file)
    response.headers[LOCATION_HEADER] = str(request.url_for(RouteName.BOOK, book_id=overview.book_record.id))
    return BookSchema.from_overview(overview)


@router.delete('/{book_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_book(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    books: FromDishka[BooksService],
) -> None:
    """Delete a project with its pages, jobs and files.

    \N{FORM FEED}
    :param book_id: Identifier of the project.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param books: Project service of the request.
    :type books: BooksService
    """
    await books.delete(actor, book_id)
