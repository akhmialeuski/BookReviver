"""Contract of the JobBoard port, run against every adapter listed in ``BoardAdapter``.

The runner is scripted, and a test looks at the board while a job is held inside its first task. ``wait_all_tasks_blocked``
lets every task of the board and of the runner run until each waits for something, which is the state a test inspects.
"""

from typing import TYPE_CHECKING
from uuid import UUID

import anyio
import pytest
from delayed_assert import expect

from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import JobId, ProjectId
from bookreviver.domain.values import JobEnd
from tests.helpers.builders import make_job
from tests.helpers.job_boards import open_board_under_test
from tests.helpers.scripted_runner import TASKS_PER_JOB, Phase

if TYPE_CHECKING:
    from tests.helpers.job_boards import BoardAdapter, BoardUnderTest

pytestmark = pytest.mark.anyio

BOOK: ProjectId = ProjectId(UUID(int=1))
OTHER_BOOK: ProjectId = ProjectId(UUID(int=2))
UNKNOWN_JOB: JobId = JobId(UUID(int=99))
KEPT_ENDED: int = 2
JOBS_PUT: int = 4


async def settle(under_test: BoardUnderTest) -> None:
    """Let the queues run until every job that can go on is waiting or done.

    :param under_test: The board with its rig.
    :type under_test: BoardUnderTest
    """
    under_test.start()
    await anyio.wait_all_tasks_blocked()


