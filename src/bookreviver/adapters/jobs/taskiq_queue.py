"""A job queue that hands jobs to Taskiq workers, one task per job kind.

The queue kicks the task by name, and the name is the value of the job's ``JobKind``. Kicking by name needs no import
of the task function, which lives in ``app/worker.py``: an adapter may not import ``app``, and the process that
enqueues a job is not necessarily the one that registers and runs its task. The task receives the text of the job's
identifier as its only argument, so the message stays a plain string whichever broker carries it, and the task reads
the job back from the database.
"""

from collections.abc import Coroutine
from typing import TYPE_CHECKING, Any, override

from taskiq.kicker import AsyncKicker

from bookreviver.ports.runtime import JobQueue

if TYPE_CHECKING:
    from taskiq import AsyncBroker

    from bookreviver.domain.entities import Job

# Signature of every job task as the kicker sees it: the job identifier as text in, nothing out
type JobKicker = AsyncKicker[[str], Coroutine[Any, Any, None]]


class TaskiqJobQueue(JobQueue):
    """Kicks the Taskiq task named after the job's kind, with the text of the job's identifier as its argument."""

    def __init__(self, broker: AsyncBroker) -> None:
        """Send jobs through ``broker``.

        :param broker: Started broker whose workers have a task registered under each job kind.
        :type broker: AsyncBroker
        """
        self._broker = broker

    @override
    async def enqueue(self, job: Job) -> None:
        """Send a message for the task named after the job's kind.

        :param job: Job already stored, whose kind names the task and whose identifier is the task's argument.
        :type job: Job
        """
        kicker: JobKicker = AsyncKicker(task_name=job.kind, broker=self._broker, labels={})
        await kicker.kiq(str(job.id))
