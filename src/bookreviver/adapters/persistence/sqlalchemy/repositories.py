"""Port repositories of the SQLAlchemy adapter, built on advanced-alchemy's async repository.

Each port repository works on two levels. A row repository, a subclass of advanced-alchemy's
:class:`~advanced_alchemy.repository.SQLAlchemyAsyncRepository`, supplies every generic query of one table: lookup by
primary key, add, update, delete, filtered and paginated listings, and counts. A mapper from
:mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turns its rows into domain entities. The port repository
adds only the queries specific to BookReviver, such as the counts of the book of every project in a listing.

The database's own checks are reported as the domain errors the in-memory adapter raises, so services never see an
advanced-alchemy exception. A missing row is a :class:`~bookreviver.domain.errors.NotFoundError` naming its key. A row
whose primary key or unique value is already stored is a :class:`~bookreviver.domain.errors.ConflictError` naming
them, and a row whose parent row is missing is a ``NotFoundError`` naming the parent's key. advanced-alchemy's own
Litestar handler answers 409 for every integrity error, but here every foreign key points at a row the caller named,
such as the project of a source or the source of a scan, so a violated one means that row does not exist, which is a
404. Any other integrity error means the adapter wrote a row the schema forbids, a defect that propagates unchanged.
"""

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, override

from advanced_alchemy.exceptions import DuplicateKeyError, ForeignKeyError, RepositoryError
from advanced_alchemy.exceptions import NotFoundError as MissingRowError
from advanced_alchemy.filters import CollectionFilter, LimitOffset
from advanced_alchemy.repository import SQLAlchemyAsyncRepository
from sqlalchemy import Table, UniqueConstraint, and_, case, delete, exists, func, inspect, or_, select, update
from sqlalchemy.orm.exc import StaleDataError

