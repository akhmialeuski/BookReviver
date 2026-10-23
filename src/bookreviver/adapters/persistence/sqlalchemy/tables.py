"""Tables of a book, private to the SQLAlchemy persistence adapter, which every book keeps in a file of its own.

The module declares the ``projects``, ``sources``, ``scans``, ``pages``, ``page_versions``, ``page_stages``,
``page_step_states``, ``step_changes``, ``page_history``, ``page_groups``, ``step_values``, ``group_values``,
``pagination_sections``, ``book_places`` and ``recipes`` tables in the SQLAlchemy 2.0 declarative style: ``Mapped``
annotations, ``mapped_column`` and ``relationship`` with ``back_populates``. Every table derives from
:class:`BookTable`, which carries the bind key ``book``, so advanced-alchemy gives the tables of a book a ``MetaData``
of their own, apart from the tables of the catalogue in :mod:`bookreviver.adapters.persistence.sqlalchemy.catalogue`,
and the migrations of the two schemas never meet. The base also brings the portable ``GUID`` and ``DateTimeUTC`` column
types for ``UUID`` and ``datetime`` annotations and the naming convention of keys and constraints. The tables are
written for SQLite alone, the one database of the application, so no column or index carries a variant for another
database.

Rows never leave the adapter. The mappers in :mod:`bookreviver.adapters.persistence.sqlalchemy.mappers` turn them into
frozen domain entities, so nothing outside this package depends on the shape of a table.

The file of a book holds one project. A project owns its sources, scans, pages and recipes, a source owns its scans,
and a page owns its versions, its stage records, its step states and its history. The foreign keys carry
``ON DELETE CASCADE``, so the database removes them with their owner. An optional reference carries
``ON DELETE SET NULL`` instead: a page keeps its row when its scan is deleted, a version when its input version is,
and a stage record when its head version or its recipe is, because a page of the book holds its own copy of its
image. The relationships use ``passive_deletes=True`` to leave that deletion to the database, and ``lazy="raise"`` so
that no query runs unseen on attribute access, inside a transaction or after its session has closed. Unique keys are
declared with their table, and the shared naming convention names them.

No table of a book names an account. The owner of the book is a row of the library in the catalogue, and the profile a
recipe was made from is named by its identifier alone, since no key can reach another file.
"""

import enum
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from advanced_alchemy.base import DefaultBase
from sqlalchemy import JSON, CheckConstraint, ForeignKey, Index, String, UniqueConstraint, false, func, select, text
from sqlalchemy.orm import Mapped, column_property, mapped_column, relationship

from bookreviver.adapters.persistence.sqlalchemy.columns import CASCADE, SET_NULL, enum_by_value
from bookreviver.domain.enums import (
    BlankFill,
    ChangeSource,
    ColorMode,
    CompareMode,
    ContentType,
    EditorKind,
    FileType,
    ImagePolicy,
    LabelStyle,
    NumberDisplay,
    Orthography,
    PageFilter,
    PageKind,
    PageOrigin,
    PlaceMode,
    RecipeKind,
    RejectionReason,
    Rendition,
    ResultMark,
    ReviewReason,
    RightsStatus,
    Script,
    SourceKind,
    SourceStatus,
    Stage,
    StageState,
    StepLayer,
    ValueScope,
    VersionScale,
    VersionState,
    ViewMode,
)

