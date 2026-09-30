"""Provider of the projects feature: the services of projects, pages, sources and scans."""

from dishka import Provider, Scope, provide

from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.runtime import Clock
from bookreviver.ports.storage import AssetStore, SourceStore
from bookreviver.services.pages import PageService
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

    @provide
    def pages(self, uow: UnitOfWork, assets: AssetStore) -> PageService:
        """Build the page service over the request's unit of work.

        :param uow: Unit of work of the current request.
        :type uow: UnitOfWork
        :param assets: Asset store of the application.
        :type assets: AssetStore
        :returns: The page service of the request.
        :rtype: PageService
        """
        return PageService(uow=uow, assets=assets)

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
