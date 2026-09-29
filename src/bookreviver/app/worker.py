"""Background worker: the Taskiq broker the settings select, and the tasks it runs.

``JOB_TASKS`` is the task registry, one entry point per ``JobKind``. ``create_broker`` registers every entry under the
value of its kind, which is the name ``TaskiqJobQueue`` kicks, so the queue never imports a task. Each entry point
takes the text of the job's identifier and its services as ``FromDishka`` parameters, and calls one service method.
dishka's Taskiq integration opens a request scope per task, so a task gets its own unit of work, like an HTTP request.

The registry is empty until the import job arrives. A job kind without an entry cannot be enqueued, and the in-process
broker refuses it at once.

The in-process broker runs tasks in the API process, which suits one machine. The Redis broker is selectable in the
settings but refused until taskiq-redis is a dependency.
"""

from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import TYPE_CHECKING

from dishka.integrations.taskiq import inject, setup_dishka
from taskiq import InMemoryBroker

from bookreviver.app.settings import JobBroker

if TYPE_CHECKING:
    from collections.abc import Mapping

    from dishka import AsyncContainer
    from taskiq import AsyncBroker

    from bookreviver.domain.enums import JobKind

# An entry point: the job identifier as text, then services marked ``FromDishka``, which dishka resolves by name
type JobTask = Callable[..., Awaitable[None]]

JOB_TASKS: Mapping[JobKind, JobTask] = MappingProxyType({})
REDIS_BROKER_PENDING: str = 'The Redis job broker needs taskiq-redis, which is not installed yet.'


def create_broker(
    kind: JobBroker, container: AsyncContainer, *, tasks: Mapping[JobKind, JobTask] = JOB_TASKS
) -> AsyncBroker:
    """Build the broker of the given kind with every task registered, resolving task dependencies from ``container``.

    :param kind: Broker the settings select.
    :type kind: JobBroker
    :param container: Application container, which opens a request scope for every task.
    :type container: AsyncContainer
    :param tasks: Entry point of every job kind, registered under the kind's value.
    :type tasks: Mapping[JobKind, JobTask]
    :returns: The broker, not yet started.
    :rtype: AsyncBroker
    :raises NotImplementedError: For the Redis broker, until taskiq-redis is a dependency.
    """
    if kind is JobBroker.REDIS:
        raise NotImplementedError(REDIS_BROKER_PENDING)
    broker = InMemoryBroker()
    for job_kind, task in tasks.items():
        broker.register_task(inject(task, patch_module=True), task_name=job_kind)
    setup_dishka(container, broker)
    return broker


async def stop_broker(broker: AsyncBroker) -> None:
    """Let the jobs running in this process finish, then shut the broker down.

    :param broker: Started broker to stop.
    :type broker: AsyncBroker
    """
    if isinstance(broker, InMemoryBroker):
        await broker.wait_all()
    await broker.shutdown()
