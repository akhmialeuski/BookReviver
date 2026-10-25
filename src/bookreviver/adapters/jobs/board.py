"""The job board in the memory of the API process: one queue per book, and one job of a book running at a time.

The queues are kept by the name of the directory of the book. ``JobLedger`` holds the jobs and decides every change of
them, ``InMemoryJobBoard`` serves the queues and tells the browser through the event channel of the book, and
``InMemoryBookJobs`` is the view of one book the board hands out. A book has a task of its own that takes the first
queued job, hands it to the runner the application supplies and goes on with the next once the runner has returned, so
the books do not wait for each other and the jobs of one book never overlap. A cancelled job that is running leaves its
place to the next job only when its current task has returned, because the control it reports through raises
``JobCancelledError`` at the next task boundary and not before.

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
from datetime import UTC, datetime
from operator import attrgetter
from typing import TYPE_CHECKING, Any, Self, override
from uuid import uuid4

from attrs import define, evolve, field

from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import JobCancelledError, JobFinishedError, NotFoundError
from bookreviver.domain.events import JobChanged
from bookreviver.domain.values import JobEnd, Progress
from bookreviver.ports.runtime import BookEvents, BookJobs, JobControl

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Sequence
    from types import TracebackType

    from bookreviver.domain.entities import Job
    from bookreviver.domain.ids import BookDirectoryName, JobId

# What the application supplies to run a job: it takes the name of the directory of the book whose queue the job was
# taken from, does the tasks of the job, reports through the control after each, and returns how the job ended, or lets
# ``JobCancelledError`` pass when the control raised it
type JobRunner = Callable[[BookDirectoryName, Job, JobControl], Awaitable[JobEnd]]

logger = logging.getLogger(__name__)


@define
class BookQueue:
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

    def __init__(self, *, kept_ended: int) -> None:
        """Start with no jobs.

        :param kept_ended: Most ended jobs held for each book, the latest ones.
        :type kept_ended: int
        """
        self._kept_ended = kept_ended
        self._books: dict[BookDirectoryName, BookQueue] = {}
        self._lock = threading.Lock()

    def add(self, dir_name: BookDirectoryName, job: Job) -> bool:
        """Put a queued job at the end of the queue of a book.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job: The queued job.
        :type job: Job
        :returns: Whether no task served the queue of the book, so that the caller starts one.
        :rtype: bool
        """
        with self._lock:
            book = self._books.setdefault(dir_name, BookQueue())
            book.jobs[job.id] = job
            book.changes += 1
            book.queue.append(job.id)
            starts = not book.serving
            book.serving = True
            return starts

    def start_next(self, dir_name: BookDirectoryName) -> Job | None:
        """Take the first queued job of a book and mark it as running.

        :param dir_name: Name of the directory of the book whose queue is served.
        :type dir_name: BookDirectoryName
        :returns: The running job, or None when the queue is empty, which also ends the service of the book.
        :rtype: Job | None
        """
        with self._lock:
            book = self._books[dir_name]
            if not book.queue:
                book.serving = False
                return None
            job = evolve(book.jobs[book.queue.popleft()], state=JobState.RUNNING, started_at=datetime.now(UTC))
            book.jobs[job.id] = job
            book.changes += 1
            return job

    def get(self, dir_name: BookDirectoryName, job_id: JobId) -> Job:
        """Return a job the ledger holds for a book.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If the ledger holds no such job of the book.
        """
        with self._lock:
            return self._locate(dir_name, job_id)[1]

    def listing(self, dir_name: BookDirectoryName, *, active: bool) -> list[Job]:
        """Return the jobs of a book, the newest first.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs, with the ones put later first among those queued at the same moment.
        :rtype: list[Job]
        """
        with self._lock:
            book = self._books.get(dir_name)
            held = [] if book is None else list(reversed(book.jobs.values()))
        return sorted(
            (job for job in held if not active or not job.state.is_final), key=attrgetter('created_at'), reverse=True
        )

    def update_progress(
        self, dir_name: BookDirectoryName, job_id: JobId, change: Callable[[Progress], Progress]
    ) -> Job | None:
        """Change the progress of a job that was not cancelled.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :param change: Function from the progress the job has to the one it gets.
        :type change: Callable[[Progress], Progress]
        :returns: The job with its new progress, or None when it was cancelled, which leaves its progress as it was.
        :rtype: Job | None
        :raises NotFoundError: If the ledger holds no such job.
        """
        with self._lock:
            book, job = self._locate(dir_name, job_id)
            if job.state is JobState.CANCELLED:
                return None
            updated = evolve(job, progress=change(job.progress))
            book.jobs[job_id] = updated
            book.changes += 1
            return updated

    def cancel(self, dir_name: BookDirectoryName, job_id: JobId) -> Job:
        """Move a queued or running job to the cancelled state, and take a queued one out of its queue.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state, with the time it was cancelled.
        :rtype: Job
        :raises NotFoundError: If the ledger holds no such job.
        :raises JobFinishedError: If the job has ended already.
        """
        with self._lock:
            book, job = self._locate(dir_name, job_id)
            if job.state.is_final:
                raise JobFinishedError
            if job.state is JobState.QUEUED:
                book.queue.remove(job_id)
            cancelled = evolve(job, state=JobState.CANCELLED, finished_at=datetime.now(UTC))
            book.jobs[job_id] = cancelled
            book.changes += 1
            self._forget_oldest_ended(book)
            return cancelled

    def end(self, dir_name: BookDirectoryName, job_id: JobId, end: JobEnd | None) -> tuple[Job, bool]:
        """Give a running job its final state, unless it was cancelled while it ran.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
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
            book, job = self._locate(dir_name, job_id)
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
            book.jobs[job_id] = final
            book.changes += 1
            self._forget_oldest_ended(book)
            return final, True

    def changes(self, dir_name: BookDirectoryName) -> int:
        """Return how many times a job of a book changed in this process.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: The number of changes, none for a book without a job.
        :rtype: int
        """
        with self._lock:
            book = self._books.get(dir_name)
            return 0 if book is None else book.changes

    def _locate(self, dir_name: BookDirectoryName, job_id: JobId) -> tuple[BookQueue, Job]:
        """Find a job of a book and the queue that holds it, with the lock held.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The queue of the book and the job.
        :rtype: tuple[BookQueue, Job]
        :raises NotFoundError: If the ledger holds no such job of the book.
        """
        if (book := self._books.get(dir_name)) is None or (job := book.jobs.get(job_id)) is None:
            raise NotFoundError(job_id)
        return book, job

    def _forget_oldest_ended(self, book: BookQueue) -> None:
        """Drop the oldest ended jobs of a book beyond the number the ledger keeps, with the lock held.

        :param book: The queue of the book whose jobs are trimmed.
        :type book: BookQueue
        """
        ended = [job_id for job_id, job in book.jobs.items() if job.state.is_final]
        for job_id in ended[: -self._kept_ended]:
            del book.jobs[job_id]


