"""Background jobs as the account holder sees them: their state, cancellation and the live events of a project.

A job belongs to a project, so every use case checks that the acting account owns the project. A job or a project of
another account is reported as missing rather than forbidden, so no account learns what another one has.

Cancelling only records the state. A running job reads its own row before every step and stops once it finds itself
cancelled, so no worker has to be interrupted. The state is written by ``JobRepository.update_if_state`` rather than
by a read and a replacement, because a worker may finish the job between the two: the write then finds the job
finished and changes nothing, and the cancellation is refused like any cancellation of a finished job.

The event stream is a subscription: it keeps every event published from the moment it is entered, even before the
first read, and replays nothing from before. A client therefore reads the job after its stream has opened, and again
after every reconnect, and the stream carries every change from there on.
"""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import JobChanged
from bookreviver.domain.values import Slice

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from contextlib import AbstractAsyncContextManager

    from bookreviver.domain.entities import Actor, Job
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import JobId, ProjectId
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher, EventStream

JOB_FINISHED: str = 'The job has already finished.'


class JobCancellation:
    """Moves an active job to the cancelled state and announces it, for whoever may take a job off the project."""

    def __init__(self, *, uow: UnitOfWork, publisher: EventPublisher, clock: Clock) -> None:
        """Cancel jobs through the unit of work.

        :param uow: Unit of work, whose ``change`` block holds the cancellation.
        :type uow: UnitOfWork
        :param publisher: Publisher announcing a cancelled job to the project's subscribers.
        :type publisher: EventPublisher
        :param clock: Clock stamping when a cancelled job finished.
        :type clock: Clock
        """
        self._uow = uow
        self._publisher = publisher
        self._clock = clock

    async def cancel(self, job: Job) -> Job | None:
        """Cancel a job that is queued or running in a ``change`` block of its own, and announce it once it committed.

        Opens its own block, so it is called outside any block.

        :param job: The job as read.
        :type job: Job
        :returns: The job in the cancelled state, with the time it finished, or None when it had finished meanwhile, in
                  which case nothing is changed or announced.
        :rtype: Job | None
        """
        async with self._uow.change():
            cancelled = await self.mark_cancelled(job)
        if cancelled is not None:
            await self._publisher.publish(JobChanged(project_id=cancelled.project_id, job=cancelled))
        return cancelled

    async def mark_cancelled(self, job: Job) -> Job | None:
        """Write the cancelled state of a job that is queued or running, and announce nothing.

        Writes inside the block of its caller and never opens one, so the caller announces the job after its block
        committed.

        :param job: The job as read.
        :type job: Job
        :returns: The job in the cancelled state, with the time it finished, or None when it had finished meanwhile, in
                  which case nothing is changed.
        :rtype: Job | None
        """
        return await self._uow.jobs.update_if_state(
            evolve(job, state=JobState.CANCELLED, finished_at=self._clock.now()), expected=JobState.active()
        )


class JobService:
    """Reads and cancels jobs, and opens the event stream of a project, for the account that owns the project."""

    def __init__(self, *, uow: UnitOfWork, publisher: EventPublisher, stream: EventStream, clock: Clock) -> None:
        """Build the service over the ports of one request.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param publisher: Publisher announcing a cancelled job to the project's subscribers.
        :type publisher: EventPublisher
        :param stream: Source of the project's events.
        :type stream: EventStream
        :param clock: Clock stamping when a cancelled job finished.
        :type clock: Clock
        """
        self._uow = uow
        self._publisher = publisher
        self._stream = stream
        self._clock = clock

    async def get(self, actor: Actor, job_id: JobId) -> Job:
        """Return a job of one of the actor's projects.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as stored.
        :rtype: Job
        :raises NotFoundError: If the job does not exist or belongs to another account's project.
        """
        job = await self._uow.jobs.get(job_id)
        await self._check_owner(actor, job.project_id)
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
        await self._check_owner(actor, project_id)
        jobs = await self._uow.jobs.list_for_project(project_id, JobState.active() if active else set(JobState))
        return Slice(items=jobs[request.offset : request.offset + request.limit], total=len(jobs))

    async def cancel(self, actor: Actor, job_id: JobId) -> Job:
        """Cancel a queued or running job, and announce it; a running job stops before its next step.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state, with the time it finished.
        :rtype: Job
        :raises NotFoundError: If the job does not exist or belongs to another account's project.
        :raises ConflictError: If the job has already finished, or finished while it was being cancelled.
        """
        job = await self.get(actor, job_id)
        cancellation = JobCancellation(uow=self._uow, publisher=self._publisher, clock=self._clock)
        if (cancelled := await cancellation.cancel(job)) is None:
            raise ConflictError(JOB_FINISHED)
        return cancelled

    async def events(
        self, actor: Actor, project_id: ProjectId
    ) -> AbstractAsyncContextManager[AsyncIterator[DomainEvent]]:
        """Return a subscription to the project's events, for the owner only.

        The ownership check runs here, before the subscription exists, so a caller learns of a refusal before it
        subscribes or starts streaming.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: Context manager that subscribes when entered, yields the iterator of every event of the project
                  published since, and unsubscribes when left.
        :rtype: AbstractAsyncContextManager[AsyncIterator[DomainEvent]]
        :raises NotFoundError: If the project does not exist or belongs to another account.
        """
        await self._check_owner(actor, project_id)
        return self._stream.subscribe(project_id)

    async def _check_owner(self, actor: Actor, project_id: ProjectId) -> None:
        """Refuse a project the actor does not own as if it did not exist.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :raises NotFoundError: If the project does not exist or belongs to another account.
        """
        project = await self._uow.projects.get(project_id)
        if not project.is_owned_by(actor):
            raise NotFoundError(project_id)
