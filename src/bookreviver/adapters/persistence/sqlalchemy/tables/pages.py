"""The tables of the pages of a book and of what a page owns: its versions, stage records, step states and history.

The relationships of a page and its children name each other with ``back_populates``, so they are declared
together, with the groups the pages are put in, the pagination sections that start at a page and the changes the
history of a page names.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String, UniqueConstraint, false
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bookreviver.adapters.persistence.sqlalchemy.columns import CASCADE, SET_NULL, enum_by_value
from bookreviver.adapters.persistence.sqlalchemy.tables.base import (
    CHANGE_ID_COLUMN,
    CHILD_CASCADE,
    EMPTY_OBJECT,
    EMPTY_TEXT,
    GROUP_ID_COLUMN,
    GROUP_LABEL_GIVEN,
    GROUP_OF_A_GROUP,
    ID_COLUMN,
    MANUAL_LABEL_GIVEN,
    NO_IMPLICIT_LOAD,
    PAGE_ID_COLUMN,
    PAGE_VERSIONS_TABLE,
    PAGES_TABLE,
    PAGINATION_SECTIONS_TABLE,
    RECIPES_TABLE,
    STAGE_COLUMN,
    STEP_KEY_OF_STAGE,
    STEP_OF_STAGE,
    STEPS_TABLE,
    VERSION_ID_LENGTH,
    BookTable,
    Relation,
)
from bookreviver.adapters.persistence.sqlalchemy.tables.sources import ScanRow
from bookreviver.domain.geometry import EditorKind
from bookreviver.domain.history import ChangeSource
from bookreviver.domain.images import Rendition
from bookreviver.domain.page_label_rules import LabelStyle
from bookreviver.domain.page_steps import StepLayer
from bookreviver.domain.pages import BlankFill, ContentType, PageKind, PageOrigin
from bookreviver.domain.pagination import NumberDisplay
from bookreviver.domain.stages import ReviewReason, Stage, StageState
from bookreviver.domain.step_values import ValueScope
from bookreviver.domain.versions import ResultMark, VersionScale, VersionState


class PageGroupRow(BookTable):
    """Row of a group the user put pages in, which the pages, the values and the history of a group name by its key.

    A group has no operation of its own: it is made when a page is first given its label, and removed by the change
    once no row names it. The key is an integer of the file, which never leaves the adapter.

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

    A book owns its sections, and so does the page a section starts at, so the database removes a section with
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

    The head version, the recipe and the step the page stopped at are optional references that the database empties
    when the version, the recipe or the step is deleted, so a record outlives them.

    :ivar page_id: Page the record belongs to.
    :ivar stage: The stage, stored by value.
    :ivar recipe_id: Recipe the page was processed by, or null.
    :ivar head_version_id: Current version of the stage, or null.
    :ivar state: Whether the current version matches the inputs of the stage, stored by value.
    :ivar through_step_id: The last step of the recipe the page was run through when that is before the last step that
                           is on, or null for a page run through all of them.
    :ivar updated_at: Time the record last changed.
    :ivar page: Page owning the record, never loaded implicitly.
    """

    __tablename__ = 'page_stages'

    page_id: Mapped[UUID] = mapped_column(ForeignKey(PageRow.id, ondelete=CASCADE), primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage), primary_key=True)
    # By table name, since the recipe class is declared in the module of the recipes
    recipe_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f'{RECIPES_TABLE}.{ID_COLUMN}', ondelete=SET_NULL), index=True
    )
    head_version_id: Mapped[str | None] = mapped_column(ForeignKey(PageVersionRow.id, ondelete=SET_NULL), index=True)
    state: Mapped[StageState] = mapped_column(enum_by_value(StageState))
    # Indexed, since the database looks the records up when a step is deleted
    through_step_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(f'{STEPS_TABLE}.{ID_COLUMN}', ondelete=SET_NULL), index=True
    )
    updated_at: Mapped[datetime]

    page: Mapped[PageRow] = relationship(back_populates=Relation.STAGES, lazy=NO_IMPLICIT_LOAD)


class PageStepStateRow(BookTable):
    """Row of what one page keeps for one step of a recipe, keyed by the page, the stage and the step.

    The step is a row of ``steps`` of the same stage, which a key of the step and the stage names. A step that is
    taken out of every recipe keeps its row, marked removed, so its state stays and finds the step again if it is put
    back. The columns of the manual edit are null while the page has no edit of the step, and the settings are an empty
    object while it changes no field.

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
    __table_args__ = (ForeignKeyConstraint(STEP_OF_STAGE, STEP_KEY_OF_STAGE), Index(None, *STEP_OF_STAGE))

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


class StepChangeRow(BookTable):
    """Row of one change of a layer of a step, which the histories of the pages it reached name and never rewrite.

    A change of the page's own layer reaches that page, and a change of the values of the odd pages, the even pages or
    a group reaches every page that took them, so the change is stored once and ``page_history`` links it to each page.
    The step is named by a key of the step and the stage, as in ``page_step_states``, and a step that is removed from
    its recipe keeps its row, so the change outlives it. The values are the whole layer before and after the change, as
    JSON objects.

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
    :ivar undoes_id: The change this one takes back, with no foreign key, or null.
    :ivar created_at: Time the change was made.
    """

    __tablename__ = 'step_changes'
    __table_args__ = (
        Index(None, 'batch_id'),
        Index(None, 'undoes_id'),
        Index(None, GROUP_ID_COLUMN),
        Index(None, *STEP_OF_STAGE),
        ForeignKeyConstraint(STEP_OF_STAGE, STEP_KEY_OF_STAGE),
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
