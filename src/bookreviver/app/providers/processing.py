"""Provider of the processing feature: the catalogue of processors, and the services that run recipes and keep edits.

The catalogue and the runner of processors keep no per-request state, so one of each serves the whole application, and
the catalogue loads the plugins of the pools the settings name when it is built. Everything that works through a
unit of work is built per request or job: the parts the use cases share, the service of the requests, the jobs of the
workers and the service of the manual edits. The IIIF root comes from the mount point of the IIIF routes, which only
``api`` knows, as it does for the import.
"""

from datetime import timedelta

from dishka import Provider, Scope, provide

from bookreviver.api.routing import IIIF_ROOT
from bookreviver.app.plugins import EntryPointCatalog
from bookreviver.app.settings import Settings
from bookreviver.ports.imaging import RenditionWriter, Tiler
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
from bookreviver.ports.storage import AssetStore
from bookreviver.services.edits import EditService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts, ProcessingRuntime
from bookreviver.services.recipes import DefaultRecipes
from bookreviver.services.stage_runs import StageRuntime
from bookreviver.services.steps import StepRunner


class ProcessingProvider(Provider):
    """Builds the processing adapters once per application, and the processing services per request or job."""

    @provide(scope=Scope.APP)
    def processor_catalog(self, settings: Settings) -> ProcessorCatalog:
        """Load the processors of the pools this process serves from the entry points.

        :param settings: Application settings, of which ``processing.worker_pools`` is read.
        :type settings: Settings
        :returns: The catalogue of processors.
        :rtype: ProcessorCatalog
        """
        return EntryPointCatalog(pools=settings.processing.worker_pools)

    @provide(scope=Scope.APP)
    def default_recipes(self) -> DefaultRecipes:
        """Give the recipes a stage starts with.

        :returns: The default recipes.
        :rtype: DefaultRecipes
        """
        return DefaultRecipes()

    @provide(scope=Scope.APP)
    def processing_config(self, settings: Settings) -> ProcessingConfig:
        """Read the retention periods and the size of a preview from the settings.

        :param settings: Application settings, of which the ``processing`` and ``imaging`` groups are read.
        :type settings: Settings
        :returns: The bounds of processing.
        :rtype: ProcessingConfig
        """
        return ProcessingConfig(
            version_retention=timedelta(days=settings.processing.version_retention_days),
            preview_retention=timedelta(hours=settings.processing.preview_retention_hours),
            preview_long_side_px=settings.imaging.preview_long_side_px,
        )

    @provide(scope=Scope.APP)
    def processing_runtime(self, publisher: EventPublisher, clock: Clock, queue: JobQueue) -> ProcessingRuntime:
        """Gather what a processing use case reports through.

        :param publisher: Publisher of the application's event bus.
        :type publisher: EventPublisher
        :param clock: Clock of the application.
        :type clock: Clock
        :param queue: Queue handing jobs to the broker.
        :type queue: JobQueue
        :returns: The runtime.
        :rtype: ProcessingRuntime
        """
        return ProcessingRuntime(publisher=publisher, clock=clock, queue=queue)

    @provide(scope=Scope.APP)
    def step_runner(
        self, assets: AssetStore, catalogue: ProcessorCatalog, renditions: RenditionWriter, tiler: Tiler
    ) -> StepRunner:
        """Build the runner of processors over the asset store and the imaging ports.

        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param renditions: Writer of the files of a page version.
        :type renditions: RenditionWriter
        :param tiler: Tiler of the application.
        :type tiler: Tiler
        :returns: The runner.
        :rtype: StepRunner
        """
        return StepRunner(assets=assets, catalogue=catalogue, renditions=renditions, tiler=tiler, iiif_root=IIIF_ROOT)

    @provide(scope=Scope.REQUEST)
    def processing_parts(
        self,
        uow: UnitOfWork,
        catalogue: ProcessorCatalog,
        defaults: DefaultRecipes,
        runtime: ProcessingRuntime,
        config: ProcessingConfig,
    ) -> ProcessingParts:
        """Build the parts the processing use cases share over the unit of work of the request or job.

        :param uow: Unit of work of the current request or job.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param defaults: The recipes a stage starts with.
        :type defaults: DefaultRecipes
        :param runtime: The publisher, the clock and the queue.
        :type runtime: ProcessingRuntime
        :param config: The retention periods and the size of a preview.
        :type config: ProcessingConfig
        :returns: The parts.
        :rtype: ProcessingParts
        """
        return ProcessingParts.build(uow, catalogue, defaults, runtime, config)

    @provide(scope=Scope.REQUEST)
    def processing_service(
        self, uow: UnitOfWork, catalogue: ProcessorCatalog, parts: ProcessingParts
    ) -> ProcessingService:
        """Build the processing service of a request.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param parts: The parts the processing use cases share.
        :type parts: ProcessingParts
        :returns: The processing service.
        :rtype: ProcessingService
        """
        return ProcessingService(uow=uow, catalogue=catalogue, parts=parts)

    @provide(scope=Scope.APP)
    def stage_runtime(
        self, runner: StepRunner, catalogue: ProcessorCatalog, runtime: ProcessingRuntime, config: ProcessingConfig
    ) -> StageRuntime:
        """Gather what a run of a stage works with, apart from the project and the unit of work.

        :param runner: Runner of processors.
        :type runner: StepRunner
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param runtime: The publisher, the clock and the queue.
        :type runtime: ProcessingRuntime
        :param config: The retention periods and the size of a preview.
        :type config: ProcessingConfig
        :returns: The runtime of a stage run.
        :rtype: StageRuntime
        """
        return StageRuntime(
            runner=runner,
            catalogue=catalogue,
            publisher=runtime.publisher,
            clock=runtime.clock,
            preview_long_side_px=config.preview_long_side_px,
        )

    @provide(scope=Scope.REQUEST)
    def processing_jobs(
        self, uow: UnitOfWork, assets: AssetStore, runtime: StageRuntime, parts: ProcessingParts
    ) -> ProcessingJobs:
        """Build the work of the processing jobs over the unit of work of a job.

        :param uow: Unit of work of the current job.
        :type uow: UnitOfWork
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param runtime: What a run of a stage works with.
        :type runtime: StageRuntime
        :param parts: The parts the processing use cases share.
        :type parts: ProcessingParts
        :returns: The jobs.
        :rtype: ProcessingJobs
        """
        return ProcessingJobs(uow=uow, assets=assets, runtime=runtime, parts=parts)

    @provide(scope=Scope.REQUEST)
    def edit_service(
        self, uow: UnitOfWork, assets: AssetStore, catalogue: ProcessorCatalog, parts: ProcessingParts, clock: Clock
    ) -> EditService:
        """Build the edit service of a request.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param catalogue: The processors the application can run.
        :type catalogue: ProcessorCatalog
        :param parts: The parts the processing use cases share, of which the stage records are used.
        :type parts: ProcessingParts
        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The edit service.
        :rtype: EditService
        """
        return EditService(uow=uow, assets=assets, catalogue=catalogue, records=parts.records, clock=clock)
