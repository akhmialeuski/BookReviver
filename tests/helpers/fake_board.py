"""A job board for the tests of services: it records the jobs put on it and runs them only when a test says so.

``InMemoryJobBoard`` starts a task for a book as soon as a job is put on it, and announces every job it holds. A test
of a service needs the opposite: it asks for a job, reads what was put, and then runs the queue itself, at the moment
it chose, so it can look at the book between the request and the work. ``FakeJobBoard`` is the real board with that one
difference. The rules of the queues, the control a job reports through and the cancellation are the real ones, so the
contract of the port holds for both, and nothing about the order in which jobs run is faked.
"""

from typing import TYPE_CHECKING, override

import anyio

from bookreviver.adapters.jobs.board import InMemoryJobBoard

if TYPE_CHECKING:
    from bookreviver.adapters.jobs.board import JobRunner
    from bookreviver.domain.entities import Job
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.runtime import Clock, EventPublisher

DEFAULT_KEPT_ENDED: int = 20


class FakeJobBoard(InMemoryJobBoard):
    """The job board whose queues run when a test calls ``run_queued``, and not before.

    :ivar put_jobs: Every job put on the board, in the order it was put, whatever became of it.
    """

    def __init__(
        self, *, runner: JobRunner, publisher: EventPublisher, clock: Clock, kept_ended: int = DEFAULT_KEPT_ENDED
    ) -> None:
        """Build a board with no jobs, which starts none by itself.

        :param runner: What runs a job when a test lets the queues run.
        :type runner: JobRunner
        :param publisher: Publisher of the events the running jobs announce.
        :type publisher: EventPublisher
        :param clock: Clock stamping the jobs.
        :type clock: Clock
        :param kept_ended: Most ended jobs held for each book.
        :type kept_ended: int
        """
        super().__init__(runner=runner, publisher=publisher, clock=clock, kept_ended=kept_ended)
        self.put_jobs: list[Job] = []
        self._being_run: set[BookId] = set()

    @override
    def put(self, job: Job) -> Job:
        """Queue the job and remember it, without announcing it and without starting a task for its book.

        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job.
        :rtype: Job
        """
        self.put_jobs.append(job)
        self._ledger.add(job)
        return job

    async def run_queued(self) -> None:
        """Run the queue of every book that had a job put on it, the books side by side, until every queue is empty.

        The jobs of a book run one at a time in the order they were put, and a job a running job puts is run too.
        """
        books = {job.book_id for job in self.put_jobs}
        async with anyio.create_task_group() as group:
            for book_id in books:
                group.start_soon(self._run_book, book_id)

    async def _run_book(self, book_id: BookId) -> None:
        """Run the queue of one book, unless a call that is still running is running it already.

        A second server of one book would take the next job while the first job is inside its task.

        :param book_id: Book whose queue is run.
        :type book_id: BookId
        """
        if book_id in self._being_run:
            return
        self._being_run.add(book_id)
        try:
            await self._serve(book_id)
        finally:
            self._being_run.discard(book_id)
