"""Tests of what is particular to the in-memory job board: its announcements, its restart of a book and its shutdown.

What every board does is the contract in ``tests/contracts/test_job_board.py``.
"""

from uuid import UUID

import anyio
import pytest
from delayed_assert import expect

from bookreviver.domain.enums import JobState
from bookreviver.domain.events import JobChanged
from bookreviver.domain.ids import ProjectId
from tests.helpers.builders import make_job
from tests.helpers.job_boards import BoardAdapter, open_board_under_test
from tests.helpers.scripted_runner import TASKS_PER_JOB, Phase

pytestmark = pytest.mark.anyio

BOOK: ProjectId = ProjectId(UUID(int=1))


class TestInMemoryJobBoard:
    """The in-memory job board, which runs the queue of each book in a task of its own."""

    async def test_it_announces_a_job_as_it_is_queued_runs_goes_on_and_ends(self) -> None:
        """Verify the browser is told of the job when it is queued, when it runs, as it goes, and when it ends."""
        job = make_job(project_id=BOOK)
        async with open_board_under_test(BoardAdapter.IN_MEMORY) as under_test:
            under_test.board.put(job)
            await anyio.wait_all_tasks_blocked()
        announced = [event.job for event in under_test.events.published if isinstance(event, JobChanged)]
        expect(announced[0].state is JobState.QUEUED)
        expect(announced[-1].state is JobState.SUCCEEDED)
        expect(JobState.RUNNING in {announced_job.state for announced_job in announced})
        expect(all(announced_job.id == job.id for announced_job in announced))
        done = [announced_job.progress.done for announced_job in announced]
        expect(done == sorted(done))
        expect(announced[-1].progress.done == TASKS_PER_JOB)

    async def test_it_announces_a_cancelled_job(self) -> None:
        """Verify the browser is told of a job the moment it is cancelled, while the running task has not returned."""
        job = make_job(project_id=BOOK)
        async with open_board_under_test(BoardAdapter.IN_MEMORY) as under_test:
            under_test.runner.hold(job)
            under_test.board.put(job)
            await anyio.wait_all_tasks_blocked()
            await under_test.board.cancel(job.id)
            states = [event.job.state for event in under_test.events.published if isinstance(event, JobChanged)]
            log_after_cancel = list(under_test.runner.log)
        expect(states[-1] is JobState.CANCELLED)
        expect((Phase.RETURNED, job.id) not in log_after_cancel)

    async def test_a_book_that_went_idle_runs_the_job_put_later(self) -> None:
        """Verify a job put on a book whose queue has run dry is run, which takes a new task for the book."""
        first, later = make_job(project_id=BOOK, minutes=0), make_job(project_id=BOOK, minutes=1)
        async with open_board_under_test(BoardAdapter.IN_MEMORY) as under_test:
            under_test.board.put(first)
            await anyio.wait_all_tasks_blocked()
            under_test.board.put(later)
            await anyio.wait_all_tasks_blocked()
            state = under_test.board.get(later.id).state
        expect(state is JobState.SUCCEEDED)
        expect(under_test.runner.begun() == [first.id, later.id])

    async def test_leaving_the_board_cuts_off_a_running_job(self) -> None:
        """Verify the tasks of the board are cancelled when it is left, so a running job does not outlive the process."""
        job = make_job(project_id=BOOK)
        async with open_board_under_test(BoardAdapter.IN_MEMORY) as under_test:
            under_test.runner.hold(job)
            under_test.board.put(job)
            await anyio.wait_all_tasks_blocked()
            begun = under_test.runner.begun()
        expect(begun == [job.id])
        expect(under_test.runner.log[-1] == (Phase.RETURNED, job.id))