class TestJobBoard:
    """Contract of JobBoard: queues of books, one job of a book at a time, cancellation and the jobs it keeps."""

    async def test_the_jobs_of_a_book_run_in_the_order_they_were_put(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify the jobs of one book begin in the order they were put, and all of them succeed.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        jobs = [make_job(project_id=BOOK, kind=JobKind.RUN_STAGE, minutes=minute) for minute in range(JOBS_PUT)]
        async with open_board_under_test(fx_board_adapter) as under_test:
            for job in jobs:
                under_test.board.put(job)
            await settle(under_test)
            states = [under_test.board.get(job.id).state for job in jobs]
        expect(under_test.runner.begun() == [job.id for job in jobs])
        expect(states == [JobState.SUCCEEDED] * JOBS_PUT)

    async def test_a_book_runs_one_job_at_a_time(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify the second job of a book waits while the first one is inside its task, and begins after it returned.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        first, second = (make_job(project_id=BOOK, minutes=minute) for minute in range(2))
        async with open_board_under_test(fx_board_adapter) as under_test:
            release = under_test.runner.hold(first)
            under_test.board.put(first)
            under_test.board.put(second)
            await settle(under_test)
            states_while_held = [under_test.board.get(job.id).state for job in (first, second)]
            begun_while_held = under_test.runner.begun()
            release.set()
            await settle(under_test)
        expect(states_while_held == [JobState.RUNNING, JobState.QUEUED])
        expect(begun_while_held == [first.id])
        expect(
            under_test.runner.log
            == [
                (Phase.BEGUN, first.id),
                (Phase.RETURNED, first.id),
                (Phase.BEGUN, second.id),
                (Phase.RETURNED, second.id),
            ]
        )
        expect(under_test.runner.peak[BOOK] == 1)

    async def test_books_do_not_wait_for_each_other(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify a job of one book runs to the end while a job of another book is held inside its task.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        held, free = make_job(project_id=BOOK), make_job(project_id=OTHER_BOOK)
        async with open_board_under_test(fx_board_adapter) as under_test:
            under_test.runner.hold(held)
            under_test.board.put(held)
            under_test.board.put(free)
            await settle(under_test)
            states = (under_test.board.get(held.id).state, under_test.board.get(free.id).state)
        expect(states == (JobState.RUNNING, JobState.SUCCEEDED))

    async def test_a_queued_job_that_is_cancelled_never_runs(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify a queued job is dropped when cancelled, and the job behind it runs after the one before it.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        first, dropped, last = (make_job(project_id=BOOK, minutes=minute) for minute in range(3))
        async with open_board_under_test(fx_board_adapter) as under_test:
            release = under_test.runner.hold(first)
            for job in (first, dropped, last):
                under_test.board.put(job)
            await settle(under_test)
            cancelled = await under_test.board.cancel(dropped.id)
            release.set()
            await settle(under_test)
            states = [under_test.board.get(job.id).state for job in (first, dropped, last)]
        expect(cancelled.state is JobState.CANCELLED)
        expect(under_test.runner.begun() == [first.id, last.id])
        expect(states == [JobState.SUCCEEDED, JobState.CANCELLED, JobState.SUCCEEDED])

    async def test_a_running_job_stops_at_its_next_task_and_the_next_job_waits_for_it(
        self, fx_board_adapter: BoardAdapter
    ) -> None:
        """Verify a running job that is cancelled stops at its next ``task_done``, and the next job begins after it returned.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        running, next_job = make_job(project_id=BOOK, minutes=0), make_job(project_id=BOOK, minutes=1)
        async with open_board_under_test(fx_board_adapter) as under_test:
            release = under_test.runner.hold(running)
            under_test.board.put(running)
            under_test.board.put(next_job)
            await settle(under_test)
            cancelled = await under_test.board.cancel(running.id)
            begun_before_return = under_test.runner.begun()
            await settle(under_test)
            begun_while_still_inside = under_test.runner.begun()
            release.set()
            await settle(under_test)
            final = under_test.board.get(running.id)
            next_state = under_test.board.get(next_job.id).state
        expect(cancelled.state is JobState.CANCELLED)
        expect(begun_before_return == [running.id])
        expect(begun_while_still_inside == [running.id])
        expect(
            under_test.runner.log
            == [
                (Phase.BEGUN, running.id),
                (Phase.RETURNED, running.id),
                (Phase.BEGUN, next_job.id),
                (Phase.RETURNED, next_job.id),
            ]
        )
        expect(final.state is JobState.CANCELLED)
        expect(final.progress.done == 0)
        expect(next_state is JobState.SUCCEEDED)

    async def test_a_succeeded_job_has_all_its_tasks_done(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify a job that ran to its end has as many tasks done as it planned.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        job = make_job(project_id=BOOK)
        async with open_board_under_test(fx_board_adapter) as under_test:
            under_test.board.put(job)
            await settle(under_test)
            ended = under_test.board.get(job.id)
        expect(ended.progress.done == TASKS_PER_JOB)
        expect(ended.progress.total == TASKS_PER_JOB)
        expect(ended.finished_at is not None)

    async def test_a_job_that_raises_fails_and_the_next_job_runs(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify an error nobody expected fails its job with the standard reason, and the book goes on.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        broken, next_job = make_job(project_id=BOOK, minutes=0), make_job(project_id=BOOK, minutes=1)
        async with open_board_under_test(fx_board_adapter) as under_test:
            under_test.runner.fail(broken)
            under_test.board.put(broken)
            under_test.board.put(next_job)
            await settle(under_test)
            failed = under_test.board.get(broken.id)
            next_state = under_test.board.get(next_job.id).state
        expect(failed.state is JobState.FAILED)
        expect(failed.error == JobEnd.UNEXPECTED_FAILURE)
        expect(next_state is JobState.SUCCEEDED)

    async def test_it_keeps_the_latest_ended_jobs_of_a_book(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify the board keeps the number of ended jobs it was told to, the latest ones, and lets the others go.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        jobs = [make_job(project_id=BOOK, minutes=minute) for minute in range(JOBS_PUT)]
        async with open_board_under_test(fx_board_adapter, kept_ended=KEPT_ENDED) as under_test:
            for job in jobs:
                under_test.board.put(job)
            await settle(under_test)
            kept = under_test.board.jobs(BOOK, active=False)
            with pytest.raises(NotFoundError):
                under_test.board.get(jobs[0].id)
        expect([job.id for job in kept] == [job.id for job in reversed(jobs[-KEPT_ENDED:])])

    async def test_it_lists_the_jobs_of_books_newest_first_and_the_active_ones_alone(
        self, fx_board_adapter: BoardAdapter
    ) -> None:
        """Verify the listings of one book and of several books, with and without the ended jobs.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        ended, held, waiting = (make_job(project_id=BOOK, minutes=minute) for minute in range(3))
        elsewhere = make_job(project_id=OTHER_BOOK, minutes=3)
        async with open_board_under_test(fx_board_adapter) as under_test:
            under_test.board.put(ended)
            await settle(under_test)
            under_test.runner.hold(held)
            under_test.runner.hold(elsewhere)
            for job in (held, waiting, elsewhere):
                under_test.board.put(job)
            await settle(under_test)
            everything = under_test.board.jobs(BOOK, active=False)
            active = under_test.board.jobs(BOOK, active=True)
            of_both = under_test.board.jobs_of([BOOK, OTHER_BOOK], active=True)
        expect([job.id for job in everything] == [waiting.id, held.id, ended.id])
        expect([job.id for job in active] == [waiting.id, held.id])
        expect([job.id for job in of_both] == [elsewhere.id, waiting.id, held.id])

    async def test_it_does_not_know_a_job_it_was_not_given(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify reading or cancelling an unknown job is reported as not found.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        async with open_board_under_test(fx_board_adapter) as under_test:
            with pytest.raises(NotFoundError):
                under_test.board.get(UNKNOWN_JOB)
            with pytest.raises(NotFoundError):
                await under_test.board.cancel(UNKNOWN_JOB)

    async def test_a_job_that_ended_cannot_be_cancelled(self, fx_board_adapter: BoardAdapter) -> None:
        """Verify cancelling a job that has ended is a conflict, and leaves it as it ended.

        :param fx_board_adapter: Adapter under test.
        :type fx_board_adapter: BoardAdapter
        """
        job = make_job(project_id=BOOK)
        async with open_board_under_test(fx_board_adapter) as under_test:
            under_test.board.put(job)
            await settle(under_test)
            with pytest.raises(ConflictError):
                await under_test.board.cancel(job.id)
            state = under_test.board.get(job.id).state
        expect(state is JobState.SUCCEEDED)
