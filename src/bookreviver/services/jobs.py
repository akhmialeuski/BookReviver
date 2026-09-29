"""Background jobs as the account holder sees them: their state, cancellation and the live events of a project.

A job belongs to a project, so every use case checks that the acting account owns the project. A job or a project of
another account is reported as missing rather than forbidden, so no account learns what another one has.

Cancelling only records the state. A running job reads its own row before every step and stops once it finds itself
cancelled, so no worker has to be interrupted.

The event stream delivers what is published after it opens and replays nothing: a client that reconnects reads the
job again and refreshes what it shows.
"""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import JobChanged

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.domain.entities import Actor, Job
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import JobId, ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher, EventStream

JOB_FINISHED: str = 'The job has already finished.'


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

    async def cancel(self, actor: Actor, job_id: JobId) -> Job:
        """Cancel a queued or running job, and announce it; a running job stops before its next step.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state, with the time it finished.
        :rtype: Job
        :raises NotFoundError: If the job does not exist or belongs to another account's project.
        :raises ConflictError: If the job has already finished.
        """
        job = await self.get(actor, job_id)
        if job.state.is_final:
            raise ConflictError(JOB_FINISHED)
        cancelled = await self._uow.jobs.update(evolve(job, state=JobState.CANCELLED, finished_at=self._clock.now()))
        await self._uow.commit()
        await self._publisher.publish(JobChanged(project_id=cancelled.project_id, job=cancelled))
        return cancelled

    async def events(self, actor: Actor, project_id: ProjectId) -> AsyncIterator[DomainEvent]:
        """Return the stream of the project's events published from now on.

        The ownership check runs here rather than inside the stream, so a caller learns of a refusal before it starts
        streaming.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: Iterator yielding each event of the project, until the consumer stops.
        :rtype: AsyncIterator[DomainEvent]
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
