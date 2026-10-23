"""Port repositories of the SQLAlchemy adapter, built on advanced-alchemy's synchronous repository.

Each port repository works on two levels. A row repository, a subclass of advanced-alchemy's
:class:`~advanced_alchemy.repository.SQLAlchemySyncRepository`, supplies every generic query of one table: lookup by
primary key, add, update, delete, filtered and paginated listings, and counts. A mapper from
:mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turns its rows into domain entities. The port repository
adds only the queries specific to BookReviver, such as the counts of the book of a project. The repositories are
synchronous, because the unit of work runs the whole work of a use case in one worker thread.
:class:`SqlAlchemyCatalogueRepositories` binds the repositories of the catalogue to the session of a transaction of the
catalogue, and :class:`SqlAlchemyBookRepositories` those of a book to a session of the file of that book.

The database's own checks are reported as the domain errors the port promises, so services never see an
advanced-alchemy exception. A missing row is a :class:`~bookreviver.domain.errors.NotFoundError` naming its key. A row
whose primary key or unique value is already stored is a :class:`~bookreviver.domain.errors.ConflictError` naming
them, and a row whose parent row is missing is a ``NotFoundError`` naming the parent's key. advanced-alchemy's own
Litestar handler answers 409 for every integrity error, but here every foreign key points at a row the caller named,
such as the owner of a book or the source of a scan, so a violated one means that row does not exist, which is a
404. Any other integrity error means the adapter wrote a row the schema forbids, a defect that propagates unchanged.
"""

import heapq
from collections import Counter
from contextlib import contextmanager
from itertools import batched, chain
from operator import attrgetter
from typing import TYPE_CHECKING, Any, ClassVar, cast, override

