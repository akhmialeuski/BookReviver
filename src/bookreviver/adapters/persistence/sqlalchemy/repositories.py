"""Port repositories of the SQLAlchemy adapter, built on advanced-alchemy's async repository.

Each port repository works on two levels. A row repository, a subclass of advanced-alchemy's
:class:`~advanced_alchemy.repository.SQLAlchemyAsyncRepository`, supplies every generic query of one table: lookup by
primary key including the composite key of pages, add, update, delete, filtered and paginated listings, and counts.
A mapper from :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turns its rows into domain entities. The port
repository adds only the queries specific to BookReviver, such as the page count of every project in a listing.

The database's own checks are reported as the domain errors the in-memory adapter raises, so services never see an
advanced-alchemy exception. A missing row is a :class:`~bookreviver.domain.errors.NotFoundError` naming its key. A row
whose key is already stored is a :class:`~bookreviver.domain.errors.ConflictError` naming that key, and a row whose
parent row is missing is a ``NotFoundError`` naming the parent's key. advanced-alchemy's own Litestar handler answers
409 for every integrity error, but here every foreign key points at the owning project, so a violated one means the
project the caller addressed does not exist, which is a 404. Any other integrity error means the adapter wrote a row
the schema forbids, a defect that propagates unchanged.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, override

from advanced_alchemy.exceptions import DuplicateKeyError, ForeignKeyError
from advanced_alchemy.exceptions import NotFoundError as MissingRowError
from advanced_alchemy.filters import CollectionFilter, LimitOffset
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from attrs import evolve
from sqlalchemy import func, inspect, select, update

from bookreviver.adapters.persistence.sqlalchemy.mappers import JobMapper, PageMapper, ProjectMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import JobRow, PageRow, ProjectRow
from bookreviver.domain.entities import Job, Project, ProjectOverview
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import JobId, ProjectId
from bookreviver.domain.values import Slice
from bookreviver.ports.persistence import JobRepository, PageRepository, ProjectRepository, Repository

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence

    from advanced_alchemy.base import ModelProtocol
    from advanced_alchemy.repository.typing import PrimaryKeyType
    from sqlalchemy.ext.asyncio import AsyncSession

    from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
    from bookreviver.domain.entities import Page
    from bookreviver.domain.enums import JobState
    from bookreviver.domain.ids import AccountId
    from bookreviver.domain.values import SliceRequest


class RowRepository[RowT: ModelProtocol](SQLAlchemyAsyncRepository[RowT]):
    """Repository of one table from advanced-alchemy, reporting the database's checks as domain errors."""

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

    @override
    async def add(self, data: RowT, **options: Any) -> RowT:
        """Insert one row.

        :param data: Transient row to insert.
        :type data: RowT
        :param options: Keyword options of :meth:`SQLAlchemyAsyncRepository.add`, passed through unchanged.
        :type options: Any
        :returns: The row, attached to the session.
        :rtype: RowT
        :raises ConflictError: If a row with this primary key is already stored.
        :raises NotFoundError: If a row this one refers to through a foreign key is not stored.
        """
        with self._reporting_integrity_errors([data]):
            return await super().add(data, **options)

    @override
    async def add_many(self, data: list[RowT], **options: Any) -> Sequence[RowT]:
        """Insert several rows in one statement.

        :param data: Transient rows to insert.
        :type data: list[RowT]
        :param options: Keyword options of :meth:`SQLAlchemyAsyncRepository.add_many`, passed through unchanged.
        :type options: Any
        :returns: The rows, attached to the session.
        :rtype: Sequence[RowT]
        :raises ConflictError: If a row with one of these primary keys is already stored.
        :raises NotFoundError: If a row these refer to through a foreign key is not stored.
        """
        with self._reporting_integrity_errors(data):
            return await super().add_many(data, **options)

    @contextmanager
    def _reporting_integrity_errors(self, rows: Sequence[RowT]) -> Iterator[None]:
        """Report the database rejecting ``rows`` as the domain error for the constraint they broke.

        The database does not say which row broke the constraint, so each error names the candidate keys: the
        primary keys of the rows for a conflict, and the distinct values of their foreign keys for a missing parent.

        :param rows: Rows being inserted.
        :type rows: Sequence[RowT]
        :returns: Iterator yielding once around the insert.
        :rtype: Iterator[None]
        :raises ConflictError: If a primary key of ``rows`` is already stored.
        :raises NotFoundError: If a row that ``rows`` refer to is not stored.
        """
        try:
            yield
        except DuplicateKeyError as error:
            raise ConflictError(*(self.get_primary_key_value(row) for row in rows)) from error
        except ForeignKeyError as error:
            references = [
                attribute.key
                for attribute in self.model_type.__mapper__.column_attrs
                if attribute.columns[0].foreign_keys
            ]
            raise NotFoundError(*dict.fromkeys(getattr(row, key) for row in rows for key in references)) from error


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

    @override
    async def update_if_state(self, entity: Job, *, expected: Collection[JobState]) -> Job | None:
        """Replace the stored job with one ``UPDATE ... WHERE state IN`` statement.

        The database evaluates the condition against the latest committed row and locks the row it changes until the
        transaction ends, so no other transaction can change the job between the check and the write. ``RETURNING``
        reloads the row into the session, replacing a copy an earlier read left there.

        :param entity: Job with its new state.
        :type entity: Job
        :param expected: States the stored job must be in for the replacement to happen.
        :type expected: Collection[JobState]
        :returns: The job as stored, or None when its stored state is not one of ``expected``.
        :rtype: Job | None
        :raises NotFoundError: If the job is not stored.
        """
        row = self._mapper.to_row(entity)
        statement = (
            update(JobRow)
            .where(JobRow.id == entity.id, JobRow.state.in_(expected))
            .values({column.key: getattr(row, column.key) for column in inspect(JobRow).column_attrs})
            .returning(JobRow)
            .execution_options(populate_existing=True)
        )
        if (updated := (await self._rows.session.execute(statement)).scalar_one_or_none()) is None:
            # A query rather than the session's copy tells a job in another state from one deleted meanwhile
            if not await self._rows.exists(id=entity.id):
                raise NotFoundError(entity.id)
            return None
        return self._mapper.to_entity(updated)
