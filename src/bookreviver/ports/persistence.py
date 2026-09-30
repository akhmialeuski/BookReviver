"""Persistence ports: repositories per aggregate and the unit of work that commits them together.

Services never see a database. They open a ``UnitOfWork``, read and change entities through its repositories, and
commit, so every change of one use case lands in one transaction. The in-memory and SQLAlchemy adapters both run the
contract suite in ``tests/contracts``, which is what makes them interchangeable.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, override

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

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """

    @abstractmethod
    async def add(self, entity: EntityT) -> EntityT:
        """Store a new entity and return it as stored.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises ConflictError: If an entity with this identifier is already stored.
        """

    @abstractmethod
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity and return it as stored.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity is not stored.
        """

    @abstractmethod
    async def delete(self, entity_id: IdT) -> None:
        """Remove the entity, together with everything that belongs to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """


class ProjectRepository(Repository[Project, ProjectId]):
    """Projects, listed per owning account."""

    @abstractmethod
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return the owner's projects with their page counts, most recently updated first, ties by identifier.

        :param owner_id: Account owning the projects.
        :type owner_id: AccountId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The projects of the window with their page counts, and the number of all the owner's projects.
        :rtype: Slice[ProjectOverview]
        """


class PageRepository(ABC):
    """Pages of a project, addressed by their position in the book."""

    @abstractmethod
    async def get(self, project_id: ProjectId, index: int) -> Page:
        """Return one page.

        :param project_id: Project owning the page.
        :type project_id: ProjectId
        :param index: Position of the page in the book, starting at 0.
        :type index: int
        :returns: The stored page.
        :rtype: Page
        :raises NotFoundError: If the project has no page at this index.
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        """Return the pages of a project in book order.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The pages of the window and the number of all the project's pages.
        :rtype: Slice[Page]
        """

    @abstractmethod
    async def replace_for_project(self, project_id: ProjectId, pages: Sequence[Page]) -> None:
        """Replace every page of a project with the given pages.

        :param project_id: Project whose pages are replaced.
        :type project_id: ProjectId
        :param pages: The project's new pages, each keyed by its own index.
        :type pages: Sequence[Page]
        :raises NotFoundError: If the project is not stored.
        """

    @abstractmethod
    async def update(self, page: Page) -> Page:
        """Replace the stored state of one page and return it as stored.

        :param page: Page with its new state, addressed by its project and index.
        :type page: Page
        :returns: The page as stored.
        :rtype: Page
        :raises NotFoundError: If the page is not stored.
        """


class JobRepository(Repository[Job, JobId]):
    """Background jobs."""

    @abstractmethod
    @override
    async def add(self, entity: Job) -> Job:
        """Store a new job of a stored project and return it as stored.

        :param entity: Job to store, with its identifier already assigned.
        :type entity: Job
        :returns: The job as stored.
        :rtype: Job
        :raises ConflictError: If a job with this identifier is already stored.
        :raises NotFoundError: If the job's project is not stored.
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        """Return the project's jobs in one of the given states, newest first.

        :param project_id: Project owning the jobs.
        :type project_id: ProjectId
        :param states: States a returned job may be in.
        :type states: Collection[JobState]
        :returns: The matching jobs, most recently created first.
        :rtype: Sequence[Job]
        """

    @abstractmethod
    async def update_if_state(self, entity: Job, *, expected: Collection[JobState]) -> Job | None:
        """Replace the stored job only while its state is one of ``expected``, checking and writing as one step.

        A job changes state from two places at once, a worker finishing it and an account holder cancelling it, so
        a read followed by ``update`` could overwrite a state committed in between. Here the check reads the latest
        committed state, as a database ``UPDATE ... WHERE`` does, and a job changed by another transaction since this
        one read it is judged by that newer state.

        :param entity: Job with its new state.
        :type entity: Job
        :param expected: States the stored job must be in for the replacement to happen.
        :type expected: Collection[JobState]
        :returns: The job as stored, or None when its stored state is not one of ``expected`` and nothing changed.
        :rtype: Job | None
        :raises NotFoundError: If the job is not stored.
        """


class UnitOfWork(ABC):
    """One transaction over every repository; nothing is visible to others before ``commit``.

    :ivar projects: Project repository of this transaction.
    :ivar pages: Page repository of this transaction.
    :ivar jobs: Job repository of this transaction.
    """

    projects: ProjectRepository
    pages: PageRepository
    jobs: JobRepository

    @abstractmethod
    async def commit(self) -> None:
        """Make every change since the last commit durable and visible.

        A job written by ``JobRepository.update_if_state`` keeps its guarded state until this commit: a database holds
        the row locked, so another writer waits, and an adapter without locks refuses the commit instead when another
        transaction changed that job in the meantime.

        :raises ConflictError: If a job this transaction wrote by ``update_if_state`` was changed and committed by
                               another transaction since; nothing of this transaction is kept then.
        """

    @abstractmethod
    async def rollback(self) -> None:
        """Discard every change since the last commit."""
