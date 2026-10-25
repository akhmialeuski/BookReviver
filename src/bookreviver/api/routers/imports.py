"""Upload of the files of a book and start of their import.

``POST /books/{book_id}/sources`` takes any number of files, whether chosen one by one or as a whole directory,
puts an import job on the board and answers 202 with it at once. The files are imported in the background, and the job
and the events of the book tell the browser how it goes.

FastAPI parses a declared body before it runs any dependency, so a route with a ``File()`` parameter would spool the
upload to disk before the caller is known to be signed in, to own the book or to be allowed another import. The
route therefore declares no body. A dependency reads the form after the actor and the book have been checked, and
the schema of the body is given to OpenAPI by ``openapi_extra``. Starlette's parser stops at 1000 files and answers a
400 of its own, so it is given no ceiling. The one limit is then ``max_upload_files``, which
``ImportService.start_import`` enforces and answers with its 413 problem for any number of files past it.
"""

import math
from collections.abc import AsyncIterator
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Path, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from bookreviver.api.access import BOOK_ID_DESCRIPTION, open_book
from bookreviver.api.schemas.imports import UploadForm
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.domain.ids import BookId
from bookreviver.services.book import Book
from bookreviver.services.imports import ImportService

# Starlette's parser takes a float limit, and infinity leaves the count of files to the service
MULTIPART_FILES_UNLIMITED: float = math.inf
MULTIPART_MEDIA_TYPE: str = 'multipart/form-data'
FILES_FIELD: str = 'files'


@inject
async def read_upload(
    book: Annotated[Book, Depends(open_book)],
    request: Request,
    imports: FromDishka[ImportService],
) -> AsyncIterator[list[UploadFile]]:
    """Read the uploaded files of the request, once the caller may import into the book.

    The form is closed when the response has been sent, which removes the temporary files it spooled.

    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param request: The request, whose multipart body holds the files.
    :type request: Request
    :param imports: Import service of the request.
    :type imports: ImportService
    :returns: Iterator yielding once the uploaded files, and closing the form afterwards.
    :rtype: AsyncIterator[list[UploadFile]]
    :raises RequestValidationError: If the form holds no file, or a ``files`` field that is not one.
    """
    await imports.authorize_upload(book)
    form = await request.form(max_files=MULTIPART_FILES_UNLIMITED)
    # try/finally rather than a context manager around the yield, because FastAPI drives this generator
    try:
        try:
            yield UploadForm.model_validate({FILES_FIELD: form.getlist(FILES_FIELD)}).files
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
    finally:
        await form.close()


router = APIRouter(prefix='/books', tags=['imports'], route_class=DishkaRoute)


@router.post(
    '/{book_id}/sources',
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        'requestBody': {
            'required': True,
            'content': {MULTIPART_MEDIA_TYPE: {'schema': UploadForm.model_json_schema()}},
        }
    },
)
async def upload_sources(
    book: Annotated[Book, Depends(open_book)],
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    files: Annotated[list[UploadFile], Depends(read_upload)],
    imports: FromDishka[ImportService],
) -> JobSchema:
    """Receive the files of an upload and import them in the background, each one as a source of the book.

    The response is the queued job. Every file of the upload becomes a source of the book, and the ones the import
    rejected, with the reason, or skipped when it was cancelled, are listed by the sources of the book until the
    reader closes the report. A book imports one upload at a time.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param files: The uploaded files, read once the caller was checked.
    :type files: list[UploadFile]
    :param imports: Import service of the request.
    :type imports: ImportService
    :returns: The queued import job.
    :rtype: JobSchema
    """
    return JobSchema.of(await imports.start_import(book, files), book_id)
