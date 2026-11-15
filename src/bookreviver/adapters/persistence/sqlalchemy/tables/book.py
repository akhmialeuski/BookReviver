"""The tables of the one record of a book and of the one place the reader left it at."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bookreviver.adapters.persistence.sqlalchemy.columns import enum_by_value
from bookreviver.adapters.persistence.sqlalchemy.tables.base import (
    BOOK_PLACE_ID,
    BOOK_RECORD_ID,
    BOOK_RECORDS_TABLE,
    EMPTY_LIST,
    EMPTY_TEXT,
    FIRST_VERSION,
    BookTable,
)
from bookreviver.domain.books import Orthography, RightsStatus, Script
from bookreviver.domain.images import ImagePolicy
from bookreviver.domain.pages import PageFilter
from bookreviver.domain.places import CompareMode, PlaceMode, ViewMode
from bookreviver.domain.stages import Stage


class BookRecordRow(BookTable):
    """Row of the book of the file, the only row of its table: its description and the settings of the work on it.

    The description is a value object in the domain and columns here: a column per text field, and a JSON column per
    list, because the items of a list have no identity, are always read and written with the record of the book, and the
    list of books shows only the title and the first author. The sources of the book are rows of their own table.

    :ivar id: Key of the one row, which a check holds at ``1``.
    :ivar title: Title of the book.
    :ivar subtitle: Words of the title page that explain the title.
    :ivar parallel_titles: Titles in other languages, as a JSON list of strings.
    :ivar original_title: Title of the original work.
    :ivar contributors: People who made the book, as a JSON list of ``{name, role}`` objects in title page order.
    :ivar publisher: Publisher of the book.
    :ivar printer: Printing house.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed, which may be approximate.
    :ivar edition: Edition statement.
    :ivar censorship: Censor's permit printed in the book.
    :ivar series: Series the book belongs to.
    :ivar series_number: Number of the book in its series.
    :ivar volume: Volume within a multi-volume work.
    :ivar languages: ISO 639-3 codes of the languages of the text, as a JSON list of strings.
    :ivar orthography: Spelling system of the text, stored by value.
    :ivar script: Writing system of the text, stored by value.
    :ivar printed_pagination: Pagination as a catalogue states it.
    :ivar height_cm: Height of the book in centimetres, or null.
    :ivar illustrations: Illustrations as a catalogue states them.
    :ivar binding: Binding or cover of the copy.
    :ivar identifiers: Numbers and addresses that identify the book, as a JSON list of ``{scheme, value}`` objects.
    :ivar subjects: Topics of the book, as a JSON list of strings.
    :ivar rights: Publishing rights, stored by value.
    :ivar copy_holder: Owner of the scanned copy.
    :ivar copy_notes: Marks of the scanned copy.
    :ivar notes: Free-form notes of the owner.
    :ivar image_policy: How the images of the book's scans and page versions are stored, stored by value.
    :ivar created_at: Time the book was created.
    :ivar updated_at: Time of the last change, set by the domain.
    :ivar version: Number of the changes of the book committed so far, which only a change of the book raises, by one
                   for every change that wrote a part of the book a reader asks for.
    """

    __tablename__ = BOOK_RECORDS_TABLE
    __table_args__ = (CheckConstraint(f'id = {BOOK_RECORD_ID}', name='one_book'),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    title: Mapped[str]
    subtitle: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    parallel_titles: Mapped[list[str]] = mapped_column(JSON, server_default=EMPTY_LIST)
    original_title: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    contributors: Mapped[list[dict[str, str]]] = mapped_column(JSON, server_default=EMPTY_LIST)
    publisher: Mapped[str]
    printer: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    publication_place: Mapped[str]
    publication_year: Mapped[str]
    edition: Mapped[str]
    censorship: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    series: Mapped[str]
    series_number: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    volume: Mapped[str]
    languages: Mapped[list[str]] = mapped_column(JSON, server_default=EMPTY_LIST)
    orthography: Mapped[Orthography] = mapped_column(enum_by_value(Orthography))
    script: Mapped[Script] = mapped_column(enum_by_value(Script), server_default=Script.UNKNOWN.value)
    printed_pagination: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    height_cm: Mapped[int | None]
    illustrations: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    binding: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    identifiers: Mapped[list[dict[str, str]]] = mapped_column(JSON, server_default=EMPTY_LIST)
    subjects: Mapped[list[str]] = mapped_column(JSON, server_default=EMPTY_LIST)
    rights: Mapped[RightsStatus] = mapped_column(enum_by_value(RightsStatus), server_default=RightsStatus.UNKNOWN.value)
    copy_holder: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    copy_notes: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    notes: Mapped[str]
    image_policy: Mapped[ImagePolicy] = mapped_column(enum_by_value(ImagePolicy))
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    version: Mapped[int] = mapped_column(server_default=FIRST_VERSION)


class BookPlaceRow(BookTable):
    """Row of the place the reader left the book at, the one row of the table, since the file holds one book.

    The page, scan, file and strip columns hold identifiers without a foreign key: the reader may delete the page the
    place names, and the place then falls back to the nearest view that still exists, so the database keeps the row.

    :ivar id: Key of the one row, which a check holds at ``1``.
    :ivar mode: Whether the reader was working on a stage or reading, stored by value.
    :ivar stage: Stage the reader was on, stored by value.
    :ivar page_id: Page that was open, or null.
    :ivar scan_id: Scan that was open, or null.
    :ivar source_id: File that was chosen, or null.
    :ivar view: How the canvas laid the pages out, stored by value.
    :ivar compare: How the stage was compared with the one before it, stored by value.
    :ivar filter: Which pages the strip or the grid listed, stored by value.
    :ivar canvas_zoom: Zoom of the canvas as a multiple of the fitted view, or null for the fitted view.
    :ivar canvas_centre_x: Horizontal coordinate of the centre of the canvas in page heights, or null.
    :ivar canvas_centre_y: Vertical coordinate of the centre of the canvas in page heights, or null.
    :ivar strip_page_id: First page in sight in the strip or the grid, or null.
    :ivar updated_at: Time the place was last written.
    """

    __tablename__ = 'book_places'
    __table_args__ = (CheckConstraint(f'id = {BOOK_PLACE_ID}', name='one_place'),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    mode: Mapped[PlaceMode] = mapped_column(enum_by_value(PlaceMode))
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    page_id: Mapped[UUID | None]
    scan_id: Mapped[UUID | None]
    source_id: Mapped[UUID | None]
    view: Mapped[ViewMode] = mapped_column(enum_by_value(ViewMode))
    compare: Mapped[CompareMode] = mapped_column(enum_by_value(CompareMode))
    filter: Mapped[PageFilter] = mapped_column(enum_by_value(PageFilter))
    canvas_zoom: Mapped[float | None]
    canvas_centre_x: Mapped[float | None]
    canvas_centre_y: Mapped[float | None]
    strip_page_id: Mapped[UUID | None]
    updated_at: Mapped[datetime]
