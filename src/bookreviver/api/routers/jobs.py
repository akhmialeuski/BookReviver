"""Background job state, cancellation and the event stream of a book.

The jobs of a book live under ``/books/{book_id}/jobs`` and its event stream under ``/books/{book_id}/events``, and
every route opens the book with ``open_book``. The jobs live in the memory of the API process, so a job the process does
not hold, such as one that ended before the process started, is answered with 404, and so is a job of another book.

The subscription is held by a dependency with ``yield`` rather than inside the endpoint. FastAPI sends the response
headers as soon as a streaming endpoint starts, so a refusal raised inside it could no longer become a problem
response. Raised in the dependency, a book of another account is still answered with 404 before any event is sent.
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

from bookreviver.api.access import open_book
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
from bookreviver.domain.ids import JobId
from bookreviver.services.book import Book
from bookreviver.services.freshness import BookFreshness
from bookreviver.services.jobs import JobService

# Module-level aliases: FastAPI and dishka read route annotations at runtime
JobIdPath = Annotated[JobId, Path(description='Identifier of the job')]
JobServiceDep = FromDishka[JobService]
BookEvents = AsyncIterator[DomainEvent]

router = APIRouter(prefix='/books', tags=['jobs'], route_class=DishkaRoute)


@router.get('/{book_id}/jobs/{job_id}')
async def read_job(book: Annotated[Book, Depends(open_book)], job_id: JobIdPath, service: JobServiceDep) -> JobSchema:
    """Return the state and progress of a job.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param job_id: Identifier of the job.
    :type job_id: JobId
    :param service: Job service of the request.
    :type service: JobService
    :returns: The job.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await service.get(book, job_id))


@router.delete('/{book_id}/jobs/{job_id}')
async def cancel_job(book: Annotated[Book, Depends(open_book)], job_id: JobIdPath, service: JobServiceDep) -> JobSchema:
    """Cancel a queued or running job and return it; a running job stops at the boundary of its current task.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param job_id: Identifier of the job.
    :type job_id: JobId
    :param service: Job service of the request.
    :type service: JobService
    :returns: The job in the cancelled state.
    :rtype: JobSchema
    """
    return JobSchema.model_validate(await service.cancel(book, job_id))


@router.get('/{book_id}/jobs')
async def list_book_jobs(
    book: Annotated[Book, Depends(open_book)],
    query: Annotated[JobQuery, Depends()],
    service: JobServiceDep,
) -> Page[JobSchema]:
    """List the jobs of a book, the newest first, or only the ones queued or running with ``active=true``.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param query: Page number and size, and whether to list running jobs alone.
    :type query: JobQuery
    :param service: Job service of the request.
    :type service: JobService
    :returns: One page of the book's jobs.
    :rtype: Page[JobSchema]
    """
    pager = Pager[Job, JobSchema](query, JobSchema.model_validate)
    return pager.page(await service.list_for_book(book, pager.request, active=query.active))


@inject
async def open_event_stream(
    book: Annotated[Book, Depends(open_book)], service: JobServiceDep
) -> AsyncIterator[BookEvents]:
    """Subscribe to the book's events for as long as the response streams, or refuse before it starts.

    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param service: Job service of the request.
    :type service: JobService
    :returns: Iterator yielding once the book's events published since subscribing, and unsubscribing afterwards.
    :rtype: AsyncIterator[BookEvents]
    """
    subscription = AsyncExitStack()
    events = await subscription.enter_async_context(await service.events(book))
    # try/finally rather than `async with` around the yield: FastAPI drives this generator (ASYNC119)
    try:
        yield events
    finally:
        await subscription.aclose()


@router.get('/{book_id}/events', response_class=EventSourceResponse, name=RouteName.BOOK_EVENTS)
async def stream_book_events(
    book: Annotated[Book, Depends(open_book)],
    events: Annotated[BookEvents, Depends(open_event_stream)],
    freshness: FromDishka[BookFreshness],
    last_event_id: Annotated[str | None, Header()] = None,
) -> AsyncIterator[ServerSentEvent]:
    """Stream the book's events as they happen: the committed changes of the book, its jobs and its ready versions.

    A committed change of the book is a ``book-changed`` event whose id is the new version of the book. A browser that
    connects is first sent ``book-version`` with the version now, so it knows which version it holds. The stream keeps
    no changes, so a browser that comes back with the id of another version is sent ``book-reset`` instead, and one that
    comes back with the id of the version now is sent nothing.

    \N{FORM FEED}
    :param book: The book the path names, open for the signed-in account.
    :type book: Book
    :param events: The book's events, subscribed to by ``open_event_stream`` before the version is read.
    :type events: BookEvents
    :param freshness: Freshness of the reads of the books of the request, which knows the version of the book.
    :type freshness: BookFreshness
    :param last_event_id: The id of the last event the browser received before it reconnected, or None.
    :type last_event_id: str | None
    :returns: Iterator yielding one server-sent event per domain event the browser listens to.
    :rtype: AsyncIterator[ServerSentEvent]
    """
    current = await freshness.version(book)
    if last_event_id is None:
        named = BookVersionSchema(book_id=book.id, version=current)
        yield ServerSentEvent(event=EventName.BOOK_VERSION, id=str(current), data=named)
    elif last_event_id != str(current):
        reset = BookVersionSchema(book_id=book.id, version=current)
        yield ServerSentEvent(event=EventName.BOOK_RESET, id=str(current), data=reset)
    async for event in events:
        match event:
            case BookChanged(book_id=changed_id, version=book_version, resources=resources):
                yield ServerSentEvent(
                    event=EventName.BOOK_CHANGED,
                    id=str(book_version),
                    data=BookChangedSchema(book_id=changed_id, version=book_version, resources=sorted(resources)),
                )
            case JobChanged(job=job):
                yield ServerSentEvent(event=EventName.JOB_CHANGED, data=JobSchema.model_validate(job))
            case PageVersionReady(book_id=ready_id, version=version):
                yield ServerSentEvent(
                    event=EventName.PAGE_VERSION_READY,
                    data=PageVersionReadySchema(book_id=ready_id, page_id=version.page_id, version_id=version.id),
                )
