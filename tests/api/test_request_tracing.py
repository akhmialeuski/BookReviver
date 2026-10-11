"""Tests for the ``http.request`` span of the tracing middleware, which the application installs when tracing is on."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect
from dishka import Provider, Scope, provide
from fastapi import status

from bookreviver.adapters.tracing.recording import RecordingTracer
from bookreviver.app.settings import TracingSettings
from bookreviver.domain.enums import SpanAttribute, SpanName
from bookreviver.ports.tracing import Tracer
from tests.helpers.builders import make_project
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Actor

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROJECT_TEMPLATE: str = f'{PROJECTS_PATH}/{{project_id}}'
UNKNOWN_PATH: str = '/api/v1/no-such-route'
SPAN_FILE_NAME: str = 'spans.jsonl'


class RecordingTracerProvider(Provider):
    """Replaces the application's tracer with one the test reads."""

    scope = Scope.APP

    def __init__(self, tracer: RecordingTracer) -> None:
        """Provide ``tracer`` in place of the application's own.

        :param tracer: Tracer the test reads.
        :type tracer: RecordingTracer
        """
        super().__init__()
        self._tracer = tracer

    @provide(override=True)
    def tracer(self) -> Tracer:
        """Return the tracer of the test.

        :returns: The recording tracer given to the constructor.
        :rtype: Tracer
        """
        return self._tracer


@pytest.fixture
def fx_tracer() -> RecordingTracer:
    """Build the tracer of the test.

    :returns: A tracer with no span recorded.
    :rtype: RecordingTracer
    """
    return RecordingTracer()


@pytest.fixture
def fx_settings(fx_settings: Settings, tmp_path: Path) -> Settings:
    """Switch the measurements on, which installs the middleware, naming a file the replaced tracer never writes.

    :param fx_settings: Settings of the suite, with tracing off.
    :type fx_settings: Settings
    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: The same settings with tracing on.
    :rtype: Settings
    """
    tracing = TracingSettings(enabled=True, export_path=tmp_path / SPAN_FILE_NAME)
    return fx_settings.model_copy(update={'tracing': tracing})


@pytest.fixture
def fx_extra_providers(fx_tracer: RecordingTracer) -> Sequence[Provider]:
    """Replace the application's tracer with the recording one.

    :param fx_tracer: Tracer of the test.
    :type fx_tracer: RecordingTracer
    :returns: The provider of the tracer.
    :rtype: Sequence[Provider]
    """
    return [RecordingTracerProvider(fx_tracer)]


class TestRequestSpan:
    """Tests for the span of a request, read from the tracer the application was given."""

    async def test_request_is_timed_under_the_template_of_its_route_not_its_path(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor, fx_tracer: RecordingTracer
    ) -> None:
        """Verify the span names the route with its parameter in braces, though the path holds the project's identifier.

        :param fx_client: Client talking to the application with tracing on.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_tracer: Tracer the application was given.
        :type fx_tracer: RecordingTracer
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)
        response = await fx_client.get(f'{PROJECTS_PATH}/{project.id}')
        (span,) = fx_tracer.named(SpanName.HTTP_REQUEST)
        expect(response.status_code == status.HTTP_200_OK)
        expect(span.attributes[SpanAttribute.ROUTE] == PROJECT_TEMPLATE)
        expect(span.attributes[SpanAttribute.METHOD] == 'GET')
        expect(span.attributes[SpanAttribute.STATUS] == status.HTTP_200_OK)
        assert_expectations()

    async def test_request_with_no_route_has_a_status_and_no_route(
        self, fx_client: httpx.AsyncClient, fx_tracer: RecordingTracer
    ) -> None:
        """Verify a request no route matched is timed with the status of its answer and without a route.

        :param fx_client: Client talking to the application with tracing on.
        :type fx_client: httpx.AsyncClient
        :param fx_tracer: Tracer the application was given.
        :type fx_tracer: RecordingTracer
        """
        response = await fx_client.get(f'{UNKNOWN_PATH}/{uuid4()}')
        (span,) = fx_tracer.named(SpanName.HTTP_REQUEST)
        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect(span.attributes[SpanAttribute.STATUS] == status.HTTP_404_NOT_FOUND)
        expect(SpanAttribute.ROUTE not in span.attributes)
        assert_expectations()
