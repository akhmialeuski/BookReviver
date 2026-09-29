"""In-process event bus: every subscriber of a project gets its own bounded queue.

Progress events of an import reach the browser over SSE, and a browser that stops reading must not stall the job
publishing them. Each subscriber therefore has a queue of ``queue_size`` events, and a full queue drops its oldest
event before taking the new one, so a slow subscriber sees the latest progress and publishers never wait.
"""

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING, override

from bookreviver.ports.runtime import EventPublisher, EventStream

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import ProjectId


class InProcessEventBus(EventPublisher, EventStream):
    """Broadcast within one process; a slow subscriber loses its oldest events instead of blocking publishers."""

    def __init__(self, *, queue_size: int) -> None:
        """Create the bus with no subscribers.

        :param queue_size: Largest number of undelivered events each subscriber keeps.
        :type queue_size: int
        """
        self._queue_size = queue_size
        self._subscribers: defaultdict[ProjectId, set[asyncio.Queue[DomainEvent]]] = defaultdict(set)

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Put the event into the queue of every subscriber of its project, dropping the oldest from a full queue.

        :param event: Event to deliver, carrying the project it belongs to.
        :type event: DomainEvent
        """
        for queue in self._subscribers[event.project_id]:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @override
    async def subscribe(self, project_id: ProjectId) -> AsyncIterator[DomainEvent]:
        """Yield the project's events from a new queue, removing the queue when the consumer stops.

        :param project_id: Project whose events are delivered.
        :type project_id: ProjectId
        :returns: Iterator yielding each event published after the subscription starts.
        :rtype: AsyncIterator[DomainEvent]
        """
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[project_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[project_id].discard(queue)
