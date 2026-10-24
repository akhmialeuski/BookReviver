"""A job board for the tests of services: it records the jobs put on it and runs them only when a test says so.

``InMemoryJobBoard`` starts a task for a book as soon as a job is put on it, and announces every job it holds. A test
of a service needs the opposite: it asks for a job, reads what was put, and then runs the queue itself, at the moment
it chose, so it can look at the book between the request and the work. ``FakeJobBoard`` is the real board with that one
difference. The rules of the queues, the control a job reports through and the cancellation are the real ones, so the
contract of the port holds for both, and nothing about the order in which jobs run is faked.

A test names a book by its identifier, and the books of the tests are in directories named by ``directory_of``, so the
board puts, reads and cancels a job by the identifier of its book. A test that wants the books it opens to use the board
registers ``board.open`` as ``BookJobs``, and the ``fx_book_parts`` fixture of the suite gives the previous registration
back after each test.
"""

from typing import TYPE_CHECKING, override

import anyio

from bookreviver.adapters.jobs.board import InMemoryJobBoard
from bookreviver.domain.errors import NotFoundError
from tests.helpers.builders import directory_of

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.jobs.board import JobRunner
    from bookreviver.domain.entities import Job
    from bookreviver.domain.ids import BookDirectoryName, BookId, JobId

DEFAULT_KEPT_ENDED: int = 20


class FakeJobBoard(InMemoryJobBoard):
    """The job board whose queues run when a test calls ``run_queued``, and not before.

    :ivar put_jobs: Every job put on the board, in the order it was put, whatever became of it.
    """

    def __init__(self, *, runner: JobRunner, kept_ended: int = DEFAULT_KEPT_ENDED) -> None:
        """Build a board with no jobs, which starts none by itself.

        :param runner: What runs a job when a test lets the queues run.
        :type runner: JobRunner
        :param kept_ended: Most ended jobs held for each book.
        :type kept_ended: int
        """
        super().__init__(runner=runner, kept_ended=kept_ended)
        self.put_jobs: list[Job] = []
        self._being_run: set[BookDirectoryName] = set()

    @override
    def put_job(self, dir_name: BookDirectoryName, job: Job) -> Job:
        """Queue the job and remember it, without announcing it and without starting a task for its book.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job.
        :rtype: Job
        """
        self.put_jobs.append(job)
        self._ledger.add(dir_name, job)
        return job

    def put(self, job: Job) -> Job:
        """Queue the job at the end of the queue of its book, as the book would.

        :param job: The job to queue, in the queued state.
        :type job: Job
        :returns: The job.
        :rtype: Job
        """
        return self.open(directory_of(job.book_id)).put(job)

    def get(self, job_id: JobId) -> Job:
        """Return a job put on the board, from the queue of its book.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job as it is now.
        :rtype: Job
        :raises NotFoundError: If no job with the identifier was put on this board.
        """
        return self.open(self._directory_of_job(job_id)).get(job_id)

    def jobs(self, book_id: BookId, *, active: bool) -> Sequence[Job]:
        """Return the jobs of a book, the newest first.

        :param book_id: The book.
        :type book_id: BookId
        :param active: Whether to list only the jobs that are queued or running.
        :type active: bool
        :returns: The jobs of the book.
        :rtype: Sequence[Job]
        """
        queue = self.open(directory_of(book_id))
        return queue.active() if active else queue.jobs()

    async def cancel(self, job_id: JobId) -> Job:
        """Cancel a job put on the board, in the queue of its book.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The job in the cancelled state.
        :rtype: Job
        :raises NotFoundError: If no job with the identifier was put on this board.
        """
        return await self.open(self._directory_of_job(job_id)).cancel(job_id)

    async def run_queued(self) -> None:
        """Run the queue of every book that had a job put on it, the books side by side, until every queue is empty.

        The jobs of a book run one at a time in the order they were put, and a job a running job puts is run too.
        """
        books = {directory_of(job.book_id) for job in self.put_jobs}
        async with anyio.create_task_group() as group:
            for dir_name in books:
                group.start_soon(self._run_book, dir_name)

    def _directory_of_job(self, job_id: JobId) -> BookDirectoryName:
        """Name the directory of the book a job was put on the board for.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :returns: The name of the directory of the book of the job.
        :rtype: BookDirectoryName
        :raises NotFoundError: If no job with the identifier was put on this board.
        """
        for job in self.put_jobs:
            if job.id == job_id:
                return directory_of(job.book_id)
        raise NotFoundError(job_id)

    async def _run_book(self, dir_name: BookDirectoryName) -> None:
        """Run the queue of one book, unless a call that is still running is running it already.

        A second server of one book would take the next job while the first job is inside its task.

        :param dir_name: Name of the directory of the book whose queue is run.
        :type dir_name: BookDirectoryName
        """
        if dir_name in self._being_run:
            return
        self._being_run.add(dir_name)
        try:
            await self._serve(dir_name)
        finally:
            self._being_run.discard(dir_name)
