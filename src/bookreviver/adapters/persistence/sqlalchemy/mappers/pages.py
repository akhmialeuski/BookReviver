"""Mappers of the pages of a book and of its pagination sections."""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    PageRow,
    PaginationSectionRow,
)
from bookreviver.domain.ids import (
    PageId,
    PaginationSectionId,
    ScanId,
)
from bookreviver.domain.pages import Page, PageKind
from bookreviver.domain.pagination import PaginationSection

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.groups import PageGroups


class PageMapper(RowMapper[Page, PageRow]):
    """Translation of a page of the book, whose fields are page columns one to one but for the group, named by its key.

    :ivar _groups: The groups of the book.
    """

    def __init__(self, groups: PageGroups) -> None:
        """Create the mapper over the groups of the book of the session.

        :param groups: The groups of the book, which translate a label into the key of its row and back.
        :type groups: PageGroups
        """
        self._groups = groups

    @override
    def to_entity(self, row: PageRow) -> Page:
        """Build the page stored in ``row``.

        :param row: Page row loaded from the database.
        :type row: PageRow
        :returns: Page with its order key, manual label, kind, origin and scan.
        :rtype: Page
        """
        return Page(
            id=PageId(row.id),
            order_key=row.order_key,
            manual_label=row.manual_label,
            kind=row.kind,
            content_type=row.content_type,
            content_by_hand=row.content_by_hand,
            origin=row.origin,
            scan_id=None if row.scan_id is None else ScanId(row.scan_id),
            slot=row.slot,
            blank_fill=row.blank_fill,
            included=row.included,
            notes=row.notes,
            group_label=self._groups.label_of(row.group_id),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: Page) -> PageRow:
        """Build the row of ``entity``.

        :param entity: Page to store.
        :type entity: Page
        :returns: Transient page row.
        :rtype: PageRow
        """
        return PageRow(
            id=entity.id,
            order_key=entity.order_key,
            manual_label=entity.manual_label,
            kind=entity.kind,
            content_type=entity.content_type,
            content_by_hand=entity.content_by_hand,
            origin=entity.origin,
            scan_id=entity.scan_id,
            slot=entity.slot,
            blank_fill=entity.blank_fill,
            included=entity.included,
            notes=entity.notes,
            group_id=self._groups.key_of(entity.group_label),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class PaginationSectionMapper(RowMapper[PaginationSection, PaginationSectionRow]):
    """Translation of a pagination section, whose kinds are a JSON list of their values."""

    @override
    def to_entity(self, row: PaginationSectionRow) -> PaginationSection:
        """Build the section stored in ``row``.

        :param row: Section row loaded from the database.
        :type row: PaginationSectionRow
        :returns: The section with its first page, style, display and kinds.
        :rtype: PaginationSection
        """
        return PaginationSection(
            id=PaginationSectionId(row.id),
            first_page_id=PageId(row.first_page_id),
            name=row.name,
            style=row.style,
            start=row.start,
            prefix=row.prefix,
            display=row.display,
            kinds=frozenset(PageKind(kind) for kind in row.kinds),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: PaginationSection) -> PaginationSectionRow:
        """Build the row of ``entity``, whose kinds are written in the order of the page kinds.

        :param entity: Section to store.
        :type entity: PaginationSection
        :returns: Transient section row.
        :rtype: PaginationSectionRow
        """
        return PaginationSectionRow(
            id=entity.id,
            first_page_id=entity.first_page_id,
            name=entity.name,
            style=entity.style,
            start=entity.start,
            prefix=entity.prefix,
            display=entity.display,
            kinds=[kind.value for kind in PageKind if kind in entity.kinds],
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
