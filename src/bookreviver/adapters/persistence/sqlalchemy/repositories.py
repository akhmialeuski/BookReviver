"""Port repositories of the SQLAlchemy adapter, built on advanced-alchemy's async repository.

Each port repository works on two levels. A row repository, a subclass of advanced-alchemy's
:class:`~advanced_alchemy.repository.SQLAlchemyAsyncRepository`, supplies every generic query of one table: lookup by
primary key including the composite key of pages, add, update, delete, filtered and paginated listings, and counts.
A mapper from :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turns its rows into domain entities. The port
repository adds only the queries specific to BookReviver, such as the page count of every project in a listing.

A missing row is reported as the domain's :class:`~bookreviver.domain.errors.NotFoundError` naming its key, the same
error the in-memory adapter raises, so services never see an advanced-alchemy exception.
"""

from typing import TYPE_CHECKING, Any, override

from advanced_alchemy.exceptions import NotFoundError as MissingRowError
from advanced_alchemy.filters import CollectionFilter, LimitOffset
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from attrs import evolve
from sqlalchemy import func, select

from bookreviver.adapters.persistence.sqlalchemy.mappers import JobMapper, PageMapper, ProjectMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import JobRow, PageRow, ProjectRow
from bookreviver.domain.entities import Job, Project, ProjectOverview
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import JobId, ProjectId
from bookreviver.domain.values import Slice
from bookreviver.ports.persistence import JobRepository, PageRepository, ProjectRepository, Repository

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from advanced_alchemy.base import ModelProtocol
    from advanced_alchemy.repository.typing import PrimaryKeyType
    from sqlalchemy.ext.asyncio import AsyncSession

    from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
    from bookreviver.domain.entities import Page
    from bookreviver.domain.enums import JobState
    from bookreviver.domain.ids import AccountId
    from bookreviver.domain.values import SliceRequest


class RowRepository[RowT: ModelProtocol](SQLAlchemyAsyncRepository[RowT]):
    """Repository of one table from advanced-alchemy, reporting a missing row as the domain's NotFoundError."""

    @override
    async def get(self, item_id: PrimaryKeyType, **options: Any) -> RowT:
        """Return the row with this primary key.

        The library's ``update`` and ``delete`` look the row up through this method, so all three report a missing
        row with its key.

        :param item_id: Primary key of the row, a tuple for a composite key.
        :type item_id: PrimaryKeyType
        :param options: Keyword options of :meth:`SQLAlchemyAsyncRepository.get`, passed through unchanged.
        :type options: Any
        :returns: The row, attached to the session.
        :rtype: RowT
        :raises NotFoundError: If no row has this key.
        """
        try:
            return await super().get(item_id, **options)
        except MissingRowError as error:
            raise NotFoundError(item_id) from error


class ProjectRows(RowRepository[ProjectRow]):
    """Rows of the ``projects`` table."""

    model_type = ProjectRow


class PageRows(RowRepository[PageRow]):
    """Rows of the ``pages`` table, keyed by project and index."""

    model_type = PageRow


class JobRows(RowRepository[JobRow]):
    """Rows of the ``jobs`` table."""

    model_type = JobRow


