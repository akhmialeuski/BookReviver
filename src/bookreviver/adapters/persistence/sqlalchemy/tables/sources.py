"""The tables of the sources of a book and of the scans each source holds."""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKey, Index, UniqueConstraint, func, select
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from bookreviver.adapters.persistence.sqlalchemy.columns import CASCADE, enum_by_value
from bookreviver.adapters.persistence.sqlalchemy.tables.base import (
    CHILD_CASCADE,
    EMPTY_TEXT,
    IMPORTED_SOURCE,
    LEGACY_FULL_FORMAT,
    METADATA_COLUMN,
    NO_IMPLICIT_LOAD,
    BookTable,
    Relation,
)
from bookreviver.domain.images import ColorMode, Rendition
from bookreviver.domain.sources import FileType, RejectionReason, SourceKind, SourceStatus


class SourceRow(BookTable):
    """Row of one uploaded file of a book: an imported source, or a file the import rejected or skipped.

    The stored files and the description values the source suggests are value objects in the domain and JSON here,
    since no query looks inside them. An imported source is unique within the book by the digest of its main file,
    which a partial unique index keeps, so a rejected or skipped file may repeat a digest, as a duplicate does.

    :ivar id: Source identifier, assigned by the domain.
    :ivar kind: Kind of the source, stored by value, or null for a rejected or skipped file of an unknown type.
    :ivar file_type: Exact format of the main file, stored by value, or null for a file of an unknown type.
    :ivar file_name: Name the browser sent for the main file.
    :ivar files: Name, size and digest of every staged file, the main file first, as JSON.
    :ivar size_bytes: Total size of the files in bytes.
    :ivar sha256: SHA-256 digest of the main file.
    :ivar scan_count: Number of scans the source holds, counted when the row is read and never stored.
    :ivar metadata_: Technical metadata of the format, as JSON in the ``metadata`` column.
    :ivar suggestion: Description values found in the file, as JSON.
    :ivar status: Whether the file was imported, rejected or skipped, stored by value.
    :ivar rejection: Why a rejected file was refused, stored by value, or null.
    :ivar detail: Text for the reader that says more than the reason, or empty.
    :ivar imported_at: Time the file was recorded, which for an imported source is when it became part of the book.
    :ivar scans: Scans of the source, never loaded implicitly.
    """

    __tablename__ = 'sources'
    __table_args__ = (Index('ix_sources_one_imported_per_digest', 'sha256', unique=True, sqlite_where=IMPORTED_SOURCE),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    kind: Mapped[SourceKind | None] = mapped_column(enum_by_value(SourceKind))
    file_type: Mapped[FileType | None] = mapped_column(enum_by_value(FileType))
    file_name: Mapped[str]
    files: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    size_bytes: Mapped[int]
    sha256: Mapped[str]
    # Mapped after ScanRow below, as a count of its rows, so the type checkers read it from here
    if TYPE_CHECKING:
        scan_count: Mapped[int]
    metadata_: Mapped[dict[str, Any]] = mapped_column(METADATA_COLUMN, JSON)
    suggestion: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[SourceStatus] = mapped_column(
        enum_by_value(SourceStatus), server_default=SourceStatus.IMPORTED.value
    )
    rejection: Mapped[RejectionReason | None] = mapped_column(enum_by_value(RejectionReason))
    detail: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    imported_at: Mapped[datetime]

    scans: Mapped[list[ScanRow]] = relationship(
        back_populates=Relation.SOURCE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )


class ScanRow(BookTable):
    """Row of one scan of a source, unique within the source by its number.

    The record of the book is stored beside the source, so the scans of a book are counted and listed without a join
    and removed with the book by their own foreign key.

    :ivar id: Scan identifier, assigned by the domain.
    :ivar source_id: Source holding the scan.
    :ivar number: Zero-based position of the scan in its source.
    :ivar source_label: Page label the file gives the scan, or empty.
    :ivar width_px: Width of the image in pixels.
    :ivar height_px: Height of the image in pixels.
    :ivar color_mode: Colour mode of the image, stored by value.
    :ivar dpi_x: Horizontal resolution, or null when the source does not state it.
    :ivar dpi_y: Vertical resolution, or null when the source does not state it.
    :ivar bits_per_component: Bit depth of one colour component, or null when unknown.
    :ivar image_format: Human name of the image format in the source.
    :ivar width_mm: Physical width in millimetres, or null when the resolution is unknown.
    :ivar height_mm: Physical height in millimetres, or null when the resolution is unknown.
    :ivar has_text_layer: Whether the scan carries a text layer.
    :ivar extra: Further facts read from the source, as JSON.
    :ivar renditions_ready: Whether every derived file of the current version is published.
    :ivar renditions_version: Version of the derived files, part of their storage keys.
    :ivar renditions_full: Format of the ``full`` image, stored by value; a scan stored before the format was recorded
                           reads as JPEG.
    :ivar source: Source holding the scan, never loaded implicitly.
    """

    __tablename__ = 'scans'
    __table_args__ = (UniqueConstraint('source_id', 'number'),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    source_id: Mapped[UUID] = mapped_column(ForeignKey(SourceRow.id, ondelete=CASCADE))
    number: Mapped[int]
    source_label: Mapped[str]
    width_px: Mapped[int]
    height_px: Mapped[int]
    color_mode: Mapped[ColorMode] = mapped_column(enum_by_value(ColorMode))
    dpi_x: Mapped[float | None]
    dpi_y: Mapped[float | None]
    bits_per_component: Mapped[int | None]
    image_format: Mapped[str]
    width_mm: Mapped[float | None]
    height_mm: Mapped[float | None]
    has_text_layer: Mapped[bool]
    extra: Mapped[dict[str, Any]] = mapped_column(JSON)
    renditions_ready: Mapped[bool]
    renditions_version: Mapped[int]
    renditions_full: Mapped[Rendition] = mapped_column(enum_by_value(Rendition), server_default=LEGACY_FULL_FORMAT)

    source: Mapped[SourceRow] = relationship(back_populates=Relation.SCANS, lazy=NO_IMPLICIT_LOAD)


# The number of scans of a source is read with the source, as SQLAlchemy maps an SQL expression to an attribute
SourceRow.scan_count = column_property(
    select(func.count(ScanRow.id)).where(ScanRow.source_id == SourceRow.id).correlate_except(ScanRow).scalar_subquery()
)
