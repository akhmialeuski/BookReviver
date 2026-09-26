"""Background job state, cancellation and project event streams."""

from collections.abc import AsyncIterator
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Path
from fastapi.sse import EventSourceResponse, ServerSentEvent

from bookreviver.api.auth import ActorDep
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.jobs import EventName, JobSchema, PageReadySchema, ProjectChangedSchema
from bookreviver.domain.events import DomainEvent, JobChanged, PageReady, ProjectChanged
from bookreviver.domain.ids import JobId, ProjectId
from bookreviver.services.jobs import JobService

# Module-level aliases: FastAPI and dishka read route annotations at runtime
JobIdPath = Annotated[JobId, Path(description='Identifier of the job')]
ProjectIdPath = Annotated[ProjectId, Path(description='Identifier of the project')]
JobServiceDep = FromDishka[JobService]
ProjectEvents = AsyncIterator[DomainEvent]

jobs = APIRouter(prefix='/jobs', route_class=DishkaRoute)
project_events = APIRouter(prefix='/projects', route_class=DishkaRoute)


@jobs.get('/{job_id}')
async def read_job(job_id: JobIdPath, actor: ActorDep, service: JobServiceDep) -> JobSchema:
    """Return the state and progress of a job."""
    return JobSchema.model_validate(await service.get(actor, job_id))


@jobs.delete('/{job_id}')
async def cancel_job(job_id: JobIdPath, actor: ActorDep, service: JobServiceDep) -> JobSchema:
    """Cancel a queued or running job and return it; a running job stops before its next page."""
    return JobSchema.model_validate(await service.cancel(actor, job_id))


@inject
async def open_event_stream(project_id: ProjectIdPath, actor: ActorDep, service: JobServiceDep) -> ProjectEvents:
    """Subscribe to the project's events; as a dependency, it can still answer with a problem before streaming."""
    return await service.events(actor, project_id)


@project_events.get('/{project_id}/events', response_class=EventSourceResponse, name=RouteName.PROJECT_EVENTS)
async def stream_project_events(
    events: Annotated[ProjectEvents, Depends(open_event_stream)],
) -> AsyncIterator[ServerSentEvent]:
    """Stream the project's events as they happen: job changes, pages becoming ready and description changes."""
    async for event in events:
        match event:
            case JobChanged(job=job):
                yield ServerSentEvent(event=EventName.JOB_CHANGED, data=JobSchema.model_validate(job))
            case PageReady(page=page):
                data = PageReadySchema(index=page.index, version=page.assets.version)
                yield ServerSentEvent(event=EventName.PAGE_READY, data=data)
            case ProjectChanged(project_id=project_id):
                yield ServerSentEvent(event=EventName.PROJECT_CHANGED, data=ProjectChangedSchema(project_id=project_id))


router = APIRouter(tags=['jobs'])
router.include_router(jobs)
router.include_router(project_events)
