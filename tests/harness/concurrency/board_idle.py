"""Waiting for the job queue of a book of the running application to finish its jobs."""

from typing import TYPE_CHECKING

import anyio

from bookreviver.ports.runtime import BookEvents, BookJobs
from tests.harness.builders.domain import directory_of

if TYPE_CHECKING:
    from bookreviver.domain.ids import BookId

# Long enough for a slow machine, short enough to fail a test whose job never ends
IDLE_TIMEOUT_SECONDS: float = 60


class JobsIdle:
    """Waits until a book has no job queued or running, woken by the events the board publishes."""

    async def __call__(self, book_id: BookId) -> None:
        """Return once the book has no job queued or running, which includes the jobs that a finished job queued.

        A job queues its follow-up before it ends, so the book is never idle between the two and one wait covers the
        chain. The subscription is entered before the jobs are read, so a job that ends in between still wakes the
        wait.

        :param book_id: Identifier of the book whose jobs are awaited.
        :type book_id: BookId
        :raises TimeoutError: When a job is still queued or running after the timeout.
        """
        dir_name = directory_of(book_id)
        jobs = BookJobs.open(dir_name)
        with anyio.fail_after(IDLE_TIMEOUT_SECONDS):
            async with BookEvents.open(dir_name).subscribe() as events:
                while jobs.active():
                    await anext(events)
