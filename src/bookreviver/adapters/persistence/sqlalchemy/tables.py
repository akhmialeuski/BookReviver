"""Tables of the books feature, private to the SQLAlchemy persistence adapter.

The module declares the ``projects``, ``pages`` and ``jobs`` tables in the SQLAlchemy 2.0 declarative style:
``Mapped`` annotations, ``mapped_column`` and ``relationship`` with ``back_populates``. Every table derives from
advanced-alchemy's :class:`~advanced_alchemy.base.DefaultBase`, which is a ``DeclarativeBase`` carrying the metadata
shared with the account tables, the portable ``GUID``, ``DateTimeUTC`` and ``JsonB`` column types for ``UUID``,
``datetime`` and ``dict`` annotations, and the naming convention of keys and constraints.

Rows never leave the adapter. The mappers in :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turn them into
frozen domain entities, so nothing outside this package depends on the shape of a table.

A project owns its pages and jobs. The foreign keys carry ``ON DELETE CASCADE``, so the database removes them with
the project. The relationships use ``passive_deletes=True`` to leave that deletion to the database, and
``lazy="raise"`` because an ``AsyncSession`` cannot load a relationship implicitly on attribute access.
"""

import enum
from datetime import datetime
from typing import Any, Final
from uuid import UUID

from advanced_alchemy.base import DefaultBase
from sqlalchemy import Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bookreviver.domain.enums import ColorMode, JobKind, JobState, Orthography, SourceKind

# Referential action that lets the database remove a project's pages and jobs with it
CASCADE: Final = 'CASCADE'
# ORM cascade of a project to its pages and jobs; deletion itself is left to the database through CASCADE
CHILD_CASCADE: Final = 'all, delete'
# Loading strategy that raises instead of emitting hidden SQL, which an AsyncSession cannot run
NO_IMPLICIT_LOAD: Final = 'raise'


class Relation(enum.StrEnum):
    """Attribute names of the relationships, which ``back_populates`` refers to by name."""

    PAGES = 'pages'
    JOBS = 'jobs'
    PROJECT = 'project'


def enum_by_value[EnumT: enum.Enum](enum_type: type[EnumT]) -> Enum:
    """Return a column type storing members of ``enum_type`` by value in a plain string column.

    SQLAlchemy stores enum members by name unless told otherwise, and a native enum type exists only on some
    databases. A non-native column holding the value reads the same on SQLite and PostgreSQL, and a renamed member
    keeps its stored rows valid as long as its value stays.

    :param enum_type: Domain enum whose members the column stores.
    :type enum_type: type[EnumT]
    :returns: Column type that writes ``member.value`` and reads it back as the member.
    :rtype: Enum
    """
    return Enum(
        enum_type,
        native_enum=False,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


class ProjectRow(DefaultBase):
    """Row of one book: its owner, its bibliographic description and the summary of its source file.

    The description and the source summary are value objects in the domain and flat columns here. Before the first
    import ``source_kind`` and ``source_imported_at`` are null, and the other source columns hold empty values.

    :ivar id: Project identifier, assigned by the domain.
    :ivar owner_id: Account that owns the project.
    :ivar title: Title of the book.
    :ivar authors: Authors as printed on the title page.
    :ivar publisher: Publisher or printing house.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed, which may be approximate.
    :ivar edition: Edition statement.
    :ivar series: Series the book belongs to.
    :ivar volume: Volume within a multi-volume work.
    :ivar language: Language code of the text.
    :ivar orthography: Spelling system of the text, stored by value.
    :ivar notes: Free-form notes of the owner.
    :ivar source_kind: Kind of the imported source file, or null before the first import.
    :ivar source_name: File name of the imported source.
    :ivar source_size_bytes: Size of the imported source in bytes.
    :ivar source_metadata: Metadata read from the source file, as JSON.
    :ivar source_imported_at: Time the source was imported, or null before the first import.
    :ivar created_at: Time the project was created.
    :ivar updated_at: Time of the last change, set by the domain through its ``Clock``.
    :ivar pages: Pages of the project, never loaded implicitly.
    :ivar jobs: Background jobs of the project, never loaded implicitly.
    """

    __tablename__ = 'projects'

    id: Mapped[UUID] = mapped_column(primary_key=True)
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

    pages: Mapped[list[PageRow]] = relationship(
        back_populates=Relation.PROJECT, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    jobs: Mapped[list[JobRow]] = relationship(
        back_populates=Relation.PROJECT, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )


class PageRow(DefaultBase):
    """Row of one page of a project, keyed by the project and the position of the page in the book.

    The columns hold the facts read from the source page and the state of the derived image assets.

    :ivar project_id: Project the page belongs to; part of the primary key.
    :ivar index: Zero-based position of the page in the book; part of the primary key.
    :ivar width_px: Width of the page image in pixels.
    :ivar height_px: Height of the page image in pixels.
    :ivar color_mode: Colour mode of the page image, stored by value.
    :ivar dpi_x: Horizontal resolution, or null when the source does not state it.
    :ivar dpi_y: Vertical resolution, or null when the source does not state it.
    :ivar bits_per_component: Bit depth of one colour component, or null when unknown.
    :ivar image_format: Encoding of the page image in the source, such as ``jpeg`` or ``jbig2``.
    :ivar width_mm: Physical width in millimetres, or null when the resolution is unknown.
    :ivar height_mm: Physical height in millimetres, or null when the resolution is unknown.
    :ivar has_text_layer: Whether the source page carries a text layer.
    :ivar source_file: File of the page inside the source, for image sets.
    :ivar extra: Further facts read from the source, as JSON.
    :ivar assets_ready: Whether the full image, thumbnail and tiles have been produced.
    :ivar assets_version: Version of the assets, part of their cache-busting URLs.
    :ivar project: Project owning the page, never loaded implicitly.
    """

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

    project: Mapped[ProjectRow] = relationship(back_populates=Relation.PAGES, lazy=NO_IMPLICIT_LOAD)


class JobRow(DefaultBase):
    """Row of one background job of a project and how far it has come.

    :ivar id: Job identifier, assigned by the domain.
    :ivar project_id: Project the job works on.
    :ivar kind: What the job does, stored by value.
    :ivar state: Where the job is in its lifecycle, stored by value.
    :ivar progress_done: Number of finished units of work.
    :ivar progress_total: Number of units of work in the job.
    :ivar error: Message of the failure, empty unless the job failed.
    :ivar created_at: Time the job was queued.
    :ivar started_at: Time the job started, or null while queued.
    :ivar finished_at: Time the job ended, or null while it runs.
    :ivar project: Project owning the job, never loaded implicitly.
    """

    __tablename__ = 'jobs'

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE), index=True)
    kind: Mapped[JobKind] = mapped_column(enum_by_value(JobKind))
    state: Mapped[JobState] = mapped_column(enum_by_value(JobState))
    progress_done: Mapped[int]
    progress_total: Mapped[int]
    error: Mapped[str]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    project: Mapped[ProjectRow] = relationship(back_populates=Relation.JOBS, lazy=NO_IMPLICIT_LOAD)
