"""Tests for what only the Taskiq job queue does, beyond the JobQueue contract in ``tests/contracts``.

The probe task and the broker setup come from ``tests.helpers.job_queues``, which the contract suite uses too.
"""

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from delayed_assert import assert_expectations, expect
from dishka import make_async_container
from taskiq.exceptions import SendTaskError

from bookreviver.adapters.tracing.recording import RecordingTracer
from bookreviver.app.worker import JOB_TASKS
from bookreviver.domain.enums import JobKind, SpanAttribute, SpanName
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
        """Verify the in-process broker refuses a job whose kind has no registered task, as with an empty registry.

        :param fx_container: Container of the worker.
        :type fx_container: AsyncContainer
        """
        job = make_job(project_id=PROJECT_ID)
        async with running_taskiq_queue(fx_container, tasks={}) as queue:
            with pytest.raises(SendTaskError):
                await queue.enqueue(job)


class TestJobSpan:
    """Tests for the span the worker puts around every task it registers."""

    async def test_task_runs_inside_a_root_job_span_of_its_kind_and_job(self, fx_probe: Probe) -> None:
        """Verify each task is timed as one ``job`` span that names the kind and the job, and has no parent.

        The job is enqueued inside the span of a request, which the in-process broker copies into the task it runs the
        job in, so the span of the job would be its child without the ``parent`` of ``traced``. The probe task still
        receives the services dishka resolves for it, so the wrapper passes them on.

        :param fx_probe: Probe the task records into.
        :type fx_probe: Probe
        """
        tracer = RecordingTracer()
        container = make_async_container(ProbeProvider(fx_probe, tracer))
        job = make_job(project_id=PROJECT_ID)
        try:
            async with running_taskiq_queue(container, tasks=PROBE_TASKS) as queue:
                with tracer.span(SpanName.HTTP_REQUEST):
                    await queue.enqueue(job)
        finally:
            await container.close()
        (span,) = tracer.named(SpanName.JOB)
        expect(span.attributes == {SpanAttribute.JOB_KIND: job.kind, SpanAttribute.JOB_ID: str(job.id)})
        expect(span.parent is None)
        expect(fx_probe.started == [job.id])
        assert_expectations()


class TestJobTasks:
    """Tests for the task registry of the worker."""

    def test_every_job_kind_has_an_entry_point(self) -> None:
        """Verify no job kind can be enqueued without a task to run it, so no job stays queued for ever."""
        assert set(JOB_TASKS) == set(JobKind)