# Format of the ``full`` image of the scans stored before the format was recorded, which were all JPEG
LEGACY_FULL_FORMAT: Final = Rendition.FULL_JPEG.value
# The bind key of the tables of a book, under which advanced-alchemy keeps their metadata and the engines of a book
BOOK_BIND_KEY: Final = 'book'
# Name of the column holding a source's metadata, which the declarative base reserves as an attribute name
METADATA_COLUMN: Final = 'metadata'
# ORM cascade of a parent to its children; deletion itself is left to the database through CASCADE
CHILD_CASCADE: Final = 'all, delete'
# Loading strategy that raises instead of emitting hidden SQL on attribute access
NO_IMPLICIT_LOAD: Final = 'raise'
# Server defaults of the columns the description gained, which existing rows take when the columns are added
EMPTY_TEXT: Final = ''
EMPTY_LIST: Final = '[]'
PROJECTS_TABLE: Final = 'projects'
PAGES_TABLE: Final = 'pages'
PAGE_VERSIONS_TABLE: Final = 'page_versions'
PAGINATION_SECTIONS_TABLE: Final = 'pagination_sections'
RECIPES_TABLE: Final = 'recipes'
# Columns that several keys, indexes and constraints name
ID_COLUMN: Final = 'id'
PAGE_ID_COLUMN: Final = 'page_id'
STAGE_COLUMN: Final = 'stage'
SEQUENCE_COLUMN: Final = 'sequence'
# Length of a page version identifier, a hash cut to 16 hexadecimal digits
VERSION_ID_LENGTH: Final = 16
# Server default of a column that holds a JSON object
EMPTY_OBJECT: Final = '{}'
# The rows of the partial unique index of ``sources``: the imported sources, which are the ones that join the book
IMPORTED_SOURCE: Final = text(f"status = '{SourceStatus.IMPORTED}'")
# The version of a book no change has written yet
FIRST_VERSION: Final = text('0')
# The key of the one row of ``book_places``
BOOK_PLACE_ID: Final = 1
# A manual label is a label, so the empty label is never one; null leaves the number to the sections
MANUAL_LABEL_GIVEN: Final = "manual_label <> ''"
# The label of a group names it, so the empty label is never one
GROUP_LABEL_GIVEN: Final = "label <> ''"
# A change of the values of a group names its group, and no other change names one
GROUP_OF_A_GROUP: Final = f"(scope = '{ValueScope.GROUP}') = (group_id IS NOT NULL)"
# The values of the odd and the even pages, which ``step_values`` holds apart from those of a group
PARITY_SCOPE: Final = f"scope IN ('{ValueScope.ODD}', '{ValueScope.EVEN}')"
# Column of the key of a group, which the rows of a group name
GROUP_ID_COLUMN: Final = 'group_id'
# Column of the change a row of the history of a page names
CHANGE_ID_COLUMN: Final = 'change_id'


class Relation(enum.StrEnum):
    """Attribute names of the relationships, which ``back_populates`` refers to by name."""

    SCANS = 'scans'
    VERSIONS = 'versions'
    STAGES = 'stages'
    STEP_STATES = 'step_states'
    HISTORY = 'history'
    SOURCE = 'source'
    PAGE = 'page'


class BookTable(DefaultBase):
    """The base of the tables of a book, whose bind key gives them the metadata of the schema of a book."""

    __abstract__ = True
    __bind_key__ = BOOK_BIND_KEY


class ProjectRow(BookTable):
    """Row of the book of the file, the only row of its table: its description and the settings of the work on it.

    The description is a value object in the domain and columns here: a column per text field, and a JSON column per
    list, because the items of a list have no identity, are always read and written with their project, and the
    project list shows only the title and the first author. The sources of the book are rows of their own table.

    :ivar id: Project identifier, assigned by the domain.
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
    :ivar created_at: Time the project was created.
    :ivar updated_at: Time of the last change, set by the domain through its ``Clock``.
    :ivar version: Number of the changes of the book committed so far, which only the unit of work raises, by one for
                   every change that wrote a part of the book a reader asks for.
    """

    __tablename__ = PROJECTS_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
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


