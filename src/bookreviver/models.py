"""Database schema: a project is one book, and a book is an ordered list of pages."""

import enum
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

TITLE_MAX_LENGTH: int = 500
SHORT_TEXT_MAX_LENGTH: int = 200
PROJECTS_TABLE: str = 'projects'
PAGES_TABLE: str = 'pages'
# Attribute names used by bidirectional relationships
PROJECT_PAGES_ATTR: str = 'pages'
PAGE_PROJECT_ATTR: str = 'project'


def utc_now() -> datetime:
    """Return the current time in UTC."""
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base shared by all tables."""

    type_annotation_map = {dict[str, Any]: JSON}  # ruff: ignore[mutable-class-default]


class SourceKind(enum.StrEnum):
    """What the book was imported from."""

    PDF = 'pdf'
    IMAGES = 'images'


class Orthography(enum.StrEnum):
    """Spelling norm of the printed text."""

    UNKNOWN = 'unknown'
    PRE_REFORM = 'pre_reform'
    MODERN = 'modern'


class ColorMode(enum.StrEnum):
    """Colour depth of a page image as stored in the source."""

    BILEVEL = 'bilevel'
    GRAY = 'gray'
    COLOR = 'color'
    UNKNOWN = 'unknown'


class Project(Base):
    """One book being digitised, with its bibliographic description."""

    __tablename__ = PROJECTS_TABLE

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, onupdate=utc_now)

    # Bibliographic description, filled automatically from the source or by hand
    authors: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH), default='')
    publisher: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH), default='')
    publication_place: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH), default='')
    publication_year: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH), default='')
    edition: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH), default='')
    series: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH), default='')
    volume: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH), default='')
    language: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH), default='')
    orthography: Mapped[Orthography] = mapped_column(Enum(Orthography), default=Orthography.UNKNOWN)
    notes: Mapped[str] = mapped_column(Text, default='')

    # Imported source; empty until a book is uploaded
    source_kind: Mapped[SourceKind | None] = mapped_column(Enum(SourceKind), default=None)
    source_name: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH), default='')
    source_size_bytes: Mapped[int] = mapped_column(default=0)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(default=dict)
    imported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    pages: Mapped[list[Page]] = relationship(
        back_populates=PAGE_PROJECT_ATTR,
        cascade='all, delete-orphan',
        order_by='Page.index',
    )


class Page(Base):
    """One page of the imported source with the technical facts read from it."""

    __tablename__ = PAGES_TABLE
    __table_args__ = (UniqueConstraint('project_id', 'index'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey(f'{PROJECTS_TABLE}.id', ondelete='CASCADE'), index=True)
    index: Mapped[int]
    # File name inside the source directory for an image set, empty for a PDF page
    source_file: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH), default='')
    width_px: Mapped[int]
    height_px: Mapped[int]
    dpi_x: Mapped[float | None] = mapped_column(default=None)
    dpi_y: Mapped[float | None] = mapped_column(default=None)
    color_mode: Mapped[ColorMode] = mapped_column(Enum(ColorMode))
    bits_per_component: Mapped[int | None] = mapped_column(default=None)
    image_format: Mapped[str] = mapped_column(String(SHORT_TEXT_MAX_LENGTH), default='')
    # Physical page size, known for PDF pages and for images with a DPI
    width_mm: Mapped[float | None] = mapped_column(default=None)
    height_mm: Mapped[float | None] = mapped_column(default=None)
    has_text_layer: Mapped[bool] = mapped_column(default=False)
    extra: Mapped[dict[str, Any]] = mapped_column(default=dict)

    project: Mapped[Project] = relationship(back_populates=PROJECT_PAGES_ATTR)
