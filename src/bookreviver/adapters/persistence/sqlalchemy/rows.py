"""Row repositories of the tables, built on advanced-alchemy's synchronous repository.

A row repository, a subclass of advanced-alchemy's :class:`~advanced_alchemy.repository.SQLAlchemySyncRepository`,
supplies every generic query of one table: lookup by primary key, add, update, delete, filtered and paginated listings,
and counts. The port repositories of :mod:`bookreviver.adapters.persistence.sqlalchemy.repositories` are built over
them.

The database's own checks are reported as the domain errors the port promises, so services never see an
advanced-alchemy exception. A missing row is a :class:`~bookreviver.domain.errors.NotFoundError` naming its key. A row
whose primary key or unique value is already stored is a :class:`~bookreviver.domain.errors.ConflictError` naming
them, and a row whose parent row is missing is a ``NotFoundError`` naming the parent's key. advanced-alchemy's own
Litestar handler answers 409 for every integrity error, but here every foreign key points at a row the caller named,
such as the owner of a book or the source of a scan, so a violated one means that row does not exist, which is a
404. Any other integrity error means the adapter wrote a row the schema forbids, a defect that propagates unchanged.
"""

import heapq
from contextlib import contextmanager
from itertools import batched, chain
from operator import attrgetter
from typing import TYPE_CHECKING, Any, ClassVar, override

from advanced_alchemy.exceptions import DuplicateKeyError, ForeignKeyError, wrap_sqlalchemy_exception
from advanced_alchemy.exceptions import NotFoundError as MissingRowError
from advanced_alchemy.filters import CollectionFilter
from advanced_alchemy.repository import DEFAULT_ERROR_MESSAGE_TEMPLATES, SQLAlchemySyncRepository
from sqlalchemy import Table, UniqueConstraint, delete, exists
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.database import SQLITE_DIALECT
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    BookPlaceRow,
    BookRecordRow,
    GroupValuesRow,
    PageHistoryRow,
    PageRow,
    PageStageRow,
    PageStepStateRow,
    PageVersionRow,
    PaginationSectionRow,
    RecipeProfileRow,
    RecipeRow,
    ScanRow,
    SourceRow,
    StepChangeRow,
    StepValuesRow,
)
from bookreviver.domain.errors import ConflictError, NotFoundError

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence
    from uuid import UUID

    from advanced_alchemy.base import ModelProtocol
    from advanced_alchemy.filters import StatementFilter
    from advanced_alchemy.repository.typing import PrimaryKeyType
    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import InstrumentedAttribute


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
    id_attribute = LibraryRow.book_id.key


class BookRecordRows(RowRepository[BookRecordRow]):
    """Rows of the ``book_records`` table of a book."""

    model_type = BookRecordRow


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


class GroupValuesRows(RowRepository[GroupValuesRow]):
    """Rows of the ``group_values`` table."""

    model_type = GroupValuesRow


class PageHistoryRows(RowRepository[PageHistoryRow]):
    """Rows of the ``page_history`` table."""

    model_type = PageHistoryRow


class StepChangeRows(RowRepository[StepChangeRow]):
    """Rows of the ``step_changes`` table, of which a change no page names any longer is deleted."""

    model_type = StepChangeRow

    def delete_unlinked(self, change_ids: Collection[UUID]) -> None:
        """Delete those of the changes that no row of the history of a page names any longer.

        :param change_ids: The changes whose rows of the history were deleted, of any number.
        :type change_ids: Collection[UUID]
        """
        for chunk in batched(change_ids, self.IN_CHUNK_SIZE, strict=False):
            self.session.execute(
                delete(StepChangeRow).where(
                    StepChangeRow.id.in_(chunk), ~exists().where(PageHistoryRow.change_id == StepChangeRow.id)
                )
            )


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
    """Rows of the ``recipe_profiles`` table of a book."""

    model_type = RecipeProfileRow
