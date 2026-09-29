"""Tests for the Taskiq job queue, run on the worker's in-process broker with a probe task the test registers.

The worker's own task registry is empty until the import job arrives, so the test registers a probe under a job kind,
as the worker registers a real entry point. The probe takes a service from the container, like an entry point does, so
the tests cover the whole path: the queue kicks by the kind's name, the broker finds the task, dishka opens a request
scope and resolves the probe's dependency.
"""

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

import anyio.lowlevel
import pytest
from delayed_assert import assert_expectations, expect
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.taskiq import FromDishka
from taskiq.exceptions import SendTaskError

from bookreviver.adapters.jobs.taskiq_queue import TaskiqJobQueue
from bookreviver.app.settings import JobBroker
from bookreviver.app.worker import JOB_TASKS, create_broker, stop_broker
from bookreviver.domain.enums import JobKind
from bookreviver.domain.ids import JobId, ProjectId
from tests.helpers.builders import make_job

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from dishka import AsyncContainer

    from bookreviver.app.worker import JobTask
    from bookreviver.ports.runtime import JobQueue

pytestmark = pytest.mark.anyio

PROBE_KIND: JobKind = JobKind.IMPORT_SOURCE
PROJECT_ID: ProjectId = ProjectId(UUID(int=1))


class Probe:
    """Records the jobs its task was started for, and the request scope each start got.

    :ivar started: Identifier of every job the task ran for, in order.
    :ivar scopes: The request-scoped marker each run received, one per run.
    """

    def __init__(self) -> None:
        """Start with no runs recorded."""
        self.started: list[JobId] = []
        self.scopes: list[RequestMarker] = []


class RequestMarker:
    """An object built once per request scope, so two runs sharing one would show up."""


class ProbeProvider(Provider):
    """Provides the test's probe for the application and a fresh marker for every task."""

    def __init__(self, probe: Probe) -> None:
        """Provide ``probe`` to every task.

        :param probe: Probe of the test.
        :type probe: Probe
        """
        super().__init__()
        self._probe = probe

    @provide(scope=Scope.APP)
    def probe(self) -> Probe:
        """Return the test's probe.

        :returns: The probe given to the constructor.
        :rtype: Probe
        """
        return self._probe

    marker = provide(RequestMarker, scope=Scope.REQUEST)


async def run_probe(job_id: str, probe: FromDishka[Probe], marker: FromDishka[RequestMarker]) -> None:
    """Record the job the probe task was started for, as an entry point would run it.

    :param job_id: Text of the job's identifier, as the queue sends it.
    :type job_id: str
    :param probe: Probe of the test, resolved by dishka.
    :type probe: Probe
    :param marker: Object of the task's request scope, resolved by dishka.
    :type marker: RequestMarker
    """
    probe.started.append(JobId(UUID(job_id)))
    probe.scopes.append(marker)
    # A real entry point awaits its service, so the run suspends once while its request scope is open
    await anyio.lowlevel.checkpoint()


PROBE_TASKS: Mapping[JobKind, JobTask] = {PROBE_KIND: run_probe}


@asynccontextmanager
async def _running_queue(container: AsyncContainer, *, tasks: Mapping[JobKind, JobTask]) -> AsyncIterator[JobQueue]:
    """Start an in-process broker with ``tasks`` registered, and stop it once its running tasks are done.

    :param container: Container the tasks resolve their dependencies from.
    :type container: AsyncContainer
    :param tasks: Entry point of every job kind the broker runs.
    :type tasks: Mapping[JobKind, JobTask]
    :returns: Iterator yielding a Taskiq queue over the started broker.
    :rtype: AsyncIterator[JobQueue]
    """
    broker = create_broker(JobBroker.IN_PROCESS, container, tasks=tasks)
    await broker.startup()
    try:
        yield TaskiqJobQueue(broker)
    finally:
        await stop_broker(broker)


@pytest.fixture
def fx_probe() -> Probe:
    """Build the probe of one test.

    :returns: A probe with no runs recorded.
    :rtype: Probe
    """
    return Probe()


@pytest.fixture
async def fx_container(fx_probe: Probe) -> AsyncIterator[AsyncContainer]:
    """Open a container providing the probe, as the application container provides services.

    :param fx_probe: Probe of the test.
    :type fx_probe: Probe
    :returns: Iterator yielding the container and closing it afterwards.
    :rtype: AsyncIterator[AsyncContainer]
    """
    container = make_async_container(ProbeProvider(fx_probe))
    yield container
    await container.close()


class TestEnqueue:
    """Tests for TaskiqJobQueue.enqueue()."""

    async def test_job_runs_task_registered_under_its_kind(self, fx_container: AsyncContainer, fx_probe: Probe) -> None:
        """Verify each enqueued job starts the task of its kind with its identifier, in a request scope of its own.

        :param fx_container: Container providing the probe.
        :type fx_container: AsyncContainer
        :param fx_probe: Probe the task records into.
        :type fx_probe: Probe
        """
        jobs = [make_job(project_id=PROJECT_ID), make_job(project_id=PROJECT_ID)]
        # Leaving the block waits for the tasks running in this process
        async with _running_queue(fx_container, tasks=PROBE_TASKS) as queue:
            for job in jobs:
                await queue.enqueue(job)
        expect(fx_probe.started == [job.id for job in jobs])
        expect(len({id(marker) for marker in fx_probe.scopes}) == len(jobs))
        assert_expectations()

    async def test_kind_without_task_is_refused(self, fx_container: AsyncContainer) -> None:
        """Verify the in-process broker refuses a job whose kind has no registered task, as with the empty registry.

        :param fx_container: Container of the worker.
        :type fx_container: AsyncContainer
        """
        job = make_job(project_id=PROJECT_ID)
        async with _running_queue(fx_container, tasks=JOB_TASKS) as queue:
            with pytest.raises(SendTaskError):
                await queue.enqueue(job)
