"""Following an import through the API of the application: when it has ended, and what it reported.

The application runs the import on its own job board, in the same event loop as the test, so a test has no handle on
the job. It reads the jobs and the report through the routes a browser reads, and waits until the project has no job
queued or running, which is also when the split of the new pages, which a successful import queues, is done.
"""

from typing import TYPE_CHECKING, Any, override

import anyio

from bookreviver.domain.enums import SourceStatus
from bookreviver.domain.events import JobChanged
from tests.helpers.fakes_jobs import RecordingEventBus

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import ProjectId

JOB_PATH: str = '/api/v1/jobs/{job_id}'
JOBS_PATH: str = '/api/v1/projects/{project_id}/jobs'
SOURCES_PATH: str = '/api/v1/projects/{project_id}/sources'
# The most files the sources route lists in one page
REPORT_PAGE_SIZE: int = 100
# Long enough for an import of thousands of files, short enough to fail a test whose import never ends
FINISH_TIMEOUT_SECONDS: float = 120


class JobEndBus(RecordingEventBus):
    """The recording event bus, which tells a waiter every time a job reaches a final state.

    :ivar ended: Set when the next job ends, and replaced by a new event at that moment, so a waiter that took it
                 before it looked at the jobs misses no end.
    """

    def __init__(self) -> None:
        """Start with no job ended."""
        super().__init__()
        self.ended = anyio.Event()

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Record and deliver the event, and announce the end of a job when the event is one.

        :param event: Event to deliver, carrying the project it belongs to.
        :type event: DomainEvent
        """
        await super().publish(event)
        if isinstance(event, JobChanged) and event.job.state.is_final:
            ended, self.ended = self.ended, anyio.Event()
            ended.set()


class ImportWatch:
    """Reads the jobs and the report of the imports of one client."""

    def __init__(self, client: httpx.AsyncClient, events: JobEndBus) -> None:
        """Read through the client of the signed-in account, and wait on the events of the application.

        :param client: Client of the signed-in account.
        :type client: httpx.AsyncClient
        :param events: Event bus of the application, which announces the end of a job.
        :type events: JobEndBus
        """
        self._client = client
        self._events = events

    async def finished(self, job_id: str) -> dict[str, Any]:
        """Wait until the project of a job has no job queued or running, and read the job as the API shows it.

        :param job_id: Identifier of the job the upload answered with.
        :type job_id: str
        :returns: The job document, in the state it ended in.
        :rtype: dict[str, Any]
        """
        with anyio.fail_after(FINISH_TIMEOUT_SECONDS):
            project_id = (await self._client.get(JOB_PATH.format(job_id=job_id))).json()['project_id']
            active_jobs = JOBS_PATH.format(project_id=project_id)
            while True:
                # The event is taken before the jobs are read, so a job that ends in between still wakes the wait
                ended = self._events.ended
                if not (await self._client.get(active_jobs, params={'active': True})).json()['total']:
                    return (await self._client.get(JOB_PATH.format(job_id=job_id))).json()
                await ended.wait()

    async def report(self, project_id: ProjectId) -> list[dict[str, Any]]:
        """Read the report of the imports of a project: the files that were rejected and the files that were skipped.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :returns: The source documents of the files, in the order they were recorded.
        :rtype: list[dict[str, Any]]
        """
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            body = (
                await self._client.get(
                    SOURCES_PATH.format(project_id=project_id),
                    params={
                        'status': [SourceStatus.REJECTED.value, SourceStatus.SKIPPED.value],
                        'page': page,
                        'size': REPORT_PAGE_SIZE,
                    },
                )
            ).json()
            files += body['items']
            if page >= body['pages']:
                return files
            page += 1
