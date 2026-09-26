"""Tests for the job routes and the server-sent event stream of a project, with the in-process broker running jobs."""

import json
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, NamedTuple

import anyio
import anyio.lowlevel
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.jobs import EventName
from bookreviver.domain.enums import JobState
from tests.helpers.builders import make_job, make_project, new_account_id

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping

    import httpx
    from fastapi import FastAPI

    from bookreviver.domain.entities import Actor, Job, Project
    from tests.helpers.fakes_imports import ImportFakes

pytestmark = pytest.mark.anyio

JOB_PATH: str = '/api/v1/jobs/{job_id}'
SOURCE_PATH: str = '/api/v1/projects/{project_id}/source'
PDF_FILE: tuple[str, bytes, str] = ('book.pdf', b'%PDF-1.7 fake', 'application/pdf')
PAGE_COUNT: int = 3
STREAM_TIMEOUT_SECONDS: float = 10
EVENT_SEPARATOR: bytes = b'\n\n'
FIELD_SEPARATOR: str = ': '
COMMENT_PREFIX: str = ':'
STATE: str = 'state'
# ASGI message keys and values
TYPE: str = 'type'
BODY: str = 'body'
HTTP: str = 'http'


class StreamedEvent(NamedTuple):
    """One server-sent event as the browser receives it."""

    name: str
    data: dict[str, Any]


class _EventStreamReader:
    """Reads an event stream until an event satisfies ``until``, then disconnects like a closed browser tab.

    httpx's ASGI transport returns a response only after the application finished it, which an event stream never
    does, so this drives the ASGI application directly.
    """

    def __init__(self, until: Callable[[StreamedEvent], bool]) -> None:
        self.events: list[StreamedEvent] = []
        self._until = until
        self._buffer = b''
        self._enough = anyio.Event()
        self._request_sent = False

    async def read(self, app: FastAPI, path: str) -> list[StreamedEvent]:
        """Send a GET request for ``path`` and return the events received until the reader disconnected."""
        scope = {
            TYPE: HTTP,
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'GET',
            'scheme': HTTP,
            'path': path,
            'raw_path': path.encode(),
            'root_path': '',
            'query_string': b'',
            'headers': [(b'host', b'testserver'), (b'accept', b'text/event-stream')],
            'server': ('testserver', 80),
            'client': ('testclient', 50_000),
        }
        with anyio.fail_after(STREAM_TIMEOUT_SECONDS):
            await app(scope, self._receive, self._send)
        return self.events

    async def _receive(self) -> dict[str, Any]:
        """Deliver the empty request body, then wait until enough events arrived and disconnect."""
        if not self._request_sent:
            self._request_sent = True
            return {TYPE: 'http.request', BODY: b'', 'more_body': False}
        await self._enough.wait()
        return {TYPE: 'http.disconnect'}

    async def _send(self, message: MutableMapping[str, Any]) -> None:
        """Split the received body into events."""
        self._buffer += message.get(BODY, b'')
        while EVENT_SEPARATOR in self._buffer:
            block, self._buffer = self._buffer.split(EVENT_SEPARATOR, 1)
            lines = [line for line in block.decode().splitlines() if not line.startswith(COMMENT_PREFIX)]
            fields = dict(line.split(FIELD_SEPARATOR, 1) for line in lines)
            self.events.append(StreamedEvent(name=fields['event'], data=json.loads(fields['data'])))
            if self._until(self.events[-1]):
                self._enough.set()
        # A real server socket write is a suspension point; keep one so the worker task interleaves with the stream
        await anyio.lowlevel.checkpoint()


@pytest.fixture
async def fx_project(fx_import_fakes: ImportFakes, fx_actor: Actor) -> Project:
    """Store a project owned by the signed-in account."""
    project = make_project(owner_id=fx_actor.account_id)
    await fx_import_fakes.store(project)
    return project


@pytest.fixture
async def fx_queued_job(fx_import_fakes: ImportFakes, fx_project: Project) -> Job:
    """Store a queued job of ``fx_project`` that no worker picks up."""
    job = make_job(project_id=fx_project.id)
    uow = InMemoryUnitOfWork(fx_import_fakes.database)
    await uow.jobs.add(job)
    await uow.commit()
    return job


