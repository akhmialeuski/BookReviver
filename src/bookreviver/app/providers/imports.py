"""Provider of the import feature: the job board, and the job and import services.

The board is an application-scoped generator, so it is built when first needed and its tasks are cancelled when the
container closes. It is built with the runner ``job_runner`` makes, which knows the container the jobs resolve their
services from, so the provider is the one place where the board and its runner meet. The board is also where a book
opens its job queue, so ``prepare_start`` registers its ``open`` as the opener of ``BookJobs``.

The job service keeps no state and is built once per application. The import service is built once per request or job
from three parts that keep no state of their own and so live as long as the application: the stores, the imaging ports,
and the order keys and limits. The limits
come from the settings, and the IIIF root from the mount point of the IIIF routes, which only ``api`` knows. The
import entry ``import_source`` and the upload route resolve the same service.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

from dishka import AsyncContainer, Provider, Scope, provide

from bookreviver.adapters.jobs.board import InMemoryJobBoard
from bookreviver.app.jobs import JobWorkers, job_runner, job_workers
from bookreviver.app.settings import Settings
from bookreviver.ports.imaging import PageRasterizer, SourceInspector
from bookreviver.ports.ordering import OrderKeys
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.imports import (
    ImportImaging,
    ImportLimits,
    ImportRuntime,
    ImportService,
)
from bookreviver.services.job_starter import JobStarter
from bookreviver.services.jobs import JobService
from bookreviver.services.page_history import PageHistoryService
from bookreviver.services.pages import PageService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.recipes import RecipeBook
from bookreviver.services.steps import StepRunner


class ImportsProvider(Provider):
    """Builds the job board and the job service once per application, and the import service per request or job."""

    @provide(scope=Scope.REQUEST)
    def workers(
        self,
        imports: ImportService,
        pages: PageService,
        processing: ProcessingJobs,
        choices: ProcessingService,
        history: PageHistoryService,
    ) -> JobWorkers:
        """Name the worker of every kind of job for the request scope one job runs in.

        :param imports: Import service of the scope.
        :type imports: ImportService
        :param pages: Page service of the scope.
        :type pages: PageService
        :param processing: The work of the processing jobs of the scope.
        :type processing: ProcessingJobs
        :param choices: Processing service of the scope, which makes a chosen version current.
        :type choices: ProcessingService
        :param history: Page history service of the scope, which clears a step on a page.
        :type history: PageHistoryService
        :returns: The workers by the kind of job.
        :rtype: JobWorkers
        """
        return job_workers(imports, pages, processing, choices, history)

    @provide(scope=Scope.APP)
    async def job_board(self, settings: Settings, container: AsyncContainer) -> AsyncIterator[InMemoryJobBoard]:
        """Open the job board, which opens the job queue of each book.

        The board is closed, which cancels the jobs running, when the application closes the container.

        :param settings: Application settings, of which ``jobs.kept_ended`` is read.
        :type settings: Settings
        :param container: Application container, which opens a request scope for every job.
        :type container: AsyncContainer
        :returns: Iterator yielding the open board and closing it afterwards.
        :rtype: AsyncIterator[InMemoryJobBoard]
        """
        # An exit stack rather than `async with` around the yield: dishka drives this generator (ASYNC119)
        stack = AsyncExitStack()
        try:
            yield await stack.enter_async_context(
                InMemoryJobBoard(runner=job_runner(container), kept_ended=settings.jobs.kept_ended)
            )
        finally:
            await stack.aclose()

    @provide(scope=Scope.APP)
    def job_service(self) -> JobService:
        """Build the job service, which keeps no state and so lives as long as the application.

        :returns: The job service.
        :rtype: JobService
        """
        return JobService()

    @provide(scope=Scope.APP)
    def import_limits(self, settings: Settings) -> ImportLimits:
        """Read the bounds of an upload and of an import from the settings.

        :param settings: Application settings, of which the upload limits and ``imaging.parallel_scans`` are read.
        :type settings: Settings
        :returns: The limits, with the IIIF root of the routes.
        :rtype: ImportLimits
        """
        return ImportLimits(
            max_files=settings.max_upload_files,
            max_bytes=settings.max_upload_bytes,
            parallel_scans=settings.imaging.parallel_scans,
        )

    @provide(scope=Scope.APP)
    def import_imaging(
        self, inspector: SourceInspector, rasterizer: PageRasterizer, base_versions: BaseVersions, runner: StepRunner
    ) -> ImportImaging:
        """Gather the imaging ports an import reads its sources and cuts its scans with.

        :param inspector: Source inspector of the application.
        :type inspector: SourceInspector
        :param rasterizer: Page rasterizer of the application.
        :type rasterizer: PageRasterizer
        :param base_versions: Builder of base versions of the application, which also cuts the files of a scan.
        :type base_versions: BaseVersions
        :param runner: Runner of processors of the application, which makes the base version of a page.
        :type runner: StepRunner
        :returns: The imaging ports.
        :rtype: ImportImaging
        """
        return ImportImaging(inspector=inspector, rasterizer=rasterizer, base_versions=base_versions, runner=runner)

    @provide(scope=Scope.APP)
    def import_runtime(self, order_keys: OrderKeys, limits: ImportLimits) -> ImportRuntime:
        """Gather what an import orders by and is bounded by.

        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param limits: Bounds of an upload and of an import.
        :type limits: ImportLimits
        :returns: The runtime of an import.
        :rtype: ImportRuntime
        """
        return ImportRuntime(order_keys=order_keys, limits=limits)

    @provide(scope=Scope.REQUEST)
    def import_service(
        self, imaging: ImportImaging, runtime: ImportRuntime, recipes: RecipeBook, starter: JobStarter
    ) -> ImportService:
        """Build the import service of the request or job.

        :param imaging: The imaging ports of the application.
        :type imaging: ImportImaging
        :param runtime: The order keys and limits of the application.
        :type runtime: ImportRuntime
        :param recipes: The recipes of the books, whose recipe of the page split says whether imported pages are split.
        :type recipes: RecipeBook
        :param starter: Putter of new jobs, with which the split of the imported pages is queued.
        :type starter: JobStarter
        :returns: The import service.
        :rtype: ImportService
        """
        return ImportService(imaging=imaging, runtime=runtime, recipes=recipes, starter=starter)
