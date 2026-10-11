"""Tests for the life of a job on a worker: start, advance and finish, each in a block of the unit of work."""

from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.events import JobChanged
from bookreviver.services.job_runs import JobTracker
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_jobs import JobFakes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.domain.entities import Job, Project

pytestmark = pytest.mark.anyio

ACTION_ARGUMENT: str = 'action'
PROGRESS_STEPS: int = 2


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the tracker runs on.

    :returns: Fakes with an empty database and nothing published.
    :rtype: JobFakes
    """
    return JobFakes()


@pytest.fixture
def fx_project() -> Project:
    """Build a project, not yet stored.

    :returns: The project.
    :rtype: Project
    """
    return make_project(owner_id=new_account_id())


def tracker_of(fakes: JobFakes) -> JobTracker:
    """Build a tracker over a unit of work of its own, as a worker has.

    :param fakes: Adapters of the test.
    :type fakes: JobFakes
    :returns: The tracker.
    :rtype: JobTracker
    """
    return JobTracker(uow=InMemoryUnitOfWork(fakes.database), publisher=fakes.events, clock=fakes.clock)


async def advance(tracker: JobTracker, job: Job) -> Job | None:
    """Record one step of progress of the job.

    :param tracker: The tracker of the worker.
    :type tracker: JobTracker
    :param job: The running job.
    :type job: Job
    :returns: What ``JobTracker.advance`` returns.
    :rtype: Job | None
    """
    return await tracker.advance(job, done=1, total=PROGRESS_STEPS)


async def finish(tracker: JobTracker, job: Job) -> None:
    """Store the job as succeeded.

    :param tracker: The tracker of the worker.
    :type tracker: JobTracker
    :param job: The running job.
    :type job: Job
    """
    await tracker.finish(job, JobState.SUCCEEDED, total=PROGRESS_STEPS)


class TestTrackerWaitsForACancellation:
    """Tests for a worker that reaches its job while the account holder is cancelling it."""

    @pytest.mark.parametrize(
        ACTION_ARGUMENT,
        [pytest.param(advance, id='advance'), pytest.param(finish, id='finish')],
    )
    async def test_write_of_the_worker_waits_for_the_cancellation_and_changes_nothing(
        self,
        fx_fakes: JobFakes,
        fx_project: Project,
        action: Callable[[JobTracker, Job], Awaitable[object]],
    ) -> None:
        """Verify a step or the end of a job waits for a cancellation in its block, then finds the job cancelled.

        The cancellation holds its block open, so the worker cannot read the job running and write over the
        cancellation. When the block commits, the write of the worker finds the job cancelled, changes nothing and
        announces nothing.

        :param fx_fakes: Adapters of the tracker.
        :type fx_fakes: JobFakes
        :param fx_project: Project owning the job.
        :type fx_project: Project
        :param action: What the worker does to the job.
        :type action: Callable[[JobTracker, Job], Awaitable[object]]
        """
        job = make_job(project_id=fx_project.id, state=JobState.RUNNING, kind=JobKind.RUN_STAGE)
        await fx_fakes.store(fx_project, job)
        canceller = InMemoryUnitOfWork(fx_fakes.database)
        cancelling = anyio.Event()
        resume = anyio.Event()
        done = anyio.Event()

        async def cancel_and_hold() -> None:
            """Cancel the job in a block that stays open until the test lets it commit."""
            async with canceller.change():
                await canceller.jobs.update_if_state(
                    evolve(job, state=JobState.CANCELLED, finished_at=fx_fakes.clock.now()),
                    expected=(JobState.RUNNING,),
                )
                cancelling.set()
                await resume.wait()

        async def work() -> None:
            """Do the action of the worker and say so."""
            await action(tracker_of(fx_fakes), job)
            done.set()

        async with anyio.create_task_group() as group:
            group.start_soon(cancel_and_hold)
            await cancelling.wait()
            group.start_soon(work)
            await anyio.wait_all_tasks_blocked()
            expect(not done.is_set())
            expect((await fx_fakes.stored_job(job)).state is JobState.RUNNING)
            resume.set()

        expect((await fx_fakes.stored_job(job)).state is JobState.CANCELLED)
        expect(not fx_fakes.events.published)
        assert_expectations()


class TestFinish:
    """Tests for ``JobTracker.finish``, which stores the final state and the follow-up in one block."""

    @pytest.mark.parametrize(
        ('active', 'expected'),
        [
            pytest.param(None, JobKind.COLLECT_VERSIONS, id='project-free'),
            pytest.param(JobKind.CUT_TILES, JobKind.CUT_TILES, id='other-processing-job-queued'),
        ],
    )
    async def test_follow_up_is_stored_with_the_final_state_unless_another_job_has_the_project(
        self, fx_fakes: JobFakes, fx_project: Project, active: JobKind | None, expected: JobKind
    ) -> None:
        """Verify the follow-up is stored with the end of the job, and is left out for a project another job holds.

        :param fx_fakes: Adapters of the tracker.
        :type fx_fakes: JobFakes
        :param fx_project: Project owning the jobs.
        :type fx_project: Project
        :param active: The kind of the processing job that is queued when the run ends, or None for no job.
        :type active: JobKind | None
        :param expected: The kind of the only job that is queued once the run has ended.
        :type expected: JobKind
        """
        run = make_job(project_id=fx_project.id, state=JobState.RUNNING, kind=JobKind.RUN_STAGE)
        others = [] if active is None else [make_job(project_id=fx_project.id, kind=active, minutes=1)]
        await fx_fakes.store(fx_project, run, *others)
        follow_up = make_job(project_id=fx_project.id, kind=JobKind.COLLECT_VERSIONS, minutes=2)

        await tracker_of(fx_fakes).finish(run, JobState.SUCCEEDED, total=PROGRESS_STEPS, follow_up=follow_up)

        reader = InMemoryUnitOfWork(fx_fakes.database)
        ended = await reader.jobs.get(run.id)
        queued = await reader.jobs.list_for_project(fx_project.id, JobState.active())
        expect(ended.state is JobState.SUCCEEDED)
        expect([job.kind for job in queued] == [expected])
        expect(JobChanged(project_id=fx_project.id, job=ended) in fx_fakes.events.published)
        assert_expectations()
