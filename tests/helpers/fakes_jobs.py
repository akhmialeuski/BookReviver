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

from bookreviver.adapters.events.broadcast import InProcessEventBus
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.ports.runtime import Clock, EventPublisher, EventStream, JobBoard
from bookreviver.services.jobs import JobService
from tests.helpers.builders import EPOCH
from tests.helpers.clock import FixedClock
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.scripted_runner import ScriptedRunner
from tests.helpers.seeding import ensure_account

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from bookreviver.adapters.jobs.board import JobRunner
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
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
    """The event bus, the clock and the job board a job test shares with the services and the application it builds.

    The database is not part of the fakes, since the application of an API test runs on the database of the test,
    which a test reaches through ``fx_database`` and ``fx_uow``. The methods that need it take it as an argument.

    :ivar events: Event bus serving both publishers and subscribers.
    :ivar clock: Clock stopped at ``EPOCH``.
    :ivar scripted: Scripted runner a test can hold or make fail, which the board runs its jobs with unless ``runner``
                    is given.
    :ivar runner: What the fake board runs its jobs with instead of the scripted runner, or None.
    :ivar board: Board that records the jobs put on it and runs them when a test calls ``run_queued``.
    """

    events: RecordingEventBus = field(factory=RecordingEventBus)
    clock: FixedClock = field(factory=lambda: FixedClock(EPOCH))
    scripted: ScriptedRunner = field(factory=ScriptedRunner)
    runner: JobRunner | None = None
    board: FakeJobBoard = field(
        init=False,
        default=Factory(
            lambda self: FakeJobBoard(runner=self.runner or self.scripted, publisher=self.events, clock=self.clock),
            takes_self=True,
        ),
    )

    def job_service(self, uow: UnitOfWork, sources: SourceStore) -> JobService:
        """Build a job service over a unit of work, as a new request would get.

        :param uow: Unit of work the service works in, such as the one ``fx_uow`` opens.
        :type uow: UnitOfWork
        :param sources: Store of the uploads, which a cancelled import that never began is removed from.
        :type sources: SourceStore
        :returns: The job service.
        :rtype: JobService
        """
        return JobService(uow=uow, board=self.board, stream=self.events, sources=sources)

    @staticmethod
    async def store(database: SqlDatabase, project: Project) -> None:
        """Commit a project to the database, and the account owning the project when it is new.

        :param database: Database to commit into.
        :type database: SqlDatabase
        :param project: Project to store.
        :type project: Project
        """
        await ensure_account(database, project.owner_id)
        await SqlAlchemyUnitOfWork(database).change(lambda repos: repos.projects.add(project))


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
    """Replaces the application's event bus and clock with the given fakes."""

    scope = Scope.APP

    def __init__(self, fakes: JobFakes) -> None:
        """Provide ``fakes`` in place of the application's own adapters.

        :param fakes: Adapters of the test.
        :type fakes: JobFakes
        """
        super().__init__()
        self._fakes = fakes

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
