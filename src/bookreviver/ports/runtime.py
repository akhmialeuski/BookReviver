"""Runtime ports: background jobs, event delivery, mail and time.

These are the parts of the environment a use case depends on without owning: where heavy work runs, how progress
reaches the browser, how mail leaves the server, and what time it is. Each has an in-process adapter now and a
networked one later, such as Redis pub/sub for events, selected in ``app``.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Sequence
    from contextlib import AbstractAsyncContextManager
    from datetime import datetime

    from bookreviver.domain.entities import Job
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import BookId, JobId
    from bookreviver.domain.values import MailMessage


class JobControl(ABC):
    """What a running job reports through between its tasks, and where it learns that it was cancelled.

    A job is made of tasks, the processing of one page by one plugin, and an import is one task. A job stops only
    between tasks, so every method that a job calls at a task boundary raises ``JobCancelledError`` when the job was
    cancelled, and no task is ever interrupted.
    """

    @abstractmethod
    def plan(self, total: int) -> None:
        """Set how many tasks the job has, which the progress counts against.

        A job may plan again once it knows more, such as a run that learns how many pages it goes over.

        :param total: Number of tasks of the job.
        :type total: int
        """

    @abstractmethod
    async def task_done(self) -> None:
        """Count one finished task and announce it, and raise ``JobCancelledError`` when the job was cancelled.

        A job that finishes more tasks than it planned has its total raised to the number done, so its progress never
        passes its end.

        :raises JobCancelledError: If the job was cancelled, which stops it before its next task.
        """

    @abstractmethod
    async def report(self, *, done: int, total: int) -> None:
        """Announce the progress inside one task, such as the files and scans of an import, and check for a cancel.

        :param done: Units of the task completed.
        :type done: int
        :param total: Units of the task in all.
        :type total: int
        :raises JobCancelledError: If the job was cancelled, which stops it at this point of the task.
        """


class JobBoard(ABC):
    """Holds the jobs of every book in memory, one queue per book, and runs one job of a book at a time.

    The board lives in one process and is empty when the process starts: nothing is stored, so a job a restart
    interrupted is asked for again, and what its tasks wrote stays. The books do not wait for each other, and the
    jobs of one book run in the order they were put.

    ``put``, ``get``, ``jobs`` and ``jobs_of`` are synchronous on purpose. A service that checks the rules of a book
    against ``jobs`` and then calls ``put`` does it with no ``await`` in between, so the two are one step of the event
    loop and no other request can put a job between them. This is what keeps one active import, one run of a stage and
    one housekeeping job per book, which a database kept with unique indexes and a transaction. The reads may also run
    in the worker thread of a read transaction, so an adapter keeps them safe to call from any thread.
    """

    @abstractmethod
    def put(self, job: Job) -> Job:
        """Append a job to the queue of its book and announce it as queued.

        The caller has checked the rules of the book without awaiting, so the check and the put are one step.

        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job as the board holds it.
        :rtype: Job
        """

    @abstractmethod
    def get(self, job_id: JobId) -> Job:
        """Return a job the process holds, which is queued, running, or one of the latest that ended.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If the process does not hold the job, as after a restart or once it left the ended jobs
                               the board keeps.
        """

    @abstractmethod
    def jobs(self, book_id: BookId, *, active: bool) -> Sequence[Job]:
        """Return the jobs of a book the process holds, the newest first.

        :param book_id: Book whose jobs are listed.
        :type book_id: BookId
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the book.
        :rtype: Sequence[Job]
        """

    @abstractmethod
    def jobs_of(self, book_ids: Collection[BookId], *, active: bool) -> Sequence[Job]:
        """Return the jobs of several books the process holds, the newest first.

        :param book_ids: Books whose jobs are listed.
        :type book_ids: Collection[BookId]
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the books.
        :rtype: Sequence[Job]
        """

    @abstractmethod
    def revision(self, book_id: BookId) -> str:
        """Return a mark that changes whenever a job of a book is put, started, advanced, cancelled or ended.

        A read of a book that shows its jobs is fresh while the version of the book and this mark are both the same.
        The mark names the process too, so it never repeats after a restart, when the jobs held before are gone.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The mark of the jobs of the book.
        :rtype: str
        """

    @abstractmethod
    async def cancel(self, job_id: JobId) -> Job:
        """Cancel a job: a queued one is dropped and never runs, a running one stops at the boundary of its task.

        The next job of the book starts only after the running task has returned, so the book is never worked on by
        two jobs at once.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state.
        :rtype: Job
        :raises NotFoundError: If the process does not hold the job.
        :raises JobFinishedError: If the job has ended already.
        """


class EventPublisher(ABC):
    """Publishes domain events to whoever listens."""

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Deliver the event to every current subscriber of its book.

        :param event: Event to deliver, carrying the book it belongs to.
        :type event: DomainEvent
        """


class EventStream(ABC):
    """Subscribes to the domain events of one book.

    A subscription is a resource with a start and an end, so it is an async context manager. Entering it subscribes,
    so every event published from then on is kept for the subscriber, even before the first read. Leaving it
    unsubscribes at once, whether the consumer stopped reading or never started.
    """

    @abstractmethod
    def subscribe(self, book_id: BookId) -> AbstractAsyncContextManager[AsyncIterator[DomainEvent]]:
        """Return a subscription to the book's events.

        :param book_id: Book whose events are delivered.
        :type book_id: BookId
        :returns: Context manager that subscribes when entered, yields the iterator of every event published since,
                  and unsubscribes when left.
        :rtype: AbstractAsyncContextManager[AsyncIterator[DomainEvent]]
        """


class Mailer(ABC):
    """Sends mail to account holders."""

    @abstractmethod
    async def send(self, message: MailMessage) -> None:
        """Send one message.

        :param message: Recipient, subject and body of the message.
        :type message: MailMessage
        """


class Clock(ABC):
    """The current time, replaceable in tests."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time with its time zone.

        :returns: The current moment, time-zone aware.
        :rtype: datetime
        """
