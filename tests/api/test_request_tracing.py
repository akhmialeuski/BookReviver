"""Tests for what the application measures once tracing is on.

The tracing middleware times every request as an ``http.request`` span, and the lifespan runs the probe whose
``loop.lag`` ticks show the delay of the event loop.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect
from dishka import Provider, Scope, provide
from fastapi import status

from bookreviver.app.settings import TracingSettings
from bookreviver.domain.enums import SpanAttribute, SpanName
from bookreviver.ports.tracing import Tracer
from tests.helpers.builders import make_record
from tests.helpers.seeding import store_book
from tests.helpers.tracers import AskingTracer

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import httpx

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Actor
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.helpers.recording_tracer import RecordingTracer

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
BOOK_TEMPLATE: str = f'{BOOKS_PATH}/{{book_id}}'
UNKNOWN_PATH: str = '/api/v1/no-such-route'
SPAN_FILE_NAME: str = 'spans.jsonl'
# Many intervals of the probe, so a probe that runs is never the one this limit stops
TICK_LIMIT_SECONDS: float = 5.0


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
def fx_tracer() -> AskingTracer:
    """Build the tracer of the test, which can also tell when a span of a name has started.

    :returns: A tracer with no span recorded.
    :rtype: AskingTracer
    """
    return AskingTracer()


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
        self,
        fx_client: httpx.AsyncClient,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_actor: Actor,
        fx_tracer: RecordingTracer,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the span names the route with its parameter in braces, though the path holds the book's identifier.

        :param fx_client: Client talking to the application with tracing on.
        :type fx_client: httpx.AsyncClient
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_tracer: Tracer the application was given.
        :type fx_tracer: RecordingTracer
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id = await store_book(fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_databases())
        response = await fx_client.get(f'{BOOKS_PATH}/{book_id}')
        (span,) = fx_tracer.named(SpanName.HTTP_REQUEST)
        expect(response.status_code == status.HTTP_200_OK)
        expect(span.attributes[SpanAttribute.ROUTE] == BOOK_TEMPLATE)
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


class TestLoopLagProbe:
    """Tests for the probe of the event-loop lag that the lifespan runs while tracing is on."""

    @pytest.mark.usefixtures('fx_app')
    async def test_running_application_records_ticks_of_the_probe_as_roots(self, fx_tracer: AskingTracer) -> None:
        """Verify the lifespan starts the probe, whose ticks are ``loop.lag`` spans with a lag and no parent.

        Without the probe in the lifespan the lock waits of a run could not be told from the delay of the loop, which
        is what the ticks measure. The application runs inside its lifespan for the whole test.

        :param fx_tracer: Tracer the application was given.
        :type fx_tracer: AskingTracer
        """
        # The tick after the next one starts as the next one ends, so the next one is recorded by then
        second_tick = fx_tracer.ask_after(2, name=SpanName.LOOP_LAG)
        with anyio.fail_after(TICK_LIMIT_SECONDS):
            await second_tick.wait()
        ticks = fx_tracer.named(SpanName.LOOP_LAG)
        expect(len(ticks) > 0, 'No tick was recorded.')
        expect(all(SpanAttribute.LAG_MS in tick.attributes for tick in ticks))
        expect(all(tick.parent is None for tick in ticks))
        assert_expectations()
