"""Mappers of the record of a book and of the place the reader left it at, and the JSON of the description."""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    BOOK_PLACE_ID,
    BOOK_RECORD_ID,
    BookPlaceRow,
    BookRecordRow,
)
from bookreviver.domain.books import (
    BookDetails,
    BookIdentifier,
    BookRecord,
    Contributor,
    ContributorRole,
    IdentifierScheme,
)
from bookreviver.domain.ids import (
    PageId,
    ScanId,
    SourceId,
)
from bookreviver.domain.places import BookPlace, CanvasPosition

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def contributors_to_json(contributors: Sequence[Contributor]) -> list[dict[str, str]]:
    """Turn contributors into the JSON list of ``{name, role}`` objects a description and a suggestion store.

    :param contributors: Contributors in title page order.
    :type contributors: Sequence[Contributor]
    :returns: One object per contributor, holding the code of the role and not the member.
    :rtype: list[dict[str, str]]
    """
    return [{'name': person.name, 'role': person.role.value} for person in contributors]


def contributors_from_json(stored: Sequence[Mapping[str, str]]) -> tuple[Contributor, ...]:
    """Build contributors from the JSON list ``contributors_to_json`` wrote.

    :param stored: Stored ``{name, role}`` objects.
    :type stored: Sequence[Mapping[str, str]]
    :returns: The contributors in the stored order.
    :rtype: tuple[Contributor, ...]
    """
    return tuple(Contributor(name=item['name'], role=ContributorRole(item['role'])) for item in stored)


def identifiers_to_json(identifiers: Sequence[BookIdentifier]) -> list[dict[str, str]]:
    """Turn identifiers into the JSON list of ``{scheme, value}`` objects a description and a suggestion store.

    :param identifiers: Identifiers in their order.
    :type identifiers: Sequence[BookIdentifier]
    :returns: One object per identifier, holding the value of the scheme and not the member.
    :rtype: list[dict[str, str]]
    """
    return [{'scheme': item.scheme.value, 'value': item.value} for item in identifiers]


def identifiers_from_json(stored: Sequence[Mapping[str, str]]) -> tuple[BookIdentifier, ...]:
    """Build identifiers from the JSON list ``identifiers_to_json`` wrote.

    :param stored: Stored ``{scheme, value}`` objects.
    :type stored: Sequence[Mapping[str, str]]
    :returns: The identifiers in the stored order.
    :rtype: tuple[BookIdentifier, ...]
    """
    return tuple(BookIdentifier(scheme=IdentifierScheme(item['scheme']), value=item['value']) for item in stored)


class BookRecordMapper(RowMapper[BookRecord, BookRecordRow]):
    """Translation of the record of a book, whose description is flattened into columns."""

    @override
    def to_entity(self, row: BookRecordRow) -> BookRecord:
        """Build the record of the book stored in ``row``.

        :param row: Row of the record of the book, loaded from the database.
        :type row: BookRecordRow
        :returns: BookRecord with its description and settings.
        :rtype: BookRecord
        """
        details = BookDetails(
            title=row.title,
            subtitle=row.subtitle,
            parallel_titles=tuple(row.parallel_titles),
            original_title=row.original_title,
            contributors=contributors_from_json(row.contributors),
            publisher=row.publisher,
            printer=row.printer,
            publication_place=row.publication_place,
            publication_year=row.publication_year,
            edition=row.edition,
            censorship=row.censorship,
            series=row.series,
            series_number=row.series_number,
            volume=row.volume,
            languages=tuple(row.languages),
            orthography=row.orthography,
            script=row.script,
            printed_pagination=row.printed_pagination,
            height_cm=row.height_cm,
            illustrations=row.illustrations,
            binding=row.binding,
            identifiers=identifiers_from_json(row.identifiers),
            subjects=tuple(row.subjects),
            rights=row.rights,
            copy_holder=row.copy_holder,
            copy_notes=row.copy_notes,
            notes=row.notes,
        )
        return BookRecord(
            details=details,
            image_policy=row.image_policy,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: BookRecord) -> BookRecordRow:
        """Build the row of ``entity``.

        :param entity: Record of the book to store.
        :type entity: BookRecord
        :returns: Transient row of the record of the book.
        :rtype: BookRecordRow
        """
        details = entity.details
        return BookRecordRow(
            id=BOOK_RECORD_ID,
            title=details.title,
            subtitle=details.subtitle,
            parallel_titles=list(details.parallel_titles),
            original_title=details.original_title,
            contributors=contributors_to_json(details.contributors),
            publisher=details.publisher,
            printer=details.printer,
            publication_place=details.publication_place,
            publication_year=details.publication_year,
            edition=details.edition,
            censorship=details.censorship,
            series=details.series,
            series_number=details.series_number,
            volume=details.volume,
            languages=list(details.languages),
            orthography=details.orthography,
            script=details.script,
            printed_pagination=details.printed_pagination,
            height_cm=details.height_cm,
            illustrations=details.illustrations,
            binding=details.binding,
            identifiers=identifiers_to_json(details.identifiers),
            subjects=list(details.subjects),
            rights=details.rights,
            copy_holder=details.copy_holder,
            copy_notes=details.copy_notes,
            notes=details.notes,
            image_policy=entity.image_policy,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class BookPlaceMapper(RowMapper[BookPlace, BookPlaceRow]):
    """Translation of the place of a book, whose canvas position is spread over three columns."""

    @override
    def to_entity(self, row: BookPlaceRow) -> BookPlace:
        """Build the place stored in ``row``.

        :param row: Book place row loaded from the database.
        :type row: BookPlaceRow
        :returns: The place with its canvas position, if the row has one.
        :rtype: BookPlace
        """
        zoom, centre_x, centre_y = row.canvas_zoom, row.canvas_centre_x, row.canvas_centre_y
        canvas = (
            None
            if zoom is None or centre_x is None or centre_y is None
            else CanvasPosition(zoom=zoom, centre_x=centre_x, centre_y=centre_y)
        )
        return BookPlace(
            mode=row.mode,
            stage=row.stage,
            page_id=None if row.page_id is None else PageId(row.page_id),
            scan_id=None if row.scan_id is None else ScanId(row.scan_id),
            source_id=None if row.source_id is None else SourceId(row.source_id),
            view=row.view,
            compare=row.compare,
            filter=row.filter,
            canvas=canvas,
            strip_page_id=None if row.strip_page_id is None else PageId(row.strip_page_id),
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: BookPlace) -> BookPlaceRow:
        """Build the row of ``entity``.

        :param entity: Place to store.
        :type entity: BookPlace
        :returns: Transient book place row.
        :rtype: BookPlaceRow
        """
        canvas = entity.canvas
        return BookPlaceRow(
            id=BOOK_PLACE_ID,
            mode=entity.mode,
            stage=entity.stage,
            page_id=entity.page_id,
            scan_id=entity.scan_id,
            source_id=entity.source_id,
            view=entity.view,
            compare=entity.compare,
            filter=entity.filter,
            canvas_zoom=None if canvas is None else canvas.zoom,
            canvas_centre_x=None if canvas is None else canvas.centre_x,
            canvas_centre_y=None if canvas is None else canvas.centre_y,
            strip_page_id=entity.strip_page_id,
            updated_at=entity.updated_at,
        )
