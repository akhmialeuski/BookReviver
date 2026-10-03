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
from bookreviver.domain.values import StageRun
from tests.helpers.builders import (
    make_job,
    make_page_stage,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.sse import EVENT_STREAM_TYPE, STREAM_TIMEOUT_SECONDS, EventStreamReader, StreamedEvent

if TYPE_CHECKING:
    import httpx
    from fastapi import FastAPI

    from bookreviver.domain.entities import Actor, Job
    from tests.helpers.fakes_jobs import JobFakes

pytestmark = pytest.mark.anyio

JOB_PATH: str = '/api/v1/jobs/{job_id}'
# Fields of the job and project-changed schemas in a JSON body
JOB_ID_FIELD: str = 'id'
JOB_STATE_FIELD: str = 'state'
PROJECT_ID_FIELD: str = 'project_id'


@pytest.fixture
async def fx_queued_job(fx_fakes: JobFakes, fx_actor: Actor) -> Job:
    """Store a project of the signed-in account with a queued job that no worker picks up.

    :param fx_fakes: Adapters the application runs on.
    :type fx_fakes: JobFakes
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The queued job.
    :rtype: Job
    """
    project = make_project(owner_id=fx_actor.account_id)
    job = make_job(project_id=project.id)
    await fx_fakes.store(project, job)
    return job


@pytest.fixture
async def fx_strangers_job(fx_fakes: JobFakes) -> Job:
    """Store a project of another account with a queued job.

    :param fx_fakes: Adapters the application runs on.
    :type fx_fakes: JobFakes
    :returns: The queued job of the other account.
    :rtype: Job
    """
    project = make_project(owner_id=new_account_id())
    job = make_job(project_id=project.id)
    await fx_fakes.store(project, job)
    return job


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
        expect(body['progress'] == {'done': 0, 'total': 0, 'fraction': 0.0})
        assert_expectations()

    async def test_run_stage_job_names_its_stage_and_an_import_job_does_not(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_actor: Actor, fx_queued_job: Job
    ) -> None:
        """Verify the job of a stage run carries its stage and the import job carries none.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_queued_job: Queued import job of the signed-in account.
        :type fx_queued_job: Job
        """
        project = make_project(owner_id=fx_actor.account_id)
        run = make_job(project_id=project.id, kind=JobKind.RUN_STAGE, params=StageRun(stage=Stage.GEOMETRY).to_map())
        await fx_fakes.store(project, run)
        run_body = (await fx_client.get(JOB_PATH.format(job_id=run.id))).json()
        import_body = (await fx_client.get(JOB_PATH.format(job_id=fx_queued_job.id))).json()
        expect((run_body['kind'], run_body['stage']) == (JobKind.RUN_STAGE, Stage.GEOMETRY))
        expect((import_body['kind'], import_body['stage']) == (JobKind.IMPORT_SOURCE, None))
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
        expect(response.headers['content-type'] == 'application/problem+json')
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
        expect((await fx_fakes.stored_job(fx_strangers_job)).state is JobState.QUEUED)
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
                EventName.PAGE_VERSION_READY, {**project, 'page_id': str(version.page_id), 'version_id': version.id}
            ),
            StreamedEvent(
                EventName.PAGE_STAGE_CHANGED,
                {
                    **project,
                    'page_id': str(page_id),
                    'stage': Stage.GEOMETRY.value,
                    'recipe_id': None,
                    'head_version_id': None,
                    'state': StageState.STALE.value,
                    'pinned': False,
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
