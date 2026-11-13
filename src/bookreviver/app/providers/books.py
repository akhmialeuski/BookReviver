"""Provider of the books feature: the services of books, pages, places, sources and scans, and their runtime."""

from dishka import Provider, Scope, provide

from bookreviver.core.plugins import ProcessorCatalog
from bookreviver.ports.imaging import Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.persistence import CatalogueDatabase
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.book_steps import BookSteps
from bookreviver.services.books import BooksService
from bookreviver.services.freshness import BookFreshness
from bookreviver.services.pages import PageService
from bookreviver.services.pages_work import PageKeeping
from bookreviver.services.pagination import PaginationService
from bookreviver.services.places import PlaceService
from bookreviver.services.sources import SourceService
from bookreviver.services.stage_records import StageRecords
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
    def base_versions(self, tiler: Tiler, processors: ProcessorCatalog) -> BaseVersions:
        """Build the builder of base versions, which names their processors by the roles the catalogue lists.

        :param tiler: Tiler of the application.
        :type tiler: Tiler
        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :returns: The builder of base versions.
        :rtype: BaseVersions
        """
        return BaseVersions(tiler=tiler, processors=processors)

    @provide(scope=Scope.APP)
    def page_keeping(
        self,
        order_keys: OrderKeys,
        base_versions: BaseVersions,
        runner: StepRunner,
        processors: ProcessorCatalog,
        records: StageRecords,
    ) -> PageKeeping:
        """Build what keeps the pages of the books, which opens them for a transaction and keeps no state of a request.

        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param base_versions: Builder of base versions of the application.
        :type base_versions: BaseVersions
        :param runner: Runner of processors of the application, which writes the files of base versions.
        :type runner: StepRunner
        :param processors: The processors the application can run, whose plugins may read the whole book.
        :type processors: ProcessorCatalog
        :param records: Writer of the stage records.
        :type records: StageRecords
        :returns: The keeping of the pages.
        :rtype: PageKeeping
        """
        return PageKeeping(
            order_keys=order_keys,
            base_versions=base_versions,
            runner=runner,
            book_steps=BookSteps(processors),
            records=records,
        )

    @provide
    def pages(self, books: BooksService, keeping: PageKeeping) -> PageService:
        """Build the page service over the request's or job's books service.

        :param books: Books service of the current request or job, which opens the books a job runs on.
        :type books: BooksService
        :param keeping: What keeps the pages of the books.
        :type keeping: PageKeeping
        :returns: The page service of the request or job.
        :rtype: PageService
        """
        return PageService(books=books, keeping=keeping)

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
