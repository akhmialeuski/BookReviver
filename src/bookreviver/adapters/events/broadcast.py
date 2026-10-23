"""In-process event bus: every subscriber of a book gets its own bounded queue.

Progress events of an import reach the browser over SSE, and a browser that stops reading must not stall the job
publishing them. Each subscriber therefore has a queue of ``queue_size`` events, and a full queue drops its oldest
event before taking the new one, so a slow subscriber sees the latest progress and publishers never wait.

A subscription registers its queue when entered, not at the first read, so nothing published between subscribing and
reading is lost, and removes it when left, so a closed stream stops receiving at once.
"""

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, override

from bookreviver.ports.runtime import EventPublisher, EventStream

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import BookId


class InProcessEventBus(EventPublisher, EventStream):
    """Broadcast within one process; a slow subscriber loses its oldest events instead of blocking publishers."""

    def __init__(self, *, queue_size: int) -> None:
        """Create the bus with no subscribers.

        :param queue_size: Largest number of undelivered events each subscriber keeps.
        :type queue_size: int
        """
        self._queue_size = queue_size
        self._subscribers: defaultdict[BookId, set[asyncio.Queue[DomainEvent]]] = defaultdict(set)

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Put the event into the queue of every subscriber of its book, dropping the oldest from a full queue.

        :param event: Event to deliver, carrying the book it belongs to.
        :type event: DomainEvent
        """
        for queue in self._subscribers[event.book_id]:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @override
    @asynccontextmanager
    async def subscribe(self, book_id: BookId) -> AsyncGenerator[AsyncIterator[DomainEvent]]:
        """Register a new queue for the book on entry, and remove it on exit.

        :param book_id: Book whose events are delivered.
        :type book_id: BookId
        :returns: Generator yielding once the reader of the new queue, which holds every event published since entry.
        :rtype: AsyncGenerator[AsyncIterator[DomainEvent]]
        """
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[book_id].add(queue)
        try:
            yield _read(queue)
        finally:
            self._subscribers[book_id].discard(queue)


async def _read(queue: asyncio.Queue[DomainEvent]) -> AsyncIterator[DomainEvent]:
    """Yield the events of one subscriber's queue as they arrive, for as long as the reader asks.

    :param queue: Queue of one subscriber.
    :type queue: asyncio.Queue[DomainEvent]
    :returns: Iterator yielding each event put into the queue.
    :rtype: AsyncIterator[DomainEvent]
    """
    while True:
        yield await queue.get()
