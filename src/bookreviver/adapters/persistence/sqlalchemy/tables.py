"""Tables of the books feature, private to the SQLAlchemy persistence adapter.

The module declares the ``projects``, ``jobs``, ``sources``, ``scans``, ``pages``, ``page_versions``, ``page_stages``,
``page_edits``, ``book_places`` and ``recipes`` tables in the SQLAlchemy 2.0 declarative style: ``Mapped`` annotations,
``mapped_column`` and ``relationship`` with ``back_populates``. Every table derives from
advanced-alchemy's :class:`~advanced_alchemy.base.DefaultBase`, which is a ``DeclarativeBase`` carrying the metadata
shared with the account tables, the portable ``GUID``, ``DateTimeUTC`` and ``JsonB`` column types for ``UUID``,
``datetime`` and ``dict`` annotations, and the naming convention of keys and constraints.

Rows never leave the adapter. The mappers in :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turn them into
frozen domain entities, so nothing outside this package depends on the shape of a table.

A project owns its sources, scans, pages, recipes and jobs, a source owns its scans, and a page owns its versions,
its stage records and its edits. The foreign keys carry ``ON DELETE CASCADE``, so the database removes them with their
owner. An optional reference carries ``ON DELETE SET NULL`` instead: a page keeps its row when its scan is deleted, a
source when its import job is, a version when its input version is, and a stage record when its head version or its
recipe is, because a page of the book holds its own copy of its image. The relationships use
``passive_deletes=True`` to leave that deletion to the database, and ``lazy="raise"`` because an ``AsyncSession``
cannot load a relationship implicitly on attribute access. Unique keys are declared with their table, and the shared
naming convention names them.

The owner of a project refers to the ``user`` table of fastapi-users with ``ON DELETE RESTRICT``. A cascade would
remove the rows of the owner's projects but not their files, so an account is deleted only after its projects have
been deleted through ``ProjectService``, which removes their files too.
"""

import enum
from datetime import datetime
from types import MappingProxyType
from typing import Any, Final
from uuid import UUID

from advanced_alchemy.base import DefaultBase
from advanced_alchemy.types import JsonB
from sqlalchemy import Enum, ForeignKey, Index, String, UniqueConstraint, false, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.domain.enums import (
    ColorMode,
    CompareMode,
    EditorKind,
    FileType,
    ImagePolicy,
    JobKind,
    JobState,
    Orthography,
    PageFilter,
    PageKind,
    PageOrigin,
    PlaceMode,
    Rendition,
    ReviewReason,
    RightsStatus,
    Script,
    SourceKind,
    Stage,
    StageState,
    VersionScale,
    VersionState,
    ViewMode,
)

# Format of the ``full`` image of the scans stored before the format was recorded, which were all JPEG
LEGACY_FULL_FORMAT: Final = Rendition.FULL_JPEG.value
# Referential action that lets the database remove the rows of a project, a source or a page with it
CASCADE: Final = 'CASCADE'
# Referential action that keeps a row whose optional parent is removed, emptying the reference
SET_NULL: Final = 'SET NULL'
# Referential action that refuses to remove a parent while rows still refer to it
RESTRICT: Final = 'RESTRICT'
# Name of the column holding a source's metadata, which the declarative base reserves as an attribute name
METADATA_COLUMN: Final = 'metadata'
# ORM cascade of a parent to its children; deletion itself is left to the database through CASCADE
CHILD_CASCADE: Final = 'all, delete'
# Loading strategy that raises instead of emitting hidden SQL, which an AsyncSession cannot run
NO_IMPLICIT_LOAD: Final = 'raise'
# Server defaults of the columns the description gained, which existing rows take when the columns are added
EMPTY_TEXT: Final = ''
EMPTY_LIST: Final = '[]'
FIRST_REVISION_NUMBER: Final = 0
FIRST_REVISION: Final = str(FIRST_REVISION_NUMBER)
PAGES_TABLE: Final = 'pages'
PAGE_VERSIONS_TABLE: Final = 'page_versions'
RECIPES_TABLE: Final = 'recipes'
# Length of a page version identifier, a hash cut to 16 hexadecimal digits
VERSION_ID_LENGTH: Final = 16
POSTGRESQL_DIALECT: Final = 'postgresql'
# Order keys compare byte by byte: SQLite's default BINARY collation does, and PostgreSQL needs the C collation
ORDER_KEY_TYPE: Final = String().with_variant(String(collation='C'), POSTGRESQL_DIALECT)
# Server default of a column that holds a JSON object
EMPTY_OBJECT: Final = '{}'
# The rows of the partial unique index of ``recipes``: the active recipe of a stage
ACTIVE_RECIPE: Final = text('active')
# The rows of the partial unique index of ``jobs``: the imports that are queued or running
ACTIVE_IMPORT: Final = text(
    f"kind = '{JobKind.IMPORT_SOURCE}' AND state IN ('{JobState.QUEUED}', '{JobState.RUNNING}')"
)
# The rows of the partial unique index that keeps a project to one job writing page images: those queued or running
ACTIVE_PREPARE: Final = text(
    f"kind = '{JobKind.PREPARE_PAGES}' AND state IN ('{JobState.QUEUED}', '{JobState.RUNNING}')"
)
# The rows of the partial unique index that keeps a project to one job processing the versions of its pages
ACTIVE_PROCESSING: Final = text(
    'kind IN ('
    + ', '.join(f"'{kind}'" for kind in sorted(JobKind.processing()))
    + f") AND state IN ('{JobState.QUEUED}', '{JobState.RUNNING}')"
)


