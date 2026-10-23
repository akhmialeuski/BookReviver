"""The job board in the memory of the API process: one queue per book, and one job of a book running at a time.

``JobLedger`` holds the jobs and decides every change of them, and ``InMemoryJobBoard`` serves the queues and tells the
browser. A book has a task of its own that takes the first queued job, hands it to the runner the application supplies
and goes on with the next once the runner has returned, so the books do not wait for each other and the jobs of one
book never overlap. A cancelled job that is running leaves its place to the next job only when its current task has
returned, because the control it reports through raises ``JobCancelledError`` at the next task boundary and not
before.

The ledger changes under a lock that is held for the length of one change and never across an ``await``. The event
loop is the only writer, but a read transaction runs in a worker thread and may ask for the jobs of a book to tell the
stages it sees running, so every read takes the same lock and copies what it returns.

The tasks of the board are plain ``asyncio`` tasks and not an ``anyio`` task group. A cancel scope belongs to the task
that entered it, and the container of the application that closes the board at shutdown does not promise to do it in
the task that opened it.
"""

import asyncio
import logging
import threading
from collections import deque
from contextlib import suppress
from operator import attrgetter
from typing import TYPE_CHECKING, Any, Self, override
from uuid import uuid4

from attrs import define, evolve, field

from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import JobCancelledError, JobFinishedError, NotFoundError
from bookreviver.domain.events import JobChanged
from bookreviver.domain.values import JobEnd, Progress
from bookreviver.ports.runtime import JobBoard, JobControl

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Collection, Coroutine, Sequence
    from types import TracebackType

    from bookreviver.domain.entities import Job
    from bookreviver.domain.ids import JobId, ProjectId
    from bookreviver.ports.runtime import Clock, EventPublisher

# What the application supplies to run a job: it does the tasks of the job, reports through the control after each,
# and returns how the job ended, or lets ``JobCancelledError`` pass when the control raised it
type JobRunner = Callable[[Job, JobControl], Awaitable[JobEnd]]

logger = logging.getLogger(__name__)


@define
class BookJobs:
    """The jobs the process holds for one book.

    :ivar jobs: Every job held, queued, running or ended, in the order they were put.
    :ivar queue: Identifiers of the queued jobs, in the order they run.
    :ivar serving: Whether a task of the board is serving the queue of the book.
    :ivar changes: How many times a job of the book was put, started, advanced, cancelled or ended in this process.
    """

    jobs: dict[JobId, Job] = field(factory=dict)
    queue: deque[JobId] = field(factory=deque)
    serving: bool = False
    changes: int = 0


