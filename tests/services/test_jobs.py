"""Tests for the job service: reading and cancelling jobs on the board, and the event stream of a project."""

from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobKind, JobState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import JobChanged, ProjectChanged
from bookreviver.domain.ids import JobId
from bookreviver.domain.values import SliceRequest
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_jobs import JobFakes
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Project
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

WINDOW: SliceRequest = SliceRequest(limit=50)
STAGED_UPLOAD_NAME: str = 'book.pdf'
UPLOAD_LIMIT_BYTES: int = 1024
UNKNOWN_JOB: JobId = JobId(new_account_id())


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the job service runs on.

    :returns: Fakes with a board with no job and nothing published.
    :rtype: JobFakes
    """
    return JobFakes()


@pytest.fixture
def fx_sources(tmp_path: Path) -> LocalSourceStore:
    """Build the source store that holds the uploads of the imports of the test.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: Local source store over the directory.
    :rtype: LocalSourceStore
    """
    return LocalSourceStore(root=tmp_path)


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
async def fx_project(fx_fakes: JobFakes, fx_database: SqlDatabase, fx_owner: Actor) -> Project:
    """Store a project of ``fx_owner``, and the account of the owner.

    :param fx_fakes: Adapters of the service.
    :type fx_fakes: JobFakes
    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_owner: Account owning the project.
    :type fx_owner: Actor
    :returns: The stored project.
    :rtype: Project
    """
    project = make_project(owner_id=fx_owner.account_id)
    await fx_fakes.store(fx_database, project)
    return project


class TestGet:
    """Tests for JobService.get()."""

    async def test_owner_reads_job(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify the owner of the project reads its job as the board holds it.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id, kind=JobKind.RUN_STAGE))
        assert await fx_fakes.job_service(fx_uow(), fx_sources).get(fx_owner, job.id) == job

    async def test_job_of_another_account_is_not_found(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_stranger: Actor,
        fx_project: Project,
    ) -> None:
        """Verify an account cannot read, or learn about, a job of a project it does not own.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_stranger: Account owning nothing.
        :type fx_stranger: Actor
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id))
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service(fx_uow(), fx_sources).get(fx_stranger, job.id)

    async def test_job_the_board_does_not_hold_is_not_found(
        self, fx_fakes: JobFakes, fx_uow: UnitOfWorkFactory, fx_sources: LocalSourceStore, fx_owner: Actor
    ) -> None:
        """Verify a job the process does not hold, such as one of before a restart, is reported as missing.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account asking for the job.
        :type fx_owner: Actor
        """
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service(fx_uow(), fx_sources).get(fx_owner, UNKNOWN_JOB)


