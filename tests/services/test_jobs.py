"""Tests for the job service: reading and cancelling jobs, and the event stream of a project."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import JobChanged, ProjectChanged
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_jobs import JobFakes

if TYPE_CHECKING:
    from bookreviver.domain.entities import Project

pytestmark = pytest.mark.anyio

ACTIVE_STATES: tuple[JobState, ...] = (JobState.QUEUED, JobState.RUNNING)
FINAL_STATES: tuple[JobState, ...] = (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED)


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the job service runs on.

    :returns: Fakes with an empty database and nothing published.
    :rtype: JobFakes
    """
    return JobFakes()


@pytest.fixture
def fx_owner() -> Actor:
    """Build the account that owns the project.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())


@pytest.fixture
def fx_stranger() -> Actor:
    """Build an account that owns nothing.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())


@pytest.fixture
def fx_project(fx_owner: Actor) -> Project:
    """Build a project of ``fx_owner``, not yet stored.

    :param fx_owner: Account owning the project.
    :type fx_owner: Actor
    :returns: The project.
    :rtype: Project
    """
    return make_project(owner_id=fx_owner.account_id)


class TestGet:
    """Tests for JobService.get()."""

    async def test_owner_reads_job(self, fx_fakes: JobFakes, fx_owner: Actor, fx_project: Project) -> None:
        """Verify the owner of the project reads its job as stored.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = make_job(project_id=fx_project.id, state=JobState.RUNNING)
        await fx_fakes.store(fx_project, job)
        assert await fx_fakes.job_service().get(fx_owner, job.id) == job

    async def test_job_of_another_account_is_not_found(
        self, fx_fakes: JobFakes, fx_stranger: Actor, fx_project: Project
    ) -> None:
        """Verify an account cannot read, or learn about, a job of a project it does not own.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_stranger: Account owning nothing.
        :type fx_stranger: Actor
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        job = make_job(project_id=fx_project.id, state=JobState.RUNNING)
        await fx_fakes.store(fx_project, job)
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service().get(fx_stranger, job.id)


class TestCancel:
    """Tests for JobService.cancel()."""

    @pytest.mark.parametrize('state', ACTIVE_STATES)
    async def test_active_job_is_cancelled_and_announced(
        self, fx_fakes: JobFakes, fx_owner: Actor, fx_project: Project, state: JobState
    ) -> None:
        """Verify cancelling a queued or running job stores it as cancelled and publishes the change.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param state: Active state the job is in before the cancellation.
        :type state: JobState
        """
        job = make_job(project_id=fx_project.id, state=state)
        await fx_fakes.store(fx_project, job)
        cancelled = await fx_fakes.job_service().cancel(fx_owner, job.id)
        expect((cancelled.state, cancelled.finished_at) == (JobState.CANCELLED, fx_fakes.clock.now()))
        expect(await fx_fakes.stored_job(job) == cancelled)
        expect(fx_fakes.events.published == [JobChanged(project_id=job.project_id, job=cancelled)])
        assert_expectations()

    @pytest.mark.parametrize('state', FINAL_STATES)
    async def test_finished_job_cannot_be_cancelled(
        self, fx_fakes: JobFakes, fx_owner: Actor, fx_project: Project, state: JobState
    ) -> None:
        """Verify a finished job keeps its final state, nothing is published, and the refusal is a conflict.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        :param state: Final state the job is in.
        :type state: JobState
        """
        job = make_job(project_id=fx_project.id, state=state)
        await fx_fakes.store(fx_project, job)
        with pytest.raises(ConflictError):
            await fx_fakes.job_service().cancel(fx_owner, job.id)
        expect((await fx_fakes.stored_job(job)).state is state)
        expect(not fx_fakes.events.published)
        assert_expectations()

    async def test_job_finished_after_it_was_read_keeps_its_final_state(
        self, fx_fakes: JobFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a job that a worker finishes after the service read it running is not overwritten as cancelled.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = make_job(project_id=fx_project.id, state=JobState.RUNNING)
        await fx_fakes.store(fx_project, job)
        # The service's unit of work begins now and sees the job running
        service = fx_fakes.job_service()
        worker = InMemoryUnitOfWork(fx_fakes.database)
        await worker.jobs.update(evolve(job, state=JobState.SUCCEEDED))
        await worker.commit()
        with pytest.raises(ConflictError):
            await service.cancel(fx_owner, job.id)
        expect((await fx_fakes.stored_job(job)).state is JobState.SUCCEEDED)
        expect(not fx_fakes.events.published)
        assert_expectations()

    async def test_job_of_another_account_cannot_be_cancelled(
        self, fx_fakes: JobFakes, fx_stranger: Actor, fx_project: Project
    ) -> None:
        """Verify an account cannot cancel a job of a project it does not own.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_stranger: Account owning nothing.
        :type fx_stranger: Actor
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        job = make_job(project_id=fx_project.id)
        await fx_fakes.store(fx_project, job)
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service().cancel(fx_stranger, job.id)
        assert (await fx_fakes.stored_job(job)).state is JobState.QUEUED


class TestEvents:
    """Tests for JobService.events()."""

    async def test_owner_receives_only_project_events(
        self, fx_fakes: JobFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a subscription keeps its project's events published before the first read, and none of another.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        other = make_project(owner_id=fx_owner.account_id)
        await fx_fakes.store(fx_project)
        async with await fx_fakes.job_service().events(fx_owner, fx_project.id) as stream:
            # Published before anything reads the stream, as while the response headers are still being sent
            await fx_fakes.events.publish(ProjectChanged(project_id=other.id))
            await fx_fakes.events.publish(ProjectChanged(project_id=fx_project.id))
            expect(await anext(stream) == ProjectChanged(project_id=fx_project.id))
        expect(fx_fakes.events.open_subscriptions == 0)
        assert_expectations()

    async def test_project_of_another_account_is_not_found(
        self, fx_fakes: JobFakes, fx_stranger: Actor, fx_project: Project
    ) -> None:
        """Verify an account cannot listen to a project it does not own.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_stranger: Account owning nothing.
        :type fx_stranger: Actor
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        await fx_fakes.store(fx_project)
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service().events(fx_stranger, fx_project.id)
        assert not fx_fakes.events.subscribed.is_set()
