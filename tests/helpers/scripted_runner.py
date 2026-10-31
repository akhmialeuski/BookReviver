"""A runner of jobs a test scripts: it logs when each job begins and returns, and can hold or break a job.

The runner counts the jobs of a book that are inside it at the same moment, and it can hold a job inside its first task
until the test sets an event, which is how a test looks at a board while a job is running. Every job has the same
number of tasks, and the runner tells the control after each.
"""

import enum
from typing import TYPE_CHECKING

import anyio

from bookreviver.domain.jobs import JobEnd, JobState

if TYPE_CHECKING:
    from bookreviver.domain.ids import BookDirectoryName, JobId
    from bookreviver.domain.jobs import Job
    from bookreviver.ports.runtime import JobControl

# How many tasks a job of the scripted runner has
TASKS_PER_JOB: int = 3


class Phase(enum.StrEnum):
    """A moment in the life of a job inside the runner."""

    BEGUN = 'begun'
    RETURNED = 'returned'


class ScriptedRunner:
    """Runs jobs as a test scripts them: three tasks each, logged, and held inside the first one when a test asks.

    :ivar log: The phases of every job, in the order they happened.
    :ivar peak: The most jobs of one book, by the name of its directory, that were inside the runner at the same moment.
    """

    def __init__(self) -> None:
        """Start with nothing run and nothing held."""
        self.log: list[tuple[Phase, JobId]] = []
        self.peak: dict[BookDirectoryName, int] = {}
        self._inside: dict[BookDirectoryName, int] = {}
        self._held: dict[JobId, anyio.Event] = {}
        self._failing: set[JobId] = set()

    def hold(self, job: Job) -> anyio.Event:
        """Make the job wait inside its first task until the returned event is set.

        :param job: The job to hold, which may not have begun yet.
        :type job: Job
        :returns: The event the job waits for.
        :rtype: anyio.Event
        """
        event = anyio.Event()
        self._held[job.id] = event
        return event

    def fail(self, job: Job) -> None:
        """Make the job raise an error nobody expects, in its first task.

        :param job: The job to make fail.
        :type job: Job
        """
        self._failing.add(job.id)

    def begun(self) -> list[JobId]:
        """List the jobs that began, in the order they began.

        :returns: The identifiers of the jobs.
        :rtype: list[JobId]
        """
        return [job_id for phase, job_id in self.log if phase is Phase.BEGUN]

    async def __call__(self, dir_name: BookDirectoryName, job: Job, control: JobControl) -> JobEnd:
        """Run the job: plan its tasks, and finish each after the hold of the test, if there is one.

        :param dir_name: Name of the directory of the book whose queue the job was taken from.
        :type dir_name: BookDirectoryName
        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: A succeeded end.
        :rtype: JobEnd
        :raises RuntimeError: If the test made the job fail.
        """
        self.log.append((Phase.BEGUN, job.id))
        inside = self._inside.get(dir_name, 0) + 1
        self._inside[dir_name] = inside
        self.peak[dir_name] = max(self.peak.get(dir_name, 0), inside)
        try:
            control.plan(TASKS_PER_JOB)
            for _ in range(TASKS_PER_JOB):
                if (gate := self._held.get(job.id)) is not None:
                    await gate.wait()
                if job.id in self._failing:
                    message = 'The test made this job fail.'
                    raise RuntimeError(message)
                await control.task_done()
        finally:
            self._inside[dir_name] -= 1
            self.log.append((Phase.RETURNED, job.id))
        return JobEnd(state=JobState.SUCCEEDED)
