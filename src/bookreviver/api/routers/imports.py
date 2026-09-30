"""Upload of the files of a book and start of their import.

``POST /projects/{project_id}/sources`` takes any number of files, whether chosen one by one or as a whole directory,
records an import job and answers 202 with it at once. The files are imported in the background, and the job, its
result and the events of the project tell the browser how it goes.

Starlette's multipart parser refuses more than 1000 files unless told otherwise, which is fewer than an upload may
hold, so the route class parses the body of its requests first, with the limit of the upload rule. FastAPI then finds
the parsed form on the request. The parser is allowed one file more than the rule, so an upload past the rule reaches
``ImportService.start_import`` and is answered with its 413 problem rather than with the parser's own 400. An upload
of two files past the rule is still refused by the parser.
"""

from typing import TYPE_CHECKING, Annotated, override

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, File, Path, Request, UploadFile, status

from bookreviver.api.auth import ActorDep
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.domain.ids import ProjectId
from bookreviver.services.imports import ImportLimits, ImportService

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from fastapi import Response

MULTIPART_FILES_OVER_THE_RULE: int = 1


class UploadRoute(DishkaRoute):
    """A route that parses its multipart body with the file limit of the upload rule."""

    @override
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        """Return the route's handler, which first parses the body with the limit of the upload rule.

        :returns: Handler that parses the form of the request and then runs the route's own handler.
        :rtype: Callable[[Request], Coroutine[Any, Any, Response]]
        """
        handler = super().get_route_handler()

        async def upload_handler(request: Request) -> Response:
            """Parse the multipart body of the request, then run the route's handler on it.

            :param request: The request received.
            :type request: Request
            :returns: The response of the route.
            :rtype: Response
            """
            limits = await request.state.dishka_container.get(ImportLimits)
            # The request keeps the parsed form, so the route's own read of it finds this one
            await request.form(max_files=limits.max_files + MULTIPART_FILES_OVER_THE_RULE)
            return await handler(request)

        return upload_handler


router = APIRouter(prefix='/projects', tags=['imports'], route_class=UploadRoute)


@router.post('/{project_id}/sources', status_code=status.HTTP_202_ACCEPTED)
async def upload_sources(
    project_id: Annotated[ProjectId, Path(description='Identifier of the project')],
    files: Annotated[list[UploadFile], File(description='The files to import, each one a source of its own')],
    actor: ActorDep,
    imports: FromDishka[ImportService],
) -> JobSchema:
    """Receive the files of an upload and import them in the background, each one as a source of the project.

    The response is the queued job. Its result, once it has finished, lists the files that were rejected and why.
    A project imports one upload at a time.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param files: The uploaded files, from a directory or chosen one by one.
    :type files: list[UploadFile]
    :param actor: The signed-in account.
    :type actor: Actor
    :param imports: Import service of the request.
    :type imports: ImportService
    :returns: The queued import job.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await imports.start_import(actor, project_id, files))
