"""Every BookJobs adapter set up with a scripted runner a test can read back, for the port contract.

``InMemoryJobBoard`` runs the queue of a book by itself, and ``FakeJobBoard`` runs it when it is told to, so the rig
lets a test start the queues in one way for both. The runner is scripted: it logs when each job begins and returns, it
counts the jobs of a book that are inside it at the same moment, and it can hold a job inside its first task until the
test sets an event, which is how a test looks at the board while a job is running.
"""

import enum
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

import anyio
from attrs import frozen

from bookreviver.adapters.jobs.board import InMemoryJobBoard
from bookreviver.ports.runtime import BookJobs
from tests.helpers.builders import directory_of
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.scripted_runner import ScriptedRunner

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from bookreviver.domain.ids import BookId


class BoardAdapter(enum.StrEnum):
    """An adapter of the job board under test."""

    IN_MEMORY = 'in-memory'
    FAKE = 'fake'


@frozen(kw_only=True)
class BoardUnderTest:
    """A job board with its scripted runner, and the way to let its queues run.

    :ivar board: The adapter under test, which is registered as the way a book opens its job queue.
    :ivar runner: The runner the board runs its jobs with.
    :ivar events: The events the board announced.
    :ivar start: Lets the queues of the jobs put so far run, which the real board has done already.
    """

    board: InMemoryJobBoard
    runner: ScriptedRunner
    events: RecordingEventBus
    start: Callable[[], None]

    def jobs(self, book_id: BookId) -> BookJobs:
        """Open the job queue of a book on the board under test.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The queue of the book.
        :rtype: BookJobs
        """
        return BookJobs.open(directory_of(book_id))


@asynccontextmanager
async def open_board_under_test(adapter: BoardAdapter, *, kept_ended: int = 20) -> AsyncGenerator[BoardUnderTest]:
    """Set up an adapter with a scripted runner; leaving cancels whatever still runs.

    :param adapter: Adapter to set up.
    :type adapter: BoardAdapter
    :param kept_ended: Most ended jobs the board keeps for each book.
    :type kept_ended: int
    :returns: Generator yielding the board, its runner and the events it announced.
    :rtype: AsyncGenerator[BoardUnderTest]
    """
    runner = ScriptedRunner()
    events = RecordingEventBus()
    previous_jobs = BookJobs.register(None)
    try:
        with events.installed():
            match adapter:
                case BoardAdapter.IN_MEMORY:
                    async with InMemoryJobBoard(runner=runner, kept_ended=kept_ended) as board:
                        BookJobs.register(board.open)
                        yield BoardUnderTest(board=board, runner=runner, events=events, start=lambda: None)
                case BoardAdapter.FAKE:
                    fake = FakeJobBoard(runner=runner, kept_ended=kept_ended)
                    BookJobs.register(fake.open)
                    async with anyio.create_task_group() as group:

                        def start() -> None:
                            """Let the queues of the jobs put so far run, in a task of the rig."""
                            group.start_soon(fake.run_queued)

                        try:
                            yield BoardUnderTest(board=fake, runner=runner, events=events, start=start)
                        finally:
                            group.cancel_scope.cancel()
    finally:
        BookJobs.register(previous_jobs)
