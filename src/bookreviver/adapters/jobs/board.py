"""The job board in the memory of the API process: one queue per book, and one job of a book running at a time.

``InMemoryBookJobs`` is the queue of one book: it holds the jobs of the book, decides every change of them and tells
the browser through the event channel of the book. ``InMemoryJobBoard`` keeps the one queue of each directory name, and
holds what the queues share: the runner the application supplies, the tasks they run in and the mark of the process. A
book has a task of its own that takes the first queued job, hands it to the runner and goes on with the next once the
runner has returned, so the books do not wait for each other and the jobs of one book never overlap. A cancelled job
that is running leaves its place to the next job only when its current task has returned, because the control it
reports through raises ``JobCancelledError`` at the next task boundary and not before.

A queue changes under a lock of its own that is held for the length of one change and never across an ``await``. The
event loop is the only writer, but a read transaction runs in a worker thread and may ask for the jobs of a book to tell
the stages it sees running, so every read takes the same lock and copies what it returns.

The tasks of the board are plain ``asyncio`` tasks and not an ``anyio`` task group. A cancel scope belongs to the task
that entered it, and the container of the application that closes the board at shutdown does not promise to do it in
the task that opened it.
"""

import asyncio
import logging
import threading
from collections import deque
from contextlib import suppress
from datetime import UTC, datetime
from operator import attrgetter
from typing import TYPE_CHECKING, Any, Self, override
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.errors import JobCancelledError, JobFinishedError, NotFoundError
from bookreviver.domain.events import JobChanged
from bookreviver.domain.jobs import JobEnd, JobState, Progress
from bookreviver.ports.runtime import BookEvents, BookJobs, JobControl

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Sequence
    from types import TracebackType

    from bookreviver.domain.ids import BookDirectoryName, JobId
    from bookreviver.domain.jobs import Job

# What the application supplies to run a job: it takes the name of the directory of the book whose queue the job was
# taken from, does the tasks of the job, reports through the control after each, and returns how the job ended, or lets
# ``JobCancelledError`` pass when the control raised it
type JobRunner = Callable[[BookDirectoryName, Job, JobControl], Awaitable[JobEnd]]

logger = logging.getLogger(__name__)


