"""Runtime ports: background jobs, event delivery, mail and time.

These are the parts of the environment a use case depends on without owning: where heavy work runs, how progress
reaches the browser, how mail leaves the server, and what time it is. Each has an in-process adapter now and a
networked one later, such as Taskiq with Redis for jobs and Redis pub/sub for events, selected in ``app``.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from bookreviver.domain.entities import Job
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import MailMessage


class JobQueue(ABC):
    """Hands recorded jobs to the workers of their pool."""

    @abstractmethod
    async def enqueue(self, job: Job) -> None:
        """Schedule the job for execution.

        :param job: Job already stored, whose kind selects the worker pool.
        :type job: Job
        """


class EventPublisher(ABC):
    """Publishes domain events to whoever listens."""

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Deliver the event to every current subscriber of its project.

        :param event: Event to deliver, carrying the project it belongs to.
        :type event: DomainEvent
        """


class EventStream(ABC):
    """Subscribes to the domain events of one project."""

    @abstractmethod
    def subscribe(self, project_id: ProjectId) -> AsyncIterator[DomainEvent]:
        """Yield the project's events as they are published, until the consumer stops.

        :param project_id: Project whose events are delivered.
        :type project_id: ProjectId
        :returns: Iterator yielding each event published after the subscription starts.
        :rtype: AsyncIterator[DomainEvent]
        """


class Mailer(ABC):
    """Sends mail to account holders."""

    @abstractmethod
    async def send(self, message: MailMessage) -> None:
        """Send one message.

        :param message: Recipient, subject and body of the message.
        :type message: MailMessage
        """


class Clock(ABC):
    """The current time, replaceable in tests."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time with its time zone.

        :returns: The current moment, time-zone aware.
        :rtype: datetime
        """
