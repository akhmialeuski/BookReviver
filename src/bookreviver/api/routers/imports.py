"""Upload of a book source and start of its import."""

from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, File, Path, UploadFile, status

from bookreviver.api.auth import ActorDep
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.domain.ids import ProjectId
from bookreviver.services.imports import ImportService

router = APIRouter(prefix='/projects', tags=['imports'], route_class=DishkaRoute)

# Module-level aliases: FastAPI and dishka read route annotations at runtime
ProjectIdPath = Annotated[ProjectId, Path(description='Identifier of the project')]
SourceFiles = Annotated[list[UploadFile], File(description='One PDF file, or one or more page images, of the book')]
ImportServiceDep = FromDishka[ImportService]


@router.post('/{project_id}/source', status_code=status.HTTP_202_ACCEPTED)
async def upload_source(
    project_id: ProjectIdPath, files: SourceFiles, actor: ActorDep, service: ImportServiceDep
) -> JobSchema:
    """Receive a new source of the book and start importing it; the returned job reports how far it has come."""
    return JobSchema.model_validate(await service.start_import(actor, project_id, files))
