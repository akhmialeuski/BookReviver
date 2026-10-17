"""Waiting for the job board of the running application to finish the jobs of a book."""

from typing import TYPE_CHECKING

import anyio

if TYPE_CHECKING:
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.runtime import EventStream, JobBoard

# Long enough for a slow machine, short enough to fail a test whose job never ends
IDLE_TIMEOUT_SECONDS: float = 60


class JobsIdle:
    """Waits until a book has no job queued or running, woken by the events the board publishes."""

    def __init__(self, board: JobBoard, stream: EventStream) -> None:
        """Watch the jobs of the board, and wait on the events of the same application.

        :param board: Job board of the running application.
        :type board: JobBoard
        :param stream: Event stream of the running application, which the board publishes every change of a job to.
        :type stream: EventStream
        """
        self._board = board
        self._stream = stream

    async def __call__(self, project_id: ProjectId) -> None:
        """Return once the book has no job queued or running, which includes the jobs that a finished job queued.

        A job queues its follow-up before it ends, so the book is never idle between the two and one wait covers the
        chain. The subscription is entered before the jobs are read, so a job that ends in between still wakes the
        wait.

        :param project_id: Identifier of the book whose jobs are awaited.
        :type project_id: ProjectId
        :raises TimeoutError: When a job is still queued or running after the timeout.
        """
        with anyio.fail_after(IDLE_TIMEOUT_SECONDS):
            async with self._stream.subscribe(project_id) as events:
                while self._board.jobs(project_id, active=True):
                    await anext(events)
