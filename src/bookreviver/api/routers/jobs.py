"""Background job state, cancellation and the event stream of a project.

The job routes live under ``/jobs`` and the event stream under ``/projects/{project_id}/events``, so the module holds
one router per prefix and joins them under the ``jobs`` tag.

The subscription is held by a dependency with ``yield`` rather than inside the endpoint. FastAPI sends the response
headers as soon as a streaming endpoint starts, so a refusal raised inside it could no longer become a problem
response. Raised in the dependency, a project of another account is still answered with 404 before any event is sent.
The dependency subscribes before the endpoint runs, so events published while the headers go out are kept, and FastAPI
leaves it only after the stream ends, which unsubscribes at once. Domain events without a browser event are skipped.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka, inject
from fastapi import APIRouter, Depends, Path
from fastapi.sse import EventSourceResponse, ServerSentEvent

from bookreviver.api.auth import ActorDep
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.jobs import EventName, JobSchema, ProjectChangedSchema
from bookreviver.domain.events import DomainEvent, JobChanged, ProjectChanged
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
    """Return the state and progress of a job.

    :param job_id: Identifier of the job.
    :type job_id: JobId
    :param actor: The signed-in account.
    :type actor: Actor
    :param service: Job service of the request.
    :type service: JobService
    :returns: The job.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await service.get(actor, job_id))


@jobs.delete('/{job_id}')
async def cancel_job(job_id: JobIdPath, actor: ActorDep, service: JobServiceDep) -> JobSchema:
    """Cancel a queued or running job and return it; a running job stops before its next step.

    :param job_id: Identifier of the job.
    :type job_id: JobId
    :param actor: The signed-in account.
    :type actor: Actor
    :param service: Job service of the request.
    :type service: JobService
    :returns: The job in the cancelled state.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await service.cancel(actor, job_id))


@inject
async def open_event_stream(
    project_id: ProjectIdPath, actor: ActorDep, service: JobServiceDep
) -> AsyncIterator[ProjectEvents]:
    """Subscribe to the project's events for as long as the response streams, or refuse before it starts.

    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param actor: The signed-in account.
    :type actor: Actor
    :param service: Job service of the request.
    :type service: JobService
    :returns: Iterator yielding once the project's events published since subscribing, and unsubscribing afterwards.
    :rtype: AsyncIterator[ProjectEvents]
    """
    subscription = AsyncExitStack()
    events = await subscription.enter_async_context(await service.events(actor, project_id))
    # try/finally rather than `async with` around the yield: FastAPI drives this generator (ASYNC119)
    try:
        yield events
    finally:
        await subscription.aclose()


@project_events.get('/{project_id}/events', response_class=EventSourceResponse, name=RouteName.PROJECT_EVENTS)
async def stream_project_events(
    events: Annotated[ProjectEvents, Depends(open_event_stream)],
) -> AsyncIterator[ServerSentEvent]:
    """Stream the project's events as they happen: job changes and description changes.

    :param events: The project's events, subscribed to by ``open_event_stream``.
    :type events: ProjectEvents
    :returns: Iterator yielding one server-sent event per domain event the browser listens to.
    :rtype: AsyncIterator[ServerSentEvent]
    """
    async for event in events:
        match event:
            case JobChanged(job=job):
                yield ServerSentEvent(event=EventName.JOB_CHANGED, data=JobSchema.model_validate(job))
            case ProjectChanged(project_id=project_id):
                yield ServerSentEvent(event=EventName.PROJECT_CHANGED, data=ProjectChangedSchema(project_id=project_id))


router = APIRouter(tags=['jobs'])
router.include_router(jobs)
router.include_router(project_events)