from advanced_alchemy.exceptions import DuplicateKeyError, ForeignKeyError, wrap_sqlalchemy_exception
from advanced_alchemy.exceptions import NotFoundError as MissingRowError
from advanced_alchemy.filters import CollectionFilter, LimitOffset
from advanced_alchemy.repository import DEFAULT_ERROR_MESSAGE_TEMPLATES, SQLAlchemySyncRepository
from attrs import evolve
from sqlalchemy import Table, UniqueConstraint, and_, case, delete, func, or_, select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bookreviver.adapters.persistence.sqlalchemy.catalogue import PROJECT_ID_COLUMN, LibraryRow, RecipeProfileRow
from bookreviver.adapters.persistence.sqlalchemy.database import SQLITE_DIALECT
from bookreviver.adapters.persistence.sqlalchemy.mappers import (
    BookPlaceMapper,
    LibraryEntryMapper,
    PageHistoryMapper,
    PageMapper,
    PageStageMapper,
    PageStepStateMapper,
    PageVersionMapper,
    PaginationSectionMapper,
    ProjectMapper,
    RecipeMapper,
    RecipeProfileMapper,
    ScanMapper,
    SourceMapper,
    StepValuesMapper,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    BOOK_PLACE_ID,
    EMPTY_TEXT,
    BookPlaceRow,
    PageHistoryRow,
    PageRow,
    PageStageRow,
    PageStepStateRow,
    PageVersionRow,
    PaginationSectionRow,
    ProjectRow,
    RecipeRow,
    ScanRow,
    SourceRow,
    StepValuesRow,
)
from bookreviver.domain.entities import (
    LibraryEntry,
    Page,
    PageHistoryEntry,
    PageStage,
    PageStepState,
    PageVersion,
    PaginationSection,
    Project,
    ProjectOverview,
    Recipe,
    RecipeProfile,
    Scan,
    Source,
)
from bookreviver.domain.enums import (
    ContentType,
    PageKind,
    PageOrigin,
    RecipeKind,
    ResultMark,
    Side,
    SourceStatus,
    Stage,
    StageState,
    ValueScope,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import (
    PageHistoryEntryId,
    PageId,
    PageVersionId,
    PaginationSectionId,
    ProjectId,
    RecipeId,
    RecipeProfileId,
    ScanId,
    SourceId,
)
from bookreviver.domain.stage_summaries import StageTally, StepTally
from bookreviver.domain.step_values import StepValues, StepValuesKey
from bookreviver.domain.values import PageSize, PageStageKey, PageStepKey, Slice
from bookreviver.domain.version_chains import collectable_versions
from bookreviver.ports.persistence import (
    BookPlaceRepository,
    BookRepositories,
    CatalogueRepositories,
    KeyedRepository,
    LibraryRepository,
    PageHistoryRepository,
    PageRepository,
    PageStageRepository,
    PageStepStateRepository,
    PageVersionRepository,
    PaginationSectionRepository,
    ProjectRepository,
    RecipeProfileRepository,
    RecipeRepository,
    Repository,
    ScanRepository,
    SourceRepository,
    StepValuesRepository,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Mapping, Sequence
    from datetime import datetime

    from advanced_alchemy.base import ModelProtocol
    from advanced_alchemy.filters import StatementFilter
    from advanced_alchemy.repository.typing import PrimaryKeyType
    from sqlalchemy import ColumnElement, CursorResult, ScalarSelect
    from sqlalchemy.orm import InstrumentedAttribute, Session

    from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
    from bookreviver.domain.entities import (
        BookPlace,
    )
    from bookreviver.domain.ids import AccountId, ChangeBatchId, StepId
    from bookreviver.domain.values import ProcessorRef, SliceRequest


class RowRepository[RowT: ModelProtocol](SQLAlchemySyncRepository[RowT]):
    """Repository of one table from advanced-alchemy, reporting the database's checks as domain errors.

    :cvar IN_CHUNK_SIZE: The most values one ``IN`` list carries, so a read of thousands of identifiers stays under the
                         limit of bound variables of a statement, which SQLite sets at 32766.
    """

    IN_CHUNK_SIZE: ClassVar[int] = 10_000

    def get_many_in(
        self,
        column: InstrumentedAttribute[Any],
        values: Collection[Any],
        *filters: StatementFilter | ColumnElement[bool],
        order: Sequence[InstrumentedAttribute[Any]] = (),
        **kwargs: Any,
    ) -> list[RowT]:
        """Return the rows whose column holds one of the values, in one statement for each chunk of the values.

        Every other filter applies to every statement. The rows of the chunks are put in the order the columns of
        ``order`` give, ascending, as one statement would have returned them.

        :param column: The column the values are matched against.
        :type column: InstrumentedAttribute[Any]
        :param values: The values wanted, of any number.
        :type values: Collection[Any]
        :param filters: Further filters of :meth:`SQLAlchemySyncRepository.get_many`.
        :type filters: StatementFilter | ColumnElement[bool]
        :param order: The columns the rows are ordered by, the first column first, or none for no order.
        :type order: Sequence[InstrumentedAttribute[Any]]
        :param kwargs: Keyword filters of :meth:`SQLAlchemySyncRepository.get_many`, passed through unchanged.
        :type kwargs: Any
        :returns: The rows, attached to the session.
        :rtype: list[RowT]
        """
        ordering: dict[str, Any] = {'order_by': [attribute.asc() for attribute in order]} if order else {}
        found = [
            self.get_many(CollectionFilter(field_name=column, values=chunk), *filters, **ordering, **kwargs)
            for chunk in batched(values, self.IN_CHUNK_SIZE, strict=False)
        ]
        if not order:
            return list(chain.from_iterable(found))
        return list(heapq.merge(*found, key=attrgetter(*(attribute.key for attribute in order))))

    @override
    def get(self, item_id: PrimaryKeyType, **options: Any) -> RowT:
        """Return the row with this primary key.

        The library's ``update`` and ``delete`` look the row up through this method, so all three report a missing
        row with its key.

        :param item_id: Primary key of the row, a tuple for a composite key.
        :type item_id: PrimaryKeyType
        :param options: Keyword options of :meth:`SQLAlchemySyncRepository.get`, passed through unchanged.
        :type options: Any
        :returns: The row, attached to the session.
        :rtype: RowT
        :raises NotFoundError: If no row has this key.
        """
        try:
            return super().get(item_id, **options)
        except MissingRowError as error:
            raise NotFoundError(item_id) from error

    @override
    def add(self, data: RowT, **options: Any) -> RowT:
        """Insert one row.

        :param data: Transient row to insert.
        :type data: RowT
        :param options: Keyword options of :meth:`SQLAlchemySyncRepository.add`, passed through unchanged.
        :type options: Any
        :returns: The row, attached to the session.
        :rtype: RowT
        :raises ConflictError: If a row with this primary key is already stored.
        :raises NotFoundError: If a row this one refers to through a foreign key is not stored.
        """
        with self._reporting_integrity_errors([data]):
            return super().add(data, **options)

    @override
    def add_many(self, data: list[RowT], **options: Any) -> Sequence[RowT]:
        """Insert several rows in one statement.

        :param data: Transient rows to insert.
        :type data: list[RowT]
        :param options: Keyword options of :meth:`SQLAlchemySyncRepository.add_many`, passed through unchanged.
        :type options: Any
        :returns: The rows, attached to the session.
        :rtype: Sequence[RowT]
        :raises ConflictError: If a row with one of these primary keys is already stored.
        :raises NotFoundError: If a row these refer to through a foreign key is not stored.
        """
        with self._reporting_integrity_errors(data):
            return super().add_many(data, **options)

    @override
    def update(self, data: RowT, **options: Any) -> RowT:
        """Replace the stored state of one row.

        :param data: Transient row holding the new state under the key of the stored row.
        :type data: RowT
        :param options: Keyword options of :meth:`SQLAlchemySyncRepository.update`, passed through unchanged.
        :type options: Any
        :returns: The row, attached to the session.
        :rtype: RowT
        :raises NotFoundError: If no row has this key, or a row this one refers to through a foreign key is not stored.
        :raises ConflictError: If the new state takes a unique value of another row.
        """
        with self._reporting_integrity_errors([data]):
            return super().update(data, **options)

    def replace_many(
        self, rows: Sequence[RowT], *, unless_newer_by: InstrumentedAttribute[Any] | None = None
    ) -> list[RowT]:
        """Store the rows whole in one upsert, each replacing the stored row of its primary key.

        The statement inserts every row, or on a conflict of the primary key sets every other column of the stored row
        from the new one, and returns the rows as stored. SQLAlchemy sends them in statements of at most its
        ``insertmanyvalues`` page size of rows and bound values, which stays under the limit of SQLite, so a long list
        needs no chunking here. The write is a statement of the transaction, so the rows are stored whole or, on an
        error, not at all.

        :param rows: Transient rows to store, of which every column is set.
        :type rows: Sequence[RowT]
        :param unless_newer_by: Column that holds the time of the write, or None to replace the stored row always. A
                                stored row whose value of the column is later than the new row's stays as it is, which
                                the database decides against the latest committed row.
        :type unless_newer_by: InstrumentedAttribute[Any] | None
        :returns: The rows as stored, attached to the session and refreshed, in the order given. A row the condition
                  refused is left out.
        :rtype: list[RowT]
        :raises NotFoundError: If a row that one of these refers to through a foreign key is not stored.
        :raises ConflictError: If a unique value of a row is another row's.
        """
        if not rows:
            return []
        mapper = self.model_type.__mapper__
        # The columns are named as their attributes, so one list serves the conflict target and the update
        names = [attribute.key for attribute in mapper.column_attrs]
        primary_key = [column.name for column in mapper.primary_key]
        insertion = sqlite_insert(self.model_type)
        where = None if unless_newer_by is None else unless_newer_by <= insertion.excluded[unless_newer_by.key]
        statement = (
            insertion.on_conflict_do_update(
                index_elements=primary_key,
                set_={name: insertion.excluded[name] for name in names if name not in primary_key},
                where=where,
            )
            .returning(self.model_type)
            # Without render_nulls the bulk insert leaves a null out of its statement, and rows that differ in which
            # of their columns are null would be sent as several statements
            .execution_options(populate_existing=True, render_nulls=True)
        )
        with self._reporting_integrity_errors(rows):
            result = self.session.execute(statement, [{name: getattr(row, name) for name in names} for row in rows])
            stored = {self.get_primary_key_value(row): row for row in result.scalars()}
        return [stored[key] for row in rows if (key := self.get_primary_key_value(row)) in stored]

    @contextmanager
    def _reporting_integrity_errors(self, rows: Sequence[RowT]) -> Iterator[None]:
        """Report the database rejecting ``rows`` as the domain error for the constraint they broke.

        The database does not say which row broke the constraint, so each error names the candidate keys: the
        primary keys and the unique values of the rows for a conflict, and the distinct values of their foreign keys
        for a missing parent. A statement of the session that the library did not send is wrapped the same way as one
        it sent.

        :param rows: Rows being written.
        :type rows: Sequence[RowT]
        :returns: Iterator yielding once around the write.
        :rtype: Iterator[None]
        :raises ConflictError: If a primary key or a unique value of ``rows`` is already stored.
        :raises NotFoundError: If a row that ``rows`` refer to is not stored.
        """
        try:
            with wrap_sqlalchemy_exception(error_messages=DEFAULT_ERROR_MESSAGE_TEMPLATES, dialect_name=SQLITE_DIALECT):
                yield
        except DuplicateKeyError as error:
            mapper, table = self.model_type.__mapper__, self.model_type.__table__
            # A declarative class maps one Table, the kind of FromClause that carries constraints and indexes. A value
            # that is unique only among some rows is a partial unique index, not a constraint, and is named too
            constraints = table.constraints if isinstance(table, Table) else set()
            indexes = table.indexes if isinstance(table, Table) else set()
            unique_columns = [
                *(constraint.columns for constraint in constraints if isinstance(constraint, UniqueConstraint)),
                *(index.columns for index in indexes if index.unique),
            ]
            unique_keys = [
                [mapper.get_property_by_column(column).key for column in columns] for columns in unique_columns
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


class LibraryRows(RowRepository[LibraryRow]):
    """Rows of the ``library`` table of the catalogue, keyed by the book."""

    model_type = LibraryRow
    id_attribute = PROJECT_ID_COLUMN


class ProjectRows(RowRepository[ProjectRow]):
    """Rows of the ``projects`` table of a book."""

    model_type = ProjectRow


class SourceRows(RowRepository[SourceRow]):
    """Rows of the ``sources`` table."""

    model_type = SourceRow


class ScanRows(RowRepository[ScanRow]):
    """Rows of the ``scans`` table."""

    model_type = ScanRow


class PageRows(RowRepository[PageRow]):
    """Rows of the ``pages`` table, several of which are updated by one flush."""

    model_type = PageRow

    def update_loaded(self, rows: Sequence[PageRow]) -> None:
        """Replace the stored state of several pages with one read and one flush.

        The stored rows are read in one chunked ``IN`` query, given the columns of the new rows, and flushed once, so
        the session sends one ``UPDATE`` for all the pages that change the same columns, where ``update`` reads and
        sends one for each page. The rows of the session are the ones changed, so the identity map stays current.

        :param rows: Transient rows holding the new state under the keys of the stored rows.
        :type rows: Sequence[PageRow]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes a key another page has.
        """
        stored = {row.id: row for row in self.get_many_in(PageRow.id, [row.id for row in rows])}
        if missing := [row.id for row in rows if row.id not in stored]:
            raise NotFoundError(*missing)
        names = [attribute.key for attribute in PageRow.__mapper__.column_attrs]
        for row in rows:
            for name in names:
                setattr(stored[row.id], name, getattr(row, name))
        with self._reporting_integrity_errors(rows):
            self.session.flush()


class PaginationSectionRows(RowRepository[PaginationSectionRow]):
    """Rows of the ``pagination_sections`` table."""

    model_type = PaginationSectionRow


class PageVersionRows(RowRepository[PageVersionRow]):
    """Rows of the ``page_versions`` table."""

    model_type = PageVersionRow


class PageStageRows(RowRepository[PageStageRow]):
    """Rows of the ``page_stages`` table."""

    model_type = PageStageRow


class PageStepStateRows(RowRepository[PageStepStateRow]):
    """Rows of the ``page_step_states`` table."""

    model_type = PageStepStateRow


class StepValuesRows(RowRepository[StepValuesRow]):
    """Rows of the ``step_values`` table."""

    model_type = StepValuesRow


class PageHistoryRows(RowRepository[PageHistoryRow]):
    """Rows of the ``page_history`` table."""

    model_type = PageHistoryRow


class BookPlaceRows(RowRepository[BookPlaceRow]):
    """The one row of the ``book_places`` table, written by one atomic upsert."""

    model_type = BookPlaceRow

    def replace_unless_newer(self, row: BookPlaceRow) -> BookPlaceRow:
        """Store the row whole in one statement, unless the stored row was written later.

        The statement is :meth:`RowRepository.replace_many` with the condition that the stored row's ``updated_at`` is
        not later than the new one. The database evaluates the condition against the latest committed row, and the
        write lock of the transaction holds until it ends, so two requests writing at once leave exactly one of them,
        whole, and never a mixture of the two. Reading the row first and updating the columns that differ would do the
        opposite: a request that read before the other committed writes only part of its columns.

        :param row: Transient row to store.
        :type row: BookPlaceRow
        :returns: The row as stored, which is the stored row when it was written later than ``row``.
        :rtype: BookPlaceRow
        """
        if stored := self.replace_many([row], unless_newer_by=BookPlaceRow.updated_at):
            return stored[0]
        # The condition refused the write, so the row that was written later stays, reloaded into the session
        return self.get(row.id, execution_options={'populate_existing': True})


class RecipeRows(RowRepository[RecipeRow]):
    """Rows of the ``recipes`` table."""

    model_type = RecipeRow


class RecipeProfileRows(RowRepository[RecipeProfileRow]):
    """Rows of the ``recipe_profiles`` table."""

    model_type = RecipeProfileRow


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
    def get(self, entity_id: IdT) -> EntityT:
        """Return the entity with this identifier.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :returns: The stored entity.
        :rtype: EntityT
        :raises NotFoundError: If no entity has this identifier.
        """
        return self._mapper.to_entity(self._rows.get(entity_id))

    @override
    def add(self, entity: EntityT) -> EntityT:
        """Store a new entity.

        :param entity: Entity to store, with its identifier already assigned.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        """
        return self._mapper.to_entity(self._rows.add(self._mapper.to_row(entity)))

    @override
    def add_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several new entities in one statement, which the database applies whole or not at all.

        :param entities: Entities to store, with their identifiers already assigned.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        """
        # An insert of no rows is no statement at all
        if not entities:
            return entities
        rows = self._rows.add_many([self._mapper.to_row(entity) for entity in entities])
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def update(self, entity: EntityT) -> EntityT:
        """Replace the stored state of an existing entity.

        :param entity: Entity with its new state.
        :type entity: EntityT
        :returns: The entity as stored.
        :rtype: EntityT
        :raises NotFoundError: If the entity is not stored.
        """
        return self._mapper.to_entity(self._rows.update(self._mapper.to_row(entity)))

    @override
    def delete(self, entity_id: IdT) -> None:
        """Remove the entity, and through the cascading foreign keys everything that belongs to it.

        :param entity_id: Identifier of the entity.
        :type entity_id: IdT
        :raises NotFoundError: If no entity has this identifier.
        """
        self._rows.delete(entity_id)


class SqlAlchemyKeyedRepository[EntityT, KeyT, RowT: ModelProtocol](
    SqlAlchemyRepository[EntityT, KeyT, RowT], KeyedRepository[EntityT, KeyT]
):
    """The operations of a table whose entities are stored under a key, built on one upsert of the row repository."""

    @override
    def save_many(self, entities: Sequence[EntityT]) -> Sequence[EntityT]:
        """Store several entities in one upsert, each replacing the stored one of its key, all of them or none.

        :param entities: Entities to store.
        :type entities: Sequence[EntityT]
        :returns: The entities as stored, in the given order.
        :rtype: Sequence[EntityT]
        :raises NotFoundError: If an entity one of them refers to is not stored.
        """
        rows = self._rows.replace_many([self._mapper.to_row(entity) for entity in entities])
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def find(self, key: KeyT) -> EntityT | None:
        """Return the entity stored under the key, read as :meth:`get` reads it.

        :param key: Key of the entity.
        :type key: KeyT
        :returns: The entity, or None.
        :rtype: EntityT | None
        """
        try:
            return self.get(key)
        except NotFoundError:
            return None


class SqlAlchemyLibraryRepository(SqlAlchemyRepository[LibraryEntry, ProjectId, LibraryRow], LibraryRepository):
    """The library of the catalogue, one entry for each book, listed per owner."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``library`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=LibraryRows(session=session), mapper=LibraryEntryMapper())

    @override
    def list_for_owner(self, owner_id: AccountId, request: SliceRequest) -> Slice[LibraryEntry]:
        """Return a slice of the owner's books, the latest added first, ties by identifier.

        The total is a separate count, which stays correct for a slice past the end where a window-function count
        would report zero.

        :param owner_id: Account whose books are listed.
        :type owner_id: AccountId
        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Entries of the slice, and the total number of the owner's books.
        :rtype: Slice[LibraryEntry]
        """
        rows = self._rows.get_many(
            LimitOffset(limit=request.limit, offset=request.offset),
            order_by=[LibraryRow.added_at.desc(), LibraryRow.project_id.asc()],
            owner_id=owner_id,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=self._rows.count(owner_id=owner_id))

    @override
    def list_all(self) -> Sequence[LibraryEntry]:
        """Return the entry of every book, by identifier.

        :returns: Every entry of the library.
        :rtype: Sequence[LibraryEntry]
        """
        return [self._mapper.to_entity(row) for row in self._rows.get_many(order_by=LibraryRow.project_id.asc())]


class SqlAlchemyProjectRepository(SqlAlchemyRepository[Project, ProjectId, ProjectRow], ProjectRepository):
    """The project of the book of the file, with the counts and the cover of its book.

    The counts are scalar subqueries, so one statement returns every count.
    """

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``projects`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=ProjectRows(session=session), mapper=ProjectMapper())

    @override
    def overview(self, project: Project) -> ProjectOverview:
        """Count the included pages, the sources and the scans of the project in one statement, and find its cover.

        The cover is the page of the kind ``cover`` that comes first in the book, else the first page of the book, and a
        book without pages has none.

        :param project: Project whose book is counted.
        :type project: Project
        :returns: The project with its counts and its cover.
        :rtype: ProjectOverview
        """
        pages, sources, scans, with_image = self._rows.session.execute(select(*self._book_counts())).one()
        cover = select(PageRow.id).order_by(PageRow.kind != PageKind.COVER, PageRow.order_key).limit(1)
        cover_id = self._rows.session.scalar(cover)
        return ProjectOverview(
            project=project,
            page_count=pages,
            source_count=sources,
            scan_count=scans,
            image_page_count=with_image,
            cover_page_id=None if cover_id is None else PageId(cover_id),
        )

    @staticmethod
    def _book_counts() -> tuple[ScalarSelect[int], ScalarSelect[int], ScalarSelect[int], ScalarSelect[int]]:
        """Return the scalar subqueries counting the included pages, the imported sources and the scans of the book.

        :returns: The count of included pages, of sources, of scans and of pages with an image, in this order.
        :rtype: tuple[ScalarSelect[int], ScalarSelect[int], ScalarSelect[int], ScalarSelect[int]]
        """
        pages, sources, scans, with_image = (
            select(func.count()).select_from(table).where(*conditions).scalar_subquery()
            for table, conditions in (
                (PageRow, (PageRow.included.is_(True),)),
                (SourceRow, (SourceRow.status == SourceStatus.IMPORTED,)),
                (ScanRow, ()),
                (PageRow, (PageRow.origin != PageOrigin.PLACEHOLDER,)),
            )
        )
        return pages, sources, scans, with_image


class SqlAlchemySourceRepository(SqlAlchemyRepository[Source, SourceId, SourceRow], SourceRepository):
    """Sources, listed in import order and found by the digest of their main file."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``sources`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=SourceRows(session=session), mapper=SourceMapper())

    @override
    def list_all(self) -> Sequence[Source]:
        """Return the project's imported sources, the earliest import first, ties by identifier.

        :returns: Every imported source of the project in import order.
        :rtype: Sequence[Source]
        """
        rows = self._rows.get_many(
            order_by=[SourceRow.imported_at.asc(), SourceRow.id.asc()],
            status=SourceStatus.IMPORTED,
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_unimported(self) -> Sequence[Source]:
        """Return the project's rejected and skipped files, the earliest first, ties by identifier.

        :returns: Every rejected or skipped source of the project in the order it was recorded.
        :rtype: Sequence[Source]
        """
        rows = self._rows.get_many(
            CollectionFilter(field_name=SourceRow.status, values=[SourceStatus.REJECTED, SourceStatus.SKIPPED]),
            order_by=[SourceRow.imported_at.asc(), SourceRow.id.asc()],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def delete_unimported(self) -> None:
        """Delete the project's rejected and skipped sources with one statement."""
        self._rows.session.execute(delete(SourceRow).where(SourceRow.status != SourceStatus.IMPORTED))

    @override
    def find_by_sha256(self, sha256: str) -> Source | None:
        """Return the project's imported source with this digest, found through the partial unique index.

        :param sha256: SHA-256 digest of a main file.
        :type sha256: str
        :returns: The imported source with this digest, or None.
        :rtype: Source | None
        """
        row = self._rows.get_one_or_none(sha256=sha256, status=SourceStatus.IMPORTED)
        return None if row is None else self._mapper.to_entity(row)


class SqlAlchemyScanRepository(SqlAlchemyRepository[Scan, ScanId, ScanRow], ScanRepository):
    """Scans, listed per source by number and over the book in the import order of their sources."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``scans`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=ScanRows(session=session), mapper=ScanMapper())

    @override
    def list_for_source(self, source_id: SourceId) -> Sequence[Scan]:
        """Return the scans of one source by number.

        :param source_id: Source holding the scans.
        :type source_id: SourceId
        :returns: Every scan of the source.
        :rtype: Sequence[Scan]
        """
        rows = self._rows.get_many(order_by=ScanRow.number.asc(), source_id=source_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_window(self, request: SliceRequest) -> Slice[Scan]:
        """Return a slice of the project's scans, source by source in import order and by number within one.

        The order comes from the sources, so the listing joins them; the total is a separate count, which stays
        correct for a slice past the end.

        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Scans of the slice and the total number of the project's scans.
        :rtype: Slice[Scan]
        """
        statement = (
            select(ScanRow)
            .join(SourceRow, ScanRow.source_id == SourceRow.id)
            .order_by(SourceRow.imported_at, SourceRow.id, ScanRow.number)
            .offset(request.offset)
            .limit(request.limit)
        )
        rows = self._rows.session.scalars(statement).all()
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=self._rows.count())

    @override
    def list_by_ids(self, scan_ids: Collection[ScanId]) -> Sequence[Scan]:
        """Return the stored scans among the given identifiers in one ``IN`` query.

        :param scan_ids: Scans to read.
        :type scan_ids: Collection[ScanId]
        :returns: The scans that are stored.
        :rtype: Sequence[Scan]
        """
        rows = self._rows.get_many_in(ScanRow.id, set(scan_ids))
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_unready(self) -> Sequence[Scan]:
        """Return the project's scans whose renditions are not ready, in the order ``list_for_project`` lists them.

        :returns: The scans without ready renditions.
        :rtype: Sequence[Scan]
        """
        statement = (
            select(ScanRow)
            .join(SourceRow, ScanRow.source_id == SourceRow.id)
            .where(ScanRow.renditions_ready.is_(False))
            .order_by(SourceRow.imported_at, SourceRow.id, ScanRow.number)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]


class SqlAlchemyPageRepository(SqlAlchemyRepository[Page, PageId, PageRow], PageRepository):
    """Pages of the book, listed in the byte order of their order keys.

    :ivar _pages: The rows of the table as their own repository, whose update of several pages the generic rows do not
                  have.
    """

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``pages`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        pages = PageRows(session=session)
        super().__init__(rows=pages, mapper=PageMapper())
        self._pages = pages

    @override
    def list_window(self, request: SliceRequest, *, included_only: bool = False) -> Slice[Page]:
        """Return a slice of the project's pages in book order.

        The column's collation compares order keys byte by byte. The total is a separate count query, which stays
        correct for a slice past the end where a window-function count would report zero.

        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :param included_only: Whether to leave out the pages kept out of the book.
        :type included_only: bool
        :returns: Pages of the slice and the total number of the pages listed.
        :rtype: Slice[Page]
        """
        conditions = [PageRow.included.is_(True)] if included_only else []
        rows, total = self._rows.get_many_and_count(
            LimitOffset(limit=request.limit, offset=request.offset),
            *conditions,
            order_by=PageRow.order_key.asc(),
            count_with_window_function=False,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=total)

    @override
    def list_by_ids(self, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a project in book order, in one ``IN`` query.

        :param page_ids: Identifiers of the pages to read.
        :type page_ids: Collection[PageId]
        :returns: The pages in book order.
        :rtype: Sequence[Page]
        :raises NotFoundError: If an identifier names no page.
        """
        wanted = set(page_ids)
        rows = self._rows.get_many_in(PageRow.id, wanted, order=[PageRow.order_key])
        if missing := wanted - {row.id for row in rows}:
            raise NotFoundError(*missing)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_for_source(self, source_id: SourceId) -> Sequence[Page]:
        """Return the pages whose scans belong to the source, joining ``pages`` with ``scans``.

        :param source_id: Source whose scans the pages show.
        :type source_id: SourceId
        :returns: The pages cut from the scans of the source, in book order.
        :rtype: Sequence[Page]
        """
        statement = (
            select(PageRow)
            .join(ScanRow, PageRow.scan_id == ScanRow.id)
            .where(ScanRow.source_id == source_id)
            .order_by(PageRow.order_key)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]

    @override
    def neighbour_key(self, key: str, side: Side, *, excluding: Collection[PageId] = ()) -> str | None:
        """Return the nearest order key on one side of ``key``, read from the unique index of the key.

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
                PageRow.order_key < key if before else PageRow.order_key > key,
                PageRow.id.not_in(excluding),
            )
            .order_by(PageRow.order_key.desc() if before else PageRow.order_key.asc())
            .limit(1)
        )
        return self._rows.session.scalar(statement)

    @override
    def update_many(self, pages: Sequence[Page]) -> None:
        """Replace the stored state of several pages with one read and one flush, in the transaction of the work.

        The transaction makes the pages change together or not at all, as the unit of work rolls it back on an error.

        :param pages: Pages with their new state.
        :type pages: Sequence[Page]
        :raises NotFoundError: If a page is not stored.
        :raises ConflictError: If the new state of a page takes a key another page has.
        """
        self._pages.update_loaded([self._mapper.to_row(page) for page in pages])

    @override
    def list_for_scan(self, scan_id: ScanId) -> Sequence[Page]:
        """Return the pages cut from one scan by their slot.

        :param scan_id: Scan the pages were cut from.
        :type scan_id: ScanId
        :returns: Every page that names the scan.
        :rtype: Sequence[Page]
        """
        rows = self._rows.get_many(order_by=PageRow.slot.asc(), scan_id=scan_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def count_before(self, page: Page) -> int:
        """Count the project's pages whose order key is smaller, which the unique index of the key answers.

        :param page: Stored page of the project.
        :type page: Page
        :returns: The position of the page in the book, from zero.
        :rtype: int
        """
        return self._rows.count(PageRow.order_key < page.order_key)

    @override
    def last_order_key(self) -> str | None:
        """Return the greatest order key of the project's pages, read from the unique index of the key.

        :returns: The order key of the last page, or None for a book without pages.
        :rtype: str | None
        """
        statement = select(func.max(PageRow.order_key))
        return self._rows.session.scalar(statement)

    @override
    def kind_tally(self) -> Mapping[RecipeKind, int]:
        """Count the pages with an image of each kind with one grouped statement over the columns the kind is read from.

        :returns: The number of pages of each kind that has any page.
        :rtype: Mapping[RecipeKind, int]
        """
        statement = (
            select(PageRow.kind, PageRow.content_type, PageRow.content_by_hand, func.count())
            .where(PageRow.origin != PageOrigin.PLACEHOLDER)
            .group_by(PageRow.kind, PageRow.content_type, PageRow.content_by_hand)
        )
        tally: Counter[RecipeKind] = Counter()
        for kind, content_type, by_hand, pages in self._rows.session.execute(statement):
            tally[RecipeKind.of(kind, ContentType.shown_by(kind, content_type, by_hand=by_hand))] += pages
        return tally


class SqlAlchemyPaginationSectionRepository(
    SqlAlchemyRepository[PaginationSection, PaginationSectionId, PaginationSectionRow], PaginationSectionRepository
):
    """The pagination sections of the book, listed in the order they were made."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``pagination_sections`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=PaginationSectionRows(session=session), mapper=PaginationSectionMapper())

    @override
    def list_all(self) -> Sequence[PaginationSection]:
        """Return the sections of a project in the order they were made, ties by identifier.

        :returns: Every section of the project.
        :rtype: Sequence[PaginationSection]
        """
        rows = self._rows.get_many(order_by=[PaginationSectionRow.created_at.asc(), PaginationSectionRow.id.asc()])
        return [self._mapper.to_entity(row) for row in rows]


class SqlAlchemyPageVersionRepository(
    SqlAlchemyRepository[PageVersion, PageVersionId, PageVersionRow], PageVersionRepository
):
    """Versions of the pages of the book, listed per page in the order they were created."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``page_versions`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=PageVersionRows(session=session), mapper=PageVersionMapper())

    @override
    def list_for_page(self, page_id: PageId) -> Sequence[PageVersion]:
        """Return the versions of one page, the earliest first, ties by identifier.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :returns: Every version of the page.
        :rtype: Sequence[PageVersion]
        """
        rows = self._rows.get_many(order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()], page_id=page_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_to_prepare(self, processor_keys: Collection[str]) -> Sequence[PageVersion]:
        """Return the pending and failed versions of the project's pages made by the given processors.

        :param processor_keys: Keys of the processors whose versions are returned.
        :type processor_keys: Collection[str]
        :returns: The versions still to prepare, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        statement = (
            select(PageVersionRow)
            .join(PageRow, PageVersionRow.page_id == PageRow.id)
            .where(
                PageVersionRow.processor_key.in_(processor_keys),
                PageVersionRow.state.in_([VersionState.PENDING, VersionState.FAILED]),
            )
            .order_by(PageVersionRow.created_at, PageVersionRow.id)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]

    @override
    def base_sizes(self) -> Sequence[PageSize]:
        """Return the sizes the base versions of the project's included scan pages record in their data.

        A leaf drawn in place of a scan is a version of the page order, so it is not counted. Only the data column is
        read, and the sizes are taken from it here, since the column is JSON.

        :returns: The size of every such base version that records one.
        :rtype: Sequence[PageSize]
        """
        statement = (
            select(PageVersionRow.data)
            .join(PageRow, PageVersionRow.page_id == PageRow.id)
            .where(
                PageRow.included.is_(True),
                PageRow.origin == PageOrigin.SCAN,
                PageVersionRow.input_id.is_(None),
                PageVersionRow.stage == Stage.PAGE_SPLIT,
            )
        )
        stored: Sequence[dict[str, Any]] = self._rows.session.scalars(statement).all()
        sizes = (PageSize.from_data(data) for data in stored)
        return [size for size in sizes if size is not None]

    @override
    def list_base_versions(self, page_ids: Collection[PageId]) -> Sequence[PageVersion]:
        """Return the versions of the given pages that have no input version, in one ``IN`` query.

        :param page_ids: Pages whose base versions are read.
        :type page_ids: Collection[PageId]
        :returns: The base versions of those pages, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        rows = self._rows.get_many_in(
            PageVersionRow.page_id,
            page_ids,
            PageVersionRow.input_id.is_(None),
            order=[PageVersionRow.created_at, PageVersionRow.id],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_by_ids(self, version_ids: Collection[PageVersionId]) -> Sequence[PageVersion]:
        """Return the stored versions among the given identifiers, in one ``IN`` query.

        :param version_ids: Identifiers of the versions to read.
        :type version_ids: Collection[PageVersionId]
        :returns: The versions found, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        rows = self._rows.get_many_in(
            PageVersionRow.id, version_ids, order=[PageVersionRow.created_at, PageVersionRow.id]
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def find(self, version_id: PageVersionId) -> PageVersion | None:
        """Return the version with this identifier.

        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The stored version, or None.
        :rtype: PageVersion | None
        """
        row = self._rows.get_one_or_none(id=version_id)
        return None if row is None else self._mapper.to_entity(row)

    @override
    def list_for_stage(
        self,
        page_id: PageId,
        stage: Stage | None,
        scale: VersionScale | None,
        request: SliceRequest,
        mark: ResultMark | None = None,
    ) -> Slice[PageVersion]:
        """Return a window of the versions of one page matching a stage, a scale and a mark, the earliest first.

        :param page_id: Page owning the versions.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :param scale: Scale listed, or None for both.
        :type scale: VersionScale | None
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param mark: Mark listed, or None for every version, marked or not.
        :type mark: ResultMark | None
        :returns: The window and the number of versions that match.
        :rtype: Slice[PageVersion]
        """
        filters: list[ColumnElement[bool]] = [PageVersionRow.page_id == page_id]
        if stage is not None:
            filters.append(PageVersionRow.stage == stage)
        if scale is not None:
            filters.append(PageVersionRow.scale == scale)
        if mark is not None:
            filters.append(PageVersionRow.mark == mark)
        rows, total = self._rows.get_many_and_count(
            LimitOffset(limit=request.limit, offset=request.offset),
            *filters,
            order_by=[PageVersionRow.created_at.asc(), PageVersionRow.id.asc()],
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=total)

    @override
    def collectable(self, previews_older_than: datetime) -> Sequence[PageVersion]:
        """Return the versions that are neither base versions nor in the chain of a current version.

        The chains are followed here, from the heads of the stage records through the input of each version, over the
        two columns of the project's versions, read in one query. A recursive query, ``WITH RECURSIVE``, would follow
        them in the database; whether it is faster is a question for a measurement on a large book.

        :param previews_older_than: Previews created before this moment may go.
        :type previews_older_than: datetime
        :returns: The versions that may be deleted, the earliest first, ties by identifier.
        :rtype: Sequence[PageVersion]
        """
        session = self._rows.session
        may_go = and_(
            PageVersionRow.input_id.is_not(None),
            or_(PageVersionRow.mark.is_(None), PageVersionRow.mark != ResultMark.GOOD),
            PageVersionRow.comment == EMPTY_TEXT,
            or_(PageVersionRow.scale != VersionScale.PREVIEW, PageVersionRow.created_at < previews_older_than),
        )
        # The two columns of every version of the project, and whether it may go, since the chains are followed here
        rows = session.execute(
            select(PageVersionRow.id, PageVersionRow.input_id, may_go).join(
                PageRow, PageVersionRow.page_id == PageRow.id
            )
        ).all()
        heads = session.scalars(
            select(PageStageRow.head_version_id)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageStageRow.head_version_id.is_not(None))
        ).all()
        goes = collectable_versions(
            {
                PageVersionId(version_id): None if input_id is None else PageVersionId(input_id)
                for version_id, input_id, _ in rows
            },
            eligible=[PageVersionId(version_id) for version_id, _, can_go in rows if can_go],
            heads=[PageVersionId(head) for head in heads if head is not None],
        )
        found = self._rows.get_many_in(PageVersionRow.id, goes, order=[PageVersionRow.created_at, PageVersionRow.id])
        return [self._mapper.to_entity(row) for row in found]

    @override
    def delete_many(self, version_ids: Collection[PageVersionId]) -> None:
        """Remove the stored versions among the given ones, with the actions of the keys that refer to them.

        :param version_ids: Versions to remove.
        :type version_ids: Collection[PageVersionId]
        """
        for chunk in batched(version_ids, self._rows.IN_CHUNK_SIZE, strict=False):
            self._rows.session.execute(delete(PageVersionRow).where(PageVersionRow.id.in_(chunk)))


class SqlAlchemyPageStageRepository(
    SqlAlchemyKeyedRepository[PageStage, PageStageKey, PageStageRow], PageStageRepository
):
    """The current version of each stage of each page, addressed by the page and the stage."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``page_stages`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=PageStageRows(session=session), mapper=PageStageMapper())

    @override
    def get(self, entity_id: PageStageKey) -> PageStage:
        """Return the record of a stage of a page.

        :param entity_id: Page and stage.
        :type entity_id: PageStageKey
        :returns: The stored record.
        :rtype: PageStage
        :raises NotFoundError: If the stage has no record on the page.
        """
        return self._mapper.to_entity(self._rows.get((entity_id.page_id, entity_id.stage)))

    @override
    def delete(self, entity_id: PageStageKey) -> None:
        """Remove the record of a stage of a page.

        :param entity_id: Page and stage.
        :type entity_id: PageStageKey
        :raises NotFoundError: If the stage has no record on the page.
        """
        self._rows.delete((entity_id.page_id, entity_id.stage))

    @override
    def list_for_page(self, page_id: PageId) -> Sequence[PageStage]:
        """Return the records of one page in the order of the stages.

        :param page_id: Page owning the records.
        :type page_id: PageId
        :returns: Every record of the page.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        records = [self._mapper.to_entity(row) for row in self._rows.get_many(page_id=page_id)]
        return sorted(records, key=lambda record: order.index(record.stage))

    @override
    def list_for_pages(self, page_ids: Collection[PageId]) -> Sequence[PageStage]:
        """Return the records of several pages in one ``IN`` query, by page and then in the order of the stages.

        :param page_ids: Pages whose records are read.
        :type page_ids: Collection[PageId]
        :returns: Every record of those pages.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        rows = self._rows.get_many_in(PageStageRow.page_id, page_ids)
        records = [self._mapper.to_entity(row) for row in rows]
        return sorted(records, key=lambda record: (str(record.page_id), order.index(record.stage)))

    @override
    def list_for_recipe(self, recipe_id: RecipeId) -> Sequence[PageStage]:
        """Return the records that name the recipe, by page identifier.

        :param recipe_id: Recipe whose pages are listed.
        :type recipe_id: RecipeId
        :returns: Every record that names the recipe.
        :rtype: Sequence[PageStage]
        """
        rows = self._rows.get_many(order_by=PageStageRow.page_id.asc(), recipe_id=recipe_id)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_fresh(self) -> Sequence[PageStage]:
        """Return the records in the fresh state, by page and then in the order of the stages.

        :returns: The fresh records of the book.
        :rtype: Sequence[PageStage]
        """
        order = list(Stage)
        records = [self._mapper.to_entity(row) for row in self._rows.get_many(state=StageState.FRESH)]
        return sorted(records, key=lambda record: (str(record.page_id), order.index(record.stage)))

    @override
    def list_replaced(self, stage: Stage, processor: ProcessorRef) -> Sequence[PageStage]:
        """Return the records of a stage whose current version a replaced version of a processor made, in one query.

        :param stage: The stage whose records are read.
        :type stage: Stage
        :param processor: The processor by key and installed version.
        :type processor: ProcessorRef
        :returns: The records whose current version was made by the key in another version, by page identifier.
        :rtype: Sequence[PageStage]
        """
        statement = (
            select(PageStageRow)
            .join(PageVersionRow, PageStageRow.head_version_id == PageVersionRow.id)
            .where(
                PageStageRow.stage == stage,
                PageVersionRow.processor_key == processor.key,
                PageVersionRow.processor_version != processor.version,
            )
            .order_by(PageStageRow.page_id)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]

    @override
    def list_for_stage(self, stage: Stage) -> Sequence[PageStage]:
        """Return the records of one stage over the pages of a project, by page identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the project.
        :rtype: Sequence[PageStage]
        """
        statement = (
            select(PageStageRow)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageStageRow.stage == stage)
            .order_by(PageStageRow.page_id)
        )
        return [self._mapper.to_entity(row) for row in self._rows.session.scalars(statement).all()]

    @override
    def head_ids(self) -> Collection[PageVersionId]:
        """Return the distinct head versions of the stage records of the project's pages.

        :returns: The identifiers of the current versions.
        :rtype: Collection[PageVersionId]
        """
        statement = (
            select(PageStageRow.head_version_id)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(PageStageRow.head_version_id.is_not(None))
            .distinct()
        )
        return {PageVersionId(version_id) for version_id in self._rows.session.scalars(statement).all() if version_id}

    @override
    def tally(self) -> Sequence[StageTally]:
        """Count the records of every stage of the project by state with one grouped statement.

        The counts are conditional sums, which every database computes alike, and the head version is joined outside
        so a record without one still counts. The check count is one more sum over the same rows, so a page that is
        both stale and marked is counted once. Placeholders have no image, so their records are not counted.

        :returns: One tally for each stage that has a record.
        :rtype: Sequence[StageTally]
        """
        marked = and_(PageStageRow.state != StageState.FAILED, PageVersionRow.review.is_not(None))
        fresh_sum, stale_sum, failed_sum, review_sum, check_sum, partial_sum = (
            func.coalesce(func.sum(case((condition, 1), else_=0)), 0)
            for condition in (
                PageStageRow.state == StageState.FRESH,
                PageStageRow.state == StageState.STALE,
                PageStageRow.state == StageState.FAILED,
                marked,
                or_(PageStageRow.state != StageState.FRESH, marked),
                and_(PageStageRow.state != StageState.FAILED, PageStageRow.through_step.is_not(None)),
            )
        )
        statement = (
            select(
                PageStageRow.stage,
                fresh_sum,
                stale_sum,
                failed_sum,
                review_sum,
                check_sum,
                partial_sum,
            )
            .select_from(PageStageRow)
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .outerjoin(PageVersionRow, PageStageRow.head_version_id == PageVersionRow.id)
            .where(PageRow.origin != PageOrigin.PLACEHOLDER)
            .group_by(PageStageRow.stage)
        )
        return [
            StageTally(
                stage=stage,
                fresh=fresh,
                stale=stale,
                failed=failed,
                review=review,
                check=check,
                partial=partial,
            )
            for stage, fresh, stale, failed, review, check, partial in self._rows.session.execute(statement)
        ]

    @override
    def step_tally(self) -> Sequence[StepTally]:
        """Count the pages a run stopped at each step, for every stage of the project, with one grouped statement.

        :returns: One tally for each step of a stage that a page stopped at.
        :rtype: Sequence[StepTally]
        """
        statement = (
            select(PageStageRow.stage, PageStageRow.through_step, func.count())
            .join(PageRow, PageStageRow.page_id == PageRow.id)
            .where(
                PageRow.origin != PageOrigin.PLACEHOLDER,
                PageStageRow.state != StageState.FAILED,
                PageStageRow.through_step.is_not(None),
            )
            .group_by(PageStageRow.stage, PageStageRow.through_step)
        )
        return [
            StepTally(stage=stage, through_step=through_step, pages=pages)
            for stage, through_step, pages in self._rows.session.execute(statement)
            if through_step is not None
        ]


class SqlAlchemyPageStepStateRepository(
    SqlAlchemyKeyedRepository[PageStepState, PageStepKey, PageStepStateRow], PageStepStateRepository
):
    """Settings and manual edits, addressed by the page, the stage and the step."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``page_step_states`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=PageStepStateRows(session=session), mapper=PageStepStateMapper())

    @override
    def get(self, entity_id: PageStepKey) -> PageStepState:
        """Return the state of one step on one page.

        :param entity_id: Page, stage and step.
        :type entity_id: PageStepKey
        :returns: The stored state.
        :rtype: PageStepState
        :raises NotFoundError: If the page has no state for the step.
        """
        row = self._rows.get((entity_id.page_id, entity_id.stage, entity_id.step_id))
        return self._mapper.to_entity(row)

    @override
    def delete(self, entity_id: PageStepKey) -> None:
        """Remove the state of one step on one page.

        :param entity_id: Page, stage and step.
        :type entity_id: PageStepKey
        :raises NotFoundError: If the page has no state for the step.
        """
        self._rows.delete((entity_id.page_id, entity_id.stage, entity_id.step_id))

    @override
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageStepState]:
        """Return the states of one page, by stage and step.

        :param page_id: Page owning the states.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :returns: The states of the page.
        :rtype: Sequence[PageStepState]
        """
        filters: list[ColumnElement[bool]] = [PageStepStateRow.page_id == page_id]
        if stage is not None:
            filters.append(PageStepStateRow.stage == stage)
        order = list(Stage)
        states = [self._mapper.to_entity(row) for row in self._rows.get_many(*filters)]
        return sorted(states, key=lambda state: (order.index(state.stage), str(state.step_id)))

    @override
    def list_for_step(self, page_ids: Collection[PageId], stage: Stage, step_id: StepId) -> Sequence[PageStepState]:
        """Return the states one step of a stage has on several pages in one ``IN`` query, by page identifier.

        :param page_ids: Pages whose states are read.
        :type page_ids: Collection[PageId]
        :param stage: Stage of the step.
        :type stage: Stage
        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The states of the step on those of the pages that have one.
        :rtype: Sequence[PageStepState]
        """
        rows = self._rows.get_many_in(
            PageStepStateRow.page_id, page_ids, order=[PageStepStateRow.page_id], stage=stage, step_id=step_id
        )
        return [self._mapper.to_entity(row) for row in rows]


class SqlAlchemyStepValuesRepository(
    SqlAlchemyKeyedRepository[StepValues, StepValuesKey, StepValuesRow], StepValuesRepository
):
    """The values of the steps for the odd pages, the even pages and the groups, addressed by project, step and part."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``step_values`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=StepValuesRows(session=session), mapper=StepValuesMapper())

    @staticmethod
    def _primary_key(key: StepValuesKey) -> tuple[StepId, ValueScope, str]:
        """Order a key as the columns of the primary key of the table.

        :param key: The step and the part of the pages.
        :type key: StepValuesKey
        :returns: The primary key of the row, as the row repository takes a composite key.
        :rtype: tuple[StepId, ValueScope, str]
        """
        return key.step_id, key.scope, key.group_label

    @override
    def get(self, entity_id: StepValuesKey) -> StepValues:
        """Return the values one part of the pages has for one step.

        :param entity_id: The project, the step and the part of the pages.
        :type entity_id: StepValuesKey
        :returns: The stored values.
        :rtype: StepValues
        :raises NotFoundError: If the part changes no field of the step.
        """
        row = self._rows.get(self._primary_key(entity_id))
        return self._mapper.to_entity(row)

    @override
    def delete(self, entity_id: StepValuesKey) -> None:
        """Remove the values one part of the pages has for one step.

        :param entity_id: The project, the step and the part of the pages.
        :type entity_id: StepValuesKey
        :raises NotFoundError: If the part changes no field of the step.
        """
        self._rows.delete(self._primary_key(entity_id))

    @override
    def list_for_step(self, step_id: StepId) -> Sequence[StepValues]:
        """Return the values every part of the pages of a project has for one step, by scope and group.

        :param step_id: The step of a recipe.
        :type step_id: StepId
        :returns: The values of the step.
        :rtype: Sequence[StepValues]
        """
        order = list(ValueScope)
        found = [self._mapper.to_entity(row) for row in self._rows.get_many(step_id=step_id)]
        return sorted(found, key=lambda values: (order.index(values.scope), values.group_label))

    @override
    def list_all(self) -> Sequence[StepValues]:
        """Return the values of every step of a project, by step, scope and group.

        :returns: The values of the project.
        :rtype: Sequence[StepValues]
        """
        order = list(ValueScope)
        found = [self._mapper.to_entity(row) for row in self._rows.get_many()]
        return sorted(found, key=lambda values: (str(values.step_id), order.index(values.scope), values.group_label))


class SqlAlchemyPageHistoryRepository(
    SqlAlchemyRepository[PageHistoryEntry, PageHistoryEntryId, PageHistoryRow], PageHistoryRepository
):
    """The history of the layers of the steps of the pages."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``page_history`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=PageHistoryRows(session=session), mapper=PageHistoryMapper())

    @override
    def add(self, entity: PageHistoryEntry) -> PageHistoryEntry:
        """Store a change, numbering it after the last change of its page.

        :param entity: Change to store.
        :type entity: PageHistoryEntry
        :returns: The change as stored, with its sequence.
        :rtype: PageHistoryEntry
        :raises ConflictError: If a change with this identifier is stored already.
        :raises NotFoundError: If the page is not stored.
        """
        [stored] = self.add_many([entity])
        return stored

    @override
    def add_many(self, entities: Sequence[PageHistoryEntry]) -> Sequence[PageHistoryEntry]:
        """Store several changes, numbering each after the last change of its page in the order given.

        :param entities: Changes to store.
        :type entities: Sequence[PageHistoryEntry]
        :returns: The changes as stored, with their sequences.
        :rtype: Sequence[PageHistoryEntry]
        :raises ConflictError: If an identifier is stored already or given twice.
        :raises NotFoundError: If a page is not stored.
        """
        next_of: dict[PageId, int] = {}
        numbered: list[PageHistoryEntry] = []
        for change in entities:
            if change.page_id not in next_of:
                last = self._rows.session.scalar(
                    select(func.max(PageHistoryRow.sequence)).where(PageHistoryRow.page_id == change.page_id)
                )
                next_of[change.page_id] = (last or 0) + 1
            numbered.append(evolve(change, sequence=next_of[change.page_id]))
            next_of[change.page_id] += 1
        return super().add_many(numbered)

    @override
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageHistoryEntry]:
        """Return the changes of one page, by their sequence.

        :param page_id: Page the changes were made on.
        :type page_id: PageId
        :param stage: Stage listed, or None for every stage.
        :type stage: Stage | None
        :returns: The changes of the page.
        :rtype: Sequence[PageHistoryEntry]
        """
        filters: list[ColumnElement[bool]] = [PageHistoryRow.page_id == page_id]
        if stage is not None:
            filters.append(PageHistoryRow.stage == stage)
        rows = self._rows.get_many(*filters, order_by=[PageHistoryRow.sequence.asc()])
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_for_batch(self, batch_id: ChangeBatchId) -> Sequence[PageHistoryEntry]:
        """Return the changes of one batch, by page and then by sequence.

        :param batch_id: Identifier the changes of the batch share.
        :type batch_id: ChangeBatchId
        :returns: The changes of the batch.
        :rtype: Sequence[PageHistoryEntry]
        """
        rows = self._rows.get_many(
            order_by=[PageHistoryRow.page_id.asc(), PageHistoryRow.sequence.asc()], batch_id=batch_id
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_undoing(self, change_ids: Collection[PageHistoryEntryId]) -> Sequence[PageHistoryEntry]:
        """Return the changes that take back any of the given changes, by page and then by sequence.

        :param change_ids: Identifiers of the changes that may have been undone.
        :type change_ids: Collection[PageHistoryEntryId]
        :returns: The undos.
        :rtype: Sequence[PageHistoryEntry]
        """
        rows = self._rows.get_many_in(
            PageHistoryRow.undoes_id,
            set(change_ids),
            order=[PageHistoryRow.page_id, PageHistoryRow.sequence],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def delete_for_step(self, key: PageStepKey) -> int:
        """Delete every change of one step on one page in one statement.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: How many changes were deleted.
        :rtype: int
        """
        statement = delete(PageHistoryRow).where(
            PageHistoryRow.page_id == key.page_id,
            PageHistoryRow.stage == key.stage,
            PageHistoryRow.step_id == key.step_id,
        )
        # An ORM delete of a mapped class returns the cursor result, whose row count the database reports
        return cast('CursorResult[Any]', self._rows.session.execute(statement)).rowcount


class SqlAlchemyBookPlaceRepository(BookPlaceRepository):
    """The one place the reader left the book at, the one row of ``book_places``.

    :ivar _places: The rows of the table, with the upsert the place is written by.
    :ivar _mapper: Mapper between the place and its row.
    """

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``book_places`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        self._places = BookPlaceRows(session=session)
        self._mapper = BookPlaceMapper()

    @override
    def save(self, place: BookPlace) -> BookPlace:
        """Store the place whole, replacing the stored one unless that one was written later.

        :param place: Place to store.
        :type place: BookPlace
        :returns: The place as stored, which is the stored place when it was written later than ``place``.
        :rtype: BookPlace
        """
        return self._mapper.to_entity(self._places.replace_unless_newer(self._mapper.to_row(place)))

    @override
    def find(self) -> BookPlace | None:
        """Return the place of the book.

        :returns: The place, or None when the book has not been worked on yet.
        :rtype: BookPlace | None
        """
        row = self._places.get_one_or_none(id=BOOK_PLACE_ID)
        return None if row is None else self._mapper.to_entity(row)


class SqlAlchemyRecipeRepository(SqlAlchemyRepository[Recipe, RecipeId, RecipeRow], RecipeRepository):
    """Recipes of the projects, of which a stage has one for each kind."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``recipes`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=RecipeRows(session=session), mapper=RecipeMapper())

    @override
    def list_for_stage(self, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage by creation, ties by identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes of the stage.
        :rtype: Sequence[Recipe]
        """
        rows = self._rows.get_many(order_by=[RecipeRow.created_at.asc(), RecipeRow.id.asc()], stage=stage)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_all(self) -> Sequence[Recipe]:
        """Return the recipes of every stage of a project, in the order of the stages and then by creation.

        :returns: The recipes of the stages that have been used.
        :rtype: Sequence[Recipe]
        """
        rows = self._rows.get_many(order_by=[RecipeRow.created_at.asc(), RecipeRow.id.asc()])
        return sorted((self._mapper.to_entity(row) for row in rows), key=lambda recipe: recipe.stage.position)


class SqlAlchemyRecipeProfileRepository(
    SqlAlchemyRepository[RecipeProfile, RecipeProfileId, RecipeProfileRow], RecipeProfileRepository
):
    """The recipe profiles of the accounts, of which a stage has one default for each account."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``recipe_profiles`` table.

        :param session: Session of the unit of work.
        :type session: Session
        """
        super().__init__(rows=RecipeProfileRows(session=session), mapper=RecipeProfileMapper())

    @override
    def list_for_account(self, account_id: AccountId, stage: Stage | None = None) -> Sequence[RecipeProfile]:
        """Return the account's profiles in the order of the stages, then by creation, ties by identifier.

        :param account_id: Account owning the profiles.
        :type account_id: AccountId
        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :returns: The profiles of the account.
        :rtype: Sequence[RecipeProfile]
        """
        rows = self._rows.get_many(
            order_by=[RecipeProfileRow.created_at.asc(), RecipeProfileRow.id.asc()], account_id=account_id
        )
        return sorted(
            (self._mapper.to_entity(row) for row in rows if stage is None or row.stage is stage),
            key=lambda profile: profile.stage.position,
        )

    @override
    def find_default(self, account_id: AccountId, stage: Stage) -> RecipeProfile | None:
        """Return the profile a new book of the account starts a stage with.

        :param account_id: Account owning the profile.
        :type account_id: AccountId
        :param stage: The stage.
        :type stage: Stage
        :returns: The default profile of the stage, or None.
        :rtype: RecipeProfile | None
        """
        row = self._rows.get_one_or_none(account_id=account_id, stage=stage, is_default=True)
        return None if row is None else self._mapper.to_entity(row)


class SqlAlchemyCatalogueRepositories(CatalogueRepositories):
    """Every repository of the catalogue, bound to the session of one transaction of the catalogue.

    The unit of work builds one for each transaction and hands it to the work, so the repositories share the session,
    and with it the transaction, and are dropped with it.

    :ivar library: Library repository bound to the session.
    :ivar recipe_profiles: Recipe profile repository bound to the session.
    """

    def __init__(self, session: Session) -> None:
        """Bind every repository of the catalogue to ``session``.

        :param session: Session of the transaction, owned and closed by the database that runs the work.
        :type session: Session
        """
        self.library = SqlAlchemyLibraryRepository(session)
        self.recipe_profiles = SqlAlchemyRecipeProfileRepository(session)


class SqlAlchemyBookRepositories(BookRepositories):
    """Every repository of a book, bound to the session of one transaction of the file of the book.

    The unit of work builds one for each transaction and hands it to the work, so the repositories share the session,
    and with it the transaction, and are dropped with it.

    :ivar projects: Project repository bound to the session.
    :ivar sources: Source repository bound to the session.
    :ivar scans: Scan repository bound to the session.
    :ivar pages: Page repository bound to the session.
    :ivar pagination_sections: Pagination section repository bound to the session.
    :ivar page_versions: Page version repository bound to the session.
    :ivar page_stages: Page stage repository bound to the session.
    :ivar page_step_states: Page step state repository bound to the session.
    :ivar page_history: Page history repository bound to the session.
    :ivar step_values: Step values repository bound to the session.
    :ivar recipes: Recipe repository bound to the session.
    :ivar book_places: Book place repository bound to the session.
    """

    def __init__(self, session: Session) -> None:
        """Bind every repository of a book to ``session``.

        :param session: Session of the transaction, owned and closed by the database that runs the work.
        :type session: Session
        """
        self.projects = SqlAlchemyProjectRepository(session)
        self.sources = SqlAlchemySourceRepository(session)
        self.scans = SqlAlchemyScanRepository(session)
        self.pages = SqlAlchemyPageRepository(session)
        self.pagination_sections = SqlAlchemyPaginationSectionRepository(session)
        self.page_versions = SqlAlchemyPageVersionRepository(session)
        self.page_stages = SqlAlchemyPageStageRepository(session)
        self.page_step_states = SqlAlchemyPageStepStateRepository(session)
        self.page_history = SqlAlchemyPageHistoryRepository(session)
        self.step_values = SqlAlchemyStepValuesRepository(session)
        self.recipes = SqlAlchemyRecipeRepository(session)
        self.book_places = SqlAlchemyBookPlaceRepository(session)