class Relation(enum.StrEnum):
    """Attribute names of the relationships, which ``back_populates`` refers to by name."""

    SOURCES = 'sources'
    SCANS = 'scans'
    PAGES = 'pages'
    VERSIONS = 'versions'
    JOBS = 'jobs'
    RECIPES = 'recipes'
    STAGES = 'stages'
    EDITS = 'edits'
    PROJECT = 'project'
    SOURCE = 'source'
    PAGE = 'page'


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


def next_revision(current: int | None) -> int:
    """Return the version a row takes when it is written, which is zero for a row that is inserted.

    SQLAlchemy's own generator starts at one. Zero is what the ``server_default`` gives the rows that were stored
    before the counter existed, and what the in-memory adapter and the domain's ``Page`` start from.

    :param current: Version the row was read at, or None for a row about to be inserted.
    :type current: int | None
    :returns: The version of the row after this write.
    :rtype: int
    """
    return FIRST_REVISION_NUMBER if current is None else current + 1


class ProjectRow(DefaultBase):
    """Row of one book: its owner, its bibliographic description and the settings of the work on it.

    The description is a value object in the domain and columns here: a column per text field, and a JSON column per
    list, because the items of a list have no identity, are always read and written with their project, and the
    project list shows only the title and the first author. The sources of the book are rows of their own table.

    :ivar id: Project identifier, assigned by the domain.
    :ivar owner_id: Account that owns the project, which the database keeps while the project exists.
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
    :ivar image_policy: How the images of the project's scans and page versions are stored, stored by value.
    :ivar cover_page_id: Page whose thumbnail the project list shows, or null for the first page.
    :ivar created_at: Time the project was created.
    :ivar updated_at: Time of the last change, set by the domain through its ``Clock``.
    :ivar sources: Sources of the project, never loaded implicitly.
    :ivar pages: Pages of the project, never loaded implicitly.
    :ivar recipes: Recipes of the project, never loaded implicitly.
    :ivar jobs: Background jobs of the project, never loaded implicitly.
    """

    __tablename__ = 'projects'

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # The column of the table, since fastapi-users declares the attribute for type checkers as a plain UUID
    owner_id: Mapped[UUID] = mapped_column(ForeignKey(AccountTable.__table__.c.id, ondelete=RESTRICT), index=True)
    title: Mapped[str]
    subtitle: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    parallel_titles: Mapped[list[str]] = mapped_column(JsonB, server_default=EMPTY_LIST)
    original_title: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    contributors: Mapped[list[dict[str, str]]] = mapped_column(JsonB, server_default=EMPTY_LIST)
    publisher: Mapped[str]
    printer: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    publication_place: Mapped[str]
    publication_year: Mapped[str]
    edition: Mapped[str]
    censorship: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    series: Mapped[str]
    series_number: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    volume: Mapped[str]
    languages: Mapped[list[str]] = mapped_column(JsonB, server_default=EMPTY_LIST)
    orthography: Mapped[Orthography] = mapped_column(enum_by_value(Orthography))
    script: Mapped[Script] = mapped_column(enum_by_value(Script), server_default=Script.UNKNOWN.value)
    printed_pagination: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    height_cm: Mapped[int | None]
    illustrations: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    binding: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    identifiers: Mapped[list[dict[str, str]]] = mapped_column(JsonB, server_default=EMPTY_LIST)
    subjects: Mapped[list[str]] = mapped_column(JsonB, server_default=EMPTY_LIST)
    rights: Mapped[RightsStatus] = mapped_column(enum_by_value(RightsStatus), server_default=RightsStatus.UNKNOWN.value)
    copy_holder: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    copy_notes: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    notes: Mapped[str]
    image_policy: Mapped[ImagePolicy] = mapped_column(enum_by_value(ImagePolicy))
    # By table name, and added after both tables exist, since the pages table refers back to this one
    cover_page_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f'{PAGES_TABLE}.id', ondelete=SET_NULL, use_alter=True)
    )
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime] = mapped_column(index=True)

    sources: Mapped[list[SourceRow]] = relationship(
        back_populates=Relation.PROJECT, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    # The project's own key of its pages, not the cover's key the other way
    pages: Mapped[list[PageRow]] = relationship(
        back_populates=Relation.PROJECT,
        cascade=CHILD_CASCADE,
        passive_deletes=True,
        lazy=NO_IMPLICIT_LOAD,
        foreign_keys=lambda: [PageRow.project_id],
    )
    recipes: Mapped[list[RecipeRow]] = relationship(
        back_populates=Relation.PROJECT, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    jobs: Mapped[list[JobRow]] = relationship(
        back_populates=Relation.PROJECT, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )


class JobRow(DefaultBase):
    """Row of one background job of a project and how far it has come.

    :ivar id: Job identifier, assigned by the domain.
    :ivar project_id: Project the job works on.
    :ivar kind: What the job does, stored by value.
    :ivar state: Where the job is in its lifecycle, stored by value.
    :ivar progress_done: Number of finished units of work.
    :ivar progress_total: Number of units of work in the job.
    :ivar error: Message of the failure, empty unless the job failed.
    :ivar request: The files an import job was asked to import, as JSON, or null for a job that takes none.
    :ivar params: What a processing job was asked to do, as a JSON object, empty for a job without.
    :ivar result: What an import job did with them, as JSON, or null until it has finished.
    :ivar created_at: Time the job was queued.
    :ivar started_at: Time the job started, or null while queued.
    :ivar finished_at: Time the job ended, or null while it runs.
    :ivar project: Project owning the job, never loaded implicitly.
    """

    __tablename__ = 'jobs'
    # A project runs one import at a time, and only the database can keep two uploads that both passed a check
    # before either committed from being both stored. It writes the images of its pages with one job at a time for the
    # same reason: two jobs that both passed the check would write the files of one version at once. It also runs one
    # job at a time that processes the versions of its pages, since a collection deletes the versions a run may be
    # reusing and two runs write the same files
    __table_args__ = (
        Index(
            'ix_jobs_one_active_import',
            'project_id',
            unique=True,
            sqlite_where=ACTIVE_IMPORT,
            postgresql_where=ACTIVE_IMPORT,
        ),
        Index(
            'ix_jobs_one_active_prepare',
            'project_id',
            unique=True,
            sqlite_where=ACTIVE_PREPARE,
            postgresql_where=ACTIVE_PREPARE,
        ),
        Index(
            'ix_jobs_one_active_processing',
            'project_id',
            unique=True,
            sqlite_where=ACTIVE_PROCESSING,
            postgresql_where=ACTIVE_PROCESSING,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE), index=True)
    kind: Mapped[JobKind] = mapped_column(enum_by_value(JobKind))
    state: Mapped[JobState] = mapped_column(enum_by_value(JobState))
    progress_done: Mapped[int]
    progress_total: Mapped[int]
    error: Mapped[str]
    request: Mapped[dict[str, Any] | None]
    params: Mapped[dict[str, Any]] = mapped_column(JsonB, server_default=EMPTY_OBJECT)
    result: Mapped[dict[str, Any] | None]
    created_at: Mapped[datetime]
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    project: Mapped[ProjectRow] = relationship(back_populates=Relation.JOBS, lazy=NO_IMPLICIT_LOAD)


class SourceRow(DefaultBase):
    """Row of one source of a project, unique within the project by the digest of its main file.

    The stored files and the description values the source suggests are value objects in the domain and JSON here,
    since no query looks inside them.

    :ivar id: Source identifier, assigned by the domain.
    :ivar project_id: Project owning the source.
    :ivar kind: Kind of the source, stored by value.
    :ivar file_type: Exact format of the main file, stored by value.
    :ivar file_name: Name the browser sent for the main file.
    :ivar files: Name, size and digest of every stored file, the main file first, as JSON.
    :ivar size_bytes: Total size of the files in bytes.
    :ivar sha256: SHA-256 digest of the main file.
    :ivar scan_count: Number of scans the source holds.
    :ivar metadata_: Technical metadata of the format, as JSON in the ``metadata`` column.
    :ivar suggestion: Description values found in the file, as JSON.
    :ivar import_job_id: Import job that created the source, emptied when that job is deleted.
    :ivar imported_at: Time the source became part of the project.
    :ivar project: Project owning the source, never loaded implicitly.
    :ivar scans: Scans of the source, never loaded implicitly.
    """

    __tablename__ = 'sources'
    __table_args__ = (UniqueConstraint('project_id', 'sha256'),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE), index=True)
    kind: Mapped[SourceKind] = mapped_column(enum_by_value(SourceKind))
    file_type: Mapped[FileType] = mapped_column(enum_by_value(FileType))
    file_name: Mapped[str]
    files: Mapped[list[dict[str, Any]]] = mapped_column(JsonB)
    size_bytes: Mapped[int]
    sha256: Mapped[str]
    scan_count: Mapped[int]
    metadata_: Mapped[dict[str, Any]] = mapped_column(METADATA_COLUMN)
    suggestion: Mapped[dict[str, Any]]
    import_job_id: Mapped[UUID | None] = mapped_column(ForeignKey(JobRow.id, ondelete=SET_NULL), index=True)
    imported_at: Mapped[datetime]

    project: Mapped[ProjectRow] = relationship(back_populates=Relation.SOURCES, lazy=NO_IMPLICIT_LOAD)
    scans: Mapped[list[ScanRow]] = relationship(
        back_populates=Relation.SOURCE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )


class ScanRow(DefaultBase):
    """Row of one scan of a source, unique within the source by its number.

    The project is stored beside the source, so the scans of a book are counted and listed without a join and removed
    with the project by their own foreign key.

    :ivar id: Scan identifier, assigned by the domain.
    :ivar project_id: Project owning the scan's source.
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
    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE), index=True)
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
    extra: Mapped[dict[str, Any]]
    renditions_ready: Mapped[bool]
    renditions_version: Mapped[int]
    renditions_full: Mapped[Rendition] = mapped_column(enum_by_value(Rendition), server_default=LEGACY_FULL_FORMAT)

    source: Mapped[SourceRow] = relationship(back_populates=Relation.SCANS, lazy=NO_IMPLICIT_LOAD)


class PageRow(DefaultBase):
    """Row of one page of the book, unique within its project by order key and within its scan by slot.

    The page keeps its row when its scan is deleted, which empties ``scan_id``. A null never matches in a unique key,
    so any number of pages without a scan share the null pair of scan and slot.

    :ivar id: Page identifier, assigned by the domain.
    :ivar project_id: Project owning the page.
    :ivar order_key: Fractional index string, compared byte by byte, whose order is the order of the book.
    :ivar label: Printed number of the page, or empty.
    :ivar kind: Role of the page in the book, stored by value.
    :ivar origin: Where the image of the page comes from, stored by value.
    :ivar scan_id: Scan the page was cut from, or null.
    :ivar slot: Part of the scan the page shows.
    :ivar included: Whether the page is part of the book.
    :ivar notes: Notes of the user.
    :ivar created_at: Time the page was created.
    :ivar updated_at: Time the page was last changed.
    :ivar revision: Version counter, which SQLAlchemy raises by one on every update and checks in the ``WHERE`` of the
                    update, so a write over a row another transaction changed after it was read updates no row.
    :ivar project: Project owning the page, never loaded implicitly.
    :ivar versions: Versions of the page, never loaded implicitly.
    :ivar stages: Stage records of the page, never loaded implicitly.
    :ivar edits: Manual edits of the page, never loaded implicitly.
    """

    __tablename__ = PAGES_TABLE
    __table_args__ = (UniqueConstraint('project_id', 'order_key'), UniqueConstraint('scan_id', 'slot'))

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE))
    order_key: Mapped[str] = mapped_column(ORDER_KEY_TYPE)
    label: Mapped[str]
    kind: Mapped[PageKind] = mapped_column(enum_by_value(PageKind))
    origin: Mapped[PageOrigin] = mapped_column(enum_by_value(PageOrigin))
    scan_id: Mapped[UUID | None] = mapped_column(ForeignKey(ScanRow.id, ondelete=SET_NULL))
    slot: Mapped[int]
    included: Mapped[bool]
    notes: Mapped[str]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    revision: Mapped[int] = mapped_column(server_default=FIRST_REVISION)

    __mapper_args__ = MappingProxyType({'version_id_col': revision, 'version_id_generator': next_revision})

    project: Mapped[ProjectRow] = relationship(
        back_populates=Relation.PAGES, lazy=NO_IMPLICIT_LOAD, foreign_keys=lambda: [PageRow.project_id]
    )
    versions: Mapped[list[PageVersionRow]] = relationship(
        back_populates=Relation.PAGE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    stages: Mapped[list[PageStageRow]] = relationship(
        back_populates=Relation.PAGE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    edits: Mapped[list[PageEditRow]] = relationship(
        back_populates=Relation.PAGE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )


class PageVersionRow(DefaultBase):
    """Row of one version of a page, identified by the hash of what produced it.

    The processor is flattened into its key and version, and the parameters, the transform and the data of the step
    are JSON. ``renditions_ready`` is null for a step without an image. A version keeps its row when its input version
    is deleted, which empties ``input_id``.

    :ivar id: Version identifier, 16 hexadecimal digits of a hash.
    :ivar page_id: Page owning the version.
    :ivar stage: Stage of the step, stored by value.
    :ivar processor_key: Key of the processor that ran the step.
    :ivar processor_version: Version of that processor.
    :ivar input_id: Version the step read, or null for a base version.
    :ivar params: Parameters of the step, as JSON.
    :ivar transform: Transform of coordinates from the input, as JSON.
    :ivar data: Data the step found, as JSON.
    :ivar review: Why the step asks for a second look at the page, stored by value, or null when it was sure.
    :ivar renditions_ready: Whether the version's image files are published, or null for a step without an image.
    :ivar renditions_full: Format of the version's ``full`` image, stored by value, or null for a step without an image.
    :ivar state: Where the version is in its lifecycle, stored by value.
    :ivar scale: Whether the step ran on the full image or on the preview, stored by value.
    :ivar edit_hash: Hash of the manual edit the step read, or empty.
    :ivar tiles_ready: Whether the IIIF pyramid of the version is cut.
    :ivar created_at: Time the version was created.
    :ivar page: Page owning the version, never loaded implicitly.
    """

    __tablename__ = PAGE_VERSIONS_TABLE
    __table_args__ = (Index(None, 'page_id', 'stage'),)

    id: Mapped[str] = mapped_column(String(VERSION_ID_LENGTH), primary_key=True)
    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE))
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    processor_key: Mapped[str]
    processor_version: Mapped[str]
    # A string, since the table refers to itself before its class exists
    input_id: Mapped[str | None] = mapped_column(ForeignKey(f'{PAGE_VERSIONS_TABLE}.id', ondelete=SET_NULL))
    params: Mapped[dict[str, Any]]
    transform: Mapped[dict[str, Any]]
    data: Mapped[dict[str, Any]]
    review: Mapped[ReviewReason | None] = mapped_column(enum_by_value(ReviewReason))
    renditions_ready: Mapped[bool | None]
    renditions_full: Mapped[Rendition | None] = mapped_column(enum_by_value(Rendition))
    state: Mapped[VersionState] = mapped_column(enum_by_value(VersionState))
    scale: Mapped[VersionScale] = mapped_column(enum_by_value(VersionScale), server_default=VersionScale.FULL.value)
    edit_hash: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    tiles_ready: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime]

    page: Mapped[PageRow] = relationship(back_populates=Relation.VERSIONS, lazy=NO_IMPLICIT_LOAD)


class PageStageRow(DefaultBase):
    """Row of the current version of one stage of one page, keyed by the page and the stage.

    The head version and the recipe are optional references that the database empties when the version or the recipe
    is deleted, so a record outlives both.

    :ivar page_id: Page the record belongs to.
    :ivar stage: The stage, stored by value.
    :ivar recipe_id: Recipe the page was processed by, or null.
    :ivar head_version_id: Current version of the stage, or null.
    :ivar state: Whether the current version matches the inputs of the stage, stored by value.
    :ivar updated_at: Time the record last changed.
    :ivar page: Page owning the record, never loaded implicitly.
    """

    __tablename__ = 'page_stages'

    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage), primary_key=True)
    # By table name, since the recipe class is declared after this one
    recipe_id: Mapped[UUID | None] = mapped_column(ForeignKey(f'{RECIPES_TABLE}.id', ondelete=SET_NULL), index=True)
    head_version_id: Mapped[str | None] = mapped_column(ForeignKey(PageVersionRow.id, ondelete=SET_NULL), index=True)
    state: Mapped[StageState] = mapped_column(enum_by_value(StageState))
    updated_at: Mapped[datetime]

    page: Mapped[PageRow] = relationship(back_populates=Relation.STAGES, lazy=NO_IMPLICIT_LOAD)


class PageEditRow(DefaultBase):
    """Row of one manual edit, keyed by the page, the stage and the processor that reads it.

    :ivar page_id: Page the edit belongs to.
    :ivar stage: Stage of the processor, stored by value.
    :ivar processor_key: Key of the processor reading the edit.
    :ivar kind: Editor that made the edit, stored by value.
    :ivar geometry: The shape the user drew, as JSON, or null for an edit that is only a mask.
    :ivar mask_key: Storage key of the painted mask, or null.
    :ivar edit_hash: Hash of the geometry and the mask.
    :ivar updated_at: Time the edit was last saved.
    :ivar page: Page owning the edit, never loaded implicitly.
    """

    __tablename__ = 'page_edits'

    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage), primary_key=True)
    processor_key: Mapped[str] = mapped_column(primary_key=True)
    kind: Mapped[EditorKind] = mapped_column(enum_by_value(EditorKind))
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JsonB)
    mask_key: Mapped[str | None]
    edit_hash: Mapped[str]
    updated_at: Mapped[datetime]

    page: Mapped[PageRow] = relationship(back_populates=Relation.EDITS, lazy=NO_IMPLICIT_LOAD)


class BookPlaceRow(DefaultBase):
    """Row of the place one account left one book at, keyed by the account and the book.

    The page, scan, file and strip columns hold identifiers without a foreign key: the reader may delete the page the
    place names, and the place then falls back to the nearest view that still exists, so the database keeps the row.

    :ivar account_id: Account the place belongs to, whose deletion removes the place.
    :ivar project_id: Book the place is in, whose deletion removes the place.
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

    account_id: Mapped[UUID] = mapped_column(
        ForeignKey(AccountTable.__table__.c.id, ondelete=CASCADE), primary_key=True
    )
    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE), primary_key=True, index=True)
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


