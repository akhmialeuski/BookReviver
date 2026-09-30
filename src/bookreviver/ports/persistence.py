"""Persistence ports: repositories per aggregate and the unit of work that commits them together.

Services never see a database. They open a ``UnitOfWork``, read and change entities through its repositories, and
commit, so every change of one use case lands in one transaction. The in-memory and SQLAlchemy adapters both run the
contract suite in ``tests/contracts``, which is what makes them interchangeable.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, override

from bookreviver.domain.entities import Job, Page, PageVersion, Project, Scan, Source
from bookreviver.domain.ids import JobId, PageId, PageVersionId, ProjectId, ScanId, SourceId

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.entities import ProjectOverview
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
        :raises ConflictError: If an entity with this identifier, or with a value its port declares unique, is
                               already stored.
        :raises NotFoundError: If an entity it refers to, such as its project, is not stored.
        """

    @abstractmethod
    async def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities at once, all of them or, on an error, none.

        After an error the unit of work is rolled back before it is used again, as a database requires.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises ConflictError: If an identifier or a unique value of one entity is stored already or given twice.
        :raises NotFoundError: If an entity one of them refers to is not stored.
        """

    @abstractmethod
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity and return it as stored.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity, or an entity it refers to, is not stored.
        :raises ConflictError: If its new state takes a value its port declares unique from another entity.
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
        """Return the owner's projects with their counts, most recently updated first, ties by identifier.

        :param owner_id: Account owning the projects.
        :type owner_id: AccountId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The projects of the window with the counts of their included pages, sources and scans, and the
                  number of all the owner's projects.
        :rtype: Slice[ProjectOverview]
        """

    @abstractmethod
    async def overview(self, project: Project) -> ProjectOverview:
        """Return a project read earlier together with the counts of its book.

        :param project: Project whose book is counted.
        :type project: Project
        :returns: The project with the counts of its included pages, sources and scans.
        :rtype: ProjectOverview
        """


class SourceRepository(Repository[Source, SourceId]):
    """Sources of the projects; the pair of a project and the digest of a source's main file is unique.

    Deleting a source removes its scans, and the pages of the book made from them stay without their scan.
    """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId) -> Sequence[Source]:
        """Return the project's sources in the order they were imported, ties by identifier.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :returns: Every source of the project, the earliest import first.
        :rtype: Sequence[Source]
        """

    @abstractmethod
    async def find_by_sha256(self, project_id: ProjectId, sha256: str) -> Source | None:
        """Return the project's source whose main file has this digest, which refuses a second upload of the file.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :param sha256: SHA-256 digest of a main file as lower-case hexadecimal digits.
        :type sha256: str
        :returns: The source with this digest, or None when the project has none.
        :rtype: Source | None
        """


class ScanRepository(Repository[Scan, ScanId]):
    """Scans of the sources; the pair of a source and the number of a scan in it is unique."""

    @abstractmethod
    async def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source in their order in the source.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source, by number.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Scan]:
        """Return the scans of a project, source by source in import order and by number within a source.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The scans of the window and the number of all the project's scans.
        :rtype: Slice[Scan]
        """


class PageRepository(Repository[Page, PageId]):
    """Pages of the book, addressed by their identifier and ordered by their order key.

    Within a project an order key is unique, and a part of a scan, the pair of a scan and a slot, belongs to one page
    at most. A page keeps its row when its scan is deleted, and loses only the reference to it. Deleting a page
    removes its versions.
    """

    @abstractmethod
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        """Return the pages of a project in book order, the byte order of their order keys.

        The position of a page in the book is the offset of the window plus its index in the window.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :returns: The pages of the window and the number of all the project's pages, included or not.
        :rtype: Slice[Page]
        """

    @abstractmethod
    async def last_order_key(self, project_id: ProjectId) -> str | None:
        """Return the order key of the last page of a project, after which new pages are appended.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The greatest order key of the project's pages, or None for a book without pages.
        :rtype: str | None
        """


class PageVersionRepository(Repository[PageVersion, PageVersionId]):
    """Versions of the pages of the book; a version whose input version is deleted keeps its row without the input."""

    @abstractmethod
    async def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page in the order they were created, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page, the earliest first.
        :rtype: Sequence[PageVersion]
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
    :ivar sources: Source repository of this transaction.
    :ivar scans: Scan repository of this transaction.
    :ivar pages: Page repository of this transaction.
    :ivar page_versions: Page version repository of this transaction.
    :ivar jobs: Job repository of this transaction.
    """

    projects: ProjectRepository
    sources: SourceRepository
    scans: ScanRepository
    pages: PageRepository
    page_versions: PageVersionRepository
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
