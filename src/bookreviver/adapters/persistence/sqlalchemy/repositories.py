"""Port repositories over advanced-alchemy's async repository, which supplies every generic query."""

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
    """advanced-alchemy's repository of one table, reporting a missing row as the domain's NotFoundError."""

    @override
    async def get(self, item_id: PrimaryKeyType, **options: Any) -> RowT:
        # The library's update and delete look the row up through this method, so all three report the key
        try:
            return await super().get(item_id, **options)
        except MissingRowError as error:
            raise NotFoundError(item_id) from error


class ProjectRows(RowRepository[ProjectRow]):
    """Rows of the project table."""

    model_type = ProjectRow


class PageRows(RowRepository[PageRow]):
    """Rows of the page table, keyed by project and index."""

    model_type = PageRow


class JobRows(RowRepository[JobRow]):
    """Rows of the job table."""

    model_type = JobRow


class SqlAlchemyRepository[EntityT, IdT, RowT: ModelProtocol](Repository[EntityT, IdT]):
    """The generic operations of a port repository, delegated to advanced-alchemy and mapped to entities."""

    def __init__(self, *, rows: RowRepository[RowT], mapper: RowMapper[EntityT, RowT]) -> None:
        self._rows = rows
        self._mapper = mapper

    @override
    async def get(self, entity_id: IdT) -> EntityT:
        return self._mapper.to_entity(await self._rows.get(entity_id))

    @override
    async def add(self, entity: EntityT) -> EntityT:
        return self._mapper.to_entity(await self._rows.add(self._mapper.to_row(entity)))

    @override
    async def update(self, entity: EntityT) -> EntityT:
        return self._mapper.to_entity(await self._rows.update(self._mapper.to_row(entity)))

    @override
    async def delete(self, entity_id: IdT) -> None:
        # The database removes whatever belongs to the row through its cascading foreign keys
        await self._rows.delete(entity_id)


class SqlAlchemyProjectRepository(SqlAlchemyRepository[Project, ProjectId, ProjectRow], ProjectRepository):
    """Projects, listed per owner together with their page counts."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(rows=ProjectRows(session=session), mapper=ProjectMapper())

    @override
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
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
    """Pages addressed by the composite key of project and index."""

    def __init__(self, session: AsyncSession) -> None:
        self._rows = PageRows(session=session)
        self._mapper = PageMapper()

    @override
    async def get(self, project_id: ProjectId, index: int) -> Page:
        return self._mapper.to_entity(await self._rows.get((project_id, index)))

    @override
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Page]:
        # Two queries rather than a window count, which reports no total for a slice past the end
        rows, total = await self._rows.get_many_and_count(
            LimitOffset(limit=request.limit, offset=request.offset),
            order_by=PageRow.index.asc(),
            count_with_window_function=False,
            project_id=project_id,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=total)

    @override
    async def replace_for_project(self, project_id: ProjectId, pages: Sequence[Page]) -> None:
        await self._rows.delete_where(project_id=project_id)
        await self._rows.add_many([self._mapper.to_row(evolve(page, project_id=project_id)) for page in pages])

    @override
    async def update(self, page: Page) -> Page:
        return self._mapper.to_entity(await self._rows.update(self._mapper.to_row(page)))


class SqlAlchemyJobRepository(SqlAlchemyRepository[Job, JobId, JobRow], JobRepository):
    """Jobs, listed per project and state."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(rows=JobRows(session=session), mapper=JobMapper())

    @override
    async def list_for_project(self, project_id: ProjectId, states: Collection[JobState]) -> Sequence[Job]:
        rows = await self._rows.get_many(
            CollectionFilter(field_name=JobRow.state, values=states),
            order_by=JobRow.created_at.desc(),
            project_id=project_id,
        )
        return [self._mapper.to_entity(row) for row in rows]
