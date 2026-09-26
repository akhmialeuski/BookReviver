"""Provider of the projects feature: the project and page services."""

from dishka import Provider, Scope, provide

from bookreviver.ports.persistence import UnitOfWork
from bookreviver.ports.runtime import Clock
from bookreviver.ports.storage import AssetStore, SourceStore
from bookreviver.services.pages import PageService
from bookreviver.services.projects import ProjectService


class ProjectsProvider(Provider):
    """Builds the project and page services, one of each per request."""

    scope = Scope.REQUEST

    @provide
    def projects(self, uow: UnitOfWork, clock: Clock, sources: SourceStore, assets: AssetStore) -> ProjectService:
        """Build the project service over the request's unit of work."""
        return ProjectService(uow, clock, sources, assets)

    @provide
    def pages(self, uow: UnitOfWork, assets: AssetStore) -> PageService:
        """Build the page service over the request's unit of work."""
        return PageService(uow, assets)
