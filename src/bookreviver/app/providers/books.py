"""Provider of the books feature: the services of books, pages, places, sources and scans, and their runtime."""

from dishka import Provider, Scope, provide

from bookreviver.ports.imaging import Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.persistence import BookDatabases, UnitOfWork
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.ports.runtime import Clock, EventPublisher, JobBoard
from bookreviver.ports.storage import BookArchive, BookFileStores
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.book import BookTools
from bookreviver.services.books import BooksService
from bookreviver.services.freshness import BookFreshness
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
    def freshness(self, board: JobBoard) -> BookFreshness:
        """Build the freshness of the reads of the books of a request.

        :param board: Board of the jobs of the books.
        :type board: JobBoard
        :returns: The freshness of the reads of the books.
        :rtype: BookFreshness
        """
        return BookFreshness(board=board)

    @provide
    def book_tools(self, stages: StageSummaries, clock: Clock, archive: BookArchive) -> BookTools:
        """Gather what every book a request opens works with besides its own database.

        :param stages: Sums of the stages of books of the request.
        :type stages: StageSummaries
        :param clock: Clock of the application.
        :type clock: Clock
        :param archive: Archive of a whole book.
        :type archive: BookArchive
        :returns: The tools of the books of the request.
        :rtype: BookTools
        """
        return BookTools(stages=stages, clock=clock, archive=archive)

    @provide
    def books(
        self, uow: UnitOfWork, databases: BookDatabases, tools: BookTools, stores: BookFileStores
    ) -> BooksService:
        """Build the books service over the request's unit of work and book databases.

        :param uow: Unit of work of the current request, which runs the transactions of the catalogue.
        :type uow: UnitOfWork
        :param databases: Opens the database of a book, which runs the transactions of that book.
        :type databases: BookDatabases
        :param tools: What every book the service opens works with.
        :type tools: BookTools
        :param stores: Opens the source store and the asset store of a book.
        :type stores: BookFileStores
        :returns: The books service of the request.
        :rtype: BooksService
        """
        return BooksService(uow=uow, databases=databases, tools=tools, stores=stores)

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
    def page_imaging(self, tiler: Tiler, runner: StepRunner) -> PageImaging:
        """Gather what builds base versions and runs their processors, whose pyramids are served from the IIIF root.

        :param tiler: Tiler of the application.
        :type tiler: Tiler
        :param runner: Runner of processors of the application.
        :type runner: StepRunner
        :returns: The builder of base versions and the runner of their processors.
        :rtype: PageImaging
        """
        base_versions = BaseVersions(tiler=tiler)
        return PageImaging(base_versions=base_versions, runner=runner)

    @provide
    def pages(
        self, books: BooksService, stores: BookFileStores, runtime: PageRuntime, imaging: PageImaging
    ) -> PageService:
        """Build the page service over the request's or job's books service.

        :param books: Books service of the current request or job, which opens the books a job runs on.
        :type books: BooksService
        :param stores: Opens the asset store of a book, whose files a viewer reads by key.
        :type stores: BookFileStores
        :param runtime: The publisher, clock, order keys and job board of the application.
        :type runtime: PageRuntime
        :param imaging: The builder of base versions and the runner of their processors.
        :type imaging: PageImaging
        :returns: The page service of the request or job.
        :rtype: PageService
        """
        return PageService(books=books, stores=stores, runtime=runtime, imaging=imaging)

    @provide
    def pagination(self, clock: Clock) -> PaginationService:
        """Build the pagination service over the request's unit of work.

        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The pagination service of the request.
        :rtype: PaginationService
        """
        return PaginationService(clock=clock)

    @provide
    def places(self, clock: Clock) -> PlaceService:
        """Build the place service over the request's unit of work.

        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The place service of the request.
        :rtype: PlaceService
        """
        return PlaceService(clock=clock)

    @provide
    def sources(self, board: JobBoard) -> SourceService:
        """Build the source service of the request.

        :param board: Board holding the jobs of every book.
        :type board: JobBoard
        :returns: The source service of the request.
        :rtype: SourceService
        """
        return SourceService(board=board)
