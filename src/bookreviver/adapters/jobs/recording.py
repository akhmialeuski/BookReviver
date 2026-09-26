"""A job queue that only records what was enqueued, for service tests."""

from typing import TYPE_CHECKING, override

from bookreviver.ports.runtime import JobQueue

if TYPE_CHECKING:
    from bookreviver.domain.entities import Job


class RecordingJobQueue(JobQueue):
    """Keeps enqueued jobs in order instead of running them."""

    def __init__(self) -> None:
        self.enqueued: list[Job] = []

    @override
    async def enqueue(self, job: Job) -> None:
        self.enqueued.append(job)
