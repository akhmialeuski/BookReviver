"""Persistence ports: repositories per aggregate and the unit of work that commits them together."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.entities import Job, Project
from bookreviver.domain.ids import JobId, ProjectId

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.entities import Page, ProjectOverview
    from bookreviver.domain.enums import JobState
    from bookreviver.domain.ids import AccountId
    from bookreviver.domain.values import Slice, SliceRequest


class Repository[EntityT, IdT](ABC):
    """Storage of one kind of entity addressed by its identifier."""

    @abstractmethod
    async def get(self, entity_id: IdT) -> EntityT:
        """Return the entity.

        :raises NotFoundError: If no entity has this identifier.
        """

    @abstractmethod
    async def add(self, entity: EntityT) -> EntityT:
        """Store a new entity and return it as stored."""

    @abstractmethod
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity and return it as stored.

        :raises NotFoundError: If the entity is not stored.
        """

    @abstractmethod
    async def delete(self, entity_id: IdT) -> None:
        """Remove the entity, together with everything that belongs to it.

        :raises NotFoundError: If no entity has this identifier.
        """


class ProjectRepository(Repository[Project, ProjectId]):
    """Projects, listed per owning account."""

    @abstractmethod
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return the owner's projects with their page counts, most recently updated first."""


class PageRepository(ABC):
    """Pages of a project, addressed by their position in the book."""

    @abstractmethod
    async def get(self, project_id: ProjectId, index: int) -> Page:
        """Return one page.

        :raises NotFoundError: If the project has no page at this index.
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        """Return the pages of a project in book order."""

    @abstractmethod
    async def replace_for_project(self, project_id: ProjectId, pages: Sequence[Page]) -> None:
        """Replace every page of a project with the given pages."""

    @abstractmethod
    async def update(self, page: Page) -> Page:
        """Replace the stored state of one page and return it as stored.

        :raises NotFoundError: If the page is not stored.
        """


class JobRepository(Repository[Job, JobId]):
    """Background jobs."""

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        """Return the project's jobs in one of the given states, newest first."""


class UnitOfWork(ABC):
    """One transaction over every repository; nothing is visible to others before ``commit``."""

    projects: ProjectRepository
    pages: PageRepository
    jobs: JobRepository

    @abstractmethod
    async def commit(self) -> None:
        """Make every change since the last commit durable and visible."""

    @abstractmethod
    async def rollback(self) -> None:
        """Discard every change since the last commit."""
