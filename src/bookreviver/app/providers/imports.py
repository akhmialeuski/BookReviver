"""Provider of the import feature: the job board, and the job and import services.

The board is an application-scoped generator, so it is built when first needed and its tasks are cancelled when the
container closes. It is built with the runner ``job_runner`` makes, which knows the container the jobs resolve their
services from, so the provider is the one place where the board and its runner meet. The board is also where a book
opens its job queue, so ``prepare_start`` registers its ``open`` as the opener of ``BookJobs``.

The job service and the cutter of scans keep no state and are built once per application. The import service is built
once per request or job from the inspector, the cutter, the order keys and the limits, which live as long as the
application, and the processing service of the scope, which queues the split of the imported pages. The limits come
from the settings, and the IIIF root from the mount point of the IIIF routes, which only ``api`` knows. The
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
from bookreviver.services.imports import ImportLimits, ImportService
from bookreviver.services.jobs import JobService
from bookreviver.services.page_history import PageHistoryService
from bookreviver.services.pages import PageService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.scan_cuts import ScanCutter
from bookreviver.services.stage_records import StageRecords
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
    def scan_cutter(
        self, rasterizer: PageRasterizer, base_versions: BaseVersions, runner: StepRunner, records: StageRecords
    ) -> ScanCutter:
        """Build the cutter of the images of a scan and the base version of its pages, which keeps no state.

        :param rasterizer: Page rasterizer of the application.
        :type rasterizer: PageRasterizer
        :param base_versions: Builder of base versions of the application, which also cuts the files of a scan.
        :type base_versions: BaseVersions
        :param runner: Runner of processors of the application, which makes the base version of a page.
        :type runner: StepRunner
        :param records: Writer of the stage records.
        :type records: StageRecords
        :returns: The cutter of scans.
        :rtype: ScanCutter
        """
        return ScanCutter(rasterizer=rasterizer, base_versions=base_versions, runner=runner, records=records)

    @provide(scope=Scope.REQUEST)
    def import_service(
        self,
        inspector: SourceInspector,
        cutter: ScanCutter,
        order_keys: OrderKeys,
        limits: ImportLimits,
        processing: ProcessingService,
    ) -> ImportService:
        """Build the import service of the request or job.

        :param inspector: Source inspector of the application.
        :type inspector: SourceInspector
        :param cutter: Cutter of the images of a scan and the base version of its pages.
        :type cutter: ScanCutter
        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param limits: Bounds of an upload and of an import.
        :type limits: ImportLimits
        :param processing: Processing service of the scope, which queues the split of the imported pages.
        :type processing: ProcessingService
        :returns: The import service.
        :rtype: ImportService
        """
        return ImportService(
            inspector=inspector, cutter=cutter, order_keys=order_keys, limits=limits, processing=processing
        )
