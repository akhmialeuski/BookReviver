"""Tests for the job routes and the server-sent event stream of a project."""

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.jobs import EventName
from bookreviver.domain.enums import JobKind, JobState, PageChange, Stage, StageState
from bookreviver.domain.events import (
    PagesChanged,
    PageStageChanged,
    PageVersionReady,
    ProjectChanged,
    ScanReady,
    SourceImported,
)
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import JobEnd, StageRun
from tests.helpers.builders import (
    make_job,
    make_page_stage,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.scripted_runner import TASKS_PER_JOB
from tests.helpers.seeding import commit_project, store_project
from tests.helpers.sse import EVENT_STREAM_TYPE, STREAM_TIMEOUT_SECONDS, EventStreamReader, StreamedEvent

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor, Job
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.fakes_jobs import JobFakes

pytestmark = pytest.mark.anyio

JOB_PATH: str = '/api/v1/jobs/{job_id}'
JOBS_PATH: str = '/api/v1/projects/{project_id}/jobs'
ACTIVE_PARAMETER: str = 'active'
CONTENT_TYPE_HEADER: str = 'content-type'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
# Fields of the job, the page of jobs and project-changed schemas in a JSON body
JOB_ID_FIELD: str = 'id'
JOB_STATE_FIELD: str = 'state'
PROJECT_ID_FIELD: str = 'project_id'
PAGE_ID_FIELD: str = 'page_id'
KIND_FIELD: str = 'kind'
STAGE_FIELD: str = 'stage'
PROGRESS_FIELD: str = 'progress'
DONE_FIELD: str = 'done'
TOTAL_FIELD: str = 'total'
FRACTION_FIELD: str = 'fraction'
ERROR_FIELD: str = 'error'
ITEMS_FIELD: str = 'items'


@pytest.fixture
async def fx_queued_job(fx_fakes: JobFakes, fx_uow: UnitOfWorkFactory, fx_actor: Actor) -> Job:
    """Store a project of the signed-in account with a queued job that nothing runs.

    :param fx_fakes: Adapters the application runs on.
    :type fx_fakes: JobFakes
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The queued job.
    :rtype: Job
    """
    project = make_project()
    await store_project(fx_uow(), project, owner=fx_actor.account_id)
    return fx_fakes.board.put(make_job(project_id=project.id))


@pytest.fixture
async def fx_strangers_job(fx_fakes: JobFakes, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory) -> Job:
    """Store a project of another account with a queued job.

    :param fx_fakes: Adapters the application runs on.
    :type fx_fakes: JobFakes
    :param fx_database: Database of the test, which the application runs on.
    :type fx_database: SqlDatabase
    :param fx_uow: Function opening a new unit of work over the database of the test.
    :type fx_uow: UnitOfWorkFactory
    :returns: The queued job of the other account.
    :rtype: Job
    """
    project = make_project()
    await commit_project(fx_database, fx_uow(), project, owner=new_account_id())
    return fx_fakes.board.put(make_job(project_id=project.id))


class TestReadJob:
    """Tests for GET /jobs/{job_id}."""

    async def test_owner_reads_job(self, fx_client: httpx.AsyncClient, fx_queued_job: Job) -> None:
        """Verify the owner reads the job's state and progress.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_queued_job: Queued job of the signed-in account.
        :type fx_queued_job: Job
        """
        response = await fx_client.get(JOB_PATH.format(job_id=fx_queued_job.id))
        expect(response.status_code == HTTPStatus.OK)
        body = response.json()
        expect((body[JOB_ID_FIELD], body[JOB_STATE_FIELD]) == (str(fx_queued_job.id), JobState.QUEUED))
        expect(body[PROGRESS_FIELD] == {DONE_FIELD: 0, TOTAL_FIELD: 0, FRACTION_FIELD: 0.0})
        assert_expectations()

    async def test_run_stage_job_names_its_stage_and_an_import_job_does_not(
        self,
        fx_client: httpx.AsyncClient,
        fx_fakes: JobFakes,
        fx_uow: UnitOfWorkFactory,
        fx_actor: Actor,
        fx_queued_job: Job,
    ) -> None:
        """Verify the job of a stage run carries its stage and the import job carries none.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_queued_job: Queued import job of the signed-in account.
        :type fx_queued_job: Job
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)
        run = fx_fakes.board.put(
            make_job(project_id=project.id, kind=JobKind.RUN_STAGE, params=StageRun(stage=Stage.GEOMETRY).to_map())
        )
        run_body = (await fx_client.get(JOB_PATH.format(job_id=run.id))).json()
        import_body = (await fx_client.get(JOB_PATH.format(job_id=fx_queued_job.id))).json()
        expect((run_body[KIND_FIELD], run_body[STAGE_FIELD]) == (JobKind.RUN_STAGE, Stage.GEOMETRY))
        expect((import_body[KIND_FIELD], import_body[STAGE_FIELD]) == (JobKind.IMPORT_SOURCE, None))
        assert_expectations()

    async def test_job_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_strangers_job: Job
    ) -> None:
        """Verify a job of another account's project answers 404.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_strangers_job: Job of another account.
        :type fx_strangers_job: Job
        """
        response = await fx_client.get(JOB_PATH.format(job_id=fx_strangers_job.id))
        assert response.status_code == HTTPStatus.NOT_FOUND

    async def test_job_the_process_does_not_hold_is_a_404_problem(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a job the board does not hold, such as one of before a restart, answers 404 as a problem document.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get(JOB_PATH.format(job_id=uuid4()))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(response.headers[CONTENT_TYPE_HEADER] == PROBLEM_MEDIA_TYPE)
        assert_expectations()


class TestListJobs:
    """Tests for GET /projects/{project_id}/jobs."""

    async def test_lists_the_jobs_of_the_project_newest_first_and_the_active_ones_alone(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_queued_job: Job
    ) -> None:
        """Verify the list answers the jobs of the project the board holds, newest first, and ``active`` drops the ended.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_queued_job: Queued job of the signed-in account.
        :type fx_queued_job: Job
        """
        await fx_fakes.board.run_queued()
        later = fx_fakes.board.put(make_job(project_id=fx_queued_job.project_id, minutes=1))
        path = JOBS_PATH.format(project_id=fx_queued_job.project_id)
        everything = (await fx_client.get(path)).json()
        active = (await fx_client.get(path, params={ACTIVE_PARAMETER: 'true'})).json()
        expect([job[JOB_ID_FIELD] for job in everything[ITEMS_FIELD]] == [str(later.id), str(fx_queued_job.id)])
        expect([job[JOB_ID_FIELD] for job in active[ITEMS_FIELD]] == [str(later.id)])
        assert_expectations()


class TestCancelJob:
    """Tests for DELETE /jobs/{job_id}."""

    async def test_queued_job_is_cancelled(self, fx_client: httpx.AsyncClient, fx_queued_job: Job) -> None:
        """Verify cancelling a queued job answers with the job in its cancelled state.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_queued_job: Queued job of the signed-in account.
        :type fx_queued_job: Job
        """
        response = await fx_client.delete(JOB_PATH.format(job_id=fx_queued_job.id))
        expect(response.status_code == HTTPStatus.OK)
        expect(response.json()[JOB_STATE_FIELD] == JobState.CANCELLED)
        assert_expectations()

    async def test_finished_job_is_a_conflict(self, fx_client: httpx.AsyncClient, fx_queued_job: Job) -> None:
        """Verify cancelling a job twice answers the second time with a conflict problem.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_queued_job: Queued job of the signed-in account.
        :type fx_queued_job: Job
        """
        await fx_client.delete(JOB_PATH.format(job_id=fx_queued_job.id))
        response = await fx_client.delete(JOB_PATH.format(job_id=fx_queued_job.id))
        expect(response.status_code == HTTPStatus.CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER] == PROBLEM_MEDIA_TYPE)
        assert_expectations()

    async def test_job_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_strangers_job: Job
    ) -> None:
        """Verify cancelling a job of another account's project answers 404 and leaves the job queued.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_strangers_job: Job of another account.
        :type fx_strangers_job: Job
        """
        response = await fx_client.delete(JOB_PATH.format(job_id=fx_strangers_job.id))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(fx_fakes.board.get(fx_strangers_job.id).state is JobState.QUEUED)
        assert_expectations()


class TestStreamProjectEvents:
    """Tests for GET /projects/{project_id}/events."""

    async def test_stream_delivers_job_and_project_changes(
        self, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_queued_job: Job
    ) -> None:
        """Verify a cancellation and a description change reach the stream as named events, in order.

        The subscription is in place before the response starts, and closed as soon as the browser disconnects.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_queued_job: Queued job of the signed-in account.
        :type fx_queued_job: Job
        """
        project_id = fx_queued_job.project_id
        reader = EventStreamReader(count=2)
        async with anyio.create_task_group() as group:
            group.start_soon(reader.read, fx_app, fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=project_id))
            # Change things only once the stream has subscribed, which happens before the endpoint runs
            await fx_fakes.events.subscribed.wait()
            await fx_client.delete(JOB_PATH.format(job_id=fx_queued_job.id))
            await fx_fakes.events.publish(ProjectChanged(project_id=project_id))

        expect((reader.status, reader.content_type.split(';')[0]) == (HTTPStatus.OK, EVENT_STREAM_TYPE))
        expect([event.name for event in reader.events] == [EventName.JOB_CHANGED, EventName.PROJECT_CHANGED])
        expect(reader.events[0].data[JOB_ID_FIELD] == str(fx_queued_job.id))
        expect(reader.events[0].data[JOB_STATE_FIELD] == JobState.CANCELLED)
        expect(reader.events[1].data == {PROJECT_ID_FIELD: str(project_id)})
        expect(fx_fakes.events.open_subscriptions == 0)
        assert_expectations()

    async def test_stream_delivers_the_progress_of_a_running_job(
        self, fx_app: FastAPI, fx_fakes: JobFakes, fx_queued_job: Job
    ) -> None:
        """Verify a job that runs is announced as running, and the tasks it plans reach the stream as its progress.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_queued_job: Queued job of the signed-in account, which a runner holds inside its first task.
        :type fx_queued_job: Job
        """
        release = fx_fakes.scripted.hold(fx_queued_job)
        reader = EventStreamReader(count=2)
        async with anyio.create_task_group() as group:
            group.start_soon(
                reader.read, fx_app, fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=fx_queued_job.project_id)
            )
            await fx_fakes.events.subscribed.wait()
            group.start_soon(fx_fakes.board.run_queued)
            # The runner holds the job inside its first task, once the board has announced it and its plan
            await anyio.wait_all_tasks_blocked()
            release.set()
        running, planned = (event.data for event in reader.events)
        expect([event.name for event in reader.events] == [EventName.JOB_CHANGED] * 2)
        expect((running[JOB_ID_FIELD], running[JOB_STATE_FIELD]) == (str(fx_queued_job.id), JobState.RUNNING))
        expect(planned[PROGRESS_FIELD][TOTAL_FIELD] == TASKS_PER_JOB)
        assert_expectations()

    async def test_stream_delivers_the_end_of_a_job(
        self, fx_app: FastAPI, fx_fakes: JobFakes, fx_queued_job: Job
    ) -> None:
        """Verify every task of a job reaches the stream, and the last event is the job as succeeded with its tasks done.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_queued_job: Queued job of the signed-in account.
        :type fx_queued_job: Job
        """
        # The running state, the three tasks, and the end
        reader = EventStreamReader(count=TASKS_PER_JOB + 2)
        async with anyio.create_task_group() as group:
            group.start_soon(
                reader.read, fx_app, fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=fx_queued_job.project_id)
            )
            await fx_fakes.events.subscribed.wait()
            await fx_fakes.board.run_queued()
        done = [event.data[PROGRESS_FIELD][DONE_FIELD] for event in reader.events]
        expect(done == [0, *range(1, TASKS_PER_JOB + 1), TASKS_PER_JOB])
        expect(reader.events[-1].data[JOB_STATE_FIELD] == JobState.SUCCEEDED)
        expect(reader.events[-1].data[PROGRESS_FIELD][FRACTION_FIELD] == pytest.approx(1.0))
        assert_expectations()

    async def test_stream_delivers_the_failure_of_a_job_with_its_reason(
        self, fx_app: FastAPI, fx_fakes: JobFakes, fx_queued_job: Job
    ) -> None:
        """Verify a job that stops on an error nobody expected reaches the stream as failed, with the standard reason.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_queued_job: Queued job of the signed-in account, which the runner is told to break.
        :type fx_queued_job: Job
        """
        fx_fakes.scripted.fail(fx_queued_job)
        reader = EventStreamReader(count=2)
        async with anyio.create_task_group() as group:
            group.start_soon(
                reader.read, fx_app, fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=fx_queued_job.project_id)
            )
            await fx_fakes.events.subscribed.wait()
            await fx_fakes.board.run_queued()
        failed = reader.events[-1].data
        expect(failed[JOB_STATE_FIELD] == JobState.FAILED)
        expect(failed[ERROR_FIELD] == JobEnd.UNEXPECTED_FAILURE)
        assert_expectations()

    async def test_stream_names_the_changes_of_the_book(
        self, fx_app: FastAPI, fx_fakes: JobFakes, fx_queued_job: Job
    ) -> None:
        """Verify an imported source, a ready scan, changed pages and a ready version reach the stream by identifier.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_queued_job: Queued job of the signed-in account, whose project the events belong to.
        :type fx_queued_job: Job
        """
        project_id = fx_queued_job.project_id
        source = make_source(project_id=project_id)
        scan = make_scan(source=source, number=0)
        page_id = PageId(uuid4())
        version = make_page_version(page_id=page_id)
        events = [
            SourceImported(project_id=project_id, source=source),
            ScanReady(project_id=project_id, scan=scan),
            PagesChanged(project_id=project_id, page_ids=[page_id], change=PageChange.MOVED),
            PageVersionReady(project_id=project_id, version=version),
            PageStageChanged(
                project_id=project_id,
                stage=make_page_stage(page_id=page_id, head_version_id=None, state=StageState.STALE),
            ),
        ]
        reader = EventStreamReader(count=len(events))
        async with anyio.create_task_group() as group:
            group.start_soon(reader.read, fx_app, fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=project_id))
            await fx_fakes.events.subscribed.wait()
            for event in events:
                await fx_fakes.events.publish(event)

        project = {PROJECT_ID_FIELD: str(project_id)}
        assert reader.events == [
            StreamedEvent(EventName.SOURCE_IMPORTED, {**project, 'source_id': str(source.id)}),
            StreamedEvent(EventName.SCAN_READY, {**project, 'scan_id': str(scan.id)}),
            StreamedEvent(
                EventName.PAGES_CHANGED, {**project, 'page_ids': [str(page_id)], 'change': PageChange.MOVED.value}
            ),
            StreamedEvent(
                EventName.PAGE_VERSION_READY, {**project, PAGE_ID_FIELD: str(version.page_id), 'version_id': version.id}
            ),
            StreamedEvent(
                EventName.PAGE_STAGE_CHANGED,
                {
                    **project,
                    PAGE_ID_FIELD: str(page_id),
                    STAGE_FIELD: Stage.GEOMETRY.value,
                    'recipe_id': None,
                    'head_version_id': None,
                    'state': StageState.STALE.value,
                    'through_step': None,
                },
            ),
        ]

    async def test_project_of_another_account_is_not_found(
        self, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_strangers_job: Job
    ) -> None:
        """Verify listening to another account's project answers 404 before any stream starts.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_strangers_job: Job of another account, whose project is listened to.
        :type fx_strangers_job: Job
        """
        path = fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=fx_strangers_job.project_id)
        # A stream that started by mistake would never end, so the request is bounded
        with anyio.fail_after(STREAM_TIMEOUT_SECONDS):
            response = await fx_client.get(path)
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(not fx_fakes.events.subscribed.is_set())
        assert_expectations()
