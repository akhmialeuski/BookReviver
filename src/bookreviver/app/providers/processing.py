"""Provider of the processing feature: the catalogue of processors and the services that run recipes and keep edits.

The catalogue, the executor and the runner of processors keep no per-request state, so one of each serves the whole
application. The catalogue loads the plugins of the pools the settings name when it is built, from the same source
that each process of the executor builds its own catalogue from. The executor starts its processes when the container
first builds it, which the lifespan does at start-up, and stops them when the container closes. Everything that serves a
request or a job is built for it, and works on the book it is given: the parts the use cases share, the service of the
requests, the jobs of the workers and the services of the manual edits, of the settings of pages, of carrying them
over, of what a run takes away and of the resets of steps. The IIIF root comes from the mount point of the IIIF routes,
which only ``api`` knows, as it does for the import.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack
from datetime import timedelta

from dishka import Provider, Scope, provide

from bookreviver.adapters.execution.process_pool import ProcessPoolStepExecutor
from bookreviver.app.plugins import EntryPointCatalogSource
from bookreviver.app.recipe_defaults import load_default_recipes
from bookreviver.app.settings import Settings
from bookreviver.ports.imaging import RenditionWriter, Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.processing import ProcessorCatalog, ProcessorSettings, StepExecutor
from bookreviver.ports.tracing import Tracer
from bookreviver.services.books import BooksService
from bookreviver.services.edits import EditService
from bookreviver.services.outdated_results import OutdatedResults
from bookreviver.services.page_carry import CarryOverService
from bookreviver.services.page_history import PageHistoryService
from bookreviver.services.page_settings import PageSettingsService
from bookreviver.services.pages import PageService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts
from bookreviver.services.recipe_order import RecipeOrder
from bookreviver.services.recipe_profiles import RecipeProfiles
from bookreviver.services.recipes import DefaultRecipes
from bookreviver.services.result_marks import ResultMarksService
from bookreviver.services.run_plans import RunImpactService
from bookreviver.services.stage_runs import StageRuntime
from bookreviver.services.steps import StepRunner


class ProcessingProvider(Provider):
    """Builds the processing adapters once per application, and the processing services per request or job."""

    @provide(scope=Scope.APP)
    def catalog_source(self, settings: Settings) -> EntryPointCatalogSource:
        """Name the processors to load, in a value that each process of the steps builds the same catalogue from.

        :param settings: Application settings, of which ``processing.worker_pools`` and ``processing.models_dir`` are
                         read.
        :type settings: Settings
        :returns: The source of the catalogue.
        :rtype: EntryPointCatalogSource
        """
        return EntryPointCatalogSource(
            pools=settings.processing.worker_pools,
            settings=ProcessorSettings(models_dir=settings.processing.models_dir.resolve()),
        )

    @provide(scope=Scope.APP)
    def processor_catalog(self, source: EntryPointCatalogSource) -> ProcessorCatalog:
        """Load the processors of the pools this process serves from the entry points.

        :param source: The source every catalogue of the application is built from.
        :type source: EntryPointCatalogSource
        :returns: The catalogue of processors.
        :rtype: ProcessorCatalog
        """
        return source()

    @provide(scope=Scope.APP)
    async def step_executor(self, source: EntryPointCatalogSource, settings: Settings) -> AsyncIterator[StepExecutor]:
        """Start the pool of processes that runs the steps of processors, and stop it with the application.

        :param source: The source each pool process builds its own catalogue from.
        :type source: EntryPointCatalogSource
        :param settings: Application settings, of which ``processing.step_processes`` and
                         ``processing.preview_step_processes`` are read.
        :type settings: Settings
        :returns: Iterator yielding the started executor, whose previews have processes of their own and whose full
                  runs have the rest, and stopping its processes afterwards.
        :rtype: AsyncIterator[StepExecutor]
        """
        previews = settings.processing.preview_step_processes
        # An exit stack rather than `async with` around the yield: dishka drives this generator (ASYNC119)
        stack = AsyncExitStack()
        try:
            yield await stack.enter_async_context(
                ProcessPoolStepExecutor(
                    source=source, run_slots=settings.processing.step_processes - previews, preview_slots=previews
                )
            )
        finally:
            await stack.aclose()

    @provide(scope=Scope.APP)
    def recipe_order(self, processors: ProcessorCatalog) -> RecipeOrder:
        """Read the order the processors ask for from their specs.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :returns: The finder of steps that stand off their place.
        :rtype: RecipeOrder
        """
        return RecipeOrder(processors)

    @provide(scope=Scope.APP)
    def default_recipes(self, settings: Settings) -> DefaultRecipes:
        """Read the recipes a stage starts with from the file the settings name, once.

        :param settings: Application settings, of which ``processing.recipes_file`` is read.
        :type settings: Settings
        :returns: The default recipes.
        :rtype: DefaultRecipes
        """
        return load_default_recipes(settings.processing.recipes_file)

    @provide(scope=Scope.APP)
    def processing_config(self, settings: Settings) -> ProcessingConfig:
        """Read the retention period and the size of a preview from the settings.

        :param settings: Application settings, of which the ``processing`` and ``imaging`` groups are read.
        :type settings: Settings
        :returns: The bounds of processing.
        :rtype: ProcessingConfig
        """
        return ProcessingConfig(
            preview_retention=timedelta(hours=settings.processing.preview_retention_hours),
            preview_long_side_px=settings.imaging.preview_long_side_px,
        )

    @provide(scope=Scope.APP)
    def step_runner(self, executor: StepExecutor, renditions: RenditionWriter, tiler: Tiler) -> StepRunner:
        """Build the runner of processors over the imaging ports.

        :param executor: Executor that runs the steps of the processors.
        :type executor: StepExecutor
        :param renditions: Writer of the files of a page version.
        :type renditions: RenditionWriter
        :param tiler: Tiler of the application.
        :type tiler: Tiler
        :returns: The runner.
        :rtype: StepRunner
        """
        return StepRunner(executor=executor, renditions=renditions, tiler=tiler)

    @provide(scope=Scope.REQUEST)
    def processing_parts(
        self,
        processors: ProcessorCatalog,
        defaults: DefaultRecipes,
        config: ProcessingConfig,
    ) -> ProcessingParts:
        """Build the parts the processing use cases share in the request or job.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param defaults: The recipes a stage starts with.
        :type defaults: DefaultRecipes
        :param config: The retention period and the size of a preview.
        :type config: ProcessingConfig
        :returns: The parts.
        :rtype: ProcessingParts
        """
        return ProcessingParts.build(processors, defaults, config)

    @provide(scope=Scope.REQUEST)
    def processing_service(self, processors: ProcessorCatalog, parts: ProcessingParts) -> ProcessingService:
        """Build the processing service of a request.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param parts: The parts the processing use cases share.
        :type parts: ProcessingParts
        :returns: The processing service.
        :rtype: ProcessingService
        """
        return ProcessingService(processors=processors, parts=parts)

    @provide(scope=Scope.REQUEST)
    def recipe_profiles(self, parts: ProcessingParts, processing: ProcessingService) -> RecipeProfiles:
        """Build the service of the recipe profiles of a request.

        :param parts: The parts the processing use cases share, of which the recipes are used.
        :type parts: ProcessingParts
        :param processing: The processing service of the request, which puts the steps of a profile into a recipe.
        :type processing: ProcessingService
        :returns: The profiles service.
        :rtype: RecipeProfiles
        """
        return RecipeProfiles(recipes=parts.recipes, processing=processing)

    @provide(scope=Scope.APP)
    def stage_runtime(
        self,
        runner: StepRunner,
        processors: ProcessorCatalog,
        tracer: Tracer,
        order_keys: OrderKeys,
        config: ProcessingConfig,
    ) -> StageRuntime:
        """Gather what a run of a stage works with, apart from the book.

        :param runner: Runner of processors.
        :type runner: StepRunner
        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param tracer: Tracer of the application.
        :type tracer: Tracer
        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param config: The retention period and the size of a preview.
        :type config: ProcessingConfig
        :returns: The runtime of a stage run.
        :rtype: StageRuntime
        """
        return StageRuntime(
            runner=runner,
            processors=processors,
            order_keys=order_keys,
            preview_long_side_px=config.preview_long_side_px,
            tracer=tracer,
        )

    @provide(scope=Scope.REQUEST)
    def processing_jobs(self, runtime: StageRuntime, parts: ProcessingParts, pages: PageService) -> ProcessingJobs:
        """Build the work of the processing jobs.

        :param runtime: What a run of a stage works with.
        :type runtime: StageRuntime
        :param parts: The parts the processing use cases share.
        :type parts: ProcessingParts
        :param pages: The page service of the job, which queues the detection of the content of the pages a split made.
        :type pages: PageService
        :returns: The jobs.
        :rtype: ProcessingJobs
        """
        return ProcessingJobs(runtime=runtime, parts=parts, pages=pages)

    @provide(scope=Scope.REQUEST)
    def edit_service(self, processors: ProcessorCatalog, parts: ProcessingParts) -> EditService:
        """Build the edit service of a request.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param parts: The parts the processing use cases share, of which the stage records are used.
        :type parts: ProcessingParts
        :returns: The edit service.
        :rtype: EditService
        """
        return EditService(processors=processors, records=parts.records)

    @provide(scope=Scope.REQUEST)
    def page_settings_service(self, processors: ProcessorCatalog, parts: ProcessingParts) -> PageSettingsService:
        """Build the page settings service of a request.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param parts: The parts the processing use cases share, of which the stage records are used.
        :type parts: ProcessingParts
        :returns: The page settings service.
        :rtype: PageSettingsService
        """
        return PageSettingsService(processors=processors, records=parts.records)

    @provide(scope=Scope.REQUEST)
    def run_impact_service(self, parts: ProcessingParts) -> RunImpactService:
        """Build the run impact service of a request.

        :param parts: The parts the processing use cases share, of which the recipes are used.
        :type parts: ProcessingParts
        :returns: The run impact service.
        :rtype: RunImpactService
        """
        return RunImpactService(recipes=parts.recipes)

    @provide(scope=Scope.REQUEST)
    def carry_over_service(self, parts: ProcessingParts) -> CarryOverService:
        """Build the carry-over service of a request.

        :param parts: The parts the processing use cases share, of which the stage records are used.
        :type parts: ProcessingParts
        :returns: The carry-over service.
        :rtype: CarryOverService
        """
        return CarryOverService(records=parts.records)

    @provide(scope=Scope.REQUEST)
    def page_history_service(self, parts: ProcessingParts) -> PageHistoryService:
        """Build the page history service of a request.

        :param parts: The parts the processing use cases share, of which the stage records and the job starter are used.
        :type parts: ProcessingParts
        :returns: The page history service.
        :rtype: PageHistoryService
        """
        return PageHistoryService(records=parts.records, starter=parts.starter)

    @provide(scope=Scope.REQUEST)
    def result_marks_service(self) -> ResultMarksService:
        """Build the result marks service of a request.

        :returns: The result marks service.
        :rtype: ResultMarksService
        """
        return ResultMarksService()

    @provide(scope=Scope.REQUEST)
    def outdated_results(self, books: BooksService, processors: ProcessorCatalog) -> OutdatedResults:
        """Build the finder of results that a replaced version of a processor made, over the database of the start.

        :param books: Books service of the start of the application, which opens each stored book.
        :type books: BooksService
        :param processors: The processors the application can run, with the versions that are installed.
        :type processors: ProcessorCatalog
        :returns: The outdated results service.
        :rtype: OutdatedResults
        """
        return OutdatedResults(books=books, processors=processors)