class SqlAlchemyRepository[EntityT, IdT, RowT: ModelProtocol](Repository[EntityT, IdT]):
    """The generic operations of a port repository, delegated to a row repository and mapped to entities."""

    def __init__(self, *, rows: RowRepository[RowT], mapper: RowMapper[EntityT, RowT]) -> None:
        """Create the repository over one table.

        :param rows: Row repository of the entity's table, bound to the session of the unit of work.
        :type rows: RowRepository[RowT]
        :param mapper: Translation between the entity and its row.
        :type mapper: RowMapper[EntityT, RowT]
        """
        self._rows = rows
        self._mapper = mapper

    @override
    async def get(self, entity_id: IdT) -> EntityT:
        """Return the entity with this identifier.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """
        return self._mapper.to_entity(await self._rows.get(entity_id))

    @override
    async def add(self, entity: EntityT) -> EntityT:
        """Store a new entity.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        """
        return self._mapper.to_entity(await self._rows.add(self._mapper.to_row(entity)))

    @override
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity is not stored.
        """
        return self._mapper.to_entity(await self._rows.update(self._mapper.to_row(entity)))

    @override
    async def delete(self, entity_id: IdT) -> None:
        """Remove the entity, and through the cascading foreign keys everything that belongs to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """
        await self._rows.delete(entity_id)


class SqlAlchemyProjectRepository(SqlAlchemyRepository[Project, ProjectId, ProjectRow], ProjectRepository):
    """Projects, listed per owner together with their page counts."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``projects`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=ProjectRows(session=session), mapper=ProjectMapper())

    @override
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return a slice of the owner's projects with their page counts, most recently updated first.

        The page count is a correlated scalar subquery inside the listing query, so one statement returns every
        project of the slice with its count. The total is a separate count, which stays correct for a slice past the
        end where a window-function count would report zero.

        :param owner_id: Account whose projects are listed.
        :type owner_id: AccountId
        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Projects of the slice with their page counts, and the total number of the owner's projects.
        :rtype: Slice[ProjectOverview]
        """
        page_count = (
            select(func.count()).where(PageRow.project_id == ProjectRow.id).correlate(ProjectRow).scalar_subquery()
        )
        statement = (
            select(ProjectRow, page_count)
            .where(ProjectRow.owner_id == owner_id)
            .order_by(ProjectRow.updated_at.desc(), ProjectRow.id)
            .offset(request.offset)
            .limit(request.limit)
        )
        rows = await self._rows.session.execute(statement)
        overviews = [ProjectOverview(project=self._mapper.to_entity(row), page_count=count) for row, count in rows]
        return Slice(items=overviews, total=await self._rows.count(owner_id=owner_id))


class SqlAlchemyPageRepository(PageRepository):
    """Pages addressed by the composite key of project and index.

    The class stands apart from :class:`SqlAlchemyRepository` because the page port is not a
    :class:`~bookreviver.ports.persistence.Repository`: a page has no identifier of its own.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``pages`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        self._rows = PageRows(session=session)
        self._mapper = PageMapper()

    @override
    async def get(self, project_id: ProjectId, index: int) -> Page:
        """Return one page of a project.

        :param project_id: Project the page belongs to.
        :type project_id: ProjectId
        :param index: Zero-based position of the page in the book.
        :type index: int
        :returns: The stored page.
        :rtype: Page
        :raises NotFoundError: If the project has no page at this index.
        """
        return self._mapper.to_entity(await self._rows.get((project_id, index)))

    @override
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        """Return a slice of the project's pages in book order.

        The total is a separate count query, which stays correct for a slice past the end where a window-function
        count would report zero.

        :param project_id: Project whose pages are listed.
        :type project_id: ProjectId
        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Pages of the slice and the total number of the project's pages.
        :rtype: Slice[Page]
        """
        rows, total = await self._rows.get_many_and_count(
            LimitOffset(limit=request.limit, offset=request.offset),
            order_by=PageRow.index.asc(),
            count_with_window_function=False,
            project_id=project_id,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=total)

    @override
    async def replace_for_project(self, project_id: ProjectId, pages: Sequence[Page]) -> None:
        """Replace every page of a project with the given pages.

        Each page is stored under ``project_id`` whatever project it names, so a caller cannot write pages into a
        different project by mistake.

        :param project_id: Project whose pages are replaced.
        :type project_id: ProjectId
        :param pages: New pages of the project, possibly none.
        :type pages: Sequence[Page]
        """
        await self._rows.delete_where(project_id=project_id)
        await self._rows.add_many([self._mapper.to_row(evolve(page, project_id=project_id)) for page in pages])

    @override
    async def update(self, page: Page) -> Page:
        """Replace the stored state of one page.

        :param page: Page with its new state.
        :type page: Page
        :returns: The page as stored.
        :rtype: Page
        :raises NotFoundError: If the page is not stored.
        """
        return self._mapper.to_entity(await self._rows.update(self._mapper.to_row(page)))


class SqlAlchemyJobRepository(SqlAlchemyRepository[Job, JobId, JobRow], JobRepository):
    """Jobs, listed per project and state."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``jobs`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=JobRows(session=session), mapper=JobMapper())

    @override
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        """Return the project's jobs in one of the given states, newest first.

        :param project_id: Project whose jobs are listed.
        :type project_id: ProjectId
        :param states: States a listed job may be in.
        :type states: Collection[JobState]
        :returns: Matching jobs, newest first.
        :rtype: Sequence[Job]
        """
        rows = await self._rows.get_many(
            CollectionFilter(field_name=JobRow.state, values=states),
            order_by=JobRow.created_at.desc(),
            project_id=project_id,
        )
        return [self._mapper.to_entity(row) for row in rows]
