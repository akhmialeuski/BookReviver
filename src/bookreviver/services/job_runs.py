"""The life of a job on a worker, which every job that reports its progress shares: start, advance and finish.

A job is stored as queued, and the worker that takes it moves it to running, reports a step of progress as it works, and
leaves it in a final state. Every write is guarded by the state of the job, because an account holder may cancel it at
any moment: the write that records a step finds the job cancelled and changes nothing, which is how a running job learns
to stop before its next step, and the write that concludes it leaves a cancelled job cancelled.

Each write is a ``change`` of its own, so a job reads as it is, and the tracker publishes ``JobChanged`` after the
transaction has committed. Every method opens its own transaction and is called only outside the work of another.
"""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.events import JobChanged
from bookreviver.domain.values import Progress

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.domain.entities import Job
    from bookreviver.domain.ids import JobId
    from bookreviver.ports.persistence import Repositories, UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher

    type HandOff = Callable[[Job, Job | None], Awaitable[None]]


class JobTracker:
    """Moves a job through its states for the worker that runs it."""

    def __init__(
        self, *, uow: UnitOfWork, publisher: EventPublisher, clock: Clock, hand_off: HandOff | None = None
    ) -> None:
        """Track jobs through the unit of work of the worker.

        :param uow: Unit of work of the job, whose ``change`` holds each write.
        :type uow: UnitOfWork
        :param publisher: Publisher of the job events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the start and the end of the job.
        :type clock: Clock
        :param hand_off: What passes the project on to the next job once a processing job has ended or found itself
                         cancelled, or None for a tracker of jobs that do not process versions.
        :type hand_off: HandOff | None
        """
        self._uow = uow
        self._publisher = publisher
        self._clock = clock
        self._hand_off = hand_off

    async def start(self, job_id: JobId) -> Job | None:
        """Move a queued job to running, or pick up a job found running, which is a delivery repeated after a crash.

        Opens its own ``change``, so it is called outside the work of any transaction.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The running job, or None when the job has finished already or left the queue since it was read, which
                  means another delivery or a cancellation took it.
        :rtype: Job | None
        :raises NotFoundError: If there is no such job.
        """

        def work(repos: Repositories) -> Job | None:
            """Read the job and move it to running when it is queued.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The running job, or None when the job has finished already or left the queue since it was read.
            :rtype: Job | None
            :raises NotFoundError: If there is no such job.
            """
            job = repos.jobs.get(job_id)
            if job.state.is_final:
                return None
            if job.state is JobState.QUEUED:
                started = evolve(job, state=JobState.RUNNING, started_at=self._clock.now())
                return repos.jobs.update_if_state(started, expected=(JobState.QUEUED,))
            return job

        if (job := await self._uow.change(work)) is None:
            return None
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))
        return job

    async def advance(self, job: Job, *, done: int, total: int) -> Job | None:
        """Record how far the job has come, which also tells whether it was cancelled.

        Opens its own ``change``, so it is called outside the work of any transaction.

        :param job: The running job as last stored.
        :type job: Job
        :param done: Steps completed.
        :type done: int
        :param total: Steps in all.
        :type total: int
        :returns: The job as stored, or None when it was cancelled, which stops it before its next step.
        :rtype: Job | None
        """
        saved = await self._uow.change(
            lambda repos: repos.jobs.update_if_state(
                evolve(job, progress=Progress(done=done, total=total)), expected=(JobState.RUNNING,)
            )
        )
        if saved is None:
            await self._pass_on(job, None)
            return None
        await self._publisher.publish(JobChanged(project_id=saved.project_id, job=saved))
        return saved

    async def _pass_on(self, job: Job, collection: Job | None) -> None:
        """Hand the project to the next job, when the job that stops was one that processes versions.

        :param job: The job that ended or found itself cancelled.
        :type job: Job
        :param collection: The collection stored with the end of the job, which is queued to a worker next, or None.
        :type collection: Job | None
        """
        if self._hand_off is not None and job.kind in JobKind.processing():
            await self._hand_off(job, collection)

    async def finish(
        self,
        job: Job,
        state: JobState,
        *,
        error: str = '',
        total: int | None = None,
        follow_up: Job | None = None,
    ) -> None:
        """Store the final state of a running job, unless it was cancelled meanwhile, and announce it.

        Opens its own ``change``, so it is called outside the work of any transaction. A follow-up job is stored in the
        same transaction as the final state, so the project is never seen free between the two, and a client that
        reacts to the end of the job finds the project busy with the follow-up. The follow-up is left out when another
        processing job of the project is queued or running already, which then is the one the project waits for. After
        the transaction the hand-off queues to a worker the follow-up, or else the job that waited for this one to end.

        :param job: The job as last stored by this run.
        :type job: Job
        :param state: The final state.
        :type state: JobState
        :param error: Why the job failed, or empty.
        :type error: str
        :param total: Number of steps the job went through, which becomes its complete progress, or None to keep the
                      progress it has.
        :type total: int | None
        :param follow_up: A queued job to store with the final state, or None for no job.
        :type follow_up: Job | None
        """
        progress = job.progress if total is None else Progress(done=total, total=total)
        final = evolve(job, state=state, error=error, progress=progress, finished_at=self._clock.now())

        def work(repos: Repositories) -> tuple[Job | None, Job | None]:
            """Store the final state of the job and, unless the project is busy, the follow-up.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The job as stored, or None when it was cancelled meanwhile, and the follow-up as stored, or None
                      when there is none or the project is busy with another processing job.
            :rtype: tuple[Job | None, Job | None]
            """
            stored = repos.jobs.update_if_state(final, expected=(JobState.RUNNING,))
            if follow_up is None:
                return stored, None
            active = repos.jobs.list_for_project(job.project_id, JobState.active())
            if any(other.kind in JobKind.processing() for other in active):
                return stored, None
            return stored, repos.jobs.add(follow_up)

        stored, queued = await self._uow.change(work)
        if stored is not None:
            await self._publisher.publish(JobChanged(project_id=stored.project_id, job=stored))
        await self._pass_on(job, queued)
