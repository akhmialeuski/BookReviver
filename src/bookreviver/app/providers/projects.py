"""Provider of the projects feature: the services of projects, pages, places, sources and scans, and their runtime."""

from dishka import Provider, Scope, provide

from bookreviver.api.routing import IIIF_ROOT
from bookreviver.ports.imaging import Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
from bookreviver.ports.storage import AssetStore, SourceStore
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.pages import PageImaging, PageRuntime, PageService
from bookreviver.services.pagination import PaginationService
from bookreviver.services.places import PlaceService
from bookreviver.services.projects import ProjectService
from bookreviver.services.sources import SourceService
from bookreviver.services.stage_summaries import StageSummaries
from bookreviver.services.steps import StepRunner


class ProjectsProvider(Provider):
    """Builds the project, page, place and source services, one per request."""

    scope = Scope.REQUEST

    @provide
    def stage_summaries(self, uow: UnitOfWork, catalogue: ProcessorCatalog) -> StageSummaries:
        """Build the sums of the stages of books over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param catalogue: The processors of the application, which say which stages are available.
        :type catalogue: ProcessorCatalog
        :returns: The stage summaries of the request.
        :rtype: StageSummaries
        """
        return StageSummaries(uow=uow, catalogue=catalogue)

    @provide
    def projects(
        self, uow: UnitOfWork, clock: Clock, sources: SourceStore, assets: AssetStore, stages: StageSummaries
    ) -> ProjectService:
        """Build the project service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param clock: Clock of the application.
        :type clock: Clock
        :param sources: Source store of the application.
        :type sources: SourceStore
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param stages: Sums of the stages of books of the request.
        :type stages: StageSummaries
        :returns: The project service of the request.
        :rtype: ProjectService
        """
        return ProjectService(uow=uow, clock=clock, sources=sources, assets=assets, stages=stages)

    @provide(scope=Scope.APP)
    def page_runtime(
        self, publisher: EventPublisher, clock: Clock, order_keys: OrderKeys, queue: JobQueue
    ) -> PageRuntime:
        """Gather what a page use case reports through and orders by.

        :param publisher: Publisher of the application's event bus.
        :type publisher: EventPublisher
        :param clock: Clock of the application.
        :type clock: Clock
        :param order_keys: Order keys of the application.
        :type order_keys: OrderKeys
        :param queue: Queue handing jobs to the broker.
        :type queue: JobQueue
        :returns: The runtime of the page service.
        :rtype: PageRuntime
        """
        return PageRuntime(publisher=publisher, clock=clock, order_keys=order_keys, queue=queue)

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
        base_versions = BaseVersions(assets=assets, tiler=tiler, iiif_root=IIIF_ROOT)
        return PageImaging(base_versions=base_versions, runner=runner)

    @provide
    def pages(self, uow: UnitOfWork, assets: AssetStore, runtime: PageRuntime, imaging: PageImaging) -> PageService:
        """Build the page service over the request's or job's unit of work.

        :param uow: Unit of work of the current request or job.
        :type uow: UnitOfWork
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param runtime: The publisher, clock, order keys and job queue of the application.
        :type runtime: PageRuntime
        :param imaging: The builder of base versions and the runner of their processors.
        :type imaging: PageImaging
        :returns: The page service of the request or job.
        :rtype: PageService
        """
        return PageService(uow=uow, assets=assets, runtime=runtime, imaging=imaging)

    @provide
    def pagination(self, uow: UnitOfWork, publisher: EventPublisher, clock: Clock) -> PaginationService:
        """Build the pagination service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param publisher: Publisher of the application's event bus.
        :type publisher: EventPublisher
        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The pagination service of the request.
        :rtype: PaginationService
        """
        return PaginationService(uow=uow, publisher=publisher, clock=clock)

    @provide
    def places(self, uow: UnitOfWork, clock: Clock) -> PlaceService:
        """Build the place service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param clock: Clock of the application.
        :type clock: Clock
        :returns: The place service of the request.
        :rtype: PlaceService
        """
        return PlaceService(uow=uow, clock=clock)

    @provide
    def sources(self, uow: UnitOfWork, sources: SourceStore, assets: AssetStore) -> SourceService:
        """Build the source service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param sources: Source store of the application.
        :type sources: SourceStore
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :returns: The source service of the request.
        :rtype: SourceService
        """
        return SourceService(uow=uow, sources=sources, assets=assets)
