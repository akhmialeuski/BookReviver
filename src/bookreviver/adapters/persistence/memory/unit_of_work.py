"""In-memory unit of work: repositories work on a copy of the database, and ``commit`` publishes only the changes.

The adapter backs the service and API tests and runs the same port contract suite as the SQLAlchemy adapter, so it
reproduces the behaviour services rely on rather than only storing rows. It mirrors the foreign keys from a page or a
job to its project, the cascade from a project to its pages and jobs, and transaction isolation: a unit of work reads
and writes a private copy of the tables, and ``commit`` merges only the rows it added, replaced or removed, so two
units of work touching different rows do not overwrite each other.
"""

from operator import attrgetter
from typing import TYPE_CHECKING, override

from attrs import define, evolve, field, fields

from bookreviver.domain.entities import Job, Project, ProjectOverview
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import JobId, ProjectId
from bookreviver.domain.values import Slice
from bookreviver.ports.persistence import JobRepository, PageRepository, ProjectRepository, Repository, UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from bookreviver.domain.entities import Page
    from bookreviver.domain.enums import JobState
    from bookreviver.domain.ids import AccountId
    from bookreviver.domain.values import SliceRequest

type PageKey = tuple[ProjectId, int]

# Attribute holding the identifier of a project and of a job
ID_ATTRIBUTE: str = 'id'


@define(kw_only=True)
class InMemoryTables:
    """The rows of every table, keyed by identifier.

    :ivar projects: Projects by identifier.
    :ivar pages: Pages by project and index.
    :ivar jobs: Jobs by identifier.
    """

    projects: dict[ProjectId, Project] = field(factory=dict)
    pages: dict[PageKey, Page] = field(factory=dict)
    jobs: dict[JobId, Job] = field(factory=dict)

    def require_project(self, project_id: ProjectId) -> None:
        """Mirror the foreign key from a page or a job to its project.

        :param project_id: Project the page or job refers to.
        :type project_id: ProjectId
        :raises NotFoundError: If the project is not stored.
        """
        if project_id not in self.projects:
            raise NotFoundError(project_id)


@define
class InMemoryDatabase:
    """The committed state shared by every unit of work, like a database server.

    :ivar tables: Committed rows of every table.
    """

    tables: InMemoryTables = field(factory=InMemoryTables)


class InMemoryRepository[EntityT, IdT](Repository[EntityT, IdT]):
    """Generic repository over one table of the working copy."""

    def __init__(self, rows: dict[IdT, EntityT], identify: Callable[[EntityT], IdT]) -> None:
        """Work on one table of the unit of work's copy.

        :param rows: Table of the working copy, changed in place.
        :type rows: dict[IdT, EntityT]
        :param identify: Function returning the identifier of an entity.
        :type identify: Callable[[EntityT], IdT]
        """
        self._rows = rows
        self._identify = identify

    @override
    async def get(self, entity_id: IdT) -> EntityT:
        """Return the entity with this identifier.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """
        if (entity := self._rows.get(entity_id)) is None:
            raise NotFoundError(entity_id)
        return entity

    @override
    async def add(self, entity: EntityT) -> EntityT:
        """Store a new entity.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises ConflictError: If an entity with this identifier is already stored.
        """
        if (entity_id := self._identify(entity)) in self._rows:
            raise ConflictError(entity_id)
        self._rows[entity_id] = entity
        return entity

    @override
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity is not stored.
        """
        await self.get(self._identify(entity))
        self._rows[self._identify(entity)] = entity
        return entity

    @override
    async def delete(self, entity_id: IdT) -> None:
        """Remove the entity.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """
        await self.get(entity_id)
        del self._rows[entity_id]


class InMemoryProjectRepository(InMemoryRepository[Project, ProjectId], ProjectRepository):
    """Projects with page counts computed from the page table."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the project table of the unit of work's copy, reading its pages and jobs as well.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.projects, attrgetter(ID_ATTRIBUTE))
        self._tables = tables

    @override
    async def delete(self, entity_id: ProjectId) -> None:
        """Remove the project together with its pages and jobs, as the database cascade does.

        :param entity_id: Identifier of the project.
        :type entity_id: ProjectId
        :raises NotFoundError: If no project has this identifier.
        """
        await super().delete(entity_id)
        # Mirror the database cascade from a project to its pages and jobs, mutating in place so every
        # repository of this unit of work keeps seeing the same tables
        for page_key in [key for key in self._tables.pages if key[0] == entity_id]:
            del self._tables.pages[page_key]
        for job_id in [key for key, job in self._tables.jobs.items() if job.project_id == entity_id]:
            del self._tables.jobs[job_id]

    @override
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return one window of the owner's projects, most recently updated first, ties by identifier.

        :param owner_id: Account owning the projects.
        :type owner_id: AccountId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The projects of the window with their page counts, and the number of all the owner's projects.
        :rtype: Slice[ProjectOverview]
        """
        by_id = sorted(
            (project for project in self._tables.projects.values() if project.owner_id == owner_id),
            key=attrgetter(ID_ATTRIBUTE),
        )
        # A stable sort keeps the identifier order among projects updated at the same moment
        owned = sorted(by_id, key=attrgetter('updated_at'), reverse=True)
        window = owned[request.offset : request.offset + request.limit]
        overviews = [
            ProjectOverview(project=project, page_count=sum(key[0] == project.id for key in self._tables.pages))
            for project in window
        ]
        return Slice(items=overviews, total=len(owned))


class InMemoryPageRepository(PageRepository):
    """Pages keyed by project and index."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the page table of the unit of work's copy, checking pages against its projects.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        self._tables = tables

    @override
    async def get(self, project_id: ProjectId, index: int) -> Page:
        """Return one page of a project.

        :param project_id: Project owning the page.
        :type project_id: ProjectId
        :param index: Position of the page in the book, starting at 0.
        :type index: int
        :returns: The stored page.
        :rtype: Page
        :raises NotFoundError: If the project has no page at this index.
        """
        if (page := self._tables.pages.get((project_id, index))) is None:
            raise NotFoundError(project_id, index)
        return page

    @override
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        """Return one window of a project's pages in book order.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The pages of the window and the number of all the project's pages.
        :rtype: Slice[Page]
        """
        pages = sorted(
            (page for page in self._tables.pages.values() if page.project_id == project_id), key=attrgetter('index')
        )
        return Slice(items=pages[request.offset : request.offset + request.limit], total=len(pages))

    @override
    async def replace_for_project(self, project_id: ProjectId, pages: Sequence[Page]) -> None:
        """Replace every page of a project with ``pages``, keyed by their own index.

        :param project_id: Project whose pages are replaced; each page is stored under this project.
        :type project_id: ProjectId
        :param pages: The project's new pages.
        :type pages: Sequence[Page]
        :raises NotFoundError: If the project is not stored.
        """
        self._tables.require_project(project_id)
        for page_key in [key for key in self._tables.pages if key[0] == project_id]:
            del self._tables.pages[page_key]
        self._tables.pages.update({(project_id, page.index): evolve(page, project_id=project_id) for page in pages})

    @override
    async def update(self, page: Page) -> Page:
        """Replace the stored state of an existing page.

        :param page: Page with its new state, addressed by its project and index.
        :type page: Page
        :returns: The page as stored.
        :rtype: Page
        :raises NotFoundError: If the page is not stored.
        """
        await self.get(page.project_id, page.index)
        self._tables.pages[page.project_id, page.index] = page
        return page