class SourceRow(BookTable):
    """Row of one uploaded file of a project: an imported source, or a file the import rejected or skipped.

    The stored files and the description values the source suggests are value objects in the domain and JSON here,
    since no query looks inside them. An imported source is unique within the project by the digest of its main file,
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
    :ivar imported_at: Time the file was recorded, which for an imported source is when it became part of the project.
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

    The project is stored beside the source, so the scans of a book are counted and listed without a join and removed
    with the project by their own foreign key.

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


class PageGroupRow(BookTable):
    """Row of a group the user put pages in, which the pages, the values and the history of a group name by its key.

    A group has no operation of its own: it is made when a page is first given its label, and removed by the unit of
    work once no row names it. The key is an integer of the file, which never leaves the adapter.

    :ivar id: Key of the group within the file of the book.
    :ivar label: Label of the group, unique and never empty.
    """

    __tablename__ = 'page_groups'
    __table_args__ = (UniqueConstraint('label'), CheckConstraint(GROUP_LABEL_GIVEN, name='label_given'))

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str]


class PageRow(BookTable):
    """Row of one page of the book, unique by order key and within its scan by slot.

    The page keeps its row when its scan is deleted, which empties ``scan_id``. A null never matches in a unique key,
    so any number of pages without a scan share the null pair of scan and slot.

    :ivar id: Page identifier, assigned by the domain.
    :ivar order_key: Fractional index string, compared byte by byte, whose order is the order of the book.
    :ivar manual_label: Printed number written by hand or taken from the scan, or null for the number the pagination
                        sections give, which is computed on read and never stored. An empty label is never stored.
    :ivar kind: Role of the page in the book, stored by value.
    :ivar content_type: What the image of the page shows, stored by value, or null while it is not detected.
    :ivar content_by_hand: Whether the user set the content type, which the detection never changes.
    :ivar origin: Where the image of the page comes from, stored by value.
    :ivar scan_id: Scan the page was cut from, or null.
    :ivar slot: Part of the scan the page shows.
    :ivar blank_fill: What the image of a blank page is, its scan or a leaf, stored by value.
    :ivar included: Whether the page is part of the book.
    :ivar notes: Notes of the user.
    :ivar group_id: Group the user put the page in, or null.
    :ivar created_at: Time the page was created.
    :ivar updated_at: Time the page was last changed.
    :ivar versions: Versions of the page, never loaded implicitly.
    :ivar stages: Stage records of the page, never loaded implicitly.
    :ivar step_states: Settings and manual edits of the steps on the page, never loaded implicitly.
    :ivar history: History of the steps on the page, never loaded implicitly.
    """

    __tablename__ = PAGES_TABLE
    __table_args__ = (
        UniqueConstraint('order_key'),
        UniqueConstraint('scan_id', 'slot'),
        CheckConstraint(MANUAL_LABEL_GIVEN, name='manual_label_given'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    # Order keys compare byte by byte, which SQLite's default BINARY collation does
    order_key: Mapped[str] = mapped_column(String)
    manual_label: Mapped[str | None]
    kind: Mapped[PageKind] = mapped_column(enum_by_value(PageKind))
    content_type: Mapped[ContentType | None] = mapped_column(enum_by_value(ContentType))
    content_by_hand: Mapped[bool] = mapped_column(server_default=false())
    origin: Mapped[PageOrigin] = mapped_column(enum_by_value(PageOrigin))
    scan_id: Mapped[UUID | None] = mapped_column(ForeignKey(ScanRow.id, ondelete=SET_NULL))
    slot: Mapped[int]
    blank_fill: Mapped[BlankFill] = mapped_column(enum_by_value(BlankFill), server_default=BlankFill.SCAN.value)
    included: Mapped[bool]
    notes: Mapped[str]
    group_id: Mapped[int | None] = mapped_column(ForeignKey(PageGroupRow.id), index=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    versions: Mapped[list[PageVersionRow]] = relationship(
        back_populates=Relation.PAGE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    stages: Mapped[list[PageStageRow]] = relationship(
        back_populates=Relation.PAGE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    step_states: Mapped[list[PageStepStateRow]] = relationship(
        back_populates=Relation.PAGE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )
    history: Mapped[list[PageHistoryRow]] = relationship(
        back_populates=Relation.PAGE, cascade=CHILD_CASCADE, passive_deletes=True, lazy=NO_IMPLICIT_LOAD
    )


class PaginationSectionRow(BookTable):
    """Row of one pagination section of a book, which numbers the pages from the page it starts at.

    A project owns its sections, and so does the page a section starts at, so the database removes a section with
    either. The kinds of a series by kind are a JSON list of the values of the kinds, empty for a section of the main
    flow, and no query looks inside it.

    :ivar id: Section identifier, assigned by the domain.
    :ivar first_page_id: Page the section starts at.
    :ivar name: Name the user sees.
    :ivar style: How the numbers are written, stored by value.
    :ivar start: Number of the first counted page of the section.
    :ivar prefix: Text written before every number.
    :ivar display: Whether the pages count and whether their numbers are printed, stored by value.
    :ivar kinds: Values of the kinds of page a series by kind takes, as JSON.
    :ivar created_at: Time the section was made.
    :ivar updated_at: Time the section last changed.
    """

    __tablename__ = PAGINATION_SECTIONS_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
    first_page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE), index=True)
    name: Mapped[str]
    style: Mapped[LabelStyle] = mapped_column(enum_by_value(LabelStyle))
    start: Mapped[int]
    prefix: Mapped[str]
    display: Mapped[NumberDisplay] = mapped_column(enum_by_value(NumberDisplay))
    kinds: Mapped[list[str]] = mapped_column(JSON)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class PageVersionRow(BookTable):
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
    :ivar mark: What the user judged of the result, stored by value, or null while it is not judged.
    :ivar comment: What the user wrote about the result, or empty.
    :ivar page: Page owning the version, never loaded implicitly.
    """

    __tablename__ = PAGE_VERSIONS_TABLE
    __table_args__ = (Index(None, PAGE_ID_COLUMN, STAGE_COLUMN),)

    id: Mapped[str] = mapped_column(String(VERSION_ID_LENGTH), primary_key=True)
    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE))
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    processor_key: Mapped[str]
    processor_version: Mapped[str]
    # A string, since the table refers to itself before its class exists
    # Indexed, since the deletion of a version by the collection looks up the versions that read it
    input_id: Mapped[str | None] = mapped_column(
        ForeignKey(f'{PAGE_VERSIONS_TABLE}.{ID_COLUMN}', ondelete=SET_NULL), index=True
    )
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    transform: Mapped[dict[str, Any]] = mapped_column(JSON)
    data: Mapped[dict[str, Any]] = mapped_column(JSON)
    review: Mapped[ReviewReason | None] = mapped_column(enum_by_value(ReviewReason))
    renditions_ready: Mapped[bool | None]
    renditions_full: Mapped[Rendition | None] = mapped_column(enum_by_value(Rendition))
    state: Mapped[VersionState] = mapped_column(enum_by_value(VersionState))
    scale: Mapped[VersionScale] = mapped_column(enum_by_value(VersionScale), server_default=VersionScale.FULL.value)
    edit_hash: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)
    tiles_ready: Mapped[bool] = mapped_column(server_default=false())
    created_at: Mapped[datetime]
    mark: Mapped[ResultMark | None] = mapped_column(enum_by_value(ResultMark))
    comment: Mapped[str] = mapped_column(server_default=EMPTY_TEXT)

    page: Mapped[PageRow] = relationship(back_populates=Relation.VERSIONS, lazy=NO_IMPLICIT_LOAD)


