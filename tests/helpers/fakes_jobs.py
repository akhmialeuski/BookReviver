"""Fakes of the job feature: an event bus that records, the adapters a job test shares, and a provider injecting them.

The event bus is the application's own ``InProcessEventBus`` with what a test needs to observe: the list of everything
published, an event set once a subscription is in place, so a test publishes only after the stream subscribed, and the
number of subscriptions still open, which a test can also wait to reach a count.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, override

import anyio
from attrs import Factory, field, frozen
from dishka import AnyOf, Provider, Scope, provide

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.events.broadcast import InProcessEventBus
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.ports.runtime import Clock, EventPublisher, EventStream, JobBoard
from bookreviver.services.jobs import JobService
from tests.helpers.book_gate import BookGate, GatedUnitOfWork
from tests.helpers.builders import EPOCH
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.scripted_runner import ScriptedRunner

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from bookreviver.adapters.jobs.board import JobRunner
    from bookreviver.domain.entities import Project
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.storage import SourceStore

EVENT_QUEUE_SIZE: int = 16


class RecordingEventBus(InProcessEventBus):
    """The in-process event bus, recording every published event and announcing its first subscriber.

    :ivar published: Every published event, in the order it was published.
    :ivar subscribed: Set once a subscription is in place.
    :ivar open_subscriptions: Number of subscriptions entered and not yet left.
    """

    def __init__(self) -> None:
        """Start with nothing published and nobody subscribed."""
        super().__init__(queue_size=EVENT_QUEUE_SIZE)
        self.published: list[DomainEvent] = []
        self.subscribed = anyio.Event()
        self.open_subscriptions = 0
        # Set and replaced by every new subscription, so a waiter for a count is woken by each one and misses none
        self._arrival = anyio.Event()

    async def wait_for_subscriptions(self, count: int) -> None:
        """Return once at least ``count`` subscriptions are open at the same time.

        :param count: Number of subscriptions to wait for.
        :type count: int
        """
        while self.open_subscriptions < count:
            await self._arrival.wait()

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Record the event, then deliver it as the in-process bus does.

        :param event: Event to deliver, carrying the project it belongs to.
        :type event: DomainEvent
        """
        self.published.append(event)
        await super().publish(event)

    @override
    @asynccontextmanager
    async def subscribe(self, project_id: ProjectId) -> AsyncGenerator[AsyncIterator[DomainEvent]]:
        """Subscribe as the in-process bus does, announcing the subscription once it is in place and counting it.

        :param project_id: Project whose events are delivered.
        :type project_id: ProjectId
        :returns: Generator yielding once the reader of every event published since entry.
        :rtype: AsyncGenerator[AsyncIterator[DomainEvent]]
        """
        async with super().subscribe(project_id) as events:
            self.open_subscriptions += 1
            self.subscribed.set()
            arrived, self._arrival = self._arrival, anyio.Event()
            arrived.set()
            try:
                yield events
            finally:
                self.open_subscriptions -= 1


@frozen(kw_only=True)
class JobFakes:
    """The adapters a job test shares with the services and the application it builds.

    :ivar database: In-memory database every unit of work opens over.
    :ivar events: Event bus serving both publishers and subscribers.
    :ivar clock: Clock stopped at ``EPOCH``.
    :ivar gate: Gate a test holds a book with, which the units of work from ``gated_uow`` wait at.
    :ivar scripted: Scripted runner a test can hold or make fail, which the board runs its jobs with unless ``runner``
                    is given.
    :ivar runner: What the fake board runs its jobs with instead of the scripted runner, or None.
    :ivar board: Board that records the jobs put on it and runs them when a test calls ``run_queued``.
    """

    database: InMemoryDatabase = field(factory=InMemoryDatabase)
    events: RecordingEventBus = field(factory=RecordingEventBus)
    clock: FixedClock = field(factory=lambda: FixedClock(EPOCH))
    gate: BookGate = field(factory=BookGate)
    scripted: ScriptedRunner = field(factory=ScriptedRunner)
    runner: JobRunner | None = None
    board: FakeJobBoard = field(
        init=False,
        default=Factory(
            lambda self: FakeJobBoard(runner=self.runner or self.scripted, publisher=self.events, clock=self.clock),
            takes_self=True,
        ),
    )

    def gated_uow(self) -> UnitOfWork:
        """Open a unit of work over the database whose changes wait at the gate, as a new request or job would get.

        :returns: A new unit of work.
        :rtype: UnitOfWork
        """
        return GatedUnitOfWork(InMemoryUnitOfWork(self.database), self.gate)

    def job_service(self, sources: SourceStore) -> JobService:
        """Build a job service over a new unit of work, as a new request would get.

        :param sources: Store of the uploads, which a cancelled import that never began is removed from.
        :type sources: SourceStore
        :returns: The job service.
        :rtype: JobService
        """
        return JobService(uow=InMemoryUnitOfWork(self.database), board=self.board, stream=self.events, sources=sources)

    async def store(self, project: Project) -> None:
        """Commit a project to the database.

        :param project: Project to store.
        :type project: Project
        """
        await InMemoryUnitOfWork(self.database).change(lambda repos: repos.projects.add(project))


class FakeBoardProvider(Provider):
    """Replaces the application's job board with a given one, such as a fake that runs a job only when told to."""

    scope = Scope.APP

    def __init__(self, board: JobBoard) -> None:
        """Provide ``board`` in place of the application's own.

        :param board: Board the application puts its jobs on and reads them from.
        :type board: JobBoard
        """
        super().__init__()
        self._board = board

    @provide(override=True)
    def job_board(self) -> JobBoard:
        """Return the given board, so the application sees the jobs a test puts on it.

        :returns: The board given to the constructor.
        :rtype: JobBoard
        """
        return self._board


class JobFakesProvider(Provider):
    """Replaces the application's database, event bus and clock with the given fakes."""

    scope = Scope.APP

    def __init__(self, fakes: JobFakes) -> None:
        """Provide ``fakes`` in place of the application's own adapters.

        :param fakes: Adapters of the test.
        :type fakes: JobFakes
        """
        super().__init__()
        self._fakes = fakes

    @provide(override=True)
    def database(self) -> InMemoryDatabase:
        """Return the fakes' database, so the application sees what a test stores.

        :returns: The in-memory database of the fakes.
        :rtype: InMemoryDatabase
        """
        return self._fakes.database

    @provide(override=True)
    def event_bus(self) -> AnyOf[InProcessEventBus, EventPublisher, EventStream]:
        """Return the recording bus as the bus itself, the publisher port and the stream port.

        :returns: The recording event bus of the fakes.
        :rtype: AnyOf[InProcessEventBus, EventPublisher, EventStream]
        """
        return self._fakes.events

    @provide(override=True)
    def clock(self) -> Clock:
        """Return the fakes' stopped clock.

        :returns: The fixed clock of the fakes.
        :rtype: Clock
        """
        return self._fakes.clock
