"""Provider of the import feature: the job board, and the job and import services.

The board is an application-scoped generator, so it is built when first needed and its tasks are cancelled when the
container closes. It is built with the runner of ``app/jobs.py``, which knows the container the jobs resolve their
services from, so the provider is the one place where the board and its runner meet.

The import service is built once per request or job from three parts that keep no state of their own and so live as
long as the application: the stores, the imaging ports, and the publisher, order keys and limits. The limits
come from the settings, and the IIIF root from the mount point of the IIIF routes, which only ``api`` knows. The
import entry in ``app/jobs.py`` and the upload route resolve the same service.
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack

from dishka import AsyncContainer, Provider, Scope, provide

from bookreviver.adapters.jobs.board import InMemoryJobBoard
from bookreviver.app.jobs import job_runner
from bookreviver.app.settings import Settings
from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.runtime import EventPublisher, EventStream, JobBoard
from bookreviver.services.books import BooksService
from bookreviver.services.imports import (
    ImportImaging,
    ImportLimits,
    ImportRuntime,
    ImportService,
)
from bookreviver.services.jobs import JobService
from bookreviver.services.processing_parts import ProcessingParts, ProcessingRuntime
from bookreviver.services.steps import StepRunner


class ImportsProvider(Provider):
    """Builds the job board once per application, and the import and job services per request or job."""

    @provide(scope=Scope.APP)
    async def job_board(
        self, settings: Settings, container: AsyncContainer, publisher: EventPublisher
    ) -> AsyncIterator[JobBoard]:
        """Open the job board, and close it, which cancels the jobs running, when the application closes the container.

        :param settings: Application settings, of which ``jobs.kept_ended`` is read.
        :type settings: Settings
        :param container: Application container, which opens a request scope for every job.
        :type container: AsyncContainer
        :param publisher: Publisher of the application's event bus.
        :type publisher: EventPublisher
        :returns: Iterator yielding the open board and closing it afterwards.
        :rtype: AsyncIterator[JobBoard]
        """
        # An exit stack rather than `async with` around the yield: dishka drives this generator (ASYNC119)
        stack = AsyncExitStack()
        try:
            yield await stack.enter_async_context(
                InMemoryJobBoard(
                    runner=job_runner(container),
                    publisher=publisher,
                    kept_ended=settings.jobs.kept_ended,
                )
            )
        finally:
            await stack.aclose()

    @provide(scope=Scope.REQUEST)
    def job_service(self, board: JobBoard, stream: EventStream) -> JobService:
        """Build the job service of the request or job.

        :param board: Board holding the jobs of every book.
        :type board: JobBoard
        :param stream: Stream of the application's event bus.
        :type stream: EventStream
        :returns: The job service.
        :rtype: JobService
        """
        return JobService(board=board, stream=stream)

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
        self, inspector: SourceInspector, rasterizer: PageRasterizer, tiler: Tiler, runner: StepRunner
    ) -> ImportImaging:
        """Gather the imaging ports an import reads its sources and cuts its scans with.

        :param inspector: Source inspector of the application.
        :type inspector: SourceInspector
        :param rasterizer: Page rasterizer of the application.
        :type rasterizer: PageRasterizer
        :param tiler: Tiler of the application.
        :type tiler: Tiler
        :param runner: Runner of processors of the application, which makes the base version of a page.
        :type runner: StepRunner
        :returns: The imaging ports.
        :rtype: ImportImaging
        """
        return ImportImaging(inspector=inspector, rasterizer=rasterizer, tiler=tiler, runner=runner)

    @provide(scope=Scope.APP)
    def import_runtime(
        self, processing: ProcessingRuntime, order_keys: OrderKeys, limits: ImportLimits
    ) -> ImportRuntime:
        """Gather what an import reports through and is bounded by.

        :param processing: The job board of the application.
        :type processing: ProcessingRuntime
        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param limits: Bounds of an upload and of an import.
        :type limits: ImportLimits
        :returns: The runtime of an import.
        :rtype: ImportRuntime
        """
        return ImportRuntime(
            order_keys=order_keys,
            limits=limits,
            board=processing.board,
        )

    @provide(scope=Scope.REQUEST)
    def import_service(
        self,
        books: BooksService,
        imaging: ImportImaging,
        runtime: ImportRuntime,
        parts: ProcessingParts,
    ) -> ImportService:
        """Build the import service over the books service of the request or job.

        :param books: Books service of the current request or job, which opens the book a job imports into.
        :type books: BooksService
        :param imaging: The imaging ports of the application.
        :type imaging: ImportImaging
        :param runtime: The publisher, order keys and limits of the application.
        :type runtime: ImportRuntime
        :param parts: The recipes and the job starter, with which the split of the imported pages is queued.
        :type parts: ProcessingParts
        :returns: The import service.
        :rtype: ImportService
        """
        return ImportService(books=books, imaging=imaging, runtime=runtime, parts=parts)
