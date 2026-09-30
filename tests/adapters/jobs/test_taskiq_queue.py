"""Tests for what only the Taskiq job queue does, beyond the JobQueue contract in ``tests/contracts``.

The probe task and the broker setup come from ``tests.helpers.job_queues``, which the contract suite uses too.
"""

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from dishka import make_async_container
from taskiq.exceptions import SendTaskError

from bookreviver.app.worker import JOB_TASKS
from bookreviver.domain.ids import ProjectId
from tests.helpers.builders import make_job
from tests.helpers.job_queues import PROBE_TASKS, Probe, ProbeProvider, running_taskiq_queue

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from dishka import AsyncContainer

pytestmark = pytest.mark.anyio

PROJECT_ID: ProjectId = ProjectId(UUID(int=1))


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

    async def test_every_job_runs_in_a_request_scope_of_its_own(
        self, fx_container: AsyncContainer, fx_probe: Probe
    ) -> None:
        """Verify each job's task gets its own request scope, and so its own unit of work in the application.

        :param fx_container: Container providing the probe.
        :type fx_container: AsyncContainer
        :param fx_probe: Probe the task records into.
        :type fx_probe: Probe
        """
        jobs = [make_job(project_id=PROJECT_ID), make_job(project_id=PROJECT_ID)]
        # Leaving the block waits for the tasks running in this process
        async with running_taskiq_queue(fx_container, tasks=PROBE_TASKS) as queue:
            for job in jobs:
                await queue.enqueue(job)
        assert len({id(marker) for marker in fx_probe.scopes}) == len(jobs)

    async def test_kind_without_task_is_refused(self, fx_container: AsyncContainer) -> None:
        """Verify the in-process broker refuses a job whose kind has no registered task, as with the empty registry.

        :param fx_container: Container of the worker.
        :type fx_container: AsyncContainer
        """
        job = make_job(project_id=PROJECT_ID)
        async with running_taskiq_queue(fx_container, tasks=JOB_TASKS) as queue:
            with pytest.raises(SendTaskError):
                await queue.enqueue(job)
