"""Provider of the runs of steps: the runner of processors, the engine and the opener of runs, and the work of the jobs.

The runner, the engine that runs the steps of a job and the detector of the content of pages keep no state of a job, so
one of each serves the whole application. What opens the work of a run and the work of the processing jobs are built
for each job, and work on the book they are given.
"""

from dishka import Provider, Scope, provide

from bookreviver.core.plugins import ProcessorCatalog
from bookreviver.ports.imaging import RenditionWriter, Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.processing import StepExecutor
from bookreviver.ports.tracing import Tracer
from bookreviver.services.content_detection import ContentDetector
from bookreviver.services.pages import PageService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts
from bookreviver.services.stage_runs import StageRuns
from bookreviver.services.stage_work import StageEngine
from bookreviver.services.steps import StepRunner


class RunsProvider(Provider):
    """Builds the runner and the engine of the runs once per application, and the work of the jobs per job."""

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

    @provide(scope=Scope.APP)
    def stage_engine(
        self, runner: StepRunner, processors: ProcessorCatalog, tracer: Tracer, config: ProcessingConfig
    ) -> StageEngine:
        """Build what runs the steps of every job, which keeps no state of a job.

        :param runner: Runner of processors.
        :type runner: StepRunner
        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param tracer: Tracer of the application.
        :type tracer: Tracer
        :param config: The size of a preview and the pages a run processes together.
        :type config: ProcessingConfig
        :returns: The engine of the runs.
        :rtype: StageEngine
        """
        return StageEngine(runner=runner, processors=processors, tracer=tracer, config=config)

    @provide(scope=Scope.REQUEST)
    def stage_runs(self, engine: StageEngine, parts: ProcessingParts, order_keys: OrderKeys) -> StageRuns:
        """Build what opens the work of the runs and the previews of a job.

        :param engine: What runs the steps.
        :type engine: StageEngine
        :param parts: The parts the processing use cases share, of which the recipes and the stage records are used.
        :type parts: ProcessingParts
        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :returns: The opener of the work of the runs.
        :rtype: StageRuns
        """
        return StageRuns(engine=engine, recipes=parts.recipes, records=parts.records, order_keys=order_keys)

    @provide(scope=Scope.REQUEST)
    def content_detector(
        self, runner: StepRunner, processors: ProcessorCatalog, parts: ProcessingParts
    ) -> ContentDetector:
        """Build the detection of the content of the pages.

        :param runner: Runner of processors.
        :type runner: StepRunner
        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param parts: The parts the processing use cases share, of which the stage records are used.
        :type parts: ProcessingParts
        :returns: The detector.
        :rtype: ContentDetector
        """
        return ContentDetector(runner=runner, processors=processors, records=parts.records)

    @provide(scope=Scope.REQUEST)
    def processing_jobs(
        self, runs: StageRuns, detector: ContentDetector, parts: ProcessingParts, pages: PageService
    ) -> ProcessingJobs:
        """Build the work of the processing jobs.

        :param runs: What opens the work of the runs and the previews.
        :type runs: StageRuns
        :param detector: The detection of the content of the pages.
        :type detector: ContentDetector
        :param parts: The parts the processing use cases share.
        :type parts: ProcessingParts
        :param pages: The page service of the job, which queues the detection of the content of the pages a split made.
        :type pages: PageService
        :returns: The jobs.
        :rtype: ProcessingJobs
        """
        return ProcessingJobs(runs=runs, detector=detector, parts=parts, pages=pages)
