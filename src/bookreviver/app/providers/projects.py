"""Provider of the projects feature: the project service."""

from dishka import Provider, Scope, provide

from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.runtime import Clock
from bookreviver.ports.storage import AssetStore, SourceStore
from bookreviver.services.projects import ProjectService


class ProjectsProvider(Provider):
    """Builds the project service, one per request."""

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
