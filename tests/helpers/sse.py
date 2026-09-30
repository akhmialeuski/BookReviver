"""Reading the server-sent event stream of a project the way a browser does, for the API tests.

httpx's ASGI transport returns a response only after the application finished it, which an event stream never does,
so the reader drives the ASGI application directly: it sends the request, collects the events, and disconnects like a
closed browser tab once it has as many as it was told to wait for.
"""

import json
from typing import TYPE_CHECKING, Any, NamedTuple

import anyio
import anyio.lowlevel

if TYPE_CHECKING:
    from collections.abc import MutableMapping

    from fastapi import FastAPI

STREAM_TIMEOUT_SECONDS: float = 10
EVENT_STREAM_TYPE: str = 'text/event-stream'
HTTP_SCHEME: str = 'http'
# Server-sent event framing
SSE_EVENT_SEPARATOR: bytes = b'\n\n'
SSE_FIELD_SEPARATOR: str = ': '
SSE_COMMENT_PREFIX: str = ':'
SSE_EVENT_FIELD: str = 'event'
SSE_DATA_FIELD: str = 'data'
# ASGI message keys and values
ASGI_TYPE_KEY: str = 'type'
ASGI_BODY_KEY: str = 'body'
ASGI_HTTP_SCOPE: str = 'http'
ASGI_RESPONSE_START: str = 'http.response.start'


class StreamedEvent(NamedTuple):
    """One server-sent event as the browser receives it.

    :ivar name: Value of the event's ``event`` field.
    :ivar data: The event's ``data`` field, parsed as JSON.
    """

    name: str
    data: dict[str, Any]


class EventStreamReader:
    """Reads an event stream until ``count`` events arrived, then disconnects like a closed browser tab.

    :ivar events: Events received so far, in order.
    :ivar status: Status of the response, or None before it started.
    :ivar content_type: Media type of the response, or empty before it started.
    """

    def __init__(self, *, count: int) -> None:
        """Prepare to read ``count`` events.

        :param count: Number of events after which the reader disconnects.
        :type count: int
        """
        self.events: list[StreamedEvent] = []
        self.status: int | None = None
        self.content_type = ''
        self._count = count
        self._buffer = b''
        self._enough = anyio.Event()
        self._request_sent = False

    async def read(self, app: FastAPI, path: str) -> None:
        """Send a GET request for ``path`` and collect events until the reader disconnects.

        :param app: The running application.
        :type app: FastAPI
        :param path: Path of the event stream.
        :type path: str
        """
        scope = {
            ASGI_TYPE_KEY: ASGI_HTTP_SCOPE,
            'asgi': {'version': '3.0'},
            'http_version': '1.1',
            'method': 'GET',
            'scheme': HTTP_SCHEME,
            'path': path,
            'raw_path': path.encode(),
            'root_path': '',
            'query_string': b'',
            'headers': [(b'host', b'testserver'), (b'accept', EVENT_STREAM_TYPE.encode())],
            'server': ('testserver', 80),
            'client': ('testclient', 50_000),
        }
        with anyio.fail_after(STREAM_TIMEOUT_SECONDS):
            await app(scope, self._receive, self._send)

    async def _receive(self) -> dict[str, Any]:
        """Deliver the empty request body, then wait until enough events arrived and disconnect.

        :returns: The next ASGI message from the client.
        :rtype: dict[str, Any]
        """
        if not self._request_sent:
            self._request_sent = True
            return {ASGI_TYPE_KEY: 'http.request', ASGI_BODY_KEY: b'', 'more_body': False}
        await self._enough.wait()
        return {ASGI_TYPE_KEY: 'http.disconnect'}

    async def _send(self, message: MutableMapping[str, Any]) -> None:
        """Record the response start, and split the body into events, skipping comment-only blocks such as pings.

        :param message: ASGI message from the application.
        :type message: MutableMapping[str, Any]
        """
        if message[ASGI_TYPE_KEY] == ASGI_RESPONSE_START:
            self.status = message['status']
            self.content_type = dict(message['headers']).get(b'content-type', b'').decode()
        self._buffer += message.get(ASGI_BODY_KEY, b'')
        while SSE_EVENT_SEPARATOR in self._buffer:
            block, self._buffer = self._buffer.split(SSE_EVENT_SEPARATOR, 1)
            lines = [line for line in block.decode().splitlines() if not line.startswith(SSE_COMMENT_PREFIX)]
            if not lines:
                continue
            fields = dict(line.split(SSE_FIELD_SEPARATOR, 1) for line in lines)
            self.events.append(StreamedEvent(name=fields[SSE_EVENT_FIELD], data=json.loads(fields[SSE_DATA_FIELD])))
            if len(self.events) >= self._count:
                self._enough.set()
        # A socket write is a suspension point of a real server; keep one so the test interleaves with the stream
        await anyio.lowlevel.checkpoint()