class BoardControl(JobControl):
    """What one running job reports through: its progress goes to the ledger and the browser, and a cancel stops it."""

    def __init__(
        self,
        *,
        dir_name: BookDirectoryName,
        job_id: JobId,
        ledger: JobLedger,
        events: BookEvents,
        announce_later: Callable[[BookDirectoryName, JobId], None],
    ) -> None:
        """Report the progress of one job.

        :param dir_name: Name of the directory of the book of the job.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the running job.
        :type job_id: JobId
        :param ledger: Ledger holding the job.
        :type ledger: JobLedger
        :param events: Event channel of the book, which the browser follows.
        :type events: BookEvents
        :param announce_later: Announcement for a place that cannot await, which plans the publication of the job as it
                               is when the publication runs.
        :type announce_later: Callable[[BookDirectoryName, JobId], None]
        """
        self._dir_name = dir_name
        self._job_id = job_id
        self._ledger = ledger
        self._events = events
        self._announce_later = announce_later

    @override
    def plan(self, total: int) -> None:
        """Set the number of tasks of the job and announce it, unless the job was cancelled.

        :param total: Number of tasks of the job.
        :type total: int
        """
        if self._ledger.update_progress(
            self._dir_name, self._job_id, lambda progress: Progress(done=progress.done, total=total)
        ):
            self._announce_later(self._dir_name, self._job_id)

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
        if (job := self._ledger.update_progress(self._dir_name, self._job_id, change)) is None:
            raise JobCancelledError
        await self._events.publish(JobChanged(job=job))


class InMemoryBookJobs(BookJobs):
    """The queue of one book on the board, which is the view of the board over the directory name of the book."""

    def __init__(self, board: InMemoryJobBoard, dir_name: BookDirectoryName) -> None:
        """View the queue of a book on a board.

        :param board: The board that holds the queue.
        :type board: InMemoryJobBoard
        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        """
        self._board = board
        self._dir_name = dir_name

    @override
    def put(self, job: Job) -> Job:
        """Queue the job at the end of the queue of the book, announce it, and start a task when none serves the book.

        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job.
        :rtype: Job
        """
        return self._board.put_job(self._dir_name, job)

    @override
    def get(self, job_id: JobId) -> Job:
        """Return a job of the book the board holds.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If the board holds no such job of the book.
        """
        return self._board.get_job(self._dir_name, job_id)

    @override
    def jobs(self) -> Sequence[Job]:
        """Return the jobs of the book, the newest first.

        :returns: The jobs of the book.
        :rtype: Sequence[Job]
        """
        return self._board.list_jobs(self._dir_name, active=False)

    @override
    def active(self) -> Sequence[Job]:
        """Return the jobs of the book that are queued or running, the newest first.

        :returns: The active jobs of the book.
        :rtype: Sequence[Job]
        """
        return self._board.list_jobs(self._dir_name, active=True)

    @override
    def revision(self) -> str:
        """Return the identifier of the process and the number of changes of the jobs of the book in it.

        :returns: The mark of the jobs of the book.
        :rtype: str
        """
        return self._board.revision_of(self._dir_name)

    @override
    async def cancel(self, job_id: JobId) -> Job:
        """Cancel a job of the book and announce it; a running job stops at its next task boundary.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state.
        :rtype: Job
        :raises NotFoundError: If the board holds no such job of the book.
        :raises JobFinishedError: If the job has ended already.
        """
        return await self._board.cancel_job(self._dir_name, job_id)


