"""A job queue that only records what was enqueued, for service tests.

A service test checks that a use case scheduled the right job without running it, so the queue keeps jobs in order
and a test reads them back from ``enqueued``.
"""

from typing import TYPE_CHECKING, override

from bookreviver.ports.runtime import JobQueue

if TYPE_CHECKING:
    from bookreviver.domain.entities import Job


class RecordingJobQueue(JobQueue):
    """Keeps enqueued jobs in order instead of running them.

    :ivar enqueued: Every enqueued job, in the order it was enqueued.
    """

    def __init__(self) -> None:
        """Start with no jobs enqueued."""
        self.enqueued: list[Job] = []

    @override
    async def enqueue(self, job: Job) -> None:
        """Record the job.

        :param job: Job to record.
        :type job: Job
        """
        self.enqueued.append(job)
