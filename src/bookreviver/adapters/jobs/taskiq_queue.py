"""A job queue that hands jobs to Taskiq workers, one task per job kind."""

from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, override

from taskiq.kicker import AsyncKicker

from bookreviver.ports.runtime import JobQueue

if TYPE_CHECKING:
    from taskiq import AsyncBroker

    from bookreviver.domain.entities import Job


class TaskiqJobQueue(JobQueue):
    """Kicks the Taskiq task named after the job's kind, with the text of the job's identifier as its only argument."""

    def __init__(self, broker: AsyncBroker) -> None:
        self._broker = broker

    @override
    async def enqueue(self, job: Job) -> None:
        # Kicking by name needs no import of the task, so the queue works whichever process registers it
        kicker = AsyncKicker[[str], Coroutine[Any, Any, None]](task_name=job.kind, broker=self._broker, labels={})
        await kicker.kiq(str(job.id))
