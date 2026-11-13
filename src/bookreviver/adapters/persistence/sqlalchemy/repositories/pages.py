"""Repositories of the pages of a book, in the order of the book, and of its pagination sections."""

from collections import Counter
from typing import TYPE_CHECKING, override

from advanced_alchemy.filters import LimitOffset
from sqlalchemy import func, select

from bookreviver.adapters.persistence.sqlalchemy.mappers import (
    PageMapper,
    PaginationSectionMapper,
)
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyRepository
from bookreviver.adapters.persistence.sqlalchemy.rows import (
    PageRows,
    PaginationSectionRows,
    StepChangeRows,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    PageHistoryRow,
    PageRow,
    PaginationSectionRow,
    ScanRow,
)
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import (
    PageId,
    PaginationSectionId,
)
from bookreviver.domain.pages import BookOrder, ContentType, Page, PageOrigin, RecipeKind
from bookreviver.domain.pagination import PaginationSection
from bookreviver.domain.sides import Side
from bookreviver.domain.slices import Slice
from bookreviver.ports.persistence import (
    PageRepository,
    PaginationSectionRepository,
)

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.groups import PageGroups
    from bookreviver.domain.ids import (
        ScanId,
        SourceId,
    )
    from bookreviver.domain.slices import SliceRequest


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
    def order(self) -> BookOrder:
        """Return the order key of every page in the byte order of the keys, read from the unique index of the key.

        :returns: The order of the pages of the book.
        :rtype: BookOrder
        """
        statement = select(PageRow.id, PageRow.order_key).order_by(PageRow.order_key)
        return BookOrder(keys={PageId(page_id): key for page_id, key in self._rows.session.execute(statement)})

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
