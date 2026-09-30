"""Tests for the in-process event bus."""

import anyio
import pytest

from bookreviver.adapters.events.broadcast import InProcessEventBus
from bookreviver.domain.events import ProjectChanged
from tests.helpers.builders import make_project, new_account_id

pytestmark = pytest.mark.anyio

QUEUE_SIZE: int = 2


class TestSubscribe:
    """Tests for InProcessEventBus.subscribe()."""

    async def test_subscription_keeps_only_its_project_events_from_entry(self) -> None:
        """Verify a subscription keeps its project's events published before the first read, and no other project's."""
        bus = InProcessEventBus(queue_size=QUEUE_SIZE)
        mine, other = make_project(owner_id=new_account_id()), make_project(owner_id=new_account_id())
        async with bus.subscribe(mine.id) as events:
            await bus.publish(ProjectChanged(project_id=other.id))
            await bus.publish(ProjectChanged(project_id=mine.id))
            assert await anext(events) == ProjectChanged(project_id=mine.id)

    async def test_left_subscription_receives_nothing(self) -> None:
        """Verify leaving a subscription unsubscribes at once, before anything reads or collects its iterator."""
        bus = InProcessEventBus(queue_size=QUEUE_SIZE)
        project = make_project(owner_id=new_account_id())
        async with bus.subscribe(project.id) as events:
            pass
        await bus.publish(ProjectChanged(project_id=project.id))
        # A delivered event is read without waiting; an empty queue waits and is cancelled at once
        with anyio.move_on_after(0) as scope:
            await anext(events)
        assert scope.cancelled_caught
