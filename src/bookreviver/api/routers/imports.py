"""Upload of the files of a book and start of their import.

``POST /projects/{project_id}/sources`` takes any number of files, whether chosen one by one or as a whole directory,
records an import job and answers 202 with it at once. The files are imported in the background, and the job, its
result and the events of the project tell the browser how it goes.

FastAPI parses a declared body before it runs any dependency, so a route with a ``File()`` parameter would spool the
upload to disk before the caller is known to be signed in, to own the project or to be allowed another import. The
route therefore declares no body. A dependency reads the form after the actor and the project have been checked, and
the schema of the body is given to OpenAPI by ``openapi_extra``. The form is parsed with the file limit of the upload
rule, since Starlette's parser stops at 1000 files, and with one file more, so an upload past the rule reaches
``ImportService.start_import`` and is answered with its 413 problem rather than with the parser's own 400. An upload
of two files past the rule is still refused by the parser.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Path, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from bookreviver.api.auth import ActorDep
from bookreviver.api.schemas.imports import UploadForm
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.domain.ids import ProjectId
from bookreviver.services.imports import ImportLimits, ImportService

MULTIPART_FILES_OVER_THE_RULE: int = 1
MULTIPART_MEDIA_TYPE: str = 'multipart/form-data'
FILES_FIELD: str = 'files'


@inject
async def read_upload(
    request: Request,
    project_id: Annotated[ProjectId, Path(description='Identifier of the project')],
    actor: ActorDep,
    imports: FromDishka[ImportService],
    limits: FromDishka[ImportLimits],
) -> AsyncIterator[list[UploadFile]]:
    """Read the uploaded files of the request, once the caller may import into the project.

    The form is closed when the response has been sent, which removes the temporary files it spooled.

    :param request: The request, whose multipart body holds the files.
    :type request: Request
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param imports: Import service of the request.
    :type imports: ImportService
    :param limits: Bounds of an upload, of which the number of files is read.
    :type limits: ImportLimits
    :returns: Iterator yielding once the uploaded files, and closing the form afterwards.
    :rtype: AsyncIterator[list[UploadFile]]
    :raises RequestValidationError: If the form holds no file, or a ``files`` field that is not one.
    """
    await imports.authorize_upload(actor, project_id)
    form = await request.form(max_files=limits.max_files + MULTIPART_FILES_OVER_THE_RULE)
    # try/finally rather than a context manager around the yield, because FastAPI drives this generator
    try:
        try:
            yield UploadForm.model_validate({FILES_FIELD: form.getlist(FILES_FIELD)}).files
        except ValidationError as error:
            raise RequestValidationError(error.errors()) from error
    finally:
        await form.close()


router = APIRouter(prefix='/projects', tags=['imports'], route_class=DishkaRoute)


@router.post(
    '/{project_id}/sources',
    status_code=status.HTTP_202_ACCEPTED,
    openapi_extra={
        'requestBody': {
            'required': True,
            'content': {MULTIPART_MEDIA_TYPE: {'schema': UploadForm.model_json_schema()}},
        }
    },
)
async def upload_sources(
    project_id: Annotated[ProjectId, Path(description='Identifier of the project')],
    files: Annotated[list[UploadFile], Depends(read_upload)],
    actor: ActorDep,
    imports: FromDishka[ImportService],
) -> JobSchema:
    """Receive the files of an upload and import them in the background, each one as a source of the project.

    The response is the queued job. Its result, once it has finished, lists the files that were rejected and why.
    A project imports one upload at a time.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param files: The uploaded files, read once the caller was checked.
    :type files: list[UploadFile]
    :param actor: The signed-in account.
    :type actor: Actor
    :param imports: Import service of the request.
    :type imports: ImportService
    :returns: The queued import job.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await imports.start_import(actor, project_id, files))
