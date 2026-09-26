"""Background jobs as the account holder sees them: their state, cancellation and the live events of a project."""

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
        self._uow = uow
        self._publisher = publisher
        self._stream = stream
        self._clock = clock

    async def get(self, actor: Actor, job_id: JobId) -> Job:
        """Return a job of one of the actor's projects.

        :raises NotFoundError: If the job does not exist or belongs to another account's project.
        """
        job = await self._uow.jobs.get(job_id)
        await self._check_owner(actor, job.project_id)
        return job

    async def cancel(self, actor: Actor, job_id: JobId) -> Job:
        """Cancel a queued or running job; a running one stops before its next step.

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
        """Return the stream of the project's events, published from now on.

        :raises NotFoundError: If the project does not exist or belongs to another account.
        """
        await self._check_owner(actor, project_id)
        return self._stream.subscribe(project_id)

    async def _check_owner(self, actor: Actor, project_id: ProjectId) -> None:
        """Raise ``NotFoundError`` unless the actor owns the project, so other accounts' projects stay invisible."""
        project = await self._uow.projects.get(project_id)
        if not project.is_owned_by(actor):
            raise NotFoundError(project_id)