class TestReadJob:
    """Tests for GET /jobs/{job_id}."""

    async def test_owner_reads_job(self, fx_client: httpx.AsyncClient, fx_queued_job: Job) -> None:
        """Verify the owner reads the job's state and progress."""
        response = await fx_client.get(JOB_PATH.format(job_id=fx_queued_job.id))
        expect(response.status_code == HTTPStatus.OK)
        expect((response.json()['id'], response.json()[STATE]) == (str(fx_queued_job.id), JobState.QUEUED))
        assert_expectations()

    async def test_job_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_import_fakes: ImportFakes
    ) -> None:
        """Verify a job of another account's project answers 404."""
        project = make_project(owner_id=new_account_id())
        await fx_import_fakes.store(project)
        job = make_job(project_id=project.id)
        uow = InMemoryUnitOfWork(fx_import_fakes.database)
        await uow.jobs.add(job)
        await uow.commit()
        response = await fx_client.get(JOB_PATH.format(job_id=job.id))
        assert response.status_code == HTTPStatus.NOT_FOUND


class TestCancelJob:
    """Tests for DELETE /jobs/{job_id}."""

    async def test_queued_job_is_cancelled(self, fx_client: httpx.AsyncClient, fx_queued_job: Job) -> None:
        """Verify cancelling a queued job answers with the job in its cancelled state."""
        response = await fx_client.delete(JOB_PATH.format(job_id=fx_queued_job.id))
        expect(response.status_code == HTTPStatus.OK)
        expect(response.json()[STATE] == JobState.CANCELLED)
        assert_expectations()

    async def test_finished_job_is_a_conflict(self, fx_client: httpx.AsyncClient, fx_queued_job: Job) -> None:
        """Verify cancelling a job twice answers the second time with a conflict."""
        await fx_client.delete(JOB_PATH.format(job_id=fx_queued_job.id))
        response = await fx_client.delete(JOB_PATH.format(job_id=fx_queued_job.id))
        assert response.status_code == HTTPStatus.CONFLICT


class TestStreamProjectEvents:
    """Tests for GET /projects/{project_id}/events."""

    async def test_stream_follows_import_until_it_succeeds(
        self, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_import_fakes: ImportFakes, fx_project: Project
    ) -> None:
        """Verify an upload run by the in-process worker streams its job, project and page events in order."""
        fx_import_fakes.inspector.page_count = PAGE_COUNT
        path = fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=str(fx_project.id))
        events: list[StreamedEvent] = []

        def succeeded(event: StreamedEvent) -> bool:
            return event.name == EventName.JOB_CHANGED and JobState(event.data[STATE]) is JobState.SUCCEEDED

        async def listen() -> None:
            events.extend(await _EventStreamReader(succeeded).read(fx_app, path))

        async with anyio.create_task_group() as group:
            group.start_soon(listen)
            # Upload only once the stream listens, so no event is published before it
            await fx_import_fakes.events.subscribed.wait()
            upload = await fx_client.post(SOURCE_PATH.format(project_id=fx_project.id), files=[('files', PDF_FILE)])
            expect(upload.status_code == HTTPStatus.ACCEPTED)

        names = [event.name for event in events]
        job_states = [event.data[STATE] for event in events if event.name == EventName.JOB_CHANGED]
        pages = sorted(event.data['index'] for event in events if event.name == EventName.PAGE_READY)
        expect(names[:3] == [EventName.JOB_CHANGED, EventName.JOB_CHANGED, EventName.PROJECT_CHANGED])
        expect(job_states[:2] == [JobState.QUEUED, JobState.RUNNING])
        expect(job_states[-1] == JobState.SUCCEEDED)
        expect(pages == list(range(PAGE_COUNT)))
        expect(events[-1].data['progress'] == {'done': PAGE_COUNT, 'total': PAGE_COUNT, 'fraction': 1.0})
        assert_expectations()

    async def test_project_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_import_fakes: ImportFakes
    ) -> None:
        """Verify listening to another account's project answers 404 before any stream starts."""
        project = make_project(owner_id=new_account_id())
        await fx_import_fakes.store(project)
        response = await fx_client.get(f'/api/v1/projects/{project.id}/events')
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(not fx_import_fakes.events.subscribed.is_set())
        assert_expectations()
