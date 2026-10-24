"""Tests for the in-process event bus."""

import anyio
import pytest

from bookreviver.adapters.events.broadcast import InProcessEventBus
from tests.helpers.builders import make_book_change, new_book_id

pytestmark = pytest.mark.anyio

QUEUE_SIZE: int = 2


class TestSubscribe:
    """Tests for InProcessEventBus.subscribe()."""

    async def test_subscription_keeps_only_its_book_events_from_entry(self) -> None:
        """Verify a subscription keeps its book's events published before the first read, and no other book's."""
        bus = InProcessEventBus(queue_size=QUEUE_SIZE)
        mine_id, other_id = new_book_id(), new_book_id()
        async with bus.subscribe(mine_id) as events:
            await bus.publish(make_book_change(book_id=other_id))
            await bus.publish(make_book_change(book_id=mine_id))
            assert await anext(events) == make_book_change(book_id=mine_id)

    async def test_left_subscription_receives_nothing(self) -> None:
        """Verify leaving a subscription unsubscribes at once, before anything reads or collects its iterator."""
        bus = InProcessEventBus(queue_size=QUEUE_SIZE)
        book_id = new_book_id()
        async with bus.subscribe(book_id) as events:
            pass
        await bus.publish(make_book_change(book_id=book_id))
        # A delivered event is read without waiting; an empty queue waits and is cancelled at once
        with anyio.move_on_after(0) as scope:
            await anext(events)
        assert scope.cancelled_caught