class RecipeRow(DefaultBase):
    """Row of one recipe of a stage of a project, of which at most one per stage is active.

    The steps are a JSON list of ``{processor_key, params}`` objects, since no query looks inside them.

    :ivar id: Recipe identifier, assigned by the domain.
    :ivar project_id: Project owning the recipe.
    :ivar stage: Stage the recipe processes, stored by value.
    :ivar name: Name the user sees.
    :ivar steps: The steps in order, as JSON.
    :ivar active: Whether the recipe is the one the stage runs by default.
    :ivar created_at: Time the recipe was created.
    :ivar updated_at: Time the recipe last changed.
    :ivar project: Project owning the recipe, never loaded implicitly.
    """

    __tablename__ = RECIPES_TABLE
    # The database keeps two requests that both switch the active recipe of a stage from both succeeding
    __table_args__ = (
        Index('ix_recipes_project_id_stage', 'project_id', 'stage'),
        Index(
            'ix_recipes_one_active',
            'project_id',
            'stage',
            unique=True,
            sqlite_where=ACTIVE_RECIPE,
            postgresql_where=ACTIVE_RECIPE,
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    project_id: Mapped[UUID] = mapped_column(ForeignKey(ProjectRow.id, ondelete=CASCADE))
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    name: Mapped[str]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JsonB)
    active: Mapped[bool]
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    project: Mapped[ProjectRow] = relationship(back_populates=Relation.RECIPES, lazy=NO_IMPLICIT_LOAD)
