"""Provider of the import feature: the Taskiq broker, the job queue and the import and job services."""

from collections.abc import AsyncIterator

from dishka import AsyncContainer, Provider, Scope, provide
from taskiq import AsyncBroker

from bookreviver.adapters.jobs.taskiq_queue import TaskiqJobQueue
from bookreviver.app.settings import Settings
from bookreviver.app.worker import create_broker, stop_broker
from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.runtime import Clock, EventPublisher, EventStream, JobQueue
from bookreviver.ports.storage import AssetStore, SourceStore
from bookreviver.services.imports import ImportImaging, ImportLimits, ImportRuntime, ImportService, ImportStorage
from bookreviver.services.jobs import JobService


class ImportsProvider(Provider):
    """Builds the broker and the job queue once per application, and the services once per request or job."""

    @provide(scope=Scope.APP)
    async def broker(self, settings: Settings, container: AsyncContainer) -> AsyncIterator[AsyncBroker]:
        """Start the broker the settings select, and stop it when the application closes the container."""
        broker = create_broker(settings.job_broker, container)
        await broker.startup()
        # try/finally rather than `async with` around the yield: dishka drives this generator (ASYNC119)
        try:
            yield broker
        finally:
            await stop_broker(broker)

    @provide(scope=Scope.APP)
    def job_queue(self, broker: AsyncBroker) -> JobQueue:
        """Build the queue that hands jobs to the broker."""
        return TaskiqJobQueue(broker)

    @provide(scope=Scope.APP)
    def import_storage(self, sources: SourceStore, assets: AssetStore) -> ImportStorage:
        """Group the storage adapters an import uses."""
        return ImportStorage(sources=sources, assets=assets)

    @provide(scope=Scope.APP)
    def import_imaging(self, inspector: SourceInspector, rasterizer: PageRasterizer, tiler: Tiler) -> ImportImaging:
        """Group the imaging adapters an import drives."""
        return ImportImaging(inspector=inspector, rasterizer=rasterizer, tiler=tiler)

    @provide(scope=Scope.APP)
    def import_runtime(self, queue: JobQueue, publisher: EventPublisher, clock: Clock) -> ImportRuntime:
        """Group the scheduling, event and time adapters an import uses."""
        return ImportRuntime(queue=queue, publisher=publisher, clock=clock)

    @provide(scope=Scope.APP)
    def import_limits(self, settings: Settings) -> ImportLimits:
        """Take the upload size limit and the page parallelism from the settings."""
        return ImportLimits(max_upload_bytes=settings.max_upload_bytes, parallel_pages=settings.imaging.parallel_pages)

    @provide(scope=Scope.REQUEST)
    def import_service(
        self,
        uow: UnitOfWork,
        storage: ImportStorage,
        imaging: ImportImaging,
        runtime: ImportRuntime,
        limits: ImportLimits,
    ) -> ImportService:
        """Build the import service over the request's unit of work."""
        return ImportService(uow=uow, storage=storage, imaging=imaging, runtime=runtime, limits=limits)

    @provide(scope=Scope.REQUEST)
    def job_service(self, uow: UnitOfWork, publisher: EventPublisher, stream: EventStream, clock: Clock) -> JobService:
        """Build the job service."""
        return JobService(uow=uow, publisher=publisher, stream=stream, clock=clock)
