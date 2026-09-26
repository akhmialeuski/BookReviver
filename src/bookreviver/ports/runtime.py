"""Runtime ports: background jobs, event delivery, mail and time."""

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
        """Schedule the job for execution."""


class EventPublisher(ABC):
    """Publishes domain events to whoever listens."""

    @abstractmethod
    async def publish(self, event: DomainEvent) -> None:
        """Deliver the event to every current subscriber of its project."""


class EventStream(ABC):
    """Subscribes to the domain events of one project."""

    @abstractmethod
    def subscribe(self, project_id: ProjectId) -> AsyncIterator[DomainEvent]:
        """Yield the project's events as they are published, until the consumer stops."""


class Mailer(ABC):
    """Sends mail to account holders."""

    @abstractmethod
    async def send(self, message: MailMessage) -> None:
        """Send one message."""


class Clock(ABC):
    """The current time, replaceable in tests."""

    @abstractmethod
    def now(self) -> datetime:
        """Return the current time with its time zone."""
