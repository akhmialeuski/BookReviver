"""Background worker: the Taskiq broker the settings select, and the tasks it runs.

Each task is named after the ``JobKind`` it runs, which is how ``TaskiqJobQueue`` finds it. dishka's Taskiq
integration opens a request scope per task, so a task gets its own unit of work, like an HTTP request.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from dishka.integrations.taskiq import FromDishka, inject, setup_dishka
from taskiq import InMemoryBroker

from bookreviver.app.settings import JobBroker
from bookreviver.domain.enums import JobKind
from bookreviver.domain.ids import JobId
from bookreviver.services.imports import ImportService

if TYPE_CHECKING:
    from dishka import AsyncContainer
    from taskiq import AsyncBroker

REDIS_BROKER_PENDING: str = 'The Redis job broker needs taskiq-redis, which is not installed yet.'

# dishka reads task annotations at runtime, so the service is named in a runtime alias rather than a bare annotation
ImportServiceDep = FromDishka[ImportService]


async def import_source(job_id: str, service: ImportServiceDep) -> None:
    """Run the import job ``job_id``, given as the text of its UUID."""
    await service.run_import(JobId(UUID(job_id)))


def create_broker(kind: JobBroker, container: AsyncContainer) -> AsyncBroker:
    """Build the broker of the given kind with every task registered, resolving task dependencies from ``container``.

    :raises NotImplementedError: For the Redis broker, until taskiq-redis is a dependency.
    """
    if kind is JobBroker.REDIS:
        raise NotImplementedError(REDIS_BROKER_PENDING)
    broker = InMemoryBroker()
    broker.register_task(inject(import_source, patch_module=True), task_name=JobKind.IMPORT_SOURCE)
    setup_dishka(container, broker)
    return broker


async def stop_broker(broker: AsyncBroker) -> None:
    """Let jobs running in this process finish, then shut the broker down."""
    if isinstance(broker, InMemoryBroker):
        await broker.wait_all()
    await broker.shutdown()
