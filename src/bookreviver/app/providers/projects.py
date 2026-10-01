"""Provider of the projects feature: the services of projects, pages, sources and scans, and what the pages share."""

from dishka import Provider, Scope, provide

from bookreviver.api.routing import IIIF_ROOT
from bookreviver.ports.imaging import BlankPageMaker, Tiler
from bookreviver.ports.ordering import OrderKeys
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
from bookreviver.ports.storage import AssetStore, SourceStore
from bookreviver.services.base_versions import BaseVersions
from bookreviver.services.pages import PageImaging, PageRuntime, PageService
from bookreviver.services.projects import ProjectService
from bookreviver.services.sources import SourceService


class ProjectsProvider(Provider):
    """Builds the project, page and source services, one per request."""

    scope = Scope.REQUEST

    @provide
    def projects(self, uow: UnitOfWork, clock: Clock, sources: SourceStore, assets: AssetStore) -> ProjectService:
        """Build the project service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param clock: Clock of the application.
        :type clock: Clock
        :param sources: Source store of the application.
        :type sources: SourceStore
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :returns: The project service of the request.
        :rtype: ProjectService
        """
        return ProjectService(uow=uow, clock=clock, sources=sources, assets=assets)

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
    def page_imaging(self, assets: AssetStore, tiler: Tiler, blank_maker: BlankPageMaker) -> PageImaging:
        """Gather what writes the images of base versions, whose pyramids are served from the IIIF root of the routes.

        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param tiler: Tiler of the application.
        :type tiler: Tiler
        :param blank_maker: Maker of blank leaves of the application.
        :type blank_maker: BlankPageMaker
        :returns: The base versions and the maker of blank leaves.
        :rtype: PageImaging
        """
        base_versions = BaseVersions(assets=assets, tiler=tiler, iiif_root=IIIF_ROOT)
        return PageImaging(base_versions=base_versions, blank_maker=blank_maker)

    @provide
    def pages(self, uow: UnitOfWork, assets: AssetStore, runtime: PageRuntime, imaging: PageImaging) -> PageService:
        """Build the page service over the request's or job's unit of work.

        :param uow: Unit of work of the current request or job.
        :type uow: UnitOfWork
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :param runtime: The publisher, clock, order keys and job queue of the application.
        :type runtime: PageRuntime
        :param imaging: The base versions and the maker of blank leaves of the application.
        :type imaging: PageImaging
        :returns: The page service of the request or job.
        :rtype: PageService
        """
        return PageService(uow=uow, assets=assets, runtime=runtime, imaging=imaging)

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
