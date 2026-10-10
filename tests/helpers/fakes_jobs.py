"""Fakes of the job feature: an event bus that records, the adapters a job test shares, and a provider injecting them.

The event bus is the application's own ``InProcessEventBus`` with what a test needs to observe: the list of everything
published, an event set once a subscription is in place, so a test publishes only after the stream subscribed, and the
number of subscriptions still open.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, override

import anyio
from attrs import field, frozen
from dishka import AnyOf, Provider, Scope, provide

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.events.broadcast import InProcessEventBus
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.ports.runtime import Clock, EventPublisher, EventStream
from bookreviver.services.jobs import JobService
from tests.helpers.builders import EPOCH

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from bookreviver.domain.entities import Job, Project
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import ProjectId

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
    """

    database: InMemoryDatabase = field(factory=InMemoryDatabase)
    events: RecordingEventBus = field(factory=RecordingEventBus)
    clock: FixedClock = field(factory=lambda: FixedClock(EPOCH))

    def job_service(self) -> JobService:
        """Build a job service over a new unit of work, as a new request would get.

        :returns: The job service.
        :rtype: JobService
        """
        return JobService(
            uow=InMemoryUnitOfWork(self.database), publisher=self.events, stream=self.events, clock=self.clock
        )

    async def store(self, project: Project, *jobs: Job) -> None:
        """Commit a project and jobs of it to the database.

        :param project: Project to store.
        :type project: Project
        :param jobs: Jobs of the project to store.
        :type jobs: Job
        """
        uow = InMemoryUnitOfWork(self.database)
        # The project and the rows of its jobs are writes outside the content of a book
        async with uow.change():
            await uow.projects.add(project)
            for job in jobs:
                await uow.jobs.add(job)

    async def stored_job(self, job: Job) -> Job:
        """Read a job back as committed.

        :param job: Job to read, identified by its identifier.
        :type job: Job
        :returns: The job as the database holds it now.
        :rtype: Job
        """
        return await InMemoryUnitOfWork(self.database).jobs.get(job.id)


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
