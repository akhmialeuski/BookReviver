"""Port repositories of the SQLAlchemy adapter, each over the row repository of its table.

Each port repository works on two levels. A row repository of
:mod:`bookreviver.adapters.persistence.sqlalchemy.rows` supplies every generic query of one table, and a mapper from
:mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turns its rows into domain entities. The port repository
adds only the queries specific to BookReviver, such as the counts of a book. The repositories are
synchronous, because the catalogue database runs the whole work of a use case in one worker thread.
:class:`SqlAlchemyCatalogueRepositories` binds the repositories of the catalogue to the session of a transaction of the
catalogue, and :class:`SqlAlchemyBookRepositories` those of a book to a session of the file of that book. The row
repositories report the database's checks as the domain errors the port promises.
"""

from collections import Counter
from itertools import batched
from typing import TYPE_CHECKING, Any, cast, override

from advanced_alchemy.filters import CollectionFilter, LimitOffset
from attrs import evolve
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.orm import contains_eager

from bookreviver.adapters.persistence.sqlalchemy.book_record import SqlAlchemyBookRecordRepository
from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.catalogue_mappers import LibraryEntryMapper
from bookreviver.adapters.persistence.sqlalchemy.groups import PageGroups
from bookreviver.adapters.persistence.sqlalchemy.mappers import (
    BookPlaceMapper,
    GroupValuesMapper,
    PageHistoryMapper,
    PageMapper,
    PageStageMapper,
    PageStepStateMapper,
    PageVersionMapper,
    PaginationSectionMapper,
    RecipeMapper,
    ScanMapper,
    SourceMapper,
    StepValuesMapper,
)
from bookreviver.adapters.persistence.sqlalchemy.profile_mappers import RecipeProfileMapper
from bookreviver.adapters.persistence.sqlalchemy.rows import (
    BookPlaceRows,
    GroupValuesRows,
    LibraryRows,
    PageHistoryRows,
    PageRows,
    PageStageRows,
    PageStepStateRows,
    PageVersionRows,
    PaginationSectionRows,
    RecipeProfileRows,
    RecipeRows,
    ScanRows,
    SourceRows,
    StepChangeRows,
    StepValuesRows,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    BOOK_PLACE_ID,
    EMPTY_TEXT,
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
from bookreviver.domain.entities import (
    LibraryEntry,
    Page,
    PageStage,
    PageStepState,
    PageVersion,
    PaginationSection,
    Recipe,
    RecipeProfile,
    Scan,
    Source,
)
from bookreviver.domain.enums import (
    ContentType,
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
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import (
    BookId,
    PageId,
    PageVersionId,
    PaginationSectionId,
    RecipeId,
    RecipeProfileId,
    ScanId,
    SourceId,
    StepId,
)
from bookreviver.domain.stage_summaries import StageTally, StepTally
from bookreviver.domain.step_values import StepValues
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
    RecipeProfileRepository,
    RecipeRepository,
    Repository,
    ScanRepository,
    SourceRepository,
    StepValuesRepository,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence
    from datetime import datetime

    from advanced_alchemy.base import ModelProtocol
    from sqlalchemy import ColumnElement, CursorResult
    from sqlalchemy.orm import InstrumentedAttribute, Session

    from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
    from bookreviver.adapters.persistence.sqlalchemy.rows import (
        RowRepository,
    )
    from bookreviver.domain.entities import (
        BookPlace,
        PageHistoryEntry,
    )
    from bookreviver.domain.ids import (
        AccountId,
        ChangeBatchId,
        PageHistoryEntryId,
    )
    from bookreviver.domain.step_values import StepValuesKey
    from bookreviver.domain.values import ProcessorRef, SliceRequest


class SqlAlchemyRepository[EntityT, IdT, RowT: ModelProtocol](Repository[EntityT, IdT]):
    """The generic operations of a port repository, delegated to a row repository and mapped to entities."""

    def __init__(self, *, rows: RowRepository[RowT], mapper: RowMapper[EntityT, RowT]) -> None:
        """Create the repository over one table.

        :param rows: Row repository of the entity's table, bound to the session of the transaction.
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


class SqlAlchemyLibraryRepository(SqlAlchemyRepository[LibraryEntry, BookId, LibraryRow], LibraryRepository):
    """The library of the catalogue, one entry for each book, listed per owner."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``library`` table.

        :param session: Session of the transaction.
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
            order_by=[LibraryRow.added_at.desc(), LibraryRow.book_id.asc()],
            owner_id=owner_id,
        )
        return Slice(items=[self._mapper.to_entity(row) for row in rows], total=self._rows.count(owner_id=owner_id))

    @override
    def list_all(self) -> Sequence[LibraryEntry]:
        """Return the entry of every book, by identifier.

        :returns: Every entry of the library.
        :rtype: Sequence[LibraryEntry]
        """
        return [self._mapper.to_entity(row) for row in self._rows.get_many(order_by=LibraryRow.book_id.asc())]


class SqlAlchemySourceRepository(SqlAlchemyRepository[Source, SourceId, SourceRow], SourceRepository):
    """Sources, listed in import order and found by the digest of their main file."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``sources`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=SourceRows(session=session), mapper=SourceMapper())

    @override
    def list_all(self) -> Sequence[Source]:
        """Return the book's imported sources, the earliest import first, ties by identifier.

        :returns: Every imported source of the book in import order.
        :rtype: Sequence[Source]
        """
        rows = self._rows.get_many(
            order_by=[SourceRow.imported_at.asc(), SourceRow.id.asc()],
            status=SourceStatus.IMPORTED,
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_unimported(self) -> Sequence[Source]:
        """Return the book's rejected and skipped files, the earliest first, ties by identifier.

        :returns: Every rejected or skipped source of the book in the order it was recorded.
        :rtype: Sequence[Source]
        """
        rows = self._rows.get_many(
            CollectionFilter(field_name=SourceRow.status, values=[SourceStatus.REJECTED, SourceStatus.SKIPPED]),
            order_by=[SourceRow.imported_at.asc(), SourceRow.id.asc()],
        )
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def delete_unimported(self) -> None:
        """Delete the book's rejected and skipped sources with one statement."""
        self._rows.session.execute(delete(SourceRow).where(SourceRow.status != SourceStatus.IMPORTED))

    @override
    def find_by_sha256(self, sha256: str) -> Source | None:
        """Return the book's imported source with this digest, found through the partial unique index.

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

        :param session: Session of the transaction.
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
        """Return a slice of the book's scans, source by source in import order and by number within one.

        The order comes from the sources, so the listing joins them; the total is a separate count, which stays
        correct for a slice past the end.

        :param request: Offset and limit of the slice.
        :type request: SliceRequest
        :returns: Scans of the slice and the total number of the book's scans.
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
        """Return the book's scans whose renditions are not ready, in the order ``list_window`` lists them.

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
    :ivar _changes: The changes of the steps, of which those that reached only a deleted page go with it.
    """

    def __init__(self, session: Session, groups: PageGroups) -> None:
        """Create the repository over the ``pages`` table.

        :param session: Session of the transaction.
        :type session: Session
        :param groups: The groups of the book on the session, which name the group of a page.
        :type groups: PageGroups
        """
        pages = PageRows(session=session)
        super().__init__(rows=pages, mapper=PageMapper(groups))
        self._pages = pages
        self._changes = StepChangeRows(session=session)

    @override
    def delete(self, entity_id: PageId) -> None:
        """Remove the page, its history with it, and the changes that reached no other page.

        :param entity_id: Identifier of the page.
        :type entity_id: PageId
        :raises NotFoundError: If the page is not stored.
        """
        reached = self._pages.session.scalars(
            select(PageHistoryRow.change_id).where(PageHistoryRow.page_id == entity_id)
        ).all()
        super().delete(entity_id)
        self._changes.delete_unlinked(reached)

    @override
    def list_window(self, request: SliceRequest, *, included_only: bool = False) -> Slice[Page]:
        """Return a slice of the book's pages in book order.

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
    def list_all(self) -> Sequence[Page]:
        """Return every page of the book in book order, in one query.

        :returns: The pages of the book.
        :rtype: Sequence[Page]
        """
        return [self._mapper.to_entity(row) for row in self._rows.get_many(order_by=PageRow.order_key.asc())]

    @override
    def list_by_ids(self, page_ids: Collection[PageId]) -> Sequence[Page]:
        """Return the given pages of a book in book order, in one ``IN`` query.

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

        The transaction makes the pages change together or not at all, because an error rolls the whole transaction
        back.

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
        """Count the book's pages whose order key is smaller, which the unique index of the key answers.

        :param page: Stored page of the book.
        :type page: Page
        :returns: The position of the page in the book, from zero.
        :rtype: int
        """
        return self._rows.count(PageRow.order_key < page.order_key)

    @override
    def last_order_key(self) -> str | None:
        """Return the greatest order key of the book's pages, read from the unique index of the key.

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

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=PaginationSectionRows(session=session), mapper=PaginationSectionMapper())

    @override
    def list_all(self) -> Sequence[PaginationSection]:
        """Return the sections of a book in the order they were made, ties by identifier.

        :returns: Every section of the book.
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

        :param session: Session of the transaction.
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
        """Return the pending and failed versions of the book's pages made by the given processors.

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
        """Return the sizes the base versions of the book's included scan pages record in their data.

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
        two columns of the book's versions, read in one query. A recursive query, ``WITH RECURSIVE``, would follow
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
        # The two columns of every version of the book, and whether it may go, since the chains are followed here
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

        :param session: Session of the transaction.
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
        """Return the records of one stage over the pages of a book, by page identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: Every record of the stage in the book.
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
        """Return the distinct head versions of the stage records of the book's pages.

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
        """Count the records of every stage of the book by state with one grouped statement.

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
        """Count the pages a run stopped at each step, for every stage of the book, with one grouped statement.

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

        :param session: Session of the transaction.
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


class SqlAlchemyStepValuesRepository(StepValuesRepository):
    """The values of the steps for the odd pages, the even pages and the groups, addressed by step and part.

    The values of the odd and the even pages are rows of ``step_values`` and those of a group rows of
    ``group_values``, so every key of a row is a primary key with no null in it. Each table has a keyed repository of
    its own, and the key of the values says which one holds them.

    :ivar _groups: The groups of the book on the session.
    :ivar _parity_rows: The rows of the values of the odd and the even pages.
    :ivar _group_rows: The rows of the values of the groups.
    :ivar _parity_mapper: Translation of the values of the odd and the even pages.
    :ivar _group_mapper: Translation of the values of a group.
    :ivar _parity: The values of the odd and the even pages, under the primary key of their table.
    :ivar _grouped: The values of the groups, under the primary key of their table.
    """

    def __init__(self, session: Session, groups: PageGroups) -> None:
        """Create the repository over the ``step_values`` and the ``group_values`` tables.

        :param session: Session of the transaction.
        :type session: Session
        :param groups: The groups of the book on the session, which name the group of the values of a group.
        :type groups: PageGroups
        """
        self._groups = groups
        self._parity_rows = StepValuesRows(session=session)
        self._group_rows = GroupValuesRows(session=session)
        self._parity_mapper = StepValuesMapper()
        self._group_mapper = GroupValuesMapper(groups)
        self._parity = SqlAlchemyKeyedRepository[StepValues, tuple[StepId, ValueScope], StepValuesRow](
            rows=self._parity_rows, mapper=self._parity_mapper
        )
        self._grouped = SqlAlchemyKeyedRepository[StepValues, tuple[StepId, int], GroupValuesRow](
            rows=self._group_rows, mapper=self._group_mapper
        )

    def _group_key(self, key: StepValuesKey) -> tuple[StepId, int]:
        """Order the key of the values of a group as the primary key of ``group_values``.

        :param key: Key of the values of a group.
        :type key: StepValuesKey
        :returns: The step and the key of the group.
        :rtype: tuple[StepId, int]
        :raises NotFoundError: If the book has no group of this label, so no values are stored for it.
        """
        if (group_id := self._groups.find(key.group_label)) is None:
            raise NotFoundError(key)
        return key.step_id, group_id

    def _each_table(
        self,
        entities: Sequence[StepValues],
        write: Callable[[KeyedRepository[StepValues, Any], Sequence[StepValues]], Sequence[StepValues]],
    ) -> Sequence[StepValues]:
        """Write the values of the odd and the even pages and the values of the groups each to its table.

        :param entities: The values to write.
        :type entities: Sequence[StepValues]
        :param write: The write of several values to one table.
        :type write: Callable[[KeyedRepository[StepValues, Any], Sequence[StepValues]], Sequence[StepValues]]
        :returns: The values as stored, in the given order.
        :rtype: Sequence[StepValues]
        """
        parity = [values for values in entities if values.scope is not ValueScope.GROUP]
        grouped = [values for values in entities if values.scope is ValueScope.GROUP]
        stored = {values.key: values for values in (*write(self._parity, parity), *write(self._grouped, grouped))}
        return [stored[values.key] for values in entities]

    @override
    def get(self, entity_id: StepValuesKey) -> StepValues:
        """Return the values one part of the pages has for one step.

        :param entity_id: The step and the part of the pages.
        :type entity_id: StepValuesKey
        :returns: The stored values.
        :rtype: StepValues
        :raises NotFoundError: If the part changes no field of the step.
        """
        if entity_id.scope is ValueScope.GROUP:
            return self._grouped.get(self._group_key(entity_id))
        return self._parity.get((entity_id.step_id, entity_id.scope))

    @override
    def find(self, key: StepValuesKey) -> StepValues | None:
        """Return the values one part of the pages has for one step, read as :meth:`get` reads them.

        :param key: The step and the part of the pages.
        :type key: StepValuesKey
        :returns: The values, or None.
        :rtype: StepValues | None
        """
        try:
            return self.get(key)
        except NotFoundError:
            return None

    @override
    def add(self, entity: StepValues) -> StepValues:
        """Store the values of a part of the pages that has none for the step yet.

        :param entity: The values to store.
        :type entity: StepValues
        :returns: The values as stored.
        :rtype: StepValues
        :raises ConflictError: If the part has values for the step already.
        """
        [stored] = self.add_many([entity])
        return stored

    @override
    def add_many(self, entities: Sequence[StepValues]) -> Sequence[StepValues]:
        """Store the values of several parts of the pages that have none for their steps yet.

        :param entities: The values to store.
        :type entities: Sequence[StepValues]
        :returns: The values as stored, in the given order.
        :rtype: Sequence[StepValues]
        :raises ConflictError: If a part has values for its step already.
        """
        return self._each_table(entities, lambda table, part: table.add_many(part))

    @override
    def update(self, entity: StepValues) -> StepValues:
        """Replace the values a part of the pages has for a step.

        :param entity: The values with their new fields.
        :type entity: StepValues
        :returns: The values as stored.
        :rtype: StepValues
        :raises NotFoundError: If the part has no values for the step.
        """
        if entity.scope is ValueScope.GROUP:
            self._group_key(entity.key)
            return self._grouped.update(entity)
        return self._parity.update(entity)

    @override
    def save_many(self, entities: Sequence[StepValues]) -> Sequence[StepValues]:
        """Store the values of several parts of the pages, each replacing those under its key, all of them or none.

        :param entities: The values to store.
        :type entities: Sequence[StepValues]
        :returns: The values as stored, in the given order.
        :rtype: Sequence[StepValues]
        """
        return self._each_table(entities, lambda table, part: table.save_many(part))

    @override
    def delete(self, entity_id: StepValuesKey) -> None:
        """Remove the values one part of the pages has for one step.

        :param entity_id: The step and the part of the pages.
        :type entity_id: StepValuesKey
        :raises NotFoundError: If the part changes no field of the step.
        """
        if entity_id.scope is ValueScope.GROUP:
            self._grouped.delete(self._group_key(entity_id))
        else:
            self._parity.delete((entity_id.step_id, entity_id.scope))

    def _listed(self, **filters: Any) -> list[StepValues]:
        """Read the values of both tables that match the filters, ordered by step, scope and group.

        :param filters: Column values every row read has.
        :type filters: Any
        :returns: The values.
        :rtype: list[StepValues]
        """
        order = list(ValueScope)
        found = [
            *(self._parity_mapper.to_entity(row) for row in self._parity_rows.get_many(**filters)),
            *(self._group_mapper.to_entity(row) for row in self._group_rows.get_many(**filters)),
        ]
        return sorted(found, key=lambda values: (str(values.step_id), order.index(values.scope), values.group_label))

    @override
    def list_for_step(self, step_id: StepId) -> Sequence[StepValues]:
        """Return the values every part of the pages has for one step, by scope and group.

        :param step_id: The step.
        :type step_id: StepId
        :returns: The values of the step.
        :rtype: Sequence[StepValues]
        """
        return self._listed(step_id=step_id)

    @override
    def list_all(self) -> Sequence[StepValues]:
        """Return the values of every step of the book, by step, scope and group.

        :returns: The values of the book.
        :rtype: Sequence[StepValues]
        """
        return self._listed()


class SqlAlchemyPageHistoryRepository(PageHistoryRepository):
    """The history of the layers of the steps of the pages: each change once, and its place in each page it reached.

    :ivar _links: The rows of ``page_history``, which place a change in the history of a page.
    :ivar _changes: The rows of ``step_changes``, one for each change.
    :ivar _mapper: Translation between an entry of the history of a page and its two rows.
    """

    def __init__(self, session: Session, groups: PageGroups) -> None:
        """Create the repository over the ``page_history`` and the ``step_changes`` tables.

        :param session: Session of the transaction.
        :type session: Session
        :param groups: The groups of the book on the session, which name the group of a change of a group.
        :type groups: PageGroups
        """
        self._links = PageHistoryRows(session=session)
        self._changes = StepChangeRows(session=session)
        self._mapper = PageHistoryMapper(groups)

    def _entries(
        self, *filters: ColumnElement[bool], order: Sequence[InstrumentedAttribute[Any]]
    ) -> list[PageHistoryEntry]:
        """Read the entries of the histories that match the filters, each with its change in the same statement.

        :param filters: Conditions over the rows of the history and of the changes.
        :type filters: ColumnElement[bool]
        :param order: The columns the entries are ordered by, ascending.
        :type order: Sequence[InstrumentedAttribute[Any]]
        :returns: The entries.
        :rtype: list[PageHistoryEntry]
        """
        statement = (
            select(PageHistoryRow)
            .join(PageHistoryRow.change)
            .options(contains_eager(PageHistoryRow.change))
            .where(*filters)
            .order_by(*(column.asc() for column in order))
        )
        return [self._mapper.to_entity(row) for row in self._links.session.scalars(statement)]

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

        The entries that share an identifier are one change reaching several pages, which is stored once.

        :param entities: Changes to store.
        :type entities: Sequence[PageHistoryEntry]
        :returns: The changes as stored, with their sequences.
        :rtype: Sequence[PageHistoryEntry]
        :raises ConflictError: If a change is stored already, or reaches a page twice.
        :raises NotFoundError: If a page is not stored.
        """
        if not entities:
            return entities
        next_of: dict[PageId, int] = {}
        numbered: list[PageHistoryEntry] = []
        for change in entities:
            if change.page_id not in next_of:
                last = self._links.session.scalar(
                    select(func.max(PageHistoryRow.sequence)).where(PageHistoryRow.page_id == change.page_id)
                )
                next_of[change.page_id] = (last or 0) + 1
            numbered.append(evolve(change, sequence=next_of[change.page_id]))
            next_of[change.page_id] += 1
        changes = {change.id: change for change in reversed(numbered)}
        self._changes.add_many([self._mapper.change_row(change) for change in changes.values()])
        self._links.add_many([self._mapper.to_row(change) for change in numbered])
        return numbered

    @override
    def list_for_page(self, page_id: PageId, stage: Stage | None = None) -> Sequence[PageHistoryEntry]:
        """Return the changes of one page, by their sequence.

        :param page_id: Page the changes reached.
        :type page_id: PageId
        :param stage: Stage whose changes are listed, or None for every stage.
        :type stage: Stage | None
        :returns: The changes of the page.
        :rtype: Sequence[PageHistoryEntry]
        """
        filters: list[ColumnElement[bool]] = [PageHistoryRow.page_id == page_id]
        if stage is not None:
            filters.append(StepChangeRow.stage == stage)
        return self._entries(*filters, order=[PageHistoryRow.sequence])

    @override
    def list_for_batch(self, batch_id: ChangeBatchId) -> Sequence[PageHistoryEntry]:
        """Return the changes of one batch, by page and then by sequence.

        :param batch_id: Identifier the changes of the batch share.
        :type batch_id: ChangeBatchId
        :returns: The changes of the batch.
        :rtype: Sequence[PageHistoryEntry]
        """
        by_page = [PageHistoryRow.page_id, PageHistoryRow.sequence]
        return self._entries(StepChangeRow.batch_id == batch_id, order=by_page)

    @override
    def list_undoing(self, change_ids: Collection[PageHistoryEntryId]) -> Sequence[PageHistoryEntry]:
        """Return the changes that take back any of the given changes, by page and then by sequence.

        :param change_ids: Identifiers of the changes that may have been undone.
        :type change_ids: Collection[PageHistoryEntryId]
        :returns: The undos.
        :rtype: Sequence[PageHistoryEntry]
        """
        found = [
            entry
            for chunk in batched(set(change_ids), self._links.IN_CHUNK_SIZE, strict=False)
            for entry in self._entries(StepChangeRow.undoes_id.in_(chunk), order=[])
        ]
        return sorted(found, key=lambda entry: (entry.page_id, entry.sequence))

    @override
    def delete_for_step(self, key: PageStepKey) -> int:
        """Delete every change of one step on one page, and the changes that reached no other page.

        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: How many changes the history of the page lost.
        :rtype: int
        """
        session = self._links.session
        of_step = select(StepChangeRow.id).where(StepChangeRow.stage == key.stage, StepChangeRow.step_id == key.step_id)
        reached = session.scalars(
            select(PageHistoryRow.change_id).where(
                PageHistoryRow.page_id == key.page_id, PageHistoryRow.change_id.in_(of_step)
            )
        ).all()
        statement = delete(PageHistoryRow).where(
            PageHistoryRow.page_id == key.page_id, PageHistoryRow.change_id.in_(of_step)
        )
        # An ORM delete of a mapped class returns the cursor result, whose row count the database reports
        removed = cast('CursorResult[Any]', session.execute(statement)).rowcount
        self._changes.delete_unlinked(reached)
        return removed


class SqlAlchemyBookPlaceRepository(BookPlaceRepository):
    """The one place the reader left the book at, the one row of ``book_places``.

    :ivar _places: The rows of the table, with the upsert the place is written by.
    :ivar _mapper: Mapper between the place and its row.
    """

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``book_places`` table.

        :param session: Session of the transaction.
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
    """Recipes of the books, of which a stage has one for each kind."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``recipes`` table.

        :param session: Session of the transaction.
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
        """Return the recipes of every stage of a book, in the order of the stages and then by creation.

        :returns: The recipes of the stages that have been used.
        :rtype: Sequence[Recipe]
        """
        rows = self._rows.get_many(order_by=[RecipeRow.created_at.asc(), RecipeRow.id.asc()])
        return sorted((self._mapper.to_entity(row) for row in rows), key=lambda recipe: recipe.stage.position)


class SqlAlchemyRecipeProfileRepository(
    SqlAlchemyRepository[RecipeProfile, RecipeProfileId, RecipeProfileRow], RecipeProfileRepository
):
    """The recipe profiles of a book."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``recipe_profiles`` table of a book.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=RecipeProfileRows(session=session), mapper=RecipeProfileMapper())

    @override
    def list_all(self, stage: Stage | None = None) -> Sequence[RecipeProfile]:
        """Return the profiles of the book in the order of the stages, then by creation, ties by identifier.

        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :returns: The profiles of the book.
        :rtype: Sequence[RecipeProfile]
        """
        rows = self._rows.get_many(order_by=[RecipeProfileRow.created_at.asc(), RecipeProfileRow.id.asc()])
        return sorted(
            (self._mapper.to_entity(row) for row in rows if stage is None or row.stage is stage),
            key=lambda profile: profile.stage.position,
        )


class SqlAlchemyCatalogueRepositories(CatalogueRepositories):
    """Every repository of the catalogue, bound to the session of one transaction of the catalogue.

    The catalogue database builds one for each transaction and hands it to the work, so the repositories share the
    session, and with it the transaction, and are dropped with it.

    :ivar library: Library repository bound to the session.
    """

    def __init__(self, session: Session) -> None:
        """Bind every repository of the catalogue to ``session``.

        :param session: Session of the transaction, owned and closed by the database that runs the work.
        :type session: Session
        """
        self.library = SqlAlchemyLibraryRepository(session)


class SqlAlchemyBookRepositories(BookRepositories):
    """Every repository of a book, bound to the session of one transaction of the file of the book.

    The database of the book builds one for each transaction and hands it to the work, so the repositories share the
    session, and with it the transaction, and are dropped with it.

    :ivar book_record: Repository of the record of the book, bound to the session.
    :ivar sources: Source repository bound to the session.
    :ivar scans: Scan repository bound to the session.
    :ivar pages: Page repository bound to the session.
    :ivar pagination_sections: Pagination section repository bound to the session.
    :ivar groups: The groups of the pages of the book on the session, which the work prunes after a change.
    :ivar page_versions: Page version repository bound to the session.
    :ivar page_stages: Page stage repository bound to the session.
    :ivar page_step_states: Page step state repository bound to the session.
    :ivar page_history: Page history repository bound to the session.
    :ivar step_values: Step values repository bound to the session.
    :ivar recipes: Recipe repository bound to the session.
    :ivar recipe_profiles: Recipe profile repository bound to the session.
    :ivar book_places: Book place repository bound to the session.
    """

    def __init__(self, session: Session) -> None:
        """Bind every repository of a book to ``session``.

        :param session: Session of the transaction, owned and closed by the database that runs the work.
        :type session: Session
        """
        self.groups = PageGroups(session)
        self.book_record = SqlAlchemyBookRecordRepository(session)
        self.sources = SqlAlchemySourceRepository(session)
        self.scans = SqlAlchemyScanRepository(session)
        self.pages = SqlAlchemyPageRepository(session, self.groups)
        self.pagination_sections = SqlAlchemyPaginationSectionRepository(session)
        self.page_versions = SqlAlchemyPageVersionRepository(session)
        self.page_stages = SqlAlchemyPageStageRepository(session)
        self.page_step_states = SqlAlchemyPageStepStateRepository(session)
        self.page_history = SqlAlchemyPageHistoryRepository(session, self.groups)
        self.step_values = SqlAlchemyStepValuesRepository(session, self.groups)
        self.recipes = SqlAlchemyRecipeRepository(session)
        self.recipe_profiles = SqlAlchemyRecipeProfileRepository(session)
        self.book_places = SqlAlchemyBookPlaceRepository(session)
