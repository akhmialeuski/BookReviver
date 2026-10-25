"""Provider of the books feature: the services of books, pages, places, sources and scans, and their runtime."""

from dishka import Provider, Scope, provide

from bookreviver.ports.imaging import Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.persistence import CatalogueDatabase
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.books import BooksService
from bookreviver.services.freshness import BookFreshness
from bookreviver.services.pages import PageImaging, PageService
from bookreviver.services.pagination import PaginationService
from bookreviver.services.places import PlaceService
from bookreviver.services.sources import SourceService
from bookreviver.services.stage_summaries import StageSummaries
from bookreviver.services.steps import StepRunner


class BooksProvider(Provider):
    """Builds the book and page services once per request, and the services that keep no state once per application."""

    scope = Scope.REQUEST

    @provide(scope=Scope.APP)
    def stage_summaries(self, processors: ProcessorCatalog) -> StageSummaries:
        """Build the sums of the stages of books, which hold the processors and read through the book they are given.

        :param processors: The processors of the application, which say which stages are available.
        :type processors: ProcessorCatalog
        :returns: The stage summaries of the application.
        :rtype: StageSummaries
        """
        return StageSummaries(processors=processors)

    @provide(scope=Scope.APP)
    def freshness(self) -> BookFreshness:
        """Build the freshness of the reads of the books, which keeps no state.

        :returns: The freshness of the reads of the books.
        :rtype: BookFreshness
        """
        return BookFreshness()

    @provide
    def books(self, catalogue: CatalogueDatabase) -> BooksService:
        """Build the books service over the request's catalogue database.

        :param catalogue: Catalogue database of the current request, which runs the transactions of the catalogue.
        :type catalogue: CatalogueDatabase
        :returns: The books service of the request.
        :rtype: BooksService
        """
        return BooksService(catalogue=catalogue)

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
    def pages(self, books: BooksService, order_keys: OrderKeys, imaging: PageImaging) -> PageService:
        """Build the page service over the request's or job's books service.

        :param books: Books service of the current request or job, which opens the books a job runs on.
        :type books: BooksService
        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param imaging: The builder of base versions and the runner of their processors.
        :type imaging: PageImaging
        :returns: The page service of the request or job.
        :rtype: PageService
        """
        return PageService(books=books, order_keys=order_keys, imaging=imaging)

    @provide(scope=Scope.APP)
    def pagination(self) -> PaginationService:
        """Build the pagination service, which works on the book it is given and keeps no state of a request.

        :returns: The pagination service of the application.
        :rtype: PaginationService
        """
        return PaginationService()

    @provide(scope=Scope.APP)
    def places(self) -> PlaceService:
        """Build the place service, which works on the book it is given and keeps no state.

        :returns: The place service of the application.
        :rtype: PlaceService
        """
        return PlaceService()

    @provide(scope=Scope.APP)
    def sources(self) -> SourceService:
        """Build the source service, which works on the book it is given and keeps no state.

        :returns: The source service of the application.
        :rtype: SourceService
        """
        return SourceService()
