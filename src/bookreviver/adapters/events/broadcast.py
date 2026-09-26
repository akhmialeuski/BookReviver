"""In-process event bus: every subscriber of a project gets its own bounded queue."""

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
        self._queue_size = queue_size
        self._subscribers: defaultdict[ProjectId, set[asyncio.Queue[DomainEvent]]] = defaultdict(set)

    @override
    async def publish(self, event: DomainEvent) -> None:
        for queue in self._subscribers[event.project_id]:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    @override
    async def subscribe(self, project_id: ProjectId) -> AsyncIterator[DomainEvent]:
        queue: asyncio.Queue[DomainEvent] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[project_id].add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers[project_id].discard(queue)
