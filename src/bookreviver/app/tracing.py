"""Timing of every HTTP request as an ``http.request`` span, by a pure ASGI middleware.

The middleware is installed only when the measurements are on, so an application without them runs no code of it. It
sits outside the CSRF middleware, so a refusal of that one is timed too. The span carries the method, the status of the
answer and the template of the route that served the request, such as ``/api/projects/{project_id}``, never the
concrete path, so the requests of a run group by the route and not by the identifiers.

The module also holds the probe of the event-loop lag. The durations that the spans of the database take are read on the
event loop, so they include the time the loop needed to get back to the coroutine, and the ``loop.lag`` spans of the
probe say how long that was at each moment of the run.
"""

from time import perf_counter
from typing import TYPE_CHECKING

import anyio

from bookreviver.app.security import RESPONSE_START
from bookreviver.app.trace_report import MILLISECONDS_PER_SECOND
from bookreviver.domain.enums import SpanAttribute, SpanName, SpanParent
from bookreviver.ports.tracing import Tracer

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Where FastAPI keeps, in the ASGI scope, the route a request matched with the prefixes of the routers it was included
# by. The ``route`` of Starlette's scope is the route as its own router declared it, without them. These are private
# keys of FastAPI 0.141 (``_FASTAPI_SCOPE_KEY`` and ``_FASTAPI_EFFECTIVE_ROUTE_CONTEXT_KEY`` in fastapi/routing.py), and
# FastAPI has no public way to get the template with the prefixes. The test
# ``test_request_is_timed_under_the_template_of_its_route_not_its_path`` in tests/api/test_request_tracing.py fails
# when they change
FASTAPI_SCOPE_KEY: str = 'fastapi'
EFFECTIVE_ROUTE_KEY: str = 'effective_route_context'
# The key of the type of an ASGI scope or message, and the attribute of a route that holds its path template
ASGI_TYPE: str = 'type'
ROUTE_PATH: str = 'path'
# The type of the scope of an HTTP request, the key of its method, the key of the status in the message that starts the
# answer, and the key of the route Starlette's router puts into the scope
HTTP_SCOPE: str = 'http'
SCOPE_METHOD: str = 'method'
MESSAGE_STATUS: str = 'status'
STARLETTE_ROUTE_KEY: str = 'route'


class TracingMiddleware:
    """Opens an ``http.request`` span around the rest of the application for each HTTP request.

    :ivar app: The ASGI application whose requests are timed.
    """

    def __init__(self, app: ASGIApp, *, container: AsyncContainer) -> None:
        """Time requests of ``app`` with the tracer of the container.

        :param app: The ASGI application to time.
        :type app: ASGIApp
        :param container: Application container, which provides the tracer once it is first asked for.
        :type container: AsyncContainer
        """
        self.app = app
        self._container = container

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Run the request inside a span, which is given the status of the answer and the route that served it.

        :param scope: The ASGI scope of the connection.
        :type scope: Scope
        :param receive: The ASGI receive function of the server.
        :type receive: Receive
        :param send: The ASGI send function of the server.
        :type send: Send
        """
        if scope[ASGI_TYPE] != HTTP_SCOPE:
            await self.app(scope, receive, send)
            return
        tracer = await self._container.get(Tracer)
        with tracer.span(SpanName.HTTP_REQUEST, {SpanAttribute.METHOD: scope[SCOPE_METHOD]}) as span:

            async def send_with_status(message: Message) -> None:
                """Record the status when the answer starts, then send the message on.

                :param message: The ASGI message being sent.
                :type message: Message
                """
                if message[ASGI_TYPE] == RESPONSE_START:
                    span.set_attribute(SpanAttribute.STATUS, message[MESSAGE_STATUS])
                await send(message)

            try:
                await self.app(scope, receive, send_with_status)
            finally:
                # The router puts the route into the scope while it matches, so it is read once the request is done
                effective = scope.get(FASTAPI_SCOPE_KEY, {}).get(EFFECTIVE_ROUTE_KEY)
                path = getattr(effective, ROUTE_PATH, None) or getattr(scope.get(STARLETTE_ROUTE_KEY), ROUTE_PATH, None)
                if path is not None:
                    span.set_attribute(SpanAttribute.ROUTE, path)


class LoopLagProbe:
    """Measures how late the event loop wakes a sleeper, as one ``loop.lag`` span for each tick.

    A tick sleeps for the interval and records how much longer than the interval the sleep took. A loop that is busy
    for 80 ms with a callback or a blocking call wakes the sleeper 80 ms late, so the lag is the delay every other
    coroutine of the process saw at that moment, the waits of the write blocks included. A tick has no parent, since the
    probe is started from the lifespan of the application and belongs to no request.
    """

    def __init__(self, *, tracer: Tracer, interval_seconds: float) -> None:
        """Probe with ``tracer`` once for every ``interval_seconds``.

        :param tracer: Tracer the ticks are recorded with.
        :type tracer: Tracer
        :param interval_seconds: How long a tick sleeps, which is the resolution of the probe.
        :type interval_seconds: float
        """
        self._tracer = tracer
        self._interval_seconds = interval_seconds

    async def run(self) -> None:
        """Record one span for each tick, for ever; the caller stops the probe by cancelling it."""
        while True:
            span = self._tracer.start(SpanName.LOOP_LAG, parent=SpanParent.NONE)
            started_at = perf_counter()
            await anyio.sleep(self._interval_seconds)
            overrun = max(0.0, perf_counter() - started_at - self._interval_seconds)
            span.set_attribute(SpanAttribute.LAG_MS, overrun * MILLISECONDS_PER_SECOND)
            span.end()
