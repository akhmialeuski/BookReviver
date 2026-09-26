"""In-memory unit of work: repositories work on a copy of the database that ``commit`` publishes."""

import copy
from operator import attrgetter
from typing import TYPE_CHECKING, override

from attrs import define, evolve, field

from bookreviver.domain.entities import Job, Project, ProjectOverview
from bookreviver.domain.errors import NotFoundError
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


@define(kw_only=True)
class InMemoryTables:
    """The rows of every table, keyed by identifier."""

    projects: dict[ProjectId, Project] = field(factory=dict)
    pages: dict[PageKey, Page] = field(factory=dict)
    jobs: dict[JobId, Job] = field(factory=dict)


@define
class InMemoryDatabase:
    """The committed state shared by every unit of work, like a database server."""

    tables: InMemoryTables = field(factory=InMemoryTables)


class InMemoryRepository[EntityT, IdT](Repository[EntityT, IdT]):
    """Generic repository over one table of the working copy."""

    def __init__(self, rows: dict[IdT, EntityT], identify: Callable[[EntityT], IdT]) -> None:
        self._rows = rows
        self._identify = identify

    @override
    async def get(self, entity_id: IdT) -> EntityT:
        if (entity := self._rows.get(entity_id)) is None:
            raise NotFoundError(entity_id)
        return entity

    @override
    async def add(self, entity: EntityT) -> EntityT:
        self._rows[self._identify(entity)] = entity
        return entity

    @override
    async def update(self, entity: EntityT) -> EntityT:
        await self.get(self._identify(entity))
        self._rows[self._identify(entity)] = entity
        return entity

    @override
    async def delete(self, entity_id: IdT) -> None:
        await self.get(entity_id)
        del self._rows[entity_id]


class InMemoryProjectRepository(InMemoryRepository[Project, ProjectId], ProjectRepository):
    """Projects with page counts computed from the page table."""

    def __init__(self, tables: InMemoryTables) -> None:
        super().__init__(tables.projects, attrgetter('id'))
        self._tables = tables

    @override
    async def delete(self, entity_id: ProjectId) -> None:
        await super().delete(entity_id)
        # Mirror the database cascade from a project to its pages and jobs, mutating in place so every
        # repository of this unit of work keeps seeing the same tables
        for page_key in [key for key in self._tables.pages if key[0] == entity_id]:
            del self._tables.pages[page_key]
        for job_id in [key for key, job in self._tables.jobs.items() if job.project_id == entity_id]:
            del self._tables.jobs[job_id]

    @override
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        owned = sorted(
            (project for project in self._tables.projects.values() if project.owner_id == owner_id),
            key=attrgetter('updated_at'),
            reverse=True,
        )
        window = owned[request.offset : request.offset + request.limit]
        overviews = [
            ProjectOverview(project=project, page_count=sum(key[0] == project.id for key in self._tables.pages))
            for project in window
        ]
        return Slice(items=overviews, total=len(owned))


class InMemoryPageRepository(PageRepository):
    """Pages keyed by project and index."""

    def __init__(self, tables: InMemoryTables) -> None:
        self._tables = tables

    @override
    async def get(self, project_id: ProjectId, index: int) -> Page:
        if (page := self._tables.pages.get((project_id, index))) is None:
            raise NotFoundError(project_id, index)
        return page

    @override
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        pages = sorted(
            (page for page in self._tables.pages.values() if page.project_id == project_id), key=attrgetter('index')
        )
        return Slice(items=pages[request.offset : request.offset + request.limit], total=len(pages))

    @override
    async def replace_for_project(self, project_id: ProjectId, pages: Sequence[Page]) -> None:
        for page_key in [key for key in self._tables.pages if key[0] == project_id]:
            del self._tables.pages[page_key]
        self._tables.pages.update({(project_id, page.index): evolve(page, project_id=project_id) for page in pages})

    @override
    async def update(self, page: Page) -> Page:
        await self.get(page.project_id, page.index)
        self._tables.pages[page.project_id, page.index] = page
        return page


class InMemoryJobRepository(InMemoryRepository[Job, JobId], JobRepository):
    """Jobs of every project."""

    def __init__(self, tables: InMemoryTables) -> None:
        super().__init__(tables.jobs, attrgetter('id'))
        self._tables = tables

    @override
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        jobs = (job for job in self._tables.jobs.values() if job.project_id == project_id and job.state in states)
        return sorted(jobs, key=attrgetter('created_at'), reverse=True)


class InMemoryUnitOfWork(UnitOfWork):
    """A transaction over a private copy of the database, published on commit."""

    def __init__(self, database: InMemoryDatabase) -> None:
        self._database = database
        self._begin()

    def _begin(self) -> None:
        """Start from a fresh copy of the committed state."""
        self._tables = copy.deepcopy(self._database.tables)
        self.projects = InMemoryProjectRepository(self._tables)
        self.pages = InMemoryPageRepository(self._tables)
        self.jobs = InMemoryJobRepository(self._tables)

    @override
    async def commit(self) -> None:
        self._database.tables = copy.deepcopy(self._tables)

    @override
    async def rollback(self) -> None:
        self._begin()
