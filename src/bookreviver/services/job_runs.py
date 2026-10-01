"""The life of a job on a worker, which every job that reports its progress shares: start, advance and finish.

A job is stored as queued, and the worker that takes it moves it to running, reports a step of progress as it works, and
leaves it in a final state. Every write is guarded by the state of the job, because an account holder may cancel it at
any moment: the write that records a step finds the job cancelled and changes nothing, which is how a running job learns
to stop before its next step, and the write that concludes it leaves a cancelled job cancelled.

The tracker commits what it writes, so a job reads as it is, and publishes ``JobChanged`` after each commit.
"""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import JobState
from bookreviver.domain.events import JobChanged
from bookreviver.domain.values import Progress

if TYPE_CHECKING:
    from bookreviver.domain.entities import Job
    from bookreviver.domain.ids import JobId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher


class JobTracker:
    """Moves a job through its states for the worker that runs it."""

    def __init__(self, *, uow: UnitOfWork, publisher: EventPublisher, clock: Clock) -> None:
        """Track jobs through the unit of work of the worker.

        :param uow: Unit of work of the job, committed after each write.
        :type uow: UnitOfWork
        :param publisher: Publisher of the job events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the start and the end of the job.
        :type clock: Clock
        """
        self._uow = uow
        self._publisher = publisher
        self._clock = clock

    async def start(self, job_id: JobId) -> Job | None:
        """Move a queued job to running, or pick up a job found running, which is a delivery repeated after a crash.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The running job, or None when the job has finished already or left the queue since it was read, which
                  means another delivery or a cancellation took it.
        :rtype: Job | None
        :raises NotFoundError: If there is no such job.
        """
        job = await self._uow.jobs.get(job_id)
        if job.state.is_final:
            return None
        if job.state is JobState.QUEUED:
            started = evolve(job, state=JobState.RUNNING, started_at=self._clock.now())
            if (running := await self._uow.jobs.update_if_state(started, expected=(JobState.QUEUED,))) is None:
                await self._uow.rollback()
                return None
            job = running
            await self._uow.commit()
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))
        return job

    async def advance(self, job: Job, *, done: int, total: int) -> Job | None:
        """Record how far the job has come, which also tells whether it was cancelled.

        :param job: The running job as last stored.
        :type job: Job
        :param done: Steps completed.
        :type done: int
        :param total: Steps in all.
        :type total: int
        :returns: The job as stored, or None when it was cancelled, which stops it before its next step.
        :rtype: Job | None
        """
        saved = await self._uow.jobs.update_if_state(
            evolve(job, progress=Progress(done=done, total=total)), expected=(JobState.RUNNING,)
        )
        if saved is None:
            await self._uow.rollback()
            return None
        await self._uow.commit()
        await self._publisher.publish(JobChanged(project_id=saved.project_id, job=saved))
        return saved

    async def finish(self, job: Job, state: JobState, *, error: str = '', total: int | None = None) -> None:
        """Store the final state of a running job, unless it was cancelled meanwhile, and announce it.

        Whatever the worker left uncommitted is discarded first.

        :param job: The job as last stored by this run.
        :type job: Job
        :param state: The final state.
        :type state: JobState
        :param error: Why the job failed, or empty.
        :type error: str
        :param total: Number of steps the job went through, which becomes its complete progress, or None to keep the
                      progress it has.
        :type total: int | None
        """
        await self._uow.rollback()
        progress = job.progress if total is None else Progress(done=total, total=total)
        final = evolve(job, state=state, error=error, progress=progress, finished_at=self._clock.now())
        stored = await self._uow.jobs.update_if_state(final, expected=(JobState.RUNNING,))
        await self._uow.commit()
        if stored is not None:
            await self._publisher.publish(JobChanged(project_id=stored.project_id, job=stored))
