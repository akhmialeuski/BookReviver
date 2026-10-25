"""In-process event channels: every subscriber of a book gets its own bounded queue.

Progress events of an import reach the browser over SSE, and a browser that stops reading must not stall the job
publishing them. Each subscriber therefore has a queue of ``queue_size`` events, and a full queue drops its oldest
event before taking the new one, so a slow subscriber sees the latest progress and publishers never wait.

A subscription registers its queue when entered, not at the first read, so nothing published between subscribing and
reading is lost, and removes it when left, so a closed stream stops receiving at once.

The channel of a book is made on the first request for its directory name and kept, so the objects of a request, of a
job and of the stream of the browser that open the same book all publish into and read from one channel.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, override

from bookreviver.ports.runtime import BookEvents

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import BookDirectoryName


class InProcessBookEvents(BookEvents):
    """The channel of one book within one process; a slow subscriber loses its oldest events instead of blocking."""

    def __init__(self, *, queue_size: int) -> None:
        """Create the channel with no subscribers.

        :param queue_size: Largest number of undelivered events each subscriber keeps.
        :type queue_size: int
        """
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue[DomainEvent]] = set()

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Put the event into the queue of every subscriber, dropping the oldest from a full queue.

        :param event: Event to deliver.
        :type event: DomainEvent
        """
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @override
    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[AsyncIterator[DomainEvent]]:
        """Register a new queue on entry, and remove it on exit.

        :returns: Generator yielding once the reader of the new queue, which holds every event published since entry.
        :rtype: AsyncGenerator[AsyncIterator[DomainEvent]]
        """
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        try:
            yield _read(queue)
        finally:
            self._subscribers.discard(queue)


class EventChannels:
    """The channels of the books of the process, one for each directory name."""

    def __init__(self, *, queue_size: int) -> None:
        """Start with no channels.

        :param queue_size: Largest number of undelivered events each subscriber of a channel keeps.
        :type queue_size: int
        """
        self._queue_size = queue_size
        self._channels: dict[BookDirectoryName, InProcessBookEvents] = {}

    def open(self, dir_name: BookDirectoryName) -> InProcessBookEvents:
        """Return the channel of the book whose directory has the given name, made on the first call.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: The one channel of the book.
        :rtype: InProcessBookEvents
        """
        if (channel := self._channels.get(dir_name)) is None:
            channel = self._channels[dir_name] = InProcessBookEvents(queue_size=self._queue_size)
        return channel


async def _read(queue: asyncio.Queue[DomainEvent]) -> AsyncIterator[DomainEvent]:
    """Yield the events of one subscriber's queue as they arrive, for as long as the reader asks.

    :param queue: Queue of one subscriber.
    :type queue: asyncio.Queue[DomainEvent]
    :returns: Iterator yielding each event put into the queue.
    :rtype: AsyncIterator[DomainEvent]
    """
    while True:
        yield await queue.get()