class JobLedger:
    """The jobs of every book and the rules by which they change, safe to read from any thread."""

    def __init__(self, *, clock: Clock, kept_ended: int) -> None:
        """Start with no jobs.

        :param clock: Clock stamping the start and the end of a job.
        :type clock: Clock
        :param kept_ended: Most ended jobs held for each book, the latest ones.
        :type kept_ended: int
        """
        self._clock = clock
        self._kept_ended = kept_ended
        self._books: dict[ProjectId, BookJobs] = {}
        self._lock = threading.Lock()

    def add(self, job: Job) -> bool:
        """Put a queued job at the end of the queue of its book.

        :param job: The queued job.
        :type job: Job
        :returns: Whether no task served the queue of the book, so that the caller starts one.
        :rtype: bool
        """
        with self._lock:
            book = self._books.setdefault(job.project_id, BookJobs())
            book.jobs[job.id] = job
            book.changes += 1
            book.queue.append(job.id)
            starts = not book.serving
            book.serving = True
            return starts

    def start_next(self, project_id: ProjectId) -> Job | None:
        """Take the first queued job of a book and mark it as running.

        :param project_id: Book whose queue is served.
        :type project_id: ProjectId
        :returns: The running job, or None when the queue is empty, which also ends the service of the book.
        :rtype: Job | None
        """
        with self._lock:
            book = self._books[project_id]
            if not book.queue:
                book.serving = False
                return None
            job = evolve(book.jobs[book.queue.popleft()], state=JobState.RUNNING, started_at=self._clock.now())
            book.jobs[job.id] = job
            book.changes += 1
            return job

    def get(self, job_id: JobId) -> Job:
        """Return a job the ledger holds.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If the ledger holds no such job.
        """
        with self._lock:
            return self._locate(job_id)[1]

    def listing(self, project_ids: Collection[ProjectId], *, active: bool) -> list[Job]:
        """Return the jobs of some books, the newest first.

        :param project_ids: Books whose jobs are listed.
        :type project_ids: Collection[ProjectId]
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs, with the ones put later first among those queued at the same moment.
        :rtype: list[Job]
        """
        with self._lock:
            held = [
                job
                for project_id in project_ids
                if (book := self._books.get(project_id)) is not None
                for job in reversed(book.jobs.values())
            ]
        return sorted(
            (job for job in held if not active or not job.state.is_final), key=attrgetter('created_at'), reverse=True
        )

    def update_progress(self, job_id: JobId, change: Callable[[Progress], Progress]) -> Job | None:
        """Change the progress of a job that was not cancelled.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :param change: Function from the progress the job has to the one it gets.
        :type change: Callable[[Progress], Progress]
        :returns: The job with its new progress, or None when it was cancelled, which leaves its progress as it was.
        :rtype: Job | None
        :raises NotFoundError: If the ledger holds no such job.
        """
        with self._lock:
            book, job = self._locate(job_id)
            if job.state is JobState.CANCELLED:
                return None
            updated = evolve(job, progress=change(job.progress))
            book.jobs[job_id] = updated
            book.changes += 1
            return updated

    def cancel(self, job_id: JobId) -> Job:
        """Move a queued or running job to the cancelled state, and take a queued one out of its queue.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state, with the time it was cancelled.
        :rtype: Job
        :raises NotFoundError: If the ledger holds no such job.
        :raises JobFinishedError: If the job has ended already.
        """
        with self._lock:
            book, job = self._locate(job_id)
            if job.state.is_final:
                raise JobFinishedError
            if job.state is JobState.QUEUED:
                book.queue.remove(job_id)
            cancelled = evolve(job, state=JobState.CANCELLED, finished_at=self._clock.now())
            book.jobs[job_id] = cancelled
            book.changes += 1
            self._forget_oldest_ended(book)
            return cancelled

    def end(self, job_id: JobId, end: JobEnd | None) -> tuple[Job, bool]:
        """Give a running job its final state, unless it was cancelled while it ran.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :param end: How the runner ended the job, or None when it stopped at a cancellation.
        :type end: JobEnd | None
        :returns: The job as it is now, and whether this call changed it, which is False for a job that was cancelled
                  already and announced as cancelled by ``cancel``.
        :rtype: tuple[Job, bool]
        :raises NotFoundError: If the ledger holds no such job.
        """
        with self._lock:
            book, job = self._locate(job_id)
            if job.state.is_final:
                return job, False
            moment = self._clock.now()
            if end is None:
                final = evolve(job, state=JobState.CANCELLED, finished_at=moment)
            elif end.state is JobState.SUCCEEDED:
                complete = Progress(done=job.progress.total, total=job.progress.total)
                final = evolve(job, state=JobState.SUCCEEDED, progress=complete, finished_at=moment)
            else:
                final = evolve(job, state=JobState.FAILED, error=end.error, finished_at=moment)
            book.jobs[job_id] = final
            book.changes += 1
            self._forget_oldest_ended(book)
            return final, True

    def changes(self, project_id: ProjectId) -> int:
        """Return how many times a job of a book changed in this process.

        :param project_id: The book.
        :type project_id: ProjectId
        :returns: The number of changes, none for a book without a job.
        :rtype: int
        """
        with self._lock:
            book = self._books.get(project_id)
            return 0 if book is None else book.changes

    def _locate(self, job_id: JobId) -> tuple[BookJobs, Job]:
        """Find a job and the book that holds it, with the lock held.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The book and the job.
        :rtype: tuple[BookJobs, Job]
        :raises NotFoundError: If the ledger holds no such job.
        """
        for book in self._books.values():
            if (job := book.jobs.get(job_id)) is not None:
                return book, job
        raise NotFoundError(job_id)

    def _forget_oldest_ended(self, book: BookJobs) -> None:
        """Drop the oldest ended jobs of a book beyond the number the ledger keeps, with the lock held.

        :param book: The book whose jobs are trimmed.
        :type book: BookJobs
        """
        ended = [job_id for job_id, job in book.jobs.items() if job.state.is_final]
        for job_id in ended[: -self._kept_ended]:
            del book.jobs[job_id]


