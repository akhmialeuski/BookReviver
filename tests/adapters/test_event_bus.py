"""Tests for the in-process event channels of the books."""

import anyio
import pytest

from bookreviver.adapters.events.broadcast import EventChannels
from bookreviver.domain.ids import BookDirectoryName
from tests.harness.builders.domain import make_book_change

pytestmark = pytest.mark.anyio

QUEUE_SIZE: int = 2
MINE: BookDirectoryName = BookDirectoryName('mine')
OTHER: BookDirectoryName = BookDirectoryName('other')


class TestSubscribe:
    """Tests for InProcessBookEvents.subscribe()."""

    async def test_subscription_keeps_only_its_book_events_from_entry(self) -> None:
        """Verify a subscription keeps its book's events published before the first read, and no other book's."""
        channels = EventChannels(queue_size=QUEUE_SIZE)
        async with channels.open(MINE).subscribe() as events:
            await channels.open(OTHER).publish(make_book_change(version=1))
            await channels.open(MINE).publish(make_book_change(version=2))
            assert await anext(events) == make_book_change(version=2)

    async def test_every_open_of_one_book_reaches_the_same_channel(self) -> None:
        """Verify the channel opened for a directory name twice is one channel, so its objects share the events."""
        channels = EventChannels(queue_size=QUEUE_SIZE)
        async with channels.open(MINE).subscribe() as events:
            await channels.open(MINE).publish(make_book_change())
            assert await anext(events) == make_book_change()

    async def test_left_subscription_receives_nothing(self) -> None:
        """Verify leaving a subscription unsubscribes at once, before anything reads or collects its iterator."""
        channel = EventChannels(queue_size=QUEUE_SIZE).open(MINE)
        async with channel.subscribe() as events:
            pass
        await channel.publish(make_book_change())
        # A delivered event is read without waiting; an empty queue waits and is cancelled at once
        with anyio.move_on_after(0) as scope:
            await anext(events)
        assert scope.cancelled_caught
