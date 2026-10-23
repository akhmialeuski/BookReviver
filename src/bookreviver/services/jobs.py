"""Background jobs as the account holder sees them: their state, cancellation and the live events of a book.

A job belongs to a book, so every use case works on an open book, whose owner the opening has checked, and a job of
another book is reported as missing rather than forbidden, so no account learns what another one has. The jobs
live in the memory of the API process, on the job board, so a job the process does not hold, such as one that ended
before the process started, is missing too.

Cancelling asks the board, which drops a queued job and stops a running one at the boundary of its current task, so no
worker has to be interrupted. A job that has ended cannot be cancelled.

The event stream is a subscription: it keeps every event published from the moment it is entered, even before the
first read, and replays nothing from before. A client therefore reads the job after its stream has opened, and again
after every reconnect, and the stream carries every change from there on.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.enums import JobKind
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.values import Slice

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from bookreviver.domain.entities import Job
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import JobId
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.runtime import EventStream, JobBoard
    from bookreviver.ports.storage import SourceStore
    from bookreviver.services.book import Book


class JobService:
    """Reads and cancels jobs, and opens the event stream of a book, for the account that owns the book."""

    def __init__(self, *, board: JobBoard, stream: EventStream, sources: SourceStore) -> None:
        """Build the service over the ports of one request.

        :param board: Board holding the jobs of every book.
        :type board: JobBoard
        :param stream: Source of the book's events.
        :type stream: EventStream
        :param sources: Store of the uploads, which holds the files of an import that is cancelled before it begins.
        :type sources: SourceStore
        """
        self._board = board
        self._stream = stream
        self._sources = sources

    async def get(self, book: Book, job_id: JobId) -> Job:
        """Return a job of the book.

        :param book: The open book.
        :type book: Book
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as the board holds it.
        :rtype: Job
        :raises NotFoundError: If the board does not hold the job, or the job belongs to another book.
        """
        job = self._board.get(job_id)
        if job.book_id != book.id:
            raise NotFoundError(job_id)
        return job

    async def list_for_book(self, book: Book, request: SliceRequest, *, active: bool) -> Slice[Job]:
        """Return a window of the jobs of the book, the newest first.

        :param book: The open book.
        :type book: Book
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the window, and the number of jobs the filter matches.
        :rtype: Slice[Job]
        """
        jobs = self._board.jobs(book.id, active=active)
        return Slice(items=jobs[request.offset : request.offset + request.limit], total=len(jobs))

    async def cancel(self, book: Book, job_id: JobId) -> Job:
        """Cancel a queued or running job; a running job stops at the boundary of its current task.

        An import that is cancelled before it began never runs, so nothing removes its upload but this use case. One
        that had begun removes its own upload when its run ends.

        :param book: The open book.
        :type book: Book
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state, with the time it finished.
        :rtype: Job
        :raises NotFoundError: If the board does not hold the job, or the job belongs to another book.
        :raises JobFinishedError: If the job has already finished, or finished while it was being cancelled.
        """
        await self.get(book, job_id)
        cancelled = await self._board.cancel(job_id)
        if cancelled.kind is JobKind.IMPORT_SOURCE and cancelled.started_at is None:
            await self._sources.discard(cancelled.book_id, cancelled.id)
        return cancelled

    async def events(self, book: Book) -> AbstractAsyncContextManager[AsyncIterator[DomainEvent]]:
        """Return a subscription to the book's events.

        The book was opened before the subscription exists, so a caller learns of a refusal before it subscribes or
        starts streaming, and the stream that stays open for as long as the browser tab does holds nothing of the
        database.

        :param book: The open book.
        :type book: Book
        :returns: Context manager that subscribes when entered, yields the iterator of every event of the book
                  published since, and unsubscribes when left.
        :rtype: AbstractAsyncContextManager[AsyncIterator[DomainEvent]]
        """
        return self._stream.subscribe(book.id)