class PageStageRow(BookTable):
    """Row of the current version of one stage of one page, keyed by the page and the stage.

    The head version and the recipe are optional references that the database empties when the version or the recipe
    is deleted, so a record outlives both.

    :ivar page_id: Page the record belongs to.
    :ivar stage: The stage, stored by value.
    :ivar recipe_id: Recipe the page was processed by, or null.
    :ivar head_version_id: Current version of the stage, or null.
    :ivar state: Whether the current version matches the inputs of the stage, stored by value.
    :ivar through_step: Index in the recipe of the last step the page was run through when that is before the last step
                        that is on, or null for a page run through all of them.
    :ivar updated_at: Time the record last changed.
    :ivar page: Page owning the record, never loaded implicitly.
    """

    __tablename__ = 'page_stages'

    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage), primary_key=True)
    # By table name, since the recipe class is declared after this one
    recipe_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f'{RECIPES_TABLE}.{ID_COLUMN}', ondelete=SET_NULL), index=True
    )
    head_version_id: Mapped[str | None] = mapped_column(ForeignKey(PageVersionRow.id, ondelete=SET_NULL), index=True)
    state: Mapped[StageState] = mapped_column(enum_by_value(StageState))
    through_step: Mapped[int | None]
    updated_at: Mapped[datetime]

    page: Mapped[PageRow] = relationship(back_populates=Relation.STAGES, lazy=NO_IMPLICIT_LOAD)


