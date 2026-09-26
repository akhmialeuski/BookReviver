"""Tests for the job service: reading and cancelling jobs, and the event stream of a project."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import JobChanged, ProjectChanged
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_imports import ImportFakes

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from bookreviver.domain.entities import Job, Project

pytestmark = pytest.mark.anyio


@pytest.fixture
def fx_fakes(tmp_path: Path) -> ImportFakes:
    """Build the fakes with their files under the test's directory."""
    return ImportFakes.under(tmp_path)


@pytest.fixture
def fx_owner() -> Actor:
    """Build the account that owns the project."""
    return Actor(account_id=new_account_id())


@pytest.fixture
def fx_stranger() -> Actor:
    """Build an account that owns nothing."""
    return Actor(account_id=new_account_id())


@pytest.fixture
async def fx_project(fx_fakes: ImportFakes, fx_owner: Actor) -> Project:
    """Store a project of ``fx_owner``."""
    project = make_project(owner_id=fx_owner.account_id)
    await fx_fakes.store(project)
    return project


@pytest.fixture
def fx_store_job(fx_fakes: ImportFakes, fx_project: Project) -> Callable[[JobState], Awaitable[Job]]:
    """Return a function that commits a job of ``fx_project`` in the given state."""

    async def store(state: JobState) -> Job:
        job = make_job(project_id=fx_project.id, state=state)
        uow = InMemoryUnitOfWork(fx_fakes.database)
        await uow.jobs.add(job)
        await uow.commit()
        return job

    return store


class TestGet:
    """Tests for JobService.get()."""

    async def test_owner_reads_job(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_store_job: Callable[[JobState], Awaitable[Job]]
    ) -> None:
        """Verify the owner of the project reads its job as stored."""
        job = await fx_store_job(JobState.RUNNING)
        assert await fx_fakes.job_service().get(fx_owner, job.id) == job

    async def test_job_of_another_account_is_not_found(
        self, fx_fakes: ImportFakes, fx_stranger: Actor, fx_store_job: Callable[[JobState], Awaitable[Job]]
    ) -> None:
        """Verify an account cannot read, or learn about, a job of a project it does not own."""
        job = await fx_store_job(JobState.RUNNING)
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service().get(fx_stranger, job.id)


class TestCancel:
    """Tests for JobService.cancel()."""

    @pytest.mark.parametrize('state', [JobState.QUEUED, JobState.RUNNING])
    async def test_active_job_is_cancelled_and_announced(
        self,
        fx_fakes: ImportFakes,
        fx_owner: Actor,
        fx_store_job: Callable[[JobState], Awaitable[Job]],
        state: JobState,
    ) -> None:
        """Verify cancelling a queued or running job stores it as cancelled and publishes the change."""
        job = await fx_store_job(state)
        cancelled = await fx_fakes.job_service().cancel(fx_owner, job.id)
        expect((cancelled.state, cancelled.finished_at) == (JobState.CANCELLED, fx_fakes.clock.now()))
        expect(await InMemoryUnitOfWork(fx_fakes.database).jobs.get(job.id) == cancelled)
        expect(fx_fakes.events.published == [JobChanged(project_id=job.project_id, job=cancelled)])
        assert_expectations()

    @pytest.mark.parametrize('state', [JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED])
    async def test_finished_job_cannot_be_cancelled(
        self,
        fx_fakes: ImportFakes,
        fx_owner: Actor,
        fx_store_job: Callable[[JobState], Awaitable[Job]],
        state: JobState,
    ) -> None:
        """Verify a finished job keeps its final state and the cancellation is refused as a conflict."""
        job = await fx_store_job(state)
        with pytest.raises(ConflictError):
            await fx_fakes.job_service().cancel(fx_owner, job.id)
        assert (await InMemoryUnitOfWork(fx_fakes.database).jobs.get(job.id)).state is state

    async def test_job_of_another_account_cannot_be_cancelled(
        self, fx_fakes: ImportFakes, fx_stranger: Actor, fx_store_job: Callable[[JobState], Awaitable[Job]]
    ) -> None:
        """Verify an account cannot cancel a job of a project it does not own."""
        job = await fx_store_job(JobState.QUEUED)
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service().cancel(fx_stranger, job.id)
        assert (await InMemoryUnitOfWork(fx_fakes.database).jobs.get(job.id)).state is JobState.QUEUED


class TestEvents:
    """Tests for JobService.events()."""

    async def test_owner_receives_project_events(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify the stream yields the events published for the project after it was opened."""
        stream = await fx_fakes.job_service().events(fx_owner, fx_project.id)
        await fx_fakes.events.publish(ProjectChanged(project_id=make_project(owner_id=fx_owner.account_id).id))
        await fx_fakes.events.publish(ProjectChanged(project_id=fx_project.id))
        assert await anext(stream) == ProjectChanged(project_id=fx_project.id)

    async def test_project_of_another_account_is_not_found(
        self, fx_fakes: ImportFakes, fx_stranger: Actor, fx_project: Project
    ) -> None:
        """Verify an account cannot listen to a project it does not own."""
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service().events(fx_stranger, fx_project.id)
        assert not fx_fakes.events.subscribed.is_set()
