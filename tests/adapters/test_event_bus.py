"""Tests for the in-process event bus."""

from typing import TYPE_CHECKING

import anyio.lowlevel
import pytest
from anyio import create_task_group

from bookreviver.adapters.events.broadcast import InProcessEventBus
from bookreviver.domain.events import ProjectChanged
from tests.helpers.builders import make_project, new_account_id

if TYPE_CHECKING:
    from bookreviver.domain.events import DomainEvent

pytestmark = pytest.mark.anyio

QUEUE_SIZE: int = 2


class TestInProcessEventBus:
    """Tests for InProcessEventBus."""

    async def test_subscriber_gets_only_its_project_events(self) -> None:
        """Verify a subscriber receives events of its project and not of others."""
        bus = InProcessEventBus(queue_size=QUEUE_SIZE)
        mine, other = make_project(owner_id=new_account_id()), make_project(owner_id=new_account_id())
        received: list[DomainEvent] = []

        async def listen() -> None:
            """Record the first event delivered to the subscriber of ``mine`` and stop."""
            async for event in bus.subscribe(mine.id):
                received.append(event)
                return

        async with create_task_group() as group:
            group.start_soon(listen)
            # Let the subscriber register before publishing
            await anyio.lowlevel.checkpoint()
            await bus.publish(ProjectChanged(project_id=other.id))
            await bus.publish(ProjectChanged(project_id=mine.id))
        assert received == [ProjectChanged(project_id=mine.id)]
