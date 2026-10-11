"""Timing of every HTTP request as an ``http.request`` span, by a pure ASGI middleware.

The middleware is installed only when the measurements are on, so an application without them runs no code of it. It
sits outside the CSRF middleware, so a refusal of that one is timed too. The span carries the method, the status of the
answer and the template of the route that served the request, such as ``/api/projects/{project_id}``, never the
concrete path, so the requests of a run group by the route and not by the identifiers.
"""

from typing import TYPE_CHECKING

from bookreviver.app.security import RESPONSE_START
from bookreviver.domain.enums import SpanAttribute, SpanName
from bookreviver.ports.tracing import Tracer

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Where FastAPI keeps, in the ASGI scope, the route a request matched with the prefixes of the routers it was included
# by. The ``route`` of Starlette's scope is the route as its own router declared it, without them
FASTAPI_SCOPE_KEY: str = 'fastapi'
EFFECTIVE_ROUTE_KEY: str = 'effective_route_context'
# The key of the type of an ASGI scope or message, and the attribute of a route that holds its path template
ASGI_TYPE: str = 'type'
ROUTE_PATH: str = 'path'


class TracingMiddleware:
    """Opens an ``http.request`` span around the rest of the application for each HTTP request."""

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
        if scope[ASGI_TYPE] != 'http':
            await self.app(scope, receive, send)
            return
        tracer = await self._container.get(Tracer)
        with tracer.span(SpanName.HTTP_REQUEST, {SpanAttribute.METHOD: scope['method']}) as span:

            async def send_with_status(message: Message) -> None:
                """Record the status when the answer starts, then send the message on.

                :param message: The ASGI message being sent.
                :type message: Message
                """
                if message[ASGI_TYPE] == RESPONSE_START:
                    span.set_attribute(SpanAttribute.STATUS, message['status'])
                await send(message)

            try:
                await self.app(scope, receive, send_with_status)
            finally:
                # The router puts the route into the scope while it matches, so it is read once the request is done
                effective = scope.get(FASTAPI_SCOPE_KEY, {}).get(EFFECTIVE_ROUTE_KEY)
                path = getattr(effective, ROUTE_PATH, None) or getattr(scope.get('route'), ROUTE_PATH, None)
                if path is not None:
                    span.set_attribute(SpanAttribute.ROUTE, path)