class PageStepStateRow(BookTable):
    """Row of what one page keeps for one step of a recipe, keyed by the page, the stage and the step.

    The step is named by the identifier its recipe gives it, with no foreign key: the steps are a JSON list in the row
    of a recipe, and a step that is removed leaves its state, which finds the step again if it is put back. The columns
    of the manual edit are null while the page has no edit of the step, and the settings are an empty object while it
    changes no field.

    :ivar page_id: Page the state belongs to.
    :ivar stage: Stage of the step, stored by value.
    :ivar step_id: Identifier of the step.
    :ivar params: The fields of the parameters of the step that this page changes, as a JSON object.
    :ivar kind: Editor that made the edit, stored by value, or null for a page without an edit.
    :ivar geometry: The shape the user drew, as JSON, or null for an edit that is only a mask or for no edit.
    :ivar mask_key: Storage key of the painted mask, or null.
    :ivar edit_hash: Hash of the geometry and the mask, or null for a page without an edit.
    :ivar edit_saved_at: Time the edit was last saved, or null for a page without an edit.
    :ivar updated_at: Time the state was last saved.
    :ivar page: Page owning the state, never loaded implicitly.
    """

    __tablename__ = 'page_step_states'

    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage), primary_key=True)
    step_id: Mapped[UUID] = mapped_column(primary_key=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, server_default=EMPTY_OBJECT)
    kind: Mapped[EditorKind | None] = mapped_column(enum_by_value(EditorKind))
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    mask_key: Mapped[str | None]
    edit_hash: Mapped[str | None]
    edit_saved_at: Mapped[datetime | None]
    updated_at: Mapped[datetime]

    page: Mapped[PageRow] = relationship(back_populates=Relation.STEP_STATES, lazy=NO_IMPLICIT_LOAD)


class StepValuesRow(BookTable):
    """Row of the values of one step for the odd pages or the even pages.

    The step is named by the identifier its recipe gives it, with no foreign key, as in ``page_step_states``. The values
    of a group are rows of ``group_values``, whose key names the group in place of the scope.

    :ivar step_id: Identifier of the step.
    :ivar scope: The odd pages or the even pages, stored by value.
    :ivar stage: Stage of the step, stored by value.
    :ivar params: The fields of the parameters of the step that the pages change, as a JSON object.
    :ivar updated_at: Time the values were last saved.
    """

    __tablename__ = 'step_values'
    __table_args__ = (Index(None, STAGE_COLUMN), CheckConstraint(PARITY_SCOPE, name='parity_scope'))

    step_id: Mapped[UUID] = mapped_column(primary_key=True)
    scope: Mapped[ValueScope] = mapped_column(enum_by_value(ValueScope), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, server_default=EMPTY_OBJECT)
    updated_at: Mapped[datetime]


class GroupValuesRow(BookTable):
    """Row of the values of one step for the pages of one group.

    The step is named by its identifier with no foreign key, as in ``step_values``.

    :ivar step_id: Identifier of the step.
    :ivar group_id: The group the values are for.
    :ivar stage: Stage of the step, stored by value.
    :ivar params: The fields of the parameters of the step that the pages of the group change, as a JSON object.
    :ivar updated_at: Time the values were last saved.
    """

    __tablename__ = 'group_values'
    __table_args__ = (Index(None, STAGE_COLUMN), Index(None, GROUP_ID_COLUMN))

    step_id: Mapped[UUID] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey(PageGroupRow.id), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, server_default=EMPTY_OBJECT)
    updated_at: Mapped[datetime]