class InMemoryJobBoard:
    """Serves the jobs of each book one at a time, in the order they were put, and tells the browser how they go.

    The board is an async context manager. Leaving it cancels the tasks that serve the books, so a job that is running
    when the process stops is cut off in the middle of its task, which is why a task writes only what a restart may
    find half-done and run again.
    """

    def __init__(self, *, runner: JobRunner, kept_ended: int) -> None:
        """Build a board with no jobs.

        :param runner: What runs a job, supplied by the application.
        :type runner: JobRunner
        :param kept_ended: Most ended jobs held for each book, the latest ones.
        :type kept_ended: int
        """
        self._runner = runner
        self._ledger = JobLedger(kept_ended=kept_ended)
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

    def open(self, dir_name: BookDirectoryName) -> InMemoryBookJobs:
        """Return the view of the queue of the book whose directory has the given name.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: The queue of the book, which holds no state of its own.
        :rtype: InMemoryBookJobs
        """
        return InMemoryBookJobs(self, dir_name)

    def put_job(self, dir_name: BookDirectoryName, job: Job) -> Job:
        """Queue the job at the end of its book, announce it, and start a task for the book when none serves it.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job.
        :rtype: Job
        """
        starts = self._ledger.add(dir_name, job)
        self._announce_later(dir_name, job.id)
        if starts:
            self._spawn(self._serve(dir_name))
        return job

    def get_job(self, dir_name: BookDirectoryName, job_id: JobId) -> Job:
        """Return a job of a book the board holds.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If the board holds no such job of the book.
        """
        return self._ledger.get(dir_name, job_id)

    def list_jobs(self, dir_name: BookDirectoryName, *, active: bool) -> Sequence[Job]:
        """Return the jobs of a book, the newest first.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the book.
        :rtype: Sequence[Job]
        """
        return self._ledger.listing(dir_name, active=active)

    def revision_of(self, dir_name: BookDirectoryName) -> str:
        """Return the identifier of this process and the number of changes of the jobs of the book in it.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: The mark of the jobs of the book.
        :rtype: str
        """
        return f'{self._process}.{self._ledger.changes(dir_name)}'

    async def cancel_job(self, dir_name: BookDirectoryName, job_id: JobId) -> Job:
        """Cancel a job of a book and announce it; a running job stops at its next task boundary.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state.
        :rtype: Job
        :raises NotFoundError: If the board holds no such job of the book.
        :raises JobFinishedError: If the job has ended already.
        """
        cancelled = self._ledger.cancel(dir_name, job_id)
        await BookEvents.open(dir_name).publish(JobChanged(job=cancelled))
        return cancelled

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        """Run work in a task of the board, which is kept until it is done.

        :param work: The coroutine to run.
        :type work: Coroutine[Any, Any, None]
        """
        task = asyncio.create_task(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _announce_later(self, dir_name: BookDirectoryName, job_id: JobId) -> None:
        """Plan the publication of a job from a place that cannot await.

        The job is read when the publication runs and not when it is planned, so an announcement that waits for its
        turn never tells the browser a state the job has left already, whatever was published meanwhile.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        """
        self._spawn(self._announce(dir_name, job_id))

    async def _announce(self, dir_name: BookDirectoryName, job_id: JobId) -> None:
        """Publish a job as it is now, unless the board has let it go meanwhile.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job_id: Identifier of the job.
        :type job_id: JobId
        """
        with suppress(NotFoundError):
            job = self._ledger.get(dir_name, job_id)
            await BookEvents.open(dir_name).publish(JobChanged(job=job))

    async def _serve(self, dir_name: BookDirectoryName) -> None:
        """Run the queue of a book, one job after another, until it is empty.

        :param dir_name: Name of the directory of the book whose queue is served.
        :type dir_name: BookDirectoryName
        """
        events = BookEvents.open(dir_name)
        while (job := self._ledger.start_next(dir_name)) is not None:
            await events.publish(JobChanged(job=job))
            control = BoardControl(
                dir_name=dir_name,
                job_id=job.id,
                ledger=self._ledger,
                events=events,
                announce_later=self._announce_later,
            )
            end: JobEnd | None
            try:
                end = await self._runner(dir_name, job, control)
            except JobCancelledError:
                end = None
            except Exception:
                logger.exception('The %s job %s stopped', job.kind, job.id)
                end = JobEnd.unexpected()
            final, changed = self._ledger.end(dir_name, job.id, end)
            if changed:
                await events.publish(JobChanged(job=final))
