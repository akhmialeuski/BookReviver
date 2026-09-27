"""Tables of the books feature, private to the adapter and registered on advanced-alchemy's shared metadata."""

import enum
from datetime import datetime
from typing import Any
from uuid import UUID

from advanced_alchemy.base import DefaultBase, UUIDBase
from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from bookreviver.domain.enums import ColorMode, JobKind, JobState, Orthography, SourceKind

# Referential action that lets the database remove a project's pages and jobs with it
CASCADE: str = 'CASCADE'


def enum_by_value[EnumT: enum.Enum](enum_type: type[EnumT]) -> Enum:
    """Return a column type storing members of ``enum_type`` by value in a plain string column.

    SQLAlchemy stores enum members by name unless told otherwise, and a native enum type exists only on some
    databases, so a string column holding the value reads the same on SQLite and PostgreSQL.
    """
    return Enum(
        enum_type,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class ProjectRow(UUIDBase):
    """A book with its description and the summary of its source.

    Before the first import the source kind and import time are null, and the other source columns hold empty values.
    """

    __tablename__ = 'projects'

    owner_id: Mapped[UUID] = mapped_column(index=True)
    title: Mapped[str]
    authors: Mapped[str]
    publisher: Mapped[str]
    publication_place: Mapped[str]
    publication_year: Mapped[str]
    edition: Mapped[str]
    series: Mapped[str]
    volume: Mapped[str]
    language: Mapped[str]
    orthography: Mapped[Orthography] = mapped_column(enum_by_value(Orthography))
    notes: Mapped[str]
    source_kind: Mapped[SourceKind | None] = mapped_column(enum_by_value(SourceKind))
    source_name: Mapped[str]
    source_size_bytes: Mapped[int]
    source_metadata: Mapped[dict[str, Any]]
    source_imported_at: Mapped[datetime | None]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(index=True)


class PageRow(DefaultBase):
    """One page of a project, keyed by the project and its position in the book."""

    __tablename__ = 'pages'

    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE), primary_key=True)
    index: Mapped[int] = mapped_column(primary_key=True)
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
    source_file: Mapped[str]
    extra: Mapped[dict[str, Any]]
    assets_ready: Mapped[bool]
    assets_version: Mapped[int]


class JobRow(UUIDBase):
    """A background job of a project and how far it has come."""

    __tablename__ = 'jobs'

    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE), index=True)
    kind: Mapped[JobKind] = mapped_column(enum_by_value(JobKind))
    state: Mapped[JobState] = mapped_column(enum_by_value(JobState))
    progress_done: Mapped[int]
    progress_total: Mapped[int]
    error: Mapped[str]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]