class BoardControl(JobControl):
    """What one running job reports through: its progress goes to the ledger and the browser, and a cancel stops it."""

    def __init__(
        self,
        *,
        job_id: JobId,
        ledger: JobLedger,
        publisher: EventPublisher,
        announce_later: Callable[[JobId], None],
    ) -> None:
        """Report the progress of one job.

        :param job_id: Identifier of the running job.
        :type job_id: JobId
        :param ledger: Ledger holding the job.
        :type ledger: JobLedger
        :param publisher: Publisher of the events the browser follows.
        :type publisher: EventPublisher
        :param announce_later: Announcement for a place that cannot await, which plans the publication of the job as it
                               is when the publication runs.
        :type announce_later: Callable[[JobId], None]
        """
        self._job_id = job_id
        self._ledger = ledger
        self._publisher = publisher
        self._announce_later = announce_later

    @override
    def plan(self, total: int) -> None:
        """Set the number of tasks of the job and announce it, unless the job was cancelled.

        :param total: Number of tasks of the job.
        :type total: int
        """
        if self._ledger.update_progress(self._job_id, lambda progress: Progress(done=progress.done, total=total)):
            self._announce_later(self._job_id)

    @override
    async def task_done(self) -> None:
        """Count one finished task, announce it, and stop the job when it was cancelled.

        :raises JobCancelledError: If the job was cancelled.
        """
        await self._advance(
            lambda progress: Progress(done=progress.done + 1, total=max(progress.total, progress.done + 1))
        )

    @override
    async def report(self, *, done: int, total: int) -> None:
        """Announce the progress inside a task, and stop the job when it was cancelled.

        :param done: Units of the task completed.
        :type done: int
        :param total: Units of the task in all.
        :type total: int
        :raises JobCancelledError: If the job was cancelled.
        """
        await self._advance(lambda _progress: Progress(done=done, total=total))

    async def _advance(self, change: Callable[[Progress], Progress]) -> None:
        """Change the progress of the job and announce it.

        :param change: Function from the progress the job has to the one it gets.
        :type change: Callable[[Progress], Progress]
        :raises JobCancelledError: If the job was cancelled, in which case nothing is changed or announced.
        """
        if (job := self._ledger.update_progress(self._job_id, change)) is None:
            raise JobCancelledError
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))