class InMemoryJobRepository(InMemoryRepository[Job, JobId], JobRepository):
    """Jobs of every project."""

    def __init__(self, tables: InMemoryTables) -> None:
        """Work on the job table of the unit of work's copy, checking jobs against its projects.

        :param tables: Every table of the working copy.
        :type tables: InMemoryTables
        """
        super().__init__(tables.jobs, attrgetter(ID_ATTRIBUTE))
        self._tables = tables

    @override
    async def add(self, entity: Job) -> Job:
        """Store a new job of an existing project.

        :param entity: Job to store, with its identifier already assigned.
        :type entity: Job
        :returns: The job as stored.
        :rtype: Job
        :raises NotFoundError: If the job's project is not stored.
        :raises ConflictError: If a job with this identifier is already stored.
        """
        self._tables.require_project(entity.project_id)
        return await super().add(entity)

    @override
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        """Return a project's jobs in the given states, newest first.

        :param project_id: Project owning the jobs.
        :type project_id: ProjectId
        :param states: States a returned job may be in.
        :type states: Collection[JobState]
        :returns: The matching jobs, most recently created first.
        :rtype: Sequence[Job]
        """
        jobs = (job for job in self._tables.jobs.values() if job.project_id == project_id and job.state in states)
        return sorted(jobs, key=attrgetter('created_at'), reverse=True)


class InMemoryUnitOfWork(UnitOfWork):
    """A transaction over a private copy of the database that publishes only its own changes on commit.

    Rows are frozen entities, so a changed row is a different object: comparing identities against the snapshot taken
    when the transaction began finds exactly what this unit added, replaced or removed.

    :ivar projects: Project repository over the working copy.
    :ivar pages: Page repository over the working copy.
    :ivar jobs: Job repository over the working copy.
    """

    def __init__(self, database: InMemoryDatabase) -> None:
        """Begin a transaction over ``database``.

        :param database: Committed state shared with every other unit of work.
        :type database: InMemoryDatabase
        """
        self._database = database
        self._begin()

    @staticmethod
    def _copy(tables: InMemoryTables) -> InMemoryTables:
        """Copy every table; the rows themselves are immutable and shared.

        :param tables: Tables to copy.
        :type tables: InMemoryTables
        :returns: New tables holding the same rows.
        :rtype: InMemoryTables
        """
        return InMemoryTables(**{table.name: dict(getattr(tables, table.name)) for table in fields(InMemoryTables)})

    def _begin(self) -> None:
        """Start a transaction from the committed state."""
        self._snapshot = self._copy(self._database.tables)
        self._tables = self._copy(self._database.tables)
        self.projects = InMemoryProjectRepository(self._tables)
        self.pages = InMemoryPageRepository(self._tables)
        self.jobs = InMemoryJobRepository(self._tables)

    @override
    async def commit(self) -> None:
        """Publish the rows this transaction added, replaced or removed, and begin a new transaction."""
        committed = self._database.tables
        for table in fields(InMemoryTables):
            before, after = getattr(self._snapshot, table.name), getattr(self._tables, table.name)
            target = getattr(committed, table.name)
            for key in before.keys() - after.keys():
                target.pop(key, None)
            target.update({key: row for key, row in after.items() if before.get(key) is not row})
        self._begin()

    @override
    async def rollback(self) -> None:
        """Discard every change of this transaction and begin a new one from the committed state."""
        self._begin()
