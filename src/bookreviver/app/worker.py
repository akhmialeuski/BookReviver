"""Background worker: the Taskiq broker the settings select, and the tasks it runs.

``JOB_TASKS`` is the task registry, one entry point per ``JobKind``. ``create_broker`` registers every entry under the
value of its kind, which is the name ``TaskiqJobQueue`` kicks, so the queue never imports a task. Each entry point
takes the text of the job's identifier and its services as ``FromDishka`` parameters, and calls one service method.
dishka's Taskiq integration opens a request scope per task, so a task gets its own unit of work, like an HTTP request.

A job kind without an entry cannot be enqueued, and the in-process broker refuses it at once.

The in-process broker runs tasks in the API process, which suits one machine. The Redis broker is selectable in the
settings but refused until taskiq-redis is a dependency.
"""

from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import TYPE_CHECKING
from uuid import UUID

from dishka import AsyncContainer
from dishka.integrations.taskiq import FromDishka, inject, setup_dishka
from taskiq import InMemoryBroker

from bookreviver.app.settings import JobBroker
from bookreviver.domain.enums import JobKind
from bookreviver.domain.ids import JobId
from bookreviver.services.imports import ImportService
from bookreviver.services.pages import PageService
from bookreviver.services.processing_jobs import ProcessingJobs

if TYPE_CHECKING:
    from collections.abc import Mapping

    from taskiq import AsyncBroker

# An entry point: the job identifier as text, then services marked ``FromDishka``, which dishka resolves by name
type JobTask = Callable[..., Awaitable[None]]

REDIS_BROKER_PENDING: str = 'The Redis job broker needs taskiq-redis, which is not installed yet.'


async def import_source(job_id: str, container: FromDishka[AsyncContainer]) -> None:
    """Run an import job: the entry point of ``JobKind.IMPORT_SOURCE``.

    The service is taken from the request scope of the task, which is the ``container`` dishka injects, so it gets the
    task's own unit of work. It is not a parameter of its own: dishka resolves the parameters of a task by their
    annotations at runtime, and a service imported only for an annotation would be moved out of the module's runtime
    imports by the linter, where dishka could no longer find it.

    :param job_id: Identifier of the job as text, the one argument the queue sends.
    :type job_id: str
    :param container: Request-scoped container of the task.
    :type container: AsyncContainer
    """
    service = await container.get(ImportService)
    await service.run_import(JobId(UUID(job_id)))


async def prepare_pages(job_id: str, container: FromDishka[AsyncContainer]) -> None:
    """Run a job that writes the images of pending page versions: the entry point of ``JobKind.PREPARE_PAGES``.

    :param job_id: Identifier of the job as text, the one argument the queue sends.
    :type job_id: str
    :param container: Request-scoped container of the task.
    :type container: AsyncContainer
    """
    service = await container.get(PageService)
    await service.prepare_images(JobId(UUID(job_id)))


async def run_stage(job_id: str, container: FromDishka[AsyncContainer]) -> None:
    """Run a stage over its pages: the entry point of ``JobKind.RUN_STAGE``.

    :param job_id: Identifier of the job as text, the one argument the queue sends.
    :type job_id: str
    :param container: Request-scoped container of the task.
    :type container: AsyncContainer
    """
    jobs = await container.get(ProcessingJobs)
    await jobs.run_stage(JobId(UUID(job_id)))


async def preview_step(job_id: str, container: FromDishka[AsyncContainer]) -> None:
    """Preview the steps of a form on a page: the entry point of ``JobKind.PREVIEW_STEP``.

    :param job_id: Identifier of the job as text, the one argument the queue sends.
    :type job_id: str
    :param container: Request-scoped container of the task.
    :type container: AsyncContainer
    """
    jobs = await container.get(ProcessingJobs)
    await jobs.preview_step(JobId(UUID(job_id)))


async def cut_tiles(job_id: str, container: FromDishka[AsyncContainer]) -> None:
    """Cut the tile pyramids of some page versions: the entry point of ``JobKind.CUT_TILES``.

    :param job_id: Identifier of the job as text, the one argument the queue sends.
    :type job_id: str
    :param container: Request-scoped container of the task.
    :type container: AsyncContainer
    """
    jobs = await container.get(ProcessingJobs)
    await jobs.cut_tiles(JobId(UUID(job_id)))


async def collect_versions(job_id: str, container: FromDishka[AsyncContainer]) -> None:
    """Delete the old page versions nothing needs: the entry point of ``JobKind.COLLECT_VERSIONS``.

    :param job_id: Identifier of the job as text, the one argument the queue sends.
    :type job_id: str
    :param container: Request-scoped container of the task.
    :type container: AsyncContainer
    """
    jobs = await container.get(ProcessingJobs)
    await jobs.collect_versions(JobId(UUID(job_id)))


async def measure_book(job_id: str, container: FromDishka[AsyncContainer]) -> None:
    """Measure the book and write the medians into its normalize step: the entry point of ``JobKind.MEASURE_BOOK``.

    :param job_id: Identifier of the job as text, the one argument the queue sends.
    :type job_id: str
    :param container: Request-scoped container of the task.
    :type container: AsyncContainer
    """
    jobs = await container.get(ProcessingJobs)
    await jobs.measure_book(JobId(UUID(job_id)))


JOB_TASKS: Mapping[JobKind, JobTask] = MappingProxyType(
    {
        JobKind.IMPORT_SOURCE: import_source,
        JobKind.PREPARE_PAGES: prepare_pages,
        JobKind.RUN_STAGE: run_stage,
        JobKind.PREVIEW_STEP: preview_step,
        JobKind.CUT_TILES: cut_tiles,
        JobKind.COLLECT_VERSIONS: collect_versions,
        JobKind.MEASURE_BOOK: measure_book,
    }
)


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
