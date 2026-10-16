"""Background jobs as the account holder sees them: their state, cancellation and the live events of a project.

A job belongs to a project, so every use case checks that the acting account owns the project. A job or a project of
another account is reported as missing rather than forbidden, so no account learns what another one has. The jobs
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
from bookreviver.domain.values import Slice
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from bookreviver.domain.entities import Actor, Job
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import JobId, ProjectId
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import EventStream, JobBoard
    from bookreviver.ports.storage import SourceStore


class JobService:
    """Reads and cancels jobs, and opens the event stream of a project, for the account that owns the project."""

    def __init__(self, *, uow: UnitOfWork, board: JobBoard, stream: EventStream, sources: SourceStore) -> None:
        """Build the service over the ports of one request.

        :param uow: Unit of work of the current request, which checks who owns a project.
        :type uow: UnitOfWork
        :param board: Board holding the jobs of every book.
        :type board: JobBoard
        :param stream: Source of the project's events.
        :type stream: EventStream
        :param sources: Store of the uploads, which holds the files of an import that is cancelled before it begins.
        :type sources: SourceStore
        """
        self._uow = uow
        self._board = board
        self._stream = stream
        self._sources = sources

    async def get(self, actor: Actor, job_id: JobId) -> Job:
        """Return a job of one of the actor's projects.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as the board holds it.
        :rtype: Job
        :raises NotFoundError: If the board does not hold the job, or the job belongs to another account's project.
        """
        job = self._board.get(job_id)
        await self._uow.read(lambda repos: owned_project(repos.projects, actor, job.project_id))
        return job

    async def list_for_project(
        self, actor: Actor, project_id: ProjectId, request: SliceRequest, *, active: bool
    ) -> Slice[Job]:
        """Return a window of the jobs of one of the actor's projects, the newest first.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the window, and the number of jobs the filter matches.
        :rtype: Slice[Job]
        :raises NotFoundError: If the project does not exist or belongs to another account.
        """
        await self._uow.read(lambda repos: owned_project(repos.projects, actor, project_id))
        jobs = self._board.jobs(project_id, active=active)
        return Slice(items=jobs[request.offset : request.offset + request.limit], total=len(jobs))

    async def cancel(self, actor: Actor, job_id: JobId) -> Job:
        """Cancel a queued or running job; a running job stops at the boundary of its current task.

        An import that is cancelled before it began never runs, so nothing removes its upload but this use case. One
        that had begun removes its own upload when its run ends.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state, with the time it finished.
        :rtype: Job
        :raises NotFoundError: If the board does not hold the job, or the job belongs to another account's project.
        :raises JobFinishedError: If the job has already finished, or finished while it was being cancelled.
        """
        await self.get(actor, job_id)
        cancelled = await self._board.cancel(job_id)
        if cancelled.kind is JobKind.IMPORT_SOURCE and cancelled.started_at is None:
            await self._sources.discard(cancelled.project_id, cancelled.id)
        return cancelled

    async def events(
        self, actor: Actor, project_id: ProjectId
    ) -> AbstractAsyncContextManager[AsyncIterator[DomainEvent]]:
        """Return a subscription to the project's events, for the owner only.

        The ownership check runs here, before the subscription exists, so a caller learns of a refusal before it
        subscribes or starts streaming. The check is a read of its own, which holds no connection once it has returned,
        so the stream that stays open for as long as the browser tab does holds nothing of the database.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: Context manager that subscribes when entered, yields the iterator of every event of the project
                  published since, and unsubscribes when left.
        :rtype: AbstractAsyncContextManager[AsyncIterator[DomainEvent]]
        :raises NotFoundError: If the project does not exist or belongs to another account.
        """
        await self._uow.read(lambda repos: owned_project(repos.projects, actor, project_id))
        return self._stream.subscribe(project_id)
