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
    from bookreviver.domain.enums import JobState, Side
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
    """Projects, listed per owning account.

    A project's cover is one of its own pages: ``add`` and ``update`` raise ``NotFoundError`` naming a cover page that
    is not stored or belongs to another project, and deleting the cover page leaves the project without a cover.
    """

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

    @abstractmethod
    async def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return several scans in one read, so a window of the book costs one query to learn its sources.

        :param scan_ids: Scans to read; one that is not stored is left out of the result.
        :type scan_ids: Collection[ScanId]
        :returns: The stored scans among them, in no particular order.
        :rtype: Sequence[Scan]
        """

    @abstractmethod
    async def list_unready(self, project_id: ProjectId) -> Sequence[Scan]:
        """Return the scans of a project whose renditions are not ready, which an import cut again.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :returns: The scans without ready renditions, source by source in import order and by number within a source.
        :rtype: Sequence[Scan]
        """


class PageRepository(Repository[Page, PageId]):
    """Pages of the book, addressed by their identifier and ordered by their order key.

    Within a project an order key is unique, and a part of a scan, the pair of a scan and a slot, belongs to one page
    at most. A page keeps its row when its scan is deleted, and loses only the reference to it. Deleting a page
    removes its versions.
    """

    @abstractmethod
    async def list_for_project(
        self, project_id: ProjectId, request: SliceRequest, *, included_only: bool = False
    ) -> Slice[Page]:
        """Return the pages of a project in book order, the byte order of their order keys.

        The position of a page in the book is the offset of the window plus its index in the window.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param request: Offset and limit of the window to return.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book, which then take no part in the
                              offset either.
        :type included_only: bool
        :returns: The pages of the window and the number of all the project's pages, or of its included pages when
                  ``included_only`` is set.
        :rtype: Slice[Page]
        """

    @abstractmethod
    async def list_by_ids(self, project_id: ProjectId, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a project in book order.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param page_ids: Identifiers of the pages to read, repeats counting once.
        :type page_ids: Collection[PageId]
        :returns: The pages, in the byte order of their order keys.
        :rtype: Sequence[Page]
        :raises NotFoundError: If an identifier names no page, or a page of another project, which is reported like a
                               missing one.
        """

    @abstractmethod
    async def list_for_source(self, project_id: ProjectId, source_id: SourceId) -> Sequence[Page]:
        """Return the pages whose scans belong to a source, in book order.

        Pages without a scan, such as placeholders and blank leaves, belong to no source and are never listed.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param source_id: Source whose scans the pages show.
        :type source_id: SourceId
        :returns: The pages cut from the scans of the source, by order key.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    async def neighbour_key(
        self, project_id: ProjectId, key: str, side: Side, *, excluding: Collection[PageId] = ()
    ) -> str | None:
        """Return the order key of the page next to ``key`` on one side, leaving some pages out of the count.

        A group of pages that is about to move is left out, so the key found is the one of the page the group will
        stand next to once it has left its old place.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param key: Order key the neighbour is looked up from, usually the key of an anchor page.
        :type key: str
        :param side: Whether to look for the nearest key before or after ``key``.
        :type side: Side
        :param excluding: Pages that do not count as neighbours.
        :type excluding: Collection[PageId]
        :returns: The nearest key on that side, or None when no page lies there, which is the start or the end of the
                  book.
        :rtype: str | None
        """

    @abstractmethod
    async def update_many(self, pages: Sequence[Page]) -> None:
        """Replace the stored state of several pages in the one transaction, all of them or, on an error, none.

        After an error the unit of work is rolled back before it is used again, as a database requires.

        :param pages: Pages with their new state.
        :type pages: Sequence[Page]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes an order key, or a part of a scan, that another page
                               of the project has, such as a page moved to the place another move took in the
                               meantime.
        """

    @abstractmethod
    async def list_for_scan(self, scan_id: ScanId) -> Sequence[Page]:
        """Return the pages cut from one scan, by their slot.

        :param scan_id: Scan the pages were cut from.
        :type scan_id: ScanId
        :returns: Every page that names the scan, the whole scan or its parts in slot order.
        :rtype: Sequence[Page]
        """

    @abstractmethod
    async def count_before(self, page: Page) -> int:
        """Return how many pages of the page's project come before it in book order, which is its position.

        Every page counts, whether it is included in the book or not, as in the windows ``list_for_project`` returns.

        :param page: Stored page of the project.
        :type page: Page
        :returns: The number of the project's pages whose order key is smaller than the page's, from zero.
        :rtype: int
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

    @abstractmethod
    async def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the base versions of several pages in one read, so a window of a book costs one query.

        A base version is a version without an input version, the one a scan or nothing feeds. A page cut from a scan
        again gets a new base version beside the old one, so a page can have several.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """


class JobRepository(Repository[Job, JobId]):
    """Background jobs; a project has at most one import job queued or running at a time."""

    @abstractmethod
    @override
    async def add(self, entity: Job) -> Job:
        """Store a new job of a stored project and return it as stored.

        :param entity: Job to store, with its identifier already assigned.
        :type entity: Job
        :returns: The job as stored.
        :rtype: Job
        :raises ConflictError: If a job with this identifier is already stored, or the job is an import that is queued
                               or running and the project already has one; the database enforces the second rule
                               with a partial unique index, so two uploads that both passed a check before either
                               committed cannot both be stored.
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