class BoardControl(JobControl):
    """What one running job reports through: its progress goes to its queue and the browser, and a cancel stops it."""

    def __init__(self, queue: InMemoryBookJobs, job_id: JobId) -> None:
        """Report the progress of one job.

        :param queue: Queue of the book of the job, which holds the job and announces it.
        :type queue: InMemoryBookJobs
        :param job_id: Identifier of the running job.
        :type job_id: JobId
        """
        self._queue = queue
        self._job_id = job_id

    @override
    def plan(self, total: int) -> None:
        """Set the number of tasks of the job and announce it, unless the job was cancelled.

        :param total: Number of tasks of the job.
        :type total: int
        """
        if self._queue.update_progress(self._job_id, lambda progress: Progress(done=progress.done, total=total)):
            self._queue.announce_later(self._job_id)

    @override
    async def task_done(self) -> None:
        """Count one finished task, announce it, and stop the job when it was cancelled.

        :raises JobCancelledError: If the job was cancelled.
        """
        await self._queue.advance(
            self._job_id,
            lambda progress: Progress(done=progress.done + 1, total=max(progress.total, progress.done + 1)),
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
        await self._queue.advance(self._job_id, lambda _progress: Progress(done=done, total=total))


class InMemoryBookJobs(BookJobs):
    """The queue of the jobs of one book: the jobs it holds, the rules by which they change, and the service of them.

    The jobs of the queue are read from any thread and changed under its lock.

    :ivar dir_name: Name of the directory of the book.
    """

    def __init__(self, board: InMemoryJobBoard, dir_name: BookDirectoryName) -> None:
        """Start the queue of a book with no jobs, and open the event channel of the book.

        :param board: The board that holds the queue, which supplies the runner, the tasks and the mark of the process.
        :type board: InMemoryJobBoard
        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        """
        self.dir_name = dir_name
        self._board = board
        self._events = BookEvents.open(dir_name)
        self._lock = threading.Lock()
        # Every job held, queued, running or ended, in the order they were put
        self._jobs: dict[JobId, Job] = {}
        # Identifiers of the queued jobs, in the order they run
        self._queue: deque[JobId] = deque()
        # Whether a task of the board is serving the queue
        self._serving = False
        # How many times a job of the book was put, started, advanced, cancelled or ended in this process
        self._changes = 0

    @override
    def put(self, job: Job) -> Job:
        """Queue the job at the end of the queue, announce it, and start a task when none serves the book.

        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job.
        :rtype: Job
        """
        with self._lock:
            self._jobs[job.id] = job
            self._changes += 1
            self._queue.append(job.id)
            starts = not self._serving
            self._serving = True
        self._board.after_put(self, job, starts=starts)
        return job

    @override
    def get(self, job_id: JobId) -> Job:
        """Return a job of the book the queue holds.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If the queue holds no such job.
        """
        with self._lock:
            return self._locate(job_id)

    @override
    def jobs(self) -> Sequence[Job]:
        """Return the jobs of the book, the newest first.

        :returns: The jobs of the book.
        :rtype: Sequence[Job]
        """
        return self._listing(active=False)

    @override
    def active(self) -> Sequence[Job]:
        """Return the jobs of the book that are queued or running, the newest first.

        :returns: The active jobs of the book.
        :rtype: Sequence[Job]
        """
        return self._listing(active=True)

    @override
    def revision(self) -> str:
        """Return the identifier of the process and the number of changes of the jobs of the book in it.

        :returns: The mark of the jobs of the book.
        :rtype: str
        """
        with self._lock:
            return f'{self._board.process}.{self._changes}'

    @override
    async def cancel(self, job_id: JobId) -> Job:
        """Cancel a job of the book and announce it; a running job stops at its next task boundary.

        A queued job is moved to the cancelled state and taken out of the queue.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state, with the time it was cancelled.
        :rtype: Job
        :raises NotFoundError: If the queue holds no such job.
        :raises JobFinishedError: If the job has ended already.
        """
        with self._lock:
            job = self._locate(job_id)
            if job.state.is_final:
                raise JobFinishedError
            if job.state is JobState.QUEUED:
                self._queue.remove(job_id)
            cancelled = evolve(job, state=JobState.CANCELLED, finished_at=datetime.now(UTC))
            self._jobs[job_id] = cancelled
            self._changes += 1
            self._forget_oldest_ended()
        await self._events.publish(JobChanged(job=cancelled))
        return cancelled

    def update_progress(self, job_id: JobId, change: Callable[[Progress], Progress]) -> Job | None:
        """Change the progress of a job that was not cancelled.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :param change: Function from the progress the job has to the one it gets.
        :type change: Callable[[Progress], Progress]
        :returns: The job with its new progress, or None when it was cancelled, which leaves its progress as it was.
        :rtype: Job | None
        :raises NotFoundError: If the queue holds no such job.
        """
        with self._lock:
            job = self._locate(job_id)
            if job.state is JobState.CANCELLED:
                return None
            updated = self._jobs[job_id] = evolve(job, progress=change(job.progress))
            self._changes += 1
            return updated

    async def advance(self, job_id: JobId, change: Callable[[Progress], Progress]) -> None:
        """Change the progress of a job and announce it.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :param change: Function from the progress the job has to the one it gets.
        :type change: Callable[[Progress], Progress]
        :raises JobCancelledError: If the job was cancelled, in which case nothing is changed or announced.
        """
        if (job := self.update_progress(job_id, change)) is None:
            raise JobCancelledError
        await self._events.publish(JobChanged(job=job))

    def announce_later(self, job_id: JobId) -> None:
        """Plan the publication of a job from a place that cannot await.

        The job is read when the publication runs and not when it is planned, so an announcement that waits for its
        turn never tells the browser a state the job has left already, whatever was published meanwhile.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        """
        self._board.spawn(self._announce(job_id))

    async def serve(self) -> None:
        """Run the queue of the book, one job after another, until it is empty."""
        while (job := self._start_next()) is not None:
            await self._events.publish(JobChanged(job=job))
            control = BoardControl(self, job.id)
            end: JobEnd | None
            try:
                end = await self._board.runner(self.dir_name, job, control)
            except JobCancelledError:
                end = None
            except Exception:
                logger.exception('The %s job %s stopped', job.kind, job.id)
                end = JobEnd.unexpected()
            final, changed = self._end(job.id, end)
            if changed:
                await self._events.publish(JobChanged(job=final))

    async def _announce(self, job_id: JobId) -> None:
        """Publish a job as it is now, unless the queue has let it go meanwhile.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        """
        with suppress(NotFoundError):
            await self._events.publish(JobChanged(job=self.get(job_id)))

    def _start_next(self) -> Job | None:
        """Take the first queued job and mark it as running.

        :returns: The running job, or None when the queue is empty, which also ends the service of the book.
        :rtype: Job | None
        """
        with self._lock:
            if not self._queue:
                self._serving = False
                return None
            job = evolve(self._jobs[self._queue.popleft()], state=JobState.RUNNING, started_at=datetime.now(UTC))
            self._jobs[job.id] = job
            self._changes += 1
            return job

    def _end(self, job_id: JobId, end: JobEnd | None) -> tuple[Job, bool]:
        """Give a running job its final state, unless it was cancelled while it ran.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :param end: How the runner ended the job, or None when it stopped at a cancellation.
        :type end: JobEnd | None
        :returns: The job as it is now, and whether this call changed it, which is False for a job that was cancelled
                  already and announced as cancelled by ``cancel``.
        :rtype: tuple[Job, bool]
        :raises NotFoundError: If the queue holds no such job.
        """
        with self._lock:
            job = self._locate(job_id)
            if job.state.is_final:
                return job, False
            moment = datetime.now(UTC)
            if end is None:
                final = evolve(job, state=JobState.CANCELLED, finished_at=moment)
            elif end.state is JobState.SUCCEEDED:
                complete = Progress(done=job.progress.total, total=job.progress.total)
                final = evolve(job, state=JobState.SUCCEEDED, progress=complete, finished_at=moment)
            else:
                final = evolve(job, state=JobState.FAILED, error=end.error, finished_at=moment)
            self._jobs[job_id] = final
            self._changes += 1
            self._forget_oldest_ended()
            return final, True

    def _listing(self, *, active: bool) -> list[Job]:
        """Return the jobs of the book, the newest first.

        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs, with the ones put later first among those queued at the same moment.
        :rtype: list[Job]
        """
        with self._lock:
            held = list(reversed(self._jobs.values()))
        return sorted(
            (job for job in held if not active or not job.state.is_final), key=attrgetter('created_at'), reverse=True
        )

    def _locate(self, job_id: JobId) -> Job:
        """Find a job of the book, with the lock held.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job.
        :rtype: Job
        :raises NotFoundError: If the queue holds no such job.
        """
        if (job := self._jobs.get(job_id)) is None:
            raise NotFoundError(job_id)
        return job

    def _forget_oldest_ended(self) -> None:
        """Drop the oldest ended jobs beyond the number the board keeps for a book, with the lock held."""
        ended = [job_id for job_id, job in self._jobs.items() if job.state.is_final]
        for job_id in ended[: -self._board.kept_ended]:
            del self._jobs[job_id]


class InMemoryJobBoard:
    """Serves the jobs of each book one at a time, in the order they were put, and tells the browser how they go.

    The board is an async context manager. Leaving it cancels the tasks that serve the books, so a job that is running
    when the process stops is cut off in the middle of its task, which is why a task writes only what a restart may
    find half-done and run again.

    :ivar runner: What runs a job, supplied by the application.
    :ivar kept_ended: Most ended jobs held for each book, the latest ones.
    :ivar process: Identifier of this process, which the mark of the jobs of a book names.
    """

    def __init__(self, *, runner: JobRunner, kept_ended: int) -> None:
        """Build a board with no queues.

        :param runner: What runs a job, supplied by the application.
        :type runner: JobRunner
        :param kept_ended: Most ended jobs held for each book, the latest ones.
        :type kept_ended: int
        """
        self.runner = runner
        self.kept_ended = kept_ended
        self.process = uuid4().hex
        self._queues: dict[BookDirectoryName, InMemoryBookJobs] = {}
        self._tasks: set[asyncio.Task[None]] = set()

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

    def open(self, dir_name: BookDirectoryName) -> InMemoryBookJobs:
        """Return the queue of the book whose directory has the given name, made on the first call.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: The one queue of the book.
        :rtype: InMemoryBookJobs
        """
        if (queue := self._queues.get(dir_name)) is None:
            queue = self._queues[dir_name] = InMemoryBookJobs(self, dir_name)
        return queue

    def after_put(self, queue: InMemoryBookJobs, job: Job, *, starts: bool) -> None:
        """Announce a job that was put on a queue, and start a task for the queue when none serves it.

        A board that runs its queues in another way overrides this one method.

        :param queue: The queue the job was put on.
        :type queue: InMemoryBookJobs
        :param job: The queued job.
        :type job: Job
        :param starts: Whether no task served the queue, so that this call starts one.
        :type starts: bool
        """
        queue.announce_later(job.id)
        if starts:
            self.spawn(queue.serve())

    def spawn(self, work: Coroutine[Any, Any, None]) -> None:
        """Run work in a task of the board, which is kept until it is done.

        :param work: The coroutine to run.
        :type work: Coroutine[Any, Any, None]
        """
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
