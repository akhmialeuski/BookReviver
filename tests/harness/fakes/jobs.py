"""Fakes of the job feature: an event bus that records, the adapters a job test shares, and a provider injecting them.

The event bus is the application's own ``EventChannels`` with what a test needs to observe: the list of everything
published to any book, an event set once a subscription is in place, so a test publishes only after the stream
subscribed, and the number of subscriptions still open, which a test can also wait to reach a count.
"""

from collections.abc import Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, ClassVar, Self, override

import anyio
from attrs import Factory, field, frozen
from dishka import Provider, Scope, provide

from bookreviver.adapters.events.broadcast import EventChannels, InProcessBookEvents
from bookreviver.adapters.jobs.board import InMemoryJobBoard
from bookreviver.domain.events import BookChanged
from bookreviver.ports.runtime import BookEvents, BookJobs
from tests.harness.builders.domain import EVENT_QUEUE_SIZE
from tests.harness.concurrency.scripted_runner import ScriptedRunner
from tests.harness.fakes.board import FakeJobBoard

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    from bookreviver.adapters.jobs.board import JobRunner
    from bookreviver.domain.books import BookResource
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import BookDirectoryName

NO_BUS: str = 'No recording event bus is installed. The fx_book_parts fixture installs one for every test.'


class RecordingBookEvents(InProcessBookEvents):
    """The channel of one book, which reports what it publishes and who subscribes to the bus that made it."""

    def __init__(self, bus: RecordingEventBus) -> None:
        """Make the channel of a book over the bus that records for every book.

        :param bus: The bus the channel records for.
        :type bus: RecordingEventBus
        """
        super().__init__(queue_size=EVENT_QUEUE_SIZE)
        self._bus = bus

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Record the event, deliver it as the in-process channel does, then run the hook of the bus.

        :param event: Event to deliver.
        :type event: DomainEvent
        """
        self._bus.published.append(event)
        await super().publish(event)
        if self._bus.after_publish is not None:
            await self._bus.after_publish(event)

    @override
    @asynccontextmanager
    async def subscribe(self) -> AsyncGenerator[AsyncIterator[DomainEvent]]:
        """Subscribe as the in-process channel does, announcing the subscription once it is in place and counting it.

        :returns: Generator yielding once the reader of every event published since entry.
        :rtype: AsyncGenerator[AsyncIterator[DomainEvent]]
        """
        async with super().subscribe() as events:
            self._bus.open_subscriptions += 1
            self._bus.subscribed.set()
            arrived, self._bus.arrival = self._bus.arrival, anyio.Event()
            arrived.set()
            try:
                yield events
            finally:
                self._bus.open_subscriptions -= 1


class RecordingEventBus(EventChannels):
    """The event channels of the books, recording every published event and announcing the first subscriber.

    One bus serves every book of a test, as the channels of the application do. ``installed`` registers it as the way
    a book opens its event channel, and ``current`` returns the one the suite installed for the running test.

    :ivar published: Every published event of every book, in the order it was published.
    :ivar after_publish: Coroutine function run with each event once it was delivered, or None.
    :ivar subscribed: Set once a subscription is in place.
    :ivar open_subscriptions: Number of subscriptions entered and not yet left.
    :ivar arrival: Set and replaced by every new subscription, so a waiter for a count is woken by each one.
    """

    _current: ClassVar[RecordingEventBus | None] = None

    def __init__(self) -> None:
        """Start with nothing published and nobody subscribed."""
        super().__init__(queue_size=EVENT_QUEUE_SIZE)
        self.published: list[DomainEvent] = []
        self.after_publish: Callable[[DomainEvent], Awaitable[None]] | None = None
        self.subscribed = anyio.Event()
        self.open_subscriptions = 0
        self.arrival = anyio.Event()
        self._recording: dict[BookDirectoryName, RecordingBookEvents] = {}

    @classmethod
    def current(cls) -> RecordingEventBus:
        """Return the bus the suite installed for the running test.

        :returns: The installed bus.
        :rtype: RecordingEventBus
        :raises RuntimeError: If no bus is installed, which is the case outside a test.
        """
        if cls._current is None:
            raise RuntimeError(NO_BUS)
        return cls._current

    @contextmanager
    def installed(self) -> Iterator[Self]:
        """Register the bus as the way a book opens its event channel, and make it the current bus while the block runs.

        :returns: Context manager yielding the bus.
        :rtype: Iterator[Self]
        """
        previous_bus, RecordingEventBus._current = RecordingEventBus._current, self
        BookEvents.register(self.open)
        try:
            yield self
        finally:
            RecordingEventBus._current = previous_bus

    @override
    def open(self, dir_name: BookDirectoryName) -> RecordingBookEvents:
        """Return the recording channel of the book whose directory has the given name, made on the first call.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: The one channel of the book.
        :rtype: RecordingBookEvents
        """
        if (channel := self._recording.get(dir_name)) is None:
            channel = self._recording[dir_name] = RecordingBookEvents(self)
        return channel

    def changed_parts(self) -> list[frozenset[BookResource]]:
        """List the parts each committed change of a book that was published wrote, in the order they were published.

        :returns: The parts of every ``BookChanged``, one entry for each.
        :rtype: list[frozenset[BookResource]]
        """
        return [event.resources for event in self.published if isinstance(event, BookChanged)]

    async def wait_for_subscriptions(self, count: int) -> None:
        """Return once at least ``count`` subscriptions are open at the same time.

        :param count: Number of subscriptions to wait for.
        :type count: int
        """
        while self.open_subscriptions < count:
            await self.arrival.wait()


@frozen(kw_only=True)
class JobFakes:
    """The event bus and the job board a job test shares with the services and the application it builds.

    The database is not part of the fakes, since the application of an API test runs on the database of the test,
    which a test reaches through ``fx_database`` and ``fx_catalogue``.

    :ivar events: The recording event bus of the test, which serves the publishers and the subscribers of every book.
    :ivar scripted: Scripted runner a test can hold or make fail, which the board runs its jobs with unless ``runner``
                    is given.
    :ivar runner: What the fake board runs its jobs with instead of the scripted runner, or None.
    :ivar board: Board that records the jobs put on it and runs them when a test calls ``run_queued``.
    """

    events: RecordingEventBus = field(factory=RecordingEventBus.current)
    scripted: ScriptedRunner = field(factory=ScriptedRunner)
    runner: JobRunner | None = None
    board: FakeJobBoard = field(
        init=False,
        default=Factory(
            lambda self: FakeJobBoard(runner=self.runner or self.scripted),
            takes_self=True,
        ),
    )

    def __attrs_post_init__(self) -> None:
        """Register the board as the way a book opens its job queue, replacing the one the suite registered."""
        BookJobs.register(self.board.open)


class FakeBoardProvider(Provider):
    """Replaces the application's job board with a given one, such as a fake that runs a job only when told to."""

    scope = Scope.APP

    def __init__(self, board: InMemoryJobBoard) -> None:
        """Provide ``board`` in place of the application's own.

        :param board: Board the application puts its jobs on and reads them from.
        :type board: InMemoryJobBoard
        """
        super().__init__()
        self._board = board

    @provide(override=True)
    def job_board(self) -> InMemoryJobBoard:
        """Return the given board as the board of the application, whose queues ``prepare_start`` registers.

        :returns: The board given to the constructor, so the application sees the jobs a test puts on it.
        :rtype: InMemoryJobBoard
        """
        return self._board


class JobFakesProvider(Provider):
    """Replaces the application's event channels with the given fake."""

    scope = Scope.APP

    def __init__(self, fakes: JobFakes) -> None:
        """Provide ``fakes`` in place of the application's own adapters.

        :param fakes: Adapters of the test.
        :type fakes: JobFakes
        """
        super().__init__()
        self._fakes = fakes

    @provide(override=True)
    def event_channels(self) -> EventChannels:
        """Return the recording bus as the event channels of the application, whose channels ``prepare_start`` registers.

        :returns: The recording event bus of the fakes.
        :rtype: EventChannels
        """
        return self._fakes.events