class InMemoryJobBoard(JobBoard):
    """Serves the jobs of each book one at a time, in the order they were put, and tells the browser how they go.

    The board is an async context manager. Leaving it cancels the tasks that serve the books, so a job that is running
    when the process stops is cut off in the middle of its task, which is why a task writes only what a restart may
    find half-done and run again.
    """

    def __init__(self, *, runner: JobRunner, publisher: EventPublisher, clock: Clock, kept_ended: int) -> None:
        """Build a board with no jobs.

        :param runner: What runs a job, supplied by the application.
        :type runner: JobRunner
        :param publisher: Publisher of the events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the jobs.
        :type clock: Clock
        :param kept_ended: Most ended jobs held for each book, the latest ones.
        :type kept_ended: int
        """
        self._runner = runner
        self._publisher = publisher
        self._ledger = JobLedger(clock=clock, kept_ended=kept_ended)
        self._tasks: set[asyncio.Task[None]] = set()
        self._process = uuid4().hex

    async def __aenter__(self) -> Self:
        """Open the board.

        :returns: The board.
        :rtype: Self
        """
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        """Cancel the tasks that serve the books, and wait until they have stopped.

        :param exc_type: Type of the exception that ends the block, or None.
        :type exc_type: type[BaseException] | None
        :param exc: The exception that ends the block, or None.
        :type exc: BaseException | None
        :param traceback: Traceback of that exception, or None.
        :type traceback: TracebackType | None
        """
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @override
    def put(self, job: Job) -> Job:
        """Queue the job at the end of its book, announce it, and start a task for the book when none serves it.

        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job.
        :rtype: Job
        """
        starts = self._ledger.add(job)
        self._announce_later(job.id)
        if starts:
            self._spawn(self._serve(job.project_id))
        return job

    @override
    def get(self, job_id: JobId) -> Job:
        """Return a job the board holds.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If the board holds no such job.
        """
        return self._ledger.get(job_id)

    @override
    def jobs(self, project_id: ProjectId, *, active: bool) -> Sequence[Job]:
        """Return the jobs of a book, the newest first.

        :param project_id: Project whose jobs are listed.
        :type project_id: ProjectId
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the book.
        :rtype: Sequence[Job]
        """
        return self._ledger.listing([project_id], active=active)

    @override
    def jobs_of(self, project_ids: Collection[ProjectId], *, active: bool) -> Sequence[Job]:
        """Return the jobs of several books, the newest first.

        :param project_ids: Projects whose jobs are listed.
        :type project_ids: Collection[ProjectId]
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the books.
        :rtype: Sequence[Job]
        """
        return self._ledger.listing(project_ids, active=active)

    @override
    def revision(self, project_id: ProjectId) -> str:
        """Return the identifier of this process and the number of changes of the jobs of the book in it.

        :param project_id: The book.
        :type project_id: ProjectId
        :returns: The mark of the jobs of the book.
        :rtype: str
        """
        return f'{self._process}.{self._ledger.changes(project_id)}'

    @override
    async def cancel(self, job_id: JobId) -> Job:
        """Cancel a job and announce it; a running job stops at its next task boundary.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state.
        :rtype: Job
        :raises NotFoundError: If the board holds no such job.
        :raises JobFinishedError: If the job has ended already.
        """
        cancelled = self._ledger.cancel(job_id)
        await self._publisher.publish(JobChanged(project_id=cancelled.project_id, job=cancelled))
        return cancelled

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        """Run work in a task of the board, which is kept until it is done.

        :param work: The coroutine to run.
        :type work: Coroutine[Any, Any, None]
        """
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _announce_later(self, job_id: JobId) -> None:
        """Plan the publication of a job from a place that cannot await.

        The job is read when the publication runs and not when it is planned, so an announcement that waits for its
        turn never tells the browser a state the job has left already, whatever was published meanwhile.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        """
        self._spawn(self._announce(job_id))

    async def _announce(self, job_id: JobId) -> None:
        """Publish a job as it is now, unless the board has let it go meanwhile.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        """
        with suppress(NotFoundError):
            job = self._ledger.get(job_id)
            await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))

    async def _serve(self, project_id: ProjectId) -> None:
        """Run the queue of a book, one job after another, until it is empty.

        :param project_id: Book whose queue is served.
        :type project_id: ProjectId
        """
        while (job := self._ledger.start_next(project_id)) is not None:
            await self._publisher.publish(JobChanged(project_id=project_id, job=job))
            control = BoardControl(
                job_id=job.id, ledger=self._ledger, publisher=self._publisher, announce_later=self._announce_later
            )
            end: JobEnd | None
            try:
                end = await self._runner(job, control)
            except JobCancelledError:
                end = None
            except Exception:
                logger.exception('The %s job %s stopped', job.kind, job.id)
                end = JobEnd.unexpected()
            final, changed = self._ledger.end(job.id, end)
            if changed:
                await self._publisher.publish(JobChanged(project_id=project_id, job=final))