from bookreviver.adapters.persistence.sqlalchemy.mappers import (
    JobMapper,
    PageEditMapper,
    PageMapper,
    PageStageMapper,
    PageVersionMapper,
    ProjectMapper,
    RecipeMapper,
    ScanMapper,
    SourceMapper,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    JobRow,
    PageEditRow,
    PageRow,
    PageStageRow,
    PageVersionRow,
    ProjectRow,
    RecipeRow,
    ScanRow,
    SourceRow,
)
from bookreviver.domain.entities import (
    Job,
    Page,
    PageEdit,
    PageStage,
    PageVersion,
    Project,
    ProjectOverview,
    Recipe,
    Scan,
    Source,
)
from bookreviver.domain.enums import PageOrigin, Side, Stage, StageState, VersionScale, VersionState
from bookreviver.domain.errors import ConcurrentChangeError, ConflictError, NotFoundError
from bookreviver.domain.ids import JobId, PageId, PageVersionId, ProjectId, RecipeId, ScanId, SourceId
from bookreviver.domain.stage_summaries import StageTally
from bookreviver.domain.values import PageEditKey, PageSize, PageStageKey, Slice
from bookreviver.domain.version_chains import collectable_versions
from bookreviver.ports.persistence import (
    JobRepository,
    PageEditRepository,
    PageRepository,
    PageStageRepository,
    PageVersionRepository,
    ProjectRepository,
    RecipeRepository,
    Repository,
    ScanRepository,
    SourceRepository,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence
    from datetime import datetime
    from uuid import UUID

    from advanced_alchemy.base import ModelProtocol
    from advanced_alchemy.repository.typing import PrimaryKeyType
    from sqlalchemy import ScalarSelect
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import QueryableAttribute

    from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
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

    @override
    async def update(self, data: RowT, **options: Any) -> RowT:
        """Replace the stored state of one row.

        :param data: Transient row holding the new state under the key of the stored row.
        :type data: RowT
        :param options: Keyword options of :meth:`SQLAlchemyAsyncRepository.update`, passed through unchanged.
        :type options: Any
        :returns: The row, attached to the session.
        :rtype: RowT
        :raises NotFoundError: If no row has this key, or a row this one refers to through a foreign key is not stored.
        :raises ConflictError: If the new state takes a unique value of another row.
        :raises ConcurrentChangeError: If the table counts versions and another transaction changed the row after the
                                       state was read.
        """
        with self._reporting_integrity_errors([data]):
            return await super().update(data, **options)

    @contextmanager
    def _reporting_integrity_errors(self, rows: Sequence[RowT]) -> Iterator[None]:
        """Report the database rejecting ``rows`` as the domain error for the constraint they broke.

        The database does not say which row broke the constraint, so each error names the candidate keys: the
        primary keys and the unique values of the rows for a conflict, and the distinct values of their foreign keys
        for a missing parent.

        :param rows: Rows being written.
        :type rows: Sequence[RowT]
        :returns: Iterator yielding once around the write.
        :rtype: Iterator[None]
        :raises ConflictError: If a primary key or a unique value of ``rows`` is already stored.
        :raises NotFoundError: If a row that ``rows`` refer to is not stored.
        """
        try:
            yield
        except DuplicateKeyError as error:
            mapper, table = self.model_type.__mapper__, self.model_type.__table__
            # A declarative class maps one Table, the kind of FromClause that carries constraints
            constraints = table.constraints if isinstance(table, Table) else set()
            unique_keys = [
                [mapper.get_property_by_column(column).key for column in constraint.columns]
                for constraint in constraints
                if isinstance(constraint, UniqueConstraint)
            ]
            candidates = [
                candidate
                for row in rows
                for candidate in (
                    self.get_primary_key_value(row),
                    *(tuple(getattr(row, key) for key in keys) for keys in unique_keys),
                )
            ]
            raise ConflictError(*candidates) from error
        except ForeignKeyError as error:
            references = [
                attribute.key
                for attribute in self.model_type.__mapper__.column_attrs
                if attribute.columns[0].foreign_keys
            ]
            parents = (getattr(row, key) for row in rows for key in references)
            raise NotFoundError(*dict.fromkeys(parent for parent in parents if parent is not None)) from error
        except RepositoryError as error:
            # The library wraps every SQLAlchemy error, so the version counter's refusal arrives as its cause
            if isinstance(error.__cause__, StaleDataError):
                raise ConcurrentChangeError from error
            raise


class ProjectRows(RowRepository[ProjectRow]):
    """Rows of the ``projects`` table."""

    model_type = ProjectRow


class SourceRows(RowRepository[SourceRow]):
    """Rows of the ``sources`` table."""

    model_type = SourceRow


class ScanRows(RowRepository[ScanRow]):
    """Rows of the ``scans`` table."""

    model_type = ScanRow


class PageRows(RowRepository[PageRow]):
    """Rows of the ``pages`` table."""

    model_type = PageRow


class PageVersionRows(RowRepository[PageVersionRow]):
    """Rows of the ``page_versions`` table."""

    model_type = PageVersionRow


class PageStageRows(RowRepository[PageStageRow]):
    """Rows of the ``page_stages`` table."""

    model_type = PageStageRow


class PageEditRows(RowRepository[PageEditRow]):
    """Rows of the ``page_edits`` table."""

    model_type = PageEditRow


class RecipeRows(RowRepository[RecipeRow]):
    """Rows of the ``recipes`` table."""

    model_type = RecipeRow


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
    async def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities in one statement, which the database applies whole or not at all.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        """
        # An insert of no rows is no statement at all
        if not entities:
            return entities
        rows = await self._rows.add_many([self._mapper.to_row(entity) for entity in entities])
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity is not stored.
        :raises ConcurrentChangeError: If the entity's row counts versions and another transaction changed it after the
                                       entity was read.
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
    """Projects, listed per owner together with the counts of their books.

    The counts are scalar subqueries, correlated with the project rows of a listing, so one statement returns every
    project of the listing with its counts.
    """

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``projects`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=ProjectRows(session=session), mapper=ProjectMapper())

    @override
    async def add(self, entity: Project) -> Project:
        """Store a new project whose cover, if any, is one of its own pages.

        :param entity: Project to store, with its identifier already assigned.
        :type entity: Project
        :returns: The project as stored.
        :rtype: Project
        :raises NotFoundError: If the cover page is not a page of the project, or the owner is not stored.
        :raises ConflictError: If a project with this identifier is already stored.
        """
        await self._require_own_cover(entity)
        return await super().add(entity)

    @override
    async def update(self, entity: Project) -> Project:
        """Replace the stored state of a project whose cover, if any, is one of its own pages.

        :param entity: Project with its new state.
        :type entity: Project
        :returns: The project as stored.
        :rtype: Project
        :raises NotFoundError: If the project is not stored, or the cover page is not a page of the project.
        """
        await self._require_own_cover(entity)
        return await super().update(entity)

    async def _require_own_cover(self, project: Project) -> None:
        """Refuse a cover that is not a page of the project.

        The foreign key of the cover proves only that the page exists and empties the cover when the page goes. A
        key over the pair of project and page cannot empty the cover alone on SQLite, so the project is checked here.

        :param project: Project about to be stored.
        :type project: Project
        :raises NotFoundError: If the cover page is not stored or belongs to another project.
        """
        if (cover_id := project.cover_page_id) is None:
            return
        own_page = exists().where(PageRow.id == cover_id, PageRow.project_id == project.id)
        if not await self._rows.session.scalar(select(own_page)):
            raise NotFoundError(cover_id)

    @override
    async def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[ProjectOverview]:
        """Return a slice of the owner's projects with their counts, most recently updated first.

        The total is a separate count, which stays correct for a slice past the end where a window-function count
        would report zero.

        :param owner_id: Account whose projects are listed.
        :type owner_id: AccountId
        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Projects of the slice with their counts, and the total number of the owner's projects.
        :rtype: Slice[ProjectOverview]
        """
        statement = (
            select(ProjectRow, *self._book_counts(ProjectRow.id))
            .where(ProjectRow.owner_id == owner_id)
            .order_by(ProjectRow.updated_at.desc(), ProjectRow.id)
            .offset(request.offset)
            .limit(request.limit)
        )
        rows = await self._rows.session.execute(statement)
        overviews = [
            ProjectOverview(
                project=self._mapper.to_entity(row),
                page_count=pages,
                source_count=sources,
                scan_count=scans,
                image_page_count=with_image,
            )
            for row, pages, sources, scans, with_image in rows
        ]
        return Slice(items=overviews, total=await self._rows.count(owner_id=owner_id))

    @override
    async def overview(self, project: Project) -> ProjectOverview:
        """Count the included pages, the sources and the scans of the project in one statement.

        :param project: Project whose book is counted.
        :type project: Project
        :returns: The project with its counts.
        :rtype: ProjectOverview
        """
        pages, sources, scans, with_image = (
            await self._rows.session.execute(select(*self._book_counts(project.id)))
        ).one()
        return ProjectOverview(
            project=project, page_count=pages, source_count=sources, scan_count=scans, image_page_count=with_image
        )

    @staticmethod
    def _book_counts(project_id: QueryableAttribute[UUID] | ProjectId) -> tuple[ScalarSelect[int], ...]:
        """Return the scalar subqueries counting a project's included pages, sources and scans.

        :param project_id: The project's identifier, or the identifier column of the enclosing query's project rows,
                           which the subqueries then correlate with.
        :type project_id: QueryableAttribute[UUID] | ProjectId
        :returns: The count of included pages, of sources, of scans and of pages with an image, in this order.
        :rtype: tuple[ScalarSelect[int], ...]
        """
        return tuple(
            select(func.count()).where(*conditions).correlate(ProjectRow).scalar_subquery()
            for conditions in (
                (PageRow.project_id == project_id, PageRow.included.is_(True)),
                (SourceRow.project_id == project_id,),
                (ScanRow.project_id == project_id,),
                (PageRow.project_id == project_id, PageRow.origin != PageOrigin.PLACEHOLDER),
            )
        )


class SqlAlchemySourceRepository(SqlAlchemyRepository[Source, SourceId, SourceRow], SourceRepository):
    """Sources, listed per project in import order and found by the digest of their main file."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``sources`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=SourceRows(session=session), mapper=SourceMapper())

    @override
    async def list_for_project(self, project_id: ProjectId) -> Sequence[Source]:
        """Return the project's sources, the earliest import first, ties by identifier.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :returns: Every source of the project in import order.
        :rtype: Sequence[Source]
        """
        rows = await self._rows.get_many(
            order_by=[SourceRow.imported_at.asc(), SourceRow.id.asc()], project_id=project_id
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def find_by_sha256(self, project_id: ProjectId, sha256: str) -> Source | None:
        """Return the project's source with this digest, found through the unique key of the pair.

        :param project_id: Project owning the sources.
        :type project_id: ProjectId
        :param sha256: SHA-256 digest of a main file.
        :type sha256: str
        :returns: The source with this digest, or None.
        :rtype: Source | None
        """
        row = await self._rows.get_one_or_none(project_id=project_id, sha256=sha256)
        return None if row is None else self._mapper.to_entity(row)


class SqlAlchemyScanRepository(SqlAlchemyRepository[Scan, ScanId, ScanRow], ScanRepository):
    """Scans, listed per source by number and per project in the import order of their sources."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``scans`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=ScanRows(session=session), mapper=ScanMapper())

    @override
    async def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source by number.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source.
        :rtype: Sequence[Scan]
        """
        rows = await self._rows.get_many(order_by=ScanRow.number.asc(), source_id=source_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def list_for_project(self, project_id: ProjectId, request: SliceRequest) -> Slice[Scan]:
        """Return a slice of the project's scans, source by source in import order and by number within one.

        The order comes from the sources, so the listing joins them; the total is a separate count, which stays
        correct for a slice past the end.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Scans of the slice and the total number of the project's scans.
        :rtype: Slice[Scan]
        """
        statement = (
            select(ScanRow)
            .join(SourceRow, ScanRow.source_id == SourceRow.id)
            .where(ScanRow.project_id == project_id)
            .order_by(SourceRow.imported_at, SourceRow.id, ScanRow.number)
            .offset(request.offset)
            .limit(request.limit)
        )
        rows = (await self._rows.session.scalars(statement)).all()
        return Slice(
            items=[self._mapper.to_entity(row) for row in rows], total=await self._rows.count(project_id=project_id)
        )

    @override
    async def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return the stored scans among the given identifiers in one ``IN`` query.

        :param scan_ids: Scans to read.
        :type scan_ids: Collection[ScanId]
        :returns: The scans that are stored.
        :rtype: Sequence[Scan]
        """
        rows = await self._rows.get_many(CollectionFilter(field_name=ScanRow.id, values=set(scan_ids)))
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def list_unready(self, project_id: ProjectId) -> Sequence[Scan]:
        """Return the project's scans whose renditions are not ready, in the order ``list_for_project`` lists them.

        :param project_id: Project owning the scans.
        :type project_id: ProjectId
        :returns: The scans without ready renditions.
        :rtype: Sequence[Scan]
        """
        statement = (
            select(ScanRow)
            .join(SourceRow, ScanRow.source_id == SourceRow.id)
            .where(ScanRow.project_id == project_id, ScanRow.renditions_ready.is_(False))
            .order_by(SourceRow.imported_at, SourceRow.id, ScanRow.number)
        )
        return [self._mapper.to_entity(row) for row in (await self._rows.session.scalars(statement)).all()]


class SqlAlchemyPageRepository(SqlAlchemyRepository[Page, PageId, PageRow], PageRepository):
    """Pages of the book, listed in the byte order of their order keys."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``pages`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=PageRows(session=session), mapper=PageMapper())

    @override
    async def update(self, entity: Page) -> Page:
        """Replace the stored state of a page that has not changed since it was read.

        The version counter of the table refuses a write over a row that changed after the library loaded it, but the
        library loads the row again at the update, and the session keeps no row nobody refers to, so the counter alone
        never sees a change committed between the read of the page and its update. The revision the page was read at
        is therefore compared with the loaded row here, and the counter still covers the time between that load and
        the ``UPDATE`` statement.

        :param entity: Page with its new state and the revision it was read at.
        :type entity: Page
        :returns: The page as stored, with its revision raised by one.
        :rtype: Page
        :raises NotFoundError: If the page is not stored.
        :raises ConflictError: If the new state takes a key another page has.
        :raises ConcurrentChangeError: If the page was changed after it was read.
        """
        # The reference keeps the row in the session, so the update below changes this very row
        stored = await self._rows.get(entity.id)
        if stored.revision != entity.revision:
            raise ConcurrentChangeError
        return await super().update(entity)

    @override
    async def list_for_project(
        self, project_id: ProjectId, request: SliceRequest, *, included_only: bool = False
    ) -> Slice[Page]:
        """Return a slice of the project's pages in book order.

        The column's collation compares order keys byte by byte. The total is a separate count query, which stays
        correct for a slice past the end where a window-function count would report zero.

        :param project_id: Project whose pages are listed.
        :type project_id: ProjectId
        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book.
        :type included_only: bool
        :returns: Pages of the slice and the total number of the pages listed.
        :rtype: Slice[Page]
        """
        conditions = [PageRow.included.is_(True)] if included_only else []
        rows, total = await self._rows.get_many_and_count(
            LimitOffset(limit=request.limit, offset=request.offset),
            *conditions,
            order_by=PageRow.order_key.asc(),
            count_with_window_function=False,
            project_id=project_id,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=total)

    @override
    async def list_by_ids(self, project_id: ProjectId, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a project in book order, in one ``IN`` query.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param page_ids: Identifiers of the pages to read.
        :type page_ids: Collection[PageId]
        :returns: The pages in book order.
        :rtype: Sequence[Page]
        :raises NotFoundError: If an identifier names no page of the project.
        """
        wanted = set(page_ids)
        rows = await self._rows.get_many(
            CollectionFilter(field_name=PageRow.id, values=wanted),
            order_by=PageRow.order_key.asc(),
            project_id=project_id,
        )
        if missing := wanted - {row.id for row in rows}:
            raise NotFoundError(*missing)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def list_range(self, project_id: ProjectId, first_key: str, last_key: str) -> Sequence[Page]:
        """Return the pages between two order keys, both included, from the unique index of project and key.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param first_key: Smallest order key of the range.
        :type first_key: str
        :param last_key: Greatest order key of the range.
        :type last_key: str
        :returns: The pages of the range in book order.
        :rtype: Sequence[Page]
        """
        rows = await self._rows.get_many(
            PageRow.order_key.between(first_key, last_key), order_by=PageRow.order_key.asc(), project_id=project_id
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def list_for_source(self, project_id: ProjectId, source_id: SourceId) -> Sequence[Page]:
        """Return the pages whose scans belong to the source, joining ``pages`` with ``scans``.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param source_id: Source whose scans the pages show.
        :type source_id: SourceId
        :returns: The pages cut from the scans of the source, in book order.
        :rtype: Sequence[Page]
        """
        statement = (
            select(PageRow)
            .join(ScanRow, PageRow.scan_id == ScanRow.id)
            .where(PageRow.project_id == project_id, ScanRow.source_id == source_id)
            .order_by(PageRow.order_key)
        )
        return [self._mapper.to_entity(row) for row in (await self._rows.session.scalars(statement)).all()]

    @override
    async def neighbour_key(
        self, project_id: ProjectId, key: str, side: Side, *, excluding: Collection[PageId] = ()
    ) -> str | None:
        """Return the nearest order key on one side of ``key``, read from the unique index of project and key.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param key: Order key the neighbour is looked up from.
        :type key: str
        :param side: Whether to look before or after ``key``.
        :type side: Side
        :param excluding: Pages that do not count as neighbours.
        :type excluding: Collection[PageId]
        :returns: The nearest key on that side, or None when no page lies there.
        :rtype: str | None
        """
        before = side is Side.BEFORE
        statement = (
            select(PageRow.order_key)
            .where(
                PageRow.project_id == project_id,
                PageRow.order_key < key if before else PageRow.order_key > key,
                PageRow.id.not_in(excluding),
            )
            .order_by(PageRow.order_key.desc() if before else PageRow.order_key.asc())
            .limit(1)
        )
        return await self._rows.session.scalar(statement)

    @override
    async def update_many(self, pages: Sequence[Page]) -> None:
        """Replace the stored state of several pages, one after the other in the transaction of the unit of work.

        The transaction makes the pages change together or not at all, as the unit of work rolls it back on an error.

        :param pages: Pages with their new state.
        :type pages: Sequence[Page]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes a key another page has.
        """
        for page in pages:
            await self.update(page)

    @override
    async def list_for_scan(self, scan_id: ScanId) -> Sequence[Page]:
        """Return the pages cut from one scan by their slot.

        :param scan_id: Scan the pages were cut from.
        :type scan_id: ScanId
        :returns: Every page that names the scan.
        :rtype: Sequence[Page]
        """
        rows = await self._rows.get_many(order_by=PageRow.slot.asc(), scan_id=scan_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def count_before(self, page: Page) -> int:
        """Count the project's pages whose order key is smaller, which the unique index of project and key answers.

        :param page: Stored page of the project.
        :type page: Page
        :returns: The position of the page in the book, from zero.
        :rtype: int
        """
        return await self._rows.count(PageRow.order_key < page.order_key, project_id=page.project_id)

    @override
    async def last_order_key(self, project_id: ProjectId) -> str | None:
        """Return the greatest order key of the project's pages, read from the unique index of project and key.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The order key of the last page, or None for a book without pages.
        :rtype: str | None
        """
        statement = select(func.max(PageRow.order_key)).where(PageRow.project_id == project_id)
        return await self._rows.session.scalar(statement)


class SqlAlchemyPageVersionRepository(
    SqlAlchemyRepository[PageVersion, PageVersionId, PageVersionRow], PageVersionRepository
):
    """Versions of the pages of the book, listed per page in the order they were created."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``page_versions`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=PageVersionRows(session=session), mapper=PageVersionMapper())

    @override
    async def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page, the earliest first, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page.
        :rtype: Sequence[PageVersion]
        """
        rows = await self._rows.get_many(
            order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()], page_id=page_id
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def list_to_prepare(self, project_id: ProjectId, processor_keys: Collection[str]) -> Sequence[PageVersion]:
        """Return the pending and failed versions of the project's pages made by the given processors.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param processor_keys: Keys of the processors whose versions are returned.
        :type processor_keys: Collection[str]
        :returns: The versions still to prepare, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        statement = (
            select(PageVersionRow)
            .join(PageRow, PageVersionRow.page_id == PageRow.id)
            .where(
                PageRow.project_id == project_id,
                PageVersionRow.processor_key.in_(processor_keys),
                PageVersionRow.state.in_([VersionState.PENDING, VersionState.FAILED]),
            )
            .order_by(PageVersionRow.created_at, PageVersionRow.id)
        )
        return [self._mapper.to_entity(row) for row in (await self._rows.session.scalars(statement)).all()]

    @override
    async def base_sizes(self, project_id: ProjectId) -> Sequence[PageSize]:
        """Return the sizes the base versions of the project's included scan pages record in their data.

        Only the data column is read, and the sizes are taken from it here, since the column is JSON and the same
        reading serves every database.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The size of every such base version that records one.
        :rtype: Sequence[PageSize]
        """
        statement = (
            select(PageVersionRow.data)
            .join(PageRow, PageVersionRow.page_id == PageRow.id)
            .where(
                PageRow.project_id == project_id,
                PageRow.included.is_(True),
                PageRow.origin == PageOrigin.SCAN,
                PageVersionRow.input_id.is_(None),
            )
        )
        sizes = (PageSize.from_data(data) for data in (await self._rows.session.scalars(statement)).all())
        return [size for size in sizes if size is not None]

    @override
    async def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the versions of the given pages that have no input version, in one ``IN`` query.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        rows = await self._rows.get_many(
            CollectionFilter(field_name=PageVersionRow.page_id, values=page_ids),
            PageVersionRow.input_id.is_(None),
            order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def list_by_ids(self, version_ids: Collection[PageVersionId]) -> Sequence[PageVersion]:
        """Return the stored versions among the given identifiers, in one ``IN`` query.

        :param version_ids: Identifiers of the versions to read.
        :type version_ids: Collection[PageVersionId]
        :returns: The versions found, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        rows = await self._rows.get_many(
            CollectionFilter(field_name=PageVersionRow.id, values=version_ids),
            order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def find(self, version_id: PageVersionId) -> PageVersion | None:
        """Return the version with this identifier.

        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The stored version, or None.
        :rtype: PageVersion | None
        """
        row = await self._rows.get_one_or_none(id=version_id)
        return None if row is None else self._mapper.to_entity(row)

    @override
    async def list_for_stage(
        self, page_id: PageId, stage: Stage | None, scale: VersionScale | None, request: SliceRequest
    ) -> Slice[PageVersion]:
        """Return a window of the versions of one page matching a stage and a scale, the earliest first.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :param scale: Scale listed, or None for both.
        :type scale: VersionScale | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The window and the number of versions that match.
        :rtype: Slice[PageVersion]
        """
        filters: dict[str, Any] = {'page_id': page_id}
        if stage is not None:
            filters['stage'] = stage
        if scale is not None:
            filters['scale'] = scale
        rows, total = await self._rows.get_many_and_count(
            LimitOffset(limit=request.limit, offset=request.offset),
            order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()],
            **filters,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=total)

    @override
    async def collectable(
        self, project_id: ProjectId, older_than: datetime, previews_older_than: datetime
    ) -> Sequence[PageVersion]:
        """Return the old versions that are neither base versions nor in the chain of a current version.

        The chains are followed here, from the heads of the stage records through the input of each version, over the
        two columns of the project's versions, since a recursive query is not portable to every database.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param older_than: Full runs created before this moment may go.
        :type older_than: datetime
        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        session = self._rows.session
        eligible_by_age = and_(
            PageVersionRow.input_id.is_not(None),
            or_(
                and_(PageVersionRow.scale == VersionScale.FULL, PageVersionRow.created_at < older_than),
                and_(PageVersionRow.scale == VersionScale.PREVIEW, PageVersionRow.created_at < previews_older_than),
            ),
        )
        # The two columns of every version of the project, and whether it is old enough to go, since the chains are
        # followed here and a recursive query is not portable to every database
        rows = (
            await session.execute(
                select(PageVersionRow.id, PageVersionRow.input_id, eligible_by_age)
                .join(PageRow, PageVersionRow.page_id == PageRow.id)
                .where(PageRow.project_id == project_id)
            )
        ).all()
        heads = (
            await session.scalars(
                select(PageStageRow.head_version_id)
                .join(PageRow, PageStageRow.page_id == PageRow.id)
                .where(PageRow.project_id == project_id, PageStageRow.head_version_id.is_not(None))
            )
        ).all()
        goes = collectable_versions(
            {PageVersionId(version_id): input_id for version_id, input_id, _ in rows},
            eligible=[PageVersionId(version_id) for version_id, _, old in rows if old],
            heads=[PageVersionId(head) for head in heads if head is not None],
        )
        statement = (
            select(PageVersionRow)
            .where(PageVersionRow.id.in_(goes))
            .order_by(PageVersionRow.created_at, PageVersionRow.id)
        )
        return [self._mapper.to_entity(row) for row in (await session.scalars(statement)).all()]

    @override
    async def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the stored versions among the given ones, with the actions of the keys that refer to them.

        :param version_ids: Versions to remove.
        :type version_ids: Collection[PageVersionId]
        """
        if version_ids:
            await self._rows.session.execute(delete(PageVersionRow).where(PageVersionRow.id.in_(version_ids)))


class SqlAlchemyPageStageRepository(SqlAlchemyRepository[PageStage, PageStageKey, PageStageRow], PageStageRepository):
    """The current version of each stage of each page, addressed by the page and the stage."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``page_stages`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=PageStageRows(session=session), mapper=PageStageMapper())

    @override
    async def get(self, entity_id: PageStageKey) -> PageStage:
        """Return the record of a stage of a page.

        :param entity_id: Page and stage.
        :type entity_id: PageStageKey
        :returns: The stored record.
        :rtype: PageStage
        :raises NotFoundError: If the stage has no record on the page.
        """
        return self._mapper.to_entity(await self._rows.get((entity_id.page_id, entity_id.stage)))

    @override
    async def delete(self, entity_id: PageStageKey) -> None:
        """Remove the record of a stage of a page.

        :param entity_id: Page and stage.
        :type entity_id: PageStageKey
        :raises NotFoundError: If the stage has no record on the page.
        """
        await self._rows.delete((entity_id.page_id, entity_id.stage))

    @override
    async def save(self, stage: PageStage) -> PageStage:
        """Store the record, replacing the one of the same page and stage.

        :param stage: Record to store.
        :type stage: PageStage
        :returns: The record as stored.
        :rtype: PageStage
        :raises NotFoundError: If the page, the head version or the recipe is not stored.
        """
        if await self.find(stage.key) is None:
            return await self.add(stage)
        return await self.update(stage)

    @override
    async def find(self, key: PageStageKey) -> PageStage | None:
        """Return the record of a stage of a page.

        :param key: Page and stage.
        :type key: PageStageKey
        :returns: The record, or None.
        :rtype: PageStage | None
        """
        row = await self._rows.get_one_or_none(page_id=key.page_id, stage=key.stage)
        return None if row is None else self._mapper.to_entity(row)

    @override
    async def list_for_page(self, page_id: PageId) -> Sequence[PageStage]:
        """Return the records of one page in the order of the stages.

        :param page_id: Page owning the records.
        :type page_id: PageId
        :returns: Every record of the page.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        records = [self._mapper.to_entity(row) for row in await self._rows.get_many(page_id=page_id)]
        return sorted(records, key=lambda record: order.index(record.stage))

    @override
    async def list_for_pages(self, page_ids: Collection[PageId]) -> Sequence[PageStage]:
        """Return the records of several pages in one ``IN`` query, by page and then in the order of the stages.

        :param page_ids: Pages whose records are read.
        :type page_ids: Collection[PageId]
        :returns: Every record of those pages.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        rows = await self._rows.get_many(CollectionFilter(field_name=PageStageRow.page_id, values=page_ids))
        records = [self._mapper.to_entity(row) for row in rows]
        return sorted(records, key=lambda record: (str(record.page_id), order.index(record.stage)))

    @override
    async def list_for_recipe(self, recipe_id: RecipeId) -> Sequence[PageStage]:
        """Return the records that name the recipe, by page identifier.

        :param recipe_id: Recipe whose pages are listed.
        :type recipe_id: RecipeId
        :returns: Every record that names the recipe.
        :rtype: Sequence[PageStage]
        """
        rows = await self._rows.get_many(order_by=PageStageRow.page_id.asc(), recipe_id=recipe_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def list_for_project_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[PageStage]:
        """Return the records of one stage over the pages of a project, by page identifier.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the project.
        :rtype: Sequence[PageStage]
        """
        statement = (
            select(PageStageRow)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageRow.project_id == project_id, PageStageRow.stage == stage)
            .order_by(PageStageRow.page_id)
        )
        return [self._mapper.to_entity(row) for row in (await self._rows.session.scalars(statement)).all()]

    @override
    async def head_ids(self, project_id: ProjectId) -> Collection[PageVersionId]:
        """Return the distinct head versions of the stage records of the project's pages.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The identifiers of the current versions.
        :rtype: Collection[PageVersionId]
        """
        statement = (
            select(PageStageRow.head_version_id)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageRow.project_id == project_id, PageStageRow.head_version_id.is_not(None))
            .distinct()
        )
        return {
            PageVersionId(version_id)
            for version_id in (await self._rows.session.scalars(statement)).all()
            if version_id
        }

    @override
    async def tally(self, project_ids: Collection[ProjectId]) -> Sequence[StageTally]:
        """Count the records of every stage of the given projects by state with one grouped statement.

        The counts are conditional sums, which every database computes alike, and the head version is joined outside
        so a record without one still counts. Placeholders have no image, so their records are not counted.

        :param project_ids: Projects whose stages are counted.
        :type project_ids: Collection[ProjectId]
        :returns: One tally for each stage of each project that has a record.
        :rtype: Sequence[StageTally]
        """
        if not project_ids:
            return list[StageTally]()
        marked = and_(PageStageRow.state != StageState.FAILED, PageVersionRow.review.is_not(None))
        counted = (
            func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
            for condition in (
                PageStageRow.state == StageState.FRESH,
                PageStageRow.state == StageState.STALE,
                PageStageRow.state == StageState.FAILED,
                marked,
            )
        )
        statement = (
            select(PageRow.project_id, PageStageRow.stage, *counted)
            .select_from(PageStageRow)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .outerjoin(PageVersionRow, PageStageRow.head_version_id == PageVersionRow.id)
            .where(PageRow.project_id.in_(project_ids), PageRow.origin != PageOrigin.PLACEHOLDER)
            .group_by(PageRow.project_id, PageStageRow.stage)
        )
        return [
            StageTally(
                project_id=ProjectId(project), stage=stage, fresh=fresh, stale=stale, failed=failed, review=review
            )
            for project, stage, fresh, stale, failed, review in await self._rows.session.execute(statement)
        ]


class SqlAlchemyPageEditRepository(SqlAlchemyRepository[PageEdit, PageEditKey, PageEditRow], PageEditRepository):
    """Manual edits, addressed by the page, the stage and the processor."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``page_edits`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=PageEditRows(session=session), mapper=PageEditMapper())

    @override
    async def get(self, entity_id: PageEditKey) -> PageEdit:
        """Return one edit.

        :param entity_id: Page, stage and processor.
        :type entity_id: PageEditKey
        :returns: The stored edit.
        :rtype: PageEdit
        :raises NotFoundError: If the processor has no edit on the page and stage.
        """
        row = await self._rows.get((entity_id.page_id, entity_id.stage, entity_id.processor_key))
        return self._mapper.to_entity(row)

    @override
    async def delete(self, entity_id: PageEditKey) -> None:
        """Remove one edit.

        :param entity_id: Page, stage and processor.
        :type entity_id: PageEditKey
        :raises NotFoundError: If the processor has no edit on the page and stage.
        """
        await self._rows.delete((entity_id.page_id, entity_id.stage, entity_id.processor_key))

    @override
    async def save(self, edit: PageEdit) -> PageEdit:
        """Store an edit, replacing the one of the same page, stage and processor.

        :param edit: Edit to store.
        :type edit: PageEdit
        :returns: The edit as stored.
        :rtype: PageEdit
        :raises NotFoundError: If the page is not stored.
        """
        if await self.find(edit.key) is None:
            return await self.add(edit)
        return await self.update(edit)

    @override
    async def find(self, key: PageEditKey) -> PageEdit | None:
        """Return one edit.

        :param key: Page, stage and processor.
        :type key: PageEditKey
        :returns: The edit, or None.
        :rtype: PageEdit | None
        """
        row = await self._rows.get_one_or_none(page_id=key.page_id, stage=key.stage, processor_key=key.processor_key)
        return None if row is None else self._mapper.to_entity(row)

    @override
    async def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageEdit]:
        """Return the edits of one page, by stage and processor.

        :param page_id: Page owning the edits.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :returns: The edits of the page.
        :rtype: Sequence[PageEdit]
        """
        filters: dict[str, Any] = {'page_id': page_id}
        if stage is not None:
            filters['stage'] = stage
        order = list(Stage)
        edits = [self._mapper.to_entity(row) for row in await self._rows.get_many(**filters)]
        return sorted(edits, key=lambda edit: (order.index(edit.stage), edit.processor_key))


class SqlAlchemyRecipeRepository(SqlAlchemyRepository[Recipe, RecipeId, RecipeRow], RecipeRepository):
    """Recipes of the projects, of which a stage has one active."""

    def __init__(self, session: AsyncSession) -> None:
        """Create the repository over the ``recipes`` table.

        :param session: Session of the unit of work.
        :type session: AsyncSession
        """
        super().__init__(rows=RecipeRows(session=session), mapper=RecipeMapper())

    @override
    async def list_for_stage(self, project_id: ProjectId, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage, the active one first, then by creation, ties by identifier.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe and the variants.
        :rtype: Sequence[Recipe]
        """
        rows = await self._rows.get_many(
            order_by=[RecipeRow.active.desc(), RecipeRow.created_at.asc(), RecipeRow.id.asc()],
            project_id=project_id,
            stage=stage,
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    async def find_active(self, project_id: ProjectId, stage: Stage) -> Recipe | None:
        """Return the active recipe of a stage of a project.

        :param project_id: Project owning the recipe.
        :type project_id: ProjectId
        :param stage: The stage.
        :type stage: Stage
        :returns: The active recipe, or None.
        :rtype: Recipe | None
        """
        row = await self._rows.get_one_or_none(project_id=project_id, stage=stage, active=True)
        return None if row is None else self._mapper.to_entity(row)

    @override
    async def list_active(self, project_id: ProjectId) -> Sequence[Recipe]:
        """Return the active recipe of every stage of a project that has one, in the order of the stages.

        :param project_id: Project owning the recipes.
        :type project_id: ProjectId
        :returns: The active recipes.
        :rtype: Sequence[Recipe]
        """
        rows = await self._rows.get_many(project_id=project_id, active=True)
        return sorted((self._mapper.to_entity(row) for row in rows), key=lambda recipe: recipe.stage.position)


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
    async def list_for_projects(
        self, project_ids: Collection[ProjectId], states: Collection[JobState]
    ) -> Sequence[Job]:
        """Return the jobs of several projects in one of the given states with one query, newest first.

        :param project_ids: Projects whose jobs are listed.
        :type project_ids: Collection[ProjectId]
        :param states: States a listed job may be in.
        :type states: Collection[JobState]
        :returns: Matching jobs of all the projects, newest first.
        :rtype: Sequence[Job]
        """
        if not project_ids:
            return list[Job]()
        rows = await self._rows.get_many(
            CollectionFilter(field_name=JobRow.state, values=states),
            CollectionFilter(field_name=JobRow.project_id, values=project_ids),
            order_by=JobRow.created_at.desc(),
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
