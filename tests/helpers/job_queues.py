"""Every JobQueue adapter set up with a consumer a test can read back, for the port contract and the adapter tests.

``RecordingJobQueue`` is its own consumer. ``TaskiqJobQueue`` hands jobs to a worker, so it runs on the worker's
in-process broker with a probe task registered under a job kind, as the worker registers a real entry point. The
probe takes a service from the container, like an entry point does, so a job reaching it has gone the whole way: the
queue kicked by the kind's name, the broker found the task, and dishka opened a request scope for it.
"""

import enum
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING
from uuid import UUID

import anyio.lowlevel
from attrs import frozen
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.taskiq import FromDishka

from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.jobs.taskiq_queue import TaskiqJobQueue
from bookreviver.app.settings import JobBroker
from bookreviver.app.worker import create_broker, stop_broker
from bookreviver.domain.enums import JobKind
from bookreviver.domain.ids import JobId

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable, Mapping, Sequence

    from dishka import AsyncContainer

    from bookreviver.app.worker import JobTask
    from bookreviver.ports.runtime import JobQueue

PROBE_KIND: JobKind = JobKind.IMPORT_SOURCE


class QueueAdapter(enum.StrEnum):
    """An adapter of the JobQueue port under test."""

    RECORDING = 'recording'
    TASKIQ = 'taskiq'


class RequestMarker:
    """An object built once per request scope, so two task runs sharing one would show up."""


class Probe:
    """Records the jobs its task was started for, and the request scope each run got.

    :ivar started: Identifier of every job the task ran for, in order.
    :ivar scopes: The request-scoped marker each run received, one per run.
    """

    def __init__(self) -> None:
        """Start with no runs recorded."""
        self.started: list[JobId] = []
        self.scopes: list[RequestMarker] = []


class ProbeProvider(Provider):
    """Provides the probe to every task and a fresh marker to each one."""

    def __init__(self, probe: Probe) -> None:
        """Provide ``probe`` to every task.

        :param probe: Probe the task records into.
        :type probe: Probe
        """
        super().__init__()
        self._probe = probe

    @provide(scope=Scope.APP)
    def probe(self) -> Probe:
        """Return the probe.

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


@frozen(kw_only=True)
class QueueUnderTest:
    """A job queue and the way to read back what reached its consumer.

    :ivar queue: The adapter under test.
    :ivar delivered: Returns the identifiers of the jobs its consumer received, in order.
    """

    queue: JobQueue
    delivered: Callable[[], Sequence[JobId]]


@asynccontextmanager
async def running_taskiq_queue(
    container: AsyncContainer, *, tasks: Mapping[JobKind, JobTask]
) -> AsyncGenerator[TaskiqJobQueue]:
    """Start an in-process broker with ``tasks`` registered, and stop it once its running tasks are done.

    :param container: Container the tasks resolve their dependencies from.
    :type container: AsyncContainer
    :param tasks: Entry point of every job kind the broker runs.
    :type tasks: Mapping[JobKind, JobTask]
    :returns: Generator yielding a Taskiq queue over the started broker.
    :rtype: AsyncGenerator[TaskiqJobQueue]
    """
    broker = create_broker(JobBroker.IN_PROCESS, container, tasks=tasks)
    await broker.startup()
    try:
        yield TaskiqJobQueue(broker)
    finally:
        await stop_broker(broker)


@asynccontextmanager
async def open_queue_under_test(adapter: QueueAdapter) -> AsyncGenerator[QueueUnderTest]:
    """Set up an adapter with its consumer; leaving waits until every enqueued job has reached the consumer.

    :param adapter: Adapter to set up.
    :type adapter: QueueAdapter
    :returns: Generator yielding the queue and the reader of its consumer.
    :rtype: AsyncGenerator[QueueUnderTest]
    """
    match adapter:
        case QueueAdapter.RECORDING:
            recording = RecordingJobQueue()
            yield QueueUnderTest(queue=recording, delivered=lambda: [job.id for job in recording.enqueued])
        case QueueAdapter.TASKIQ:
            probe = Probe()
            container = make_async_container(ProbeProvider(probe))
            try:
                async with running_taskiq_queue(container, tasks=PROBE_TASKS) as taskiq:
                    yield QueueUnderTest(queue=taskiq, delivered=lambda: list(probe.started))
            finally:
                await container.close()