class TestList:
    """Tests for JobService.list_for_project()."""

    async def test_lists_the_jobs_of_the_project_newest_first_and_the_active_ones_alone(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify the list holds the jobs of the project, newest first, and ``active`` leaves the ended ones out.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        ended = fx_fakes.board.put(make_job(project_id=fx_project.id, minutes=0))
        await fx_fakes.board.run_queued()
        queued = fx_fakes.board.put(make_job(project_id=fx_project.id, minutes=1))
        elsewhere = fx_fakes.board.put(make_job(project_id=make_project(owner_id=fx_owner.account_id).id, minutes=2))
        service = fx_fakes.job_service(fx_uow(), fx_sources)
        everything = await service.list_for_project(fx_owner, fx_project.id, WINDOW, active=False)
        active = await service.list_for_project(fx_owner, fx_project.id, WINDOW, active=True)
        expect([job.id for job in everything.items] == [queued.id, ended.id])
        expect(everything.total == len(everything.items))
        expect([job.id for job in active.items] == [queued.id])
        expect(elsewhere.id not in {job.id for job in everything.items})
        assert_expectations()

    async def test_project_of_another_account_is_not_found(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_stranger: Actor,
        fx_project: Project,
    ) -> None:
        """Verify an account cannot list the jobs of a project it does not own.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_stranger: Account owning nothing.
        :type fx_stranger: Actor
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service(fx_uow(), fx_sources).list_for_project(
                fx_stranger, fx_project.id, WINDOW, active=False
            )


class TestCancel:
    """Tests for JobService.cancel()."""

    async def test_queued_job_is_cancelled_announced_and_never_runs(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify cancelling a queued job drops it from the queue, publishes the change, and the job never runs.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id, kind=JobKind.RUN_STAGE))
        cancelled = await fx_fakes.job_service(fx_uow(), fx_sources).cancel(fx_owner, job.id)
        await fx_fakes.board.run_queued()
        expect((cancelled.state, cancelled.finished_at) == (JobState.CANCELLED, fx_fakes.clock.now()))
        expect(fx_fakes.board.get(job.id) == cancelled)
        expect(fx_fakes.events.published == [JobChanged(project_id=job.project_id, job=cancelled)])
        expect(fx_fakes.scripted.begun() == [])
        assert_expectations()

    async def test_running_job_is_cancelled_and_stops_at_its_next_task(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify cancelling a running job answers at once, and the job ends cancelled once its task has returned.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id, kind=JobKind.RUN_STAGE))
        release = fx_fakes.scripted.hold(job)
        running = cancelled = None
        async with anyio.create_task_group() as group:
            group.start_soon(fx_fakes.board.run_queued)
            await anyio.wait_all_tasks_blocked()
            running = fx_fakes.board.get(job.id).state
            cancelled = await fx_fakes.job_service(fx_uow(), fx_sources).cancel(fx_owner, job.id)
            release.set()
        expect(running is JobState.RUNNING)
        expect(cancelled is not None and cancelled.state is JobState.CANCELLED)
        expect(fx_fakes.board.get(job.id).state is JobState.CANCELLED)
        expect(fx_fakes.board.get(job.id).progress.done == 0)
        assert_expectations()

    async def test_import_cancelled_before_it_began_loses_its_upload(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify the upload of an import that never ran is removed, since nothing else would remove it.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id, kind=JobKind.IMPORT_SOURCE))
        await fx_sources.stage(fx_project.id, job.id, [upload(STAGED_UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
        await fx_fakes.job_service(fx_uow(), fx_sources).cancel(fx_owner, job.id)
        with pytest.raises(NotFoundError):
            async with fx_sources.staged_files(fx_project.id, job.id):
                pass

    async def test_import_cancelled_while_running_keeps_its_upload_for_its_own_run_to_remove(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify the service leaves the upload of a running import alone, which its run still reads.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id, kind=JobKind.IMPORT_SOURCE))
        await fx_sources.stage(fx_project.id, job.id, [upload(STAGED_UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
        release = fx_fakes.scripted.hold(job)
        names: list[str] = []
        async with anyio.create_task_group() as group:
            group.start_soon(fx_fakes.board.run_queued)
            await anyio.wait_all_tasks_blocked()
            await fx_fakes.job_service(fx_uow(), fx_sources).cancel(fx_owner, job.id)
            async with fx_sources.staged_files(fx_project.id, job.id) as staged:
                names.extend(staged)
            release.set()
        expect(names == [STAGED_UPLOAD_NAME])

    async def test_finished_job_cannot_be_cancelled(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify a finished job keeps its final state, nothing more is published, and the refusal is a conflict.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id))
        await fx_fakes.board.run_queued()
        published = list(fx_fakes.events.published)
        with pytest.raises(ConflictError):
            await fx_fakes.job_service(fx_uow(), fx_sources).cancel(fx_owner, job.id)
        expect(fx_fakes.board.get(job.id).state is JobState.SUCCEEDED)
        expect(fx_fakes.events.published == published)
        assert_expectations()

    async def test_job_of_another_account_cannot_be_cancelled(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_stranger: Actor,
        fx_project: Project,
    ) -> None:
        """Verify an account cannot cancel a job of a project it does not own.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_stranger: Account owning nothing.
        :type fx_stranger: Actor
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        job = fx_fakes.board.put(make_job(project_id=fx_project.id))
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service(fx_uow(), fx_sources).cancel(fx_stranger, job.id)
        assert fx_fakes.board.get(job.id).state is JobState.QUEUED


class TestEvents:
    """Tests for JobService.events()."""

    async def test_owner_receives_only_project_events(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_owner: Actor,
        fx_project: Project,
    ) -> None:
        """Verify a subscription keeps its project's events published before the first read, and none of another.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_owner: Account owning the project.
        :type fx_owner: Actor
        :param fx_project: Project of ``fx_owner``.
        :type fx_project: Project
        """
        other = make_project(owner_id=fx_owner.account_id)
        async with await fx_fakes.job_service(fx_uow(), fx_sources).events(fx_owner, fx_project.id) as stream:
            # Published before anything reads the stream, as while the response headers are still being sent
            await fx_fakes.events.publish(ProjectChanged(project_id=other.id))
            await fx_fakes.events.publish(ProjectChanged(project_id=fx_project.id))
            expect(await anext(stream) == ProjectChanged(project_id=fx_project.id))
        expect(fx_fakes.events.open_subscriptions == 0)
        assert_expectations()

    async def test_project_of_another_account_is_not_found(
        self,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_sources: LocalSourceStore,
        fx_stranger: Actor,
        fx_project: Project,
    ) -> None:
        """Verify an account cannot listen to a project it does not own, and that no subscription is made for it.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_sources: Store of the uploads.
        :type fx_sources: LocalSourceStore
        :param fx_stranger: Account owning nothing.
        :type fx_stranger: Actor
        :param fx_project: Project of another account.
        :type fx_project: Project
        """
        with pytest.raises(NotFoundError):
            await fx_fakes.job_service(fx_uow(), fx_sources).events(fx_stranger, fx_project.id)
        expect(not fx_fakes.events.subscribed.is_set())
        assert_expectations()
