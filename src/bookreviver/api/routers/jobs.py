"""Background job state, cancellation and the event stream of a project.

The job routes live under ``/jobs`` and the event stream under ``/projects/{project_id}/events``, so the module holds
one router per prefix and joins them under the ``jobs`` tag. The jobs live in the memory of the API process, so a job
the process does not hold, such as one that ended before the process started, is answered with 404.

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
from fastapi import APIRouter, Depends, Header, Path
from fastapi.sse import EventSourceResponse, ServerSentEvent
from fastapi_pagination import Page

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import Pager
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.jobs import (
    BookChangedSchema,
    BookVersionSchema,
    EventName,
    JobQuery,
    JobSchema,
    PageVersionReadySchema,
)
from bookreviver.domain.entities import Job
from bookreviver.domain.events import BookChanged, DomainEvent, JobChanged, PageVersionReady
from bookreviver.domain.ids import JobId, ProjectId
from bookreviver.services.freshness import BookFreshness
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

    \N{FORM FEED}
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
    """Cancel a queued or running job and return it; a running job stops at the boundary of its current task.

    \N{FORM FEED}
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


@project_events.get('/{project_id}/jobs')
async def list_project_jobs(
    project_id: ProjectIdPath,
    query: Annotated[JobQuery, Depends()],
    actor: ActorDep,
    service: JobServiceDep,
) -> Page[JobSchema]:
    """List the jobs of a project, the newest first, or only the ones queued or running with ``active=true``.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param query: Page number and size, and whether to list running jobs alone.
    :type query: JobQuery
    :param actor: The signed-in account.
    :type actor: Actor
    :param service: Job service of the request.
    :type service: JobService
    :returns: One page of the project's jobs.
    :rtype: Page[JobSchema]
    """
    pager = Pager[Job, JobSchema](query, JobSchema.model_validate)
    return pager.page(await service.list_for_project(actor, project_id, pager.request, active=query.active))


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
    project_id: ProjectIdPath,
    events: Annotated[ProjectEvents, Depends(open_event_stream)],
    actor: ActorDep,
    freshness: FromDishka[BookFreshness],
    last_event_id: Annotated[str | None, Header()] = None,
) -> AsyncIterator[ServerSentEvent]:
    """Stream the project's events as they happen: the committed changes of the book, its jobs and its ready versions.

    A committed change of the book is a ``book-changed`` event whose id is the new version of the book. A browser that
    connects is first sent ``book-version`` with the version now, so it knows which version it holds. The stream keeps
    no changes, so a browser that comes back with the id of another version is sent ``book-reset`` instead, and one that
    comes back with the id of the version now is sent nothing.

    \N{FORM FEED}
    :param project_id: Identifier of the project.
    :type project_id: ProjectId
    :param events: The project's events, subscribed to by ``open_event_stream`` before the version is read.
    :type events: ProjectEvents
    :param actor: The signed-in account.
    :type actor: Actor
    :param freshness: Freshness of the reads of the books of the request, which knows the version of the book.
    :type freshness: BookFreshness
    :param last_event_id: The id of the last event the browser received before it reconnected, or None.
    :type last_event_id: str | None
    :returns: Iterator yielding one server-sent event per domain event the browser listens to.
    :rtype: AsyncIterator[ServerSentEvent]
    """
    current = await freshness.version(actor, project_id)
    if last_event_id is None:
        named = BookVersionSchema(project_id=project_id, version=current)
        yield ServerSentEvent(event=EventName.BOOK_VERSION, id=str(current), data=named)
    elif last_event_id != str(current):
        reset = BookVersionSchema(project_id=project_id, version=current)
        yield ServerSentEvent(event=EventName.BOOK_RESET, id=str(current), data=reset)
    async for event in events:
        match event:
            case BookChanged(project_id=changed_id, version=book_version, resources=resources):
                yield ServerSentEvent(
                    event=EventName.BOOK_CHANGED,
                    id=str(book_version),
                    data=BookChangedSchema(project_id=changed_id, version=book_version, resources=sorted(resources)),
                )
            case JobChanged(job=job):
                yield ServerSentEvent(event=EventName.JOB_CHANGED, data=JobSchema.model_validate(job))
            case PageVersionReady(project_id=project_id, version=version):
                yield ServerSentEvent(
                    event=EventName.PAGE_VERSION_READY,
                    data=PageVersionReadySchema(project_id=project_id, page_id=version.page_id, version_id=version.id),
                )


router = APIRouter(tags=['jobs'])
router.include_router(jobs)
router.include_router(project_events)
