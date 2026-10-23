"""Provider of the books feature: the services of books, pages, places, sources and scans, and their runtime."""

from dishka import Provider, Scope, provide

from bookreviver.ports.imaging import Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.ports.runtime import Clock, EventPublisher, JobBoard
from bookreviver.ports.storage import AssetStore, BookArchive, SourceStore
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.books import BooksService, BookStores
from bookreviver.services.freshness import BookFreshness
from bookreviver.services.library import Library
from bookreviver.services.pages import PageImaging, PageRuntime, PageService
from bookreviver.services.pagination import PaginationService
from bookreviver.services.places import PlaceService
from bookreviver.services.sources import SourceService
from bookreviver.services.stage_summaries import StageSummaries
from bookreviver.services.steps import StepRunner


class BooksProvider(Provider):
    """Builds the book, page, place and source services, one per request."""

    scope = Scope.REQUEST

    @provide
    def stage_summaries(self, catalogue: ProcessorCatalog, board: JobBoard) -> StageSummaries:
        """Build the sums of the stages of books, which read through the repositories their caller passes.

        :param catalogue: The processors of the application, which say which stages are available.
        :type catalogue: ProcessorCatalog
        :param board: Board holding the jobs of every book, which tells the stages that are running.
        :type board: JobBoard
        :returns: The stage summaries of the request.
        :rtype: StageSummaries
        """
        return StageSummaries(catalogue=catalogue, board=board)

    @provide
    def freshness(self, uow: UnitOfWork, library: Library, board: JobBoard) -> BookFreshness:
        """Build the freshness of the reads of the books of a request.

        :param uow: Unit of work of the request.
        :type uow: UnitOfWork
        :param library: Library of the request.
        :type library: Library
        :param board: Board of the jobs of the books.
        :type board: JobBoard
        :returns: The freshness of the reads of the books.
        :rtype: BookFreshness
        """
        return BookFreshness(uow=uow, library=library, board=board)

    @provide
    def library(self, uow: UnitOfWork) -> Library:
        """Build the library over the request's unit of work, which opens the books of the acting account.

        :param uow: Unit of work of the current request or job.
        :type uow: UnitOfWork
        :returns: The library of the request.
        :rtype: Library
        """
        return Library(uow=uow)

    @provide(scope=Scope.APP)
    def book_files(self, sources: SourceStore, assets: AssetStore, archive: BookArchive) -> BookStores:
        """Gather the stores whose files go when a book is deleted, and the archive of a whole book.

        :param sources: Source store of the application.
        :type sources: SourceStore
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param archive: Archive of a whole book.
        :type archive: BookArchive
        :returns: The stores of the files of a book.
        :rtype: BookStores
        """
        return BookStores(sources=sources, assets=assets, archive=archive)

    @provide
    def books(
        self, uow: UnitOfWork, library: Library, clock: Clock, files: BookStores, stages: StageSummaries
    ) -> BooksService:
        """Build the books service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param library: Library of the current request.
        :type library: Library
        :param clock: Clock of the application.
        :type clock: Clock
        :param files: Source store and asset store of the application.
        :type files: BookStores
        :param stages: Sums of the stages of books of the request.
        :type stages: StageSummaries
        :returns: The books service of the request.
        :rtype: BooksService
        """
        return BooksService(uow=uow, library=library, clock=clock, files=files, stages=stages)

    @provide(scope=Scope.APP)
    def page_runtime(
        self, publisher: EventPublisher, clock: Clock, order_keys: OrderKeys, board: JobBoard
    ) -> PageRuntime:
        """Gather what a page use case reports through and orders by.

        :param publisher: Publisher of the application's event bus.
        :type publisher: EventPublisher
        :param clock: Clock of the application.
        :type clock: Clock
        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param board: Board holding the jobs of every book.
        :type board: JobBoard
        :returns: The runtime of the page service.
        :rtype: PageRuntime
        """
        return PageRuntime(publisher=publisher, clock=clock, order_keys=order_keys, board=board)

    @provide(scope=Scope.APP)
    def page_imaging(self, assets: AssetStore, tiler: Tiler, runner: StepRunner) -> PageImaging:
        """Gather what builds base versions and runs their processors, whose pyramids are served from the IIIF root.

        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param tiler: Tiler of the application.
        :type tiler: Tiler
        :param runner: Runner of processors of the application.
        :type runner: StepRunner
        :returns: The builder of base versions and the runner of their processors.
        :rtype: PageImaging
        """
        base_versions = BaseVersions(assets=assets, tiler=tiler)
        return PageImaging(base_versions=base_versions, runner=runner)

    @provide
    def pages(
        self, uow: UnitOfWork, library: Library, assets: AssetStore, runtime: PageRuntime, imaging: PageImaging
    ) -> PageService:
        """Build the page service over the request's or job's unit of work.

        :param uow: Unit of work of the current request or job.
        :type uow: UnitOfWork
        :param library: Library of the current request or job.
        :type library: Library
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param runtime: The publisher, clock, order keys and job board of the application.
        :type runtime: PageRuntime
        :param imaging: The builder of base versions and the runner of their processors.
        :type imaging: PageImaging
        :returns: The page service of the request or job.
        :rtype: PageService
        """
        return PageService(uow=uow, library=library, assets=assets, runtime=runtime, imaging=imaging)

    @provide
    def pagination(self, uow: UnitOfWork, library: Library, clock: Clock) -> PaginationService:
        """Build the pagination service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param library: Library of the current request.
        :type library: Library
        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The pagination service of the request.
        :rtype: PaginationService
        """
        return PaginationService(uow=uow, library=library, clock=clock)

    @provide
    def places(self, uow: UnitOfWork, library: Library, clock: Clock) -> PlaceService:
        """Build the place service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param library: Library of the current request.
        :type library: Library
        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The place service of the request.
        :rtype: PlaceService
        """
        return PlaceService(uow=uow, library=library, clock=clock)

    @provide
    def sources(
        self, uow: UnitOfWork, library: Library, board: JobBoard, sources: SourceStore, assets: AssetStore
    ) -> SourceService:
        """Build the source service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param library: Library of the current request.
        :type library: Library
        :param board: Board holding the jobs of every book.
        :type board: JobBoard
        :param sources: Source store of the application.
        :type sources: SourceStore
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :returns: The source service of the request.
        :rtype: SourceService
        """
        return SourceService(uow=uow, library=library, board=board, sources=sources, assets=assets)