class StepChangeRow(BookTable):
    """Row of one change of a layer of a step, which the histories of the pages it reached name and never rewrite.

    A change of the page's own layer reaches that page, and a change of the values of the odd pages, the even pages or
    a group reaches every page that took them, so the change is stored once and ``page_history`` links it to each page.
    The step is named by its identifier with no foreign key, as in ``page_step_states``, since a change outlives a step
    that is removed from its recipe. The values are the whole layer before and after the change, as JSON objects.

    :ivar id: Change identifier, assigned by the domain.
    :ivar stage: Stage of the step, stored by value.
    :ivar step_id: Identifier of the step.
    :ivar layer: The layer that changed, stored by value.
    :ivar scope: Whose settings the layer holds, the page's own or a part of the pages it takes, stored by value.
    :ivar group_id: The group for the scope of a group, or null.
    :ivar before: Content of the layer before the change, or null for an empty layer.
    :ivar after: Content of the layer after the change, or null when it is emptied.
    :ivar source: What made the change, stored by value.
    :ivar batch_id: Identifier shared by the changes of one batch, or null.
    :ivar undoes_id: The change this one takes back, with no foreign key like the step, or null.
    :ivar created_at: Time the change was made.
    """

    __tablename__ = 'step_changes'
    __table_args__ = (
        Index(None, 'batch_id'),
        Index(None, 'undoes_id'),
        Index(None, GROUP_ID_COLUMN),
        CheckConstraint(GROUP_OF_A_GROUP, name='group_of_a_group'),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    step_id: Mapped[UUID]
    layer: Mapped[StepLayer] = mapped_column(enum_by_value(StepLayer))
    scope: Mapped[ValueScope] = mapped_column(enum_by_value(ValueScope))
    group_id: Mapped[int | None] = mapped_column(ForeignKey(PageGroupRow.id))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    source: Mapped[ChangeSource] = mapped_column(enum_by_value(ChangeSource))
    batch_id: Mapped[UUID | None]
    undoes_id: Mapped[UUID | None]
    created_at: Mapped[datetime]


class PageHistoryRow(BookTable):
    """Row of the history of a page: the place of one change in the order of the changes that reached the page.

    :ivar page_id: Page the change reached.
    :ivar sequence: Place of the change in the history of the page, from one, unique within the page.
    :ivar change_id: The change, which reaches the page once.
    :ivar page: Page owning the history, never loaded implicitly.
    :ivar change: The change, loaded by the queries of the history with the row.
    """

    __tablename__ = 'page_history'
    __table_args__ = (UniqueConstraint(PAGE_ID_COLUMN, CHANGE_ID_COLUMN), Index(None, CHANGE_ID_COLUMN))

    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    change_id: Mapped[UUID] = mapped_column(ForeignKey(StepChangeRow.id, ondelete=CASCADE))

    page: Mapped[PageRow] = relationship(back_populates=Relation.HISTORY, lazy=NO_IMPLICIT_LOAD)
    change: Mapped[StepChangeRow] = relationship(lazy=NO_IMPLICIT_LOAD)


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


class RecipeRow(BookTable):
    """Row of one recipe of a stage of a project, of which there is one for each kind of page.

    The steps are a JSON list of ``{processor_key, params, enabled, step_id}`` objects, since no query looks inside
    them.

    :ivar id: Recipe identifier, assigned by the domain.
    :ivar stage: Stage the recipe processes, stored by value.
    :ivar kind: Kind of the pages the recipe processes, stored by value.
    :ivar steps: The steps in order, as JSON.
    :ivar profile_id: Profile of the catalogue the recipe was made from, which the service empties with the profile.
    :ivar created_at: Time the recipe was created.
    :ivar updated_at: Time the recipe last changed.
    """

    __tablename__ = RECIPES_TABLE
    # The database keeps two requests that both make the recipes of a stage from both succeeding
    __table_args__ = (UniqueConstraint(STAGE_COLUMN, 'kind'),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    kind: Mapped[RecipeKind] = mapped_column(enum_by_value(RecipeKind))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    # The profile lives in the catalogue, which no key of this file can reach
    profile_id: Mapped[UUID | None] = mapped_column(index=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
