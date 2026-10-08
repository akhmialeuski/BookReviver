"""Entities: domain objects with an identity, frozen and changed through ``attrs.evolve``."""

import hashlib
import json
import re
from typing import TYPE_CHECKING, Any, ClassVar, Self
from uuid import uuid4

from attrs import evolve, field, frozen, validators

from bookreviver.domain.enums import (
    PICTURE_KINDS,
    BlankFill,
    ChangeSource,
    CompareMode,
    ContentSource,
    ContentType,
    EditorKind,
    ImagePolicy,
    JobKind,
    JobState,
    NumberDisplay,
    OrderMode,
    PageFilter,
    PageKind,
    PageOrigin,
    RecipeKind,
    StageState,
    StepLayer,
    ValueScope,
    VersionOrigin,
    VersionScale,
    VersionState,
    ViewMode,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import Transform, geometry_from_data
from bookreviver.domain.ids import PageStepChangeId, PageVersionId, StorageKey
from bookreviver.domain.values import (
    SHA256_PATTERN,
    BookPlaceKey,
    MetadataSuggestion,
    PageStageKey,
    PageStepKey,
    Progress,
    Renditions,
    StageRun,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.enums import (
        FileType,
        LabelStyle,
        PageSide,
        PlaceMode,
        ResultMark,
        ReviewReason,
        SourceKind,
        Stage,
    )
    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.ids import (
        AccountId,
        ChangeBatchId,
        JobId,
        PageId,
        PaginationSectionId,
        ProjectId,
        RecipeId,
        RecipeProfileId,
        ResultMarkChangeId,
        ScanId,
        SourceId,
        StepId,
    )
    from bookreviver.domain.stage_summaries import BookProgress
    from bookreviver.domain.step_values import StepValues
    from bookreviver.domain.values import (
        BookDetails,
        CanvasPosition,
        ImportRequest,
        ImportResult,
        MetadataMap,
        ProcessorRef,
        ScanFacts,
        SourceFile,
        Step,
    )

# The length of a page version identifier: a hash cut to 16 hexadecimal digits
VERSION_ID_LENGTH: int = 16
VERSION_ID_PATTERN: str = rf'[0-9a-f]{{{VERSION_ID_LENGTH}}}'
LAYER_NOT_KEPT: str = 'The layer {layer} of a step is not kept on the page yet.'
# The names of the fields of a manual edit as the history keeps it and of its shape as the hash of the edit reads it
KIND_FIELD: str = 'kind'
GEOMETRY_FIELD: str = 'geometry'
MASK_KEY_FIELD: str = 'mask_key'
EDIT_HASH_FIELD: str = 'edit_hash'


@frozen(kw_only=True)
class Actor:
    """The signed-in account on whose behalf a use case runs.

    :ivar account_id: Identifier of the signed-in account.
    """

    account_id: AccountId


@frozen(kw_only=True)
class Project:
    """One book being digitised, owned by one account.

    The project holds the description of the book and the settings of the work on it. The files the book was
    assembled from are its sources, which the project does not name.

    :ivar id: Identifier of the project.
    :ivar owner_id: Account owning the project, the only one that may read or change it.
    :ivar details: Bibliographic description of the book.
    :ivar image_policy: How the ``full`` images of the project's scans and page versions are stored.
    :ivar cover_page_id: Page whose thumbnail the project list shows, or None for the first page of the book.
    :ivar created_at: When the project was created.
    :ivar updated_at: When the project was last changed, which orders the owner's project list.
    """

    DEFAULT_IMAGE_POLICY: ClassVar[ImagePolicy] = ImagePolicy.COMPACT

    id: ProjectId
    owner_id: AccountId
    details: BookDetails
    image_policy: ImagePolicy = DEFAULT_IMAGE_POLICY
    cover_page_id: PageId | None = None
    created_at: datetime
    updated_at: datetime

    def is_owned_by(self, actor: Actor) -> bool:
        """Return whether the actor owns this project.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :returns: True when the actor's account is the project's owner.
        :rtype: bool
        """
        return self.owner_id == actor.account_id


@frozen(kw_only=True)
class ProjectOverview:
    """A project as shown in a list, with the size of its book, counted when read.

    :ivar project: The listed project.
    :ivar page_count: Number of pages of the book, without the pages kept out of it.
    :ivar source_count: Number of sources the book was assembled from.
    :ivar scan_count: Number of scans in all the sources.
    :ivar image_page_count: Number of pages with an image, kept out of the book or not, which a run goes over.
    :ivar progress: How far the book has come along the pipeline, which a repository cannot tell and a service adds, or
                    None before it has.
    """

    project: Project
    page_count: int = field(default=0, validator=validators.ge(0))
    source_count: int = field(default=0, validator=validators.ge(0))
    scan_count: int = field(default=0, validator=validators.ge(0))
    image_page_count: int = field(default=0, validator=validators.ge(0))
    progress: BookProgress | None = None


@frozen(kw_only=True)
class Source:
    """One uploaded file of a book, or the files of one indirect DjVu document, never changed after its import.

    :ivar id: Identifier of the source, which also names its storage directory.
    :ivar project_id: Project owning the source.
    :ivar kind: Kind of the source, which selects the format that reads it.
    :ivar file_type: Exact format of the main file.
    :ivar file_name: Name the browser sent, with the relative path of a directory upload sanitised.
    :ivar files: Stored files, the main file first; more than one only for an indirect DjVu document.
    :ivar size_bytes: Total size of the files in bytes.
    :ivar sha256: SHA-256 digest of the main file, which refuses a second upload of the same file to the project.
    :ivar scan_count: Number of scans the source holds.
    :ivar metadata: Technical metadata of the format, which stays here and is never copied into the project.
    :ivar suggestion: Values of the book description found in the file, which fill only empty description fields.
    :ivar import_job_id: Import job that created the source, or None once that job is deleted.
    :ivar imported_at: When the source became part of the project.
    """

    id: SourceId
    project_id: ProjectId
    kind: SourceKind
    file_type: FileType
    file_name: str = field(validator=validators.min_len(1))
    files: Sequence[SourceFile] = field(validator=validators.min_len(1))
    size_bytes: int = field(validator=validators.ge(0))
    sha256: str = field(validator=validators.matches_re(SHA256_PATTERN))
    scan_count: int = field(validator=validators.ge(0))
    metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)
    import_job_id: JobId | None = None
    imported_at: datetime


@frozen(kw_only=True)
class Scan:
    """One image of a source as the file holds it: a page of a PDF or DjVu document, or a frame of an image file.

    A scan is created by the import and never changes, apart from the state of its derived files.

    :ivar id: Identifier of the scan.
    :ivar project_id: Project owning the scan's source, which lists the scans of a book without reading its sources.
    :ivar source_id: Source holding the scan.
    :ivar number: Position of the scan in its source, starting at 0.
    :ivar source_label: Page label the file itself gives, such as the PDF page label ``xii``, or empty.
    :ivar facts: Technical facts of the image.
    :ivar renditions: Readiness and version of the derived files.
    """

    id: ScanId
    project_id: ProjectId
    source_id: SourceId
    number: int = field(validator=validators.ge(0))
    source_label: str = ''
    facts: ScanFacts
    renditions: Renditions = field(factory=Renditions)


@frozen(kw_only=True)
class Page:
    """A page of the book in book order, which stands on its own once created.

    A page is cut from a scan by the page split, or added by the user in the page order stage as a blank leaf or a
    placeholder. It keeps its own copy of its image in its base version, so nothing about it depends on its source,
    and its identity survives reordering, splitting again and re-running stages. Its position is the order of its key
    among the keys of the project's pages, and the position number shown to the user is computed when it is read.

    :ivar id: Identifier of the page, which names its storage directory.
    :ivar project_id: Project owning the page.
    :ivar order_key: Fractional index string whose byte order is the order of the pages in the book.
    :ivar label: Printed number, such as ``xii``, ``12`` or ``[4]``, or empty for an unnumbered page. It is computed
                 from the pagination sections of the project, except when ``label_manual`` is set.
    :ivar label_manual: Whether the label was written by hand, or came with the scan, which makes it an exception that
                        a recompute of the numbers of the book never changes.
    :ivar kind: Role of the page in the book.
    :ivar content_type: What the image of the page shows as the program found it, or as the user set it when
                        ``content_by_hand`` is set, or None while the content is not detected, which leaves it to the
                        kind of the page.
    :ivar content_by_hand: Whether the user set the content type, which makes it an exception that the detection of the
                           content never changes.
    :ivar origin: Where the image of the page comes from.
    :ivar scan_id: Scan the page was cut from, or None for a blank leaf, a placeholder, or a page whose source was
                   deleted.
    :ivar slot: Part of the scan the page shows: ``0`` the whole scan, ``1`` and ``2`` the halves of a spread, higher
                for a fold-out.
    :ivar blank_fill: What the image of a page of kind blank is: its scan, or a leaf drawn in its place. The scan of the
                      page stays linked whatever the choice, so choosing the scan again brings it back.
    :ivar included: Whether the page is part of the book; off for a colour chart or a duplicate.
    :ivar notes: Notes of the user.
    :ivar group_label: Label of the group the user put the page in by hand, or empty for no group.
    :ivar created_at: When the page was created.
    :ivar updated_at: When the page was last changed.
    :ivar revision: How many times the stored page has been written since it was added, which a write has to match so
                    that it never replaces a change it did not read.
    """

    WHOLE_SCAN: ClassVar[int] = 0
    LEFT_HALF: ClassVar[int] = 1
    RIGHT_HALF: ClassVar[int] = 2

    id: PageId
    project_id: ProjectId
    order_key: str = field(validator=validators.min_len(1))
    label: str = ''
    label_manual: bool = False
    kind: PageKind = PageKind.TEXT
    content_type: ContentType | None = None
    content_by_hand: bool = False
    origin: PageOrigin
    scan_id: ScanId | None = None
    slot: int = field(default=WHOLE_SCAN, validator=validators.ge(WHOLE_SCAN))
    blank_fill: BlankFill = BlankFill.SCAN
    included: bool = True
    notes: str = ''
    group_label: str = ''
    created_at: datetime
    updated_at: datetime
    revision: int = field(default=0, validator=validators.ge(0))

    def __attrs_post_init__(self) -> None:
        """Check the scan of the page, its leaf and its content type.

        Only a page cut from a scan names a scan, only a blank one of them has a leaf, and a content type said to be set
        by hand has to be given.

        :raises ValueError: If a blank leaf or a placeholder names a scan, a leaf stands in place of the scan of a page
                            that is not a blank page cut from a scan, or the content type is said to be set by hand and
                            is none.
        """
        if self.scan_id is not None and self.origin is not PageOrigin.SCAN:
            err_msg = f'A page of origin {self.origin} has no scan, but names scan {self.scan_id}.'
            raise ValueError(err_msg)
        if self.content_by_hand and self.content_type is None:
            err_msg = 'A content type set by hand has to be given.'
            raise ValueError(err_msg)
        cut_blank = self.origin is PageOrigin.SCAN and self.kind is PageKind.BLANK
        if self.blank_fill is not BlankFill.SCAN and not cut_blank:
            err_msg = (
                f'A leaf replaces the scan of a blank page cut from a scan, not of a {self.kind} page of {self.origin}.'
            )
            raise ValueError(err_msg)

    @property
    def is_leaf(self) -> bool:
        """Whether the image of the page is drawn by the program, so no step of a stage has anything to find in it."""
        return self.origin is PageOrigin.BLANK or self.blank_fill is not BlankFill.SCAN

    @property
    def content(self) -> ContentType:
        """What the page shows: the content type the user set, else the one found, else the one its kind gives."""
        return ContentType.shown_by(self.kind, self.content_type, by_hand=self.content_by_hand)

    @property
    def recipe_kind(self) -> RecipeKind:
        """The kind of the page, by which every stage that has recipes chooses the recipe that processes the page."""
        return RecipeKind.of(self.kind, self.content)

    @property
    def content_source(self) -> ContentSource:
        """Where what the page shows comes from: the user, the detection, or the kind of the page."""
        if self.content_by_hand:
            return ContentSource.HAND
        if self.kind not in PICTURE_KINDS and self.content_type is not None:
            return ContentSource.DETECTED
        return ContentSource.KIND


@frozen(kw_only=True)
class PaginationSection:
    """A run of the pages of a book that is numbered by one rule, such as the Roman numbers of a preface.

    A section starts at its first page and lasts until the next section of its own flow starts. A section that names
    no kinds belongs to the main flow of the book, and a section with kinds is a series by kind: it takes the pages of
    those kinds from its first page on, wherever they stand, and keeps its own count, so the plates of a book get
    ``Plate I`` and ``Plate II`` without disturbing the numbers of the text.

    :ivar id: Identifier of the section.
    :ivar project_id: Project owning the section.
    :ivar first_page_id: The page the section starts at, so the section follows the page when it is moved.
    :ivar name: Name the user sees, such as ``Preface``.
    :ivar style: How the numbers of the section are written.
    :ivar start: Number of the first counted page of the section, from 1.
    :ivar prefix: Text written before every number, such as ``Plate ``; empty for none.
    :ivar display: Whether the pages count, and whether their numbers are printed or only implied.
    :ivar kinds: Kinds of page the section takes as a series by kind; empty for a section of the main flow.
    :ivar created_at: When the section was created.
    :ivar updated_at: When the section was last changed.
    """

    id: PaginationSectionId
    project_id: ProjectId
    first_page_id: PageId
    name: str = ''
    style: LabelStyle
    start: int = field(default=1, validator=validators.ge(1))
    prefix: str = ''
    display: NumberDisplay = NumberDisplay.PRINTED
    kinds: frozenset[PageKind] = frozenset()
    created_at: datetime
    updated_at: datetime

    def __attrs_post_init__(self) -> None:
        """Check that the first number can be written in the style.

        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        self.style.write(self.start)

    @property
    def is_series(self) -> bool:
        """Whether the section is a series by kind, which does not interrupt the main flow."""
        return bool(self.kinds)

    def label(self, number: int) -> str:
        """Write the label of the page that takes ``number`` in the section.

        :param number: Number of the page, counted from ``start``.
        :type number: int
        :returns: The number in the style of the section after its prefix, in square brackets when the pages are counted
                  and not printed, and empty when the pages are not counted or the style writes no number.
        :rtype: str
        :raises ValueError: If the style cannot write the number, such as 4000 in Roman numerals.
        """
        text = self.style.write(number)
        if not text or not self.display.counts:
            return ''
        label = f'{self.prefix}{text}'
        return f'[{label}]' if self.display is NumberDisplay.COUNTED else label

    def clashes_with(self, other: PaginationSection) -> bool:
        """Tell whether the two sections start at one page and would take the same pages, so the later one wins.

        :param other: Another section of the project.
        :type other: PaginationSection
        :returns: True when both start at the same page and are both of the main flow, or are series that share a kind.
        :rtype: bool
        """
        if self.id == other.id or self.first_page_id != other.first_page_id:
            return False
        if self.is_series and other.is_series:
            return bool(self.kinds & other.kinds)
        return self.is_series == other.is_series


@frozen(kw_only=True)
class VersionInputs:
    """Everything the result of one step on one page depends on, which the identifier of the version hashes.

    Equal inputs give equal results, so a repeated step finds the files of its earlier result, and an input that changes
    the image must change the identifier or the cache would return another result. What does not change the image, such
    as the time of the run or the recipe a step came from, is not an input, so one step with the same parameters in two
    recipes is one version.

    :ivar page_id: Page the step runs on. It is hashed because a base version has no input version, and two blank
                   leaves of one size would share an identifier without it.
    :ivar processor: Key and version of the processor running the step.
    :ivar params: Parameters of the step after validation, which must be JSON-compatible.
    :ivar input_id: Version the step reads, or None for a base version.
    :ivar edit_hash: Hash of the manual edit the step reads, or empty for none.
    :ivar scale: Whether the step runs on the full image or on the preview.
    :ivar side: Side of the book the page lies on, for a step that reads it, or None for a step that does not.
    :ivar skipped: Whether the page is a leaf the program drew and passes the step unchanged. Such a version
                   holds the image of its input whatever the parameters and the edit of the step are, so it has none.
    """

    page_id: PageId
    processor: ProcessorRef
    params: MetadataMap = field(factory=dict)
    input_id: PageVersionId | None = None
    edit_hash: str = ''
    scale: VersionScale = VersionScale.FULL
    side: PageSide | None = None
    skipped: bool = False

    def identify(self) -> PageVersionId:
        """Return the identifier of the version these inputs produce, a hash of all of them.

        The edit, the scale, the side and the skip join the hash only when they are not the empty edit, the full scale,
        no side and a page that is processed, so a full run without an edit hashes what it hashed before they existed,
        and the identifiers already stored stay the ones a repeated run finds.

        :returns: The SHA-256 of the inputs in canonical JSON, cut to 16 lower-case hexadecimal digits.
        :rtype: PageVersionId
        """
        produced_by = [str(self.page_id), self.processor.key, self.processor.version, self.params, self.input_id]
        if self.edit_hash:
            produced_by.append({'edit': self.edit_hash})
        if self.scale is not VersionScale.FULL:
            produced_by.append({'scale': self.scale.value})
        if self.side is not None:
            produced_by.append({'side': self.side.value})
        if self.skipped:
            produced_by.append({'skipped': True})
        digest = hashlib.sha256(json.dumps(produced_by, sort_keys=True, separators=(',', ':')).encode())
        return PageVersionId(digest.hexdigest()[:VERSION_ID_LENGTH])


@frozen(kw_only=True)
class PageVersion:
    """The result of one processing step on one page of the book, never changed once ready.

    A version is identified by the hash of its ``VersionInputs``, so equal work gets the same identifier and its cached
    result. The first version of a page, its base version,
    has no input version: its input is a scan or, for a blank leaf, nothing.

    :ivar id: Identifier of the version, the hash of what produced it.
    :ivar page_id: Page of the book the version belongs to.
    :ivar stage: Stage of the step.
    :ivar processor: Key and version of the processor that ran the step.
    :ivar input_id: Version the step read, or None for a base version.
    :ivar params: Parameters of the step, following the processor's JSON Schema.
    :ivar transform: Transform of coordinates from the input to this version.
    :ivar data: Data the step found, such as an angle, a frame or a confidence.
    :ivar review: Why the page is to be looked at again though the step finished, as the processor reported it, or None
                  when the step was sure of its result.
    :ivar renditions: State of the version's image files, or None for a step without an image.
    :ivar state: Where the version is in its lifecycle.
    :ivar scale: Whether the step ran on the full image or on the preview, which only the preview of a parameter shows.
    :ivar edit_hash: Hash of the manual edit the step read, or empty for a step without one.
    :ivar tiles_ready: Whether the IIIF tile pyramid of the version is cut, which is done for the current version of a
                       stage and for any other version on request.
    :ivar created_at: When the version was created.
    :ivar files_removed_at: When a collection removed the files of the version, or None while it has them. The row
                            stays with its parameters, data and edit hash, and a run makes the files again under the
                            same identifier.
    :ivar mark: What the user judged of the result, or None while it is not judged. The mark and the comment are the
                user's notes on the result, not an input of the step, so neither changes the identifier.
    :ivar comment: What the user wrote about the result, or empty.
    """

    id: PageVersionId
    page_id: PageId
    stage: Stage
    processor: ProcessorRef
    input_id: PageVersionId | None = None
    params: MetadataMap = field(factory=dict)
    transform: Transform = field(factory=Transform)
    data: MetadataMap = field(factory=dict)
    review: ReviewReason | None = None
    renditions: Renditions | None = field(factory=Renditions)
    state: VersionState = VersionState.PENDING
    scale: VersionScale = VersionScale.FULL
    edit_hash: str = ''
    tiles_ready: bool = False
    created_at: datetime
    files_removed_at: datetime | None = None
    mark: ResultMark | None = None
    comment: str = ''

    @property
    def files_removed(self) -> bool:
        """Whether a collection removed the files of the version, which a run can make again.

        :returns: True from the moment the files were removed until they are made again.
        :rtype: bool
        """
        return self.files_removed_at is not None

    @property
    def origin(self) -> VersionOrigin:
        """How the result came about: set by hand when the step read a manual edit, else made by the step itself."""
        return VersionOrigin.HAND if self.edit_hash else VersionOrigin.AUTO

    def __attrs_post_init__(self) -> None:
        """Check the identifier and the renditions, which place the version's files in storage.

        :raises ValueError: If the identifier is not 16 lower-case hexadecimal digits, or the renditions are past their
                            first version, since a version is written once into the directory of its identifier.
        """
        if not re.fullmatch(VERSION_ID_PATTERN, self.id):
            err_msg = f'A page version id is 16 lower-case hexadecimal digits, not {self.id!r}.'
            raise ValueError(err_msg)
        if self.renditions is not None and self.renditions.version != Renditions.FIRST_VERSION:
            err_msg = 'The renditions of a page version are written once and stay at their first version.'
            raise ValueError(err_msg)


@frozen(kw_only=True)
class PageOverview:
    """A page of the book with what a client shows of it: its place in the book and the image it starts from.

    :ivar page: The page of the book.
    :ivar position: Place of the page in the book from zero, counted over every page of the project, excluded or not.
    :ivar image_version: The version whose renditions show the page: the current version of the latest stage that has
                         an image, which is the base version until a later stage makes one, or None for a page that
                         has no image yet, such as a placeholder.
    :ivar source_id: Source holding the page's scan, which selects every page of one source, or None for a page
                     without a scan.
    :ivar section_id: The pagination section that governs the page, or None for a page kept out of the book, a page
                      before the first section and a book without sections.
    """

    page: Page
    position: int = field(validator=validators.ge(0))
    image_version: PageVersion | None = None
    source_id: SourceId | None = None
    section_id: PaginationSectionId | None = None


@frozen(kw_only=True)
class Job:
    """A background job and how far it has come.

    :ivar id: Identifier of the job.
    :ivar project_id: Project the job works on.
    :ivar kind: What the job does, which also selects its worker pool.
    :ivar state: Where the job is in its life cycle.
    :ivar progress: How many of its steps are done.
    :ivar error: Why the job failed, shown to the user, or empty.
    :ivar request: The files an import job was asked to import, or None for a job that takes none.
    :ivar params: What a processing job was asked to do, in the form its value class writes, empty for a job without.
    :ivar result: What an import job did with them, or None until it has finished.
    :ivar created_at: When the job was recorded.
    :ivar started_at: When a worker started the job, or None while it is queued.
    :ivar finished_at: When the job reached a final state, or None before.
    """

    id: JobId
    project_id: ProjectId
    kind: JobKind
    state: JobState = JobState.QUEUED
    progress: Progress = field(factory=Progress)
    error: str = ''
    request: ImportRequest | None = None
    params: MetadataMap = field(factory=dict)
    result: ImportResult | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @property
    def stage(self) -> Stage | None:
        """Name the stage the job runs, or None for a job that runs none or whose parameters cannot be read.

        :returns: The stage of a ``run-stage`` job.
        :rtype: Stage | None
        """
        if self.kind is not JobKind.RUN_STAGE:
            return None
        try:
            return StageRun.from_map(self.params).stage
        except InvalidParametersError:
            return None


@frozen(kw_only=True)
class Recipe:
    """The ordered steps of one stage for one kind of page, saved in a project.

    A stage that has recipes has exactly one for each ``RecipeKind``, and a page is processed by the recipe of its kind.
    The name of a recipe is the label of its kind.

    :ivar id: Identifier of the recipe.
    :ivar project_id: Project owning the recipe.
    :ivar stage: Stage the recipe processes.
    :ivar kind: Kind of the pages the recipe processes, which is unique among the recipes of the stage.
    :ivar steps: The steps in the order they run, each a processor with its parameters.
    :ivar profile_id: The profile of the account the recipe was made from, or None for a recipe that was not. The
                      steps may have changed since, and the link survives that, so the book can tell how it differs
                      from the profile. Deleting the profile clears it.
    :ivar created_at: When the recipe was created.
    :ivar updated_at: When the recipe was last changed.
    """

    id: RecipeId
    project_id: ProjectId
    stage: Stage
    kind: RecipeKind
    steps: tuple[Step, ...]
    profile_id: RecipeProfileId | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def enabled_steps(self) -> tuple[Step, ...]:
        """The steps a run and a preview run, in order, which leaves out the ones switched off."""
        return tuple(step for step in self.steps if step.enabled)

    def place_of(self, step_id: StepId) -> int | None:
        """Give the place of a step among the steps that are on, which is where its version stands in a chain.

        Every step that is on stores one version that reads the one before it, so the place of a step in the chain of a
        page is the number of steps that are on before it.

        :param step_id: The step.
        :type step_id: StepId
        :returns: The place, from zero, or None when the recipe has no such step or has it switched off.
        :rtype: int | None
        """
        return next((place for place, step in enumerate(self.enabled_steps) if step.step_id == step_id), None)

    def indexed_steps_through(self, through_step: int | None) -> tuple[tuple[int, Step], ...]:
        """Give the steps that are on up to one of them, each with its index in the recipe.

        :param through_step: Index of the last step to take, or None to take every step that is on.
        :type through_step: int | None
        :returns: The pairs of index and step in the order they run. Empty when every step up to the index is off.
        :rtype: tuple[tuple[int, Step], ...]
        """
        return tuple(
            (index, step)
            for index, step in enumerate(self.steps)
            if step.enabled and (through_step is None or index <= through_step)
        )

    def stopped_at(self, through_step: int | None) -> int | None:
        """Tell where a run through one step stops when that is short of the last step that is on.

        :param through_step: Index of the last step to run, or None for every step that is on.
        :type through_step: int | None
        :returns: The index of the last step that run makes, or None when it makes the last step that is on, or none.
        :rtype: int | None
        """
        reached = self.indexed_steps_through(through_step)
        every = self.indexed_steps_through(None)
        if not reached or reached[-1] == every[-1]:
            return None
        return reached[-1][0]


@frozen(kw_only=True)
class RecipeProfile:
    """The steps of a recipe of one stage that an account saved, to apply to its books.

    A recipe belongs to one book, and a profile to the account, so a book that is set up once can be set up again in
    another by applying the profile. The steps keep their order, their parameters and their ``enabled`` switch. An
    account has at most one default profile for each stage, which a new book takes as the recipe of text pages.

    :ivar id: Identifier of the profile.
    :ivar account_id: Account owning the profile, the only one that may read or change it.
    :ivar stage: Stage whose recipes the profile can be applied to.
    :ivar name: Name the user sees, such as ``Photographed book``.
    :ivar steps: The steps in the order they run, each a processor with its parameters.
    :ivar order: Whether the steps were saved in the usual order, which refuses a step where it cannot work, or in the
                 free order, which lets it stand with a warning. A book opened from the profile starts in this mode.
    :ivar is_default: Whether a new book of the account starts the stage with this profile.
    :ivar created_at: When the profile was saved.
    :ivar updated_at: When the profile was last changed.
    """

    id: RecipeProfileId
    account_id: AccountId
    stage: Stage
    name: str = field(validator=validators.min_len(1))
    steps: tuple[Step, ...]
    order: OrderMode = OrderMode.USUAL
    is_default: bool = False
    created_at: datetime
    updated_at: datetime


@frozen(kw_only=True)
class PageStage:
    """The current version of one stage of one page, and whether it still matches the stage's inputs.

    The record keeps the last version of the recipe the page was processed by, so a version knows nothing of whether it
    is current. A change of an earlier stage marks the later ones stale and deletes none of their versions, so the
    interface shows the old result until it is recomputed.

    :ivar page_id: Page the record belongs to.
    :ivar stage: The stage.
    :ivar recipe_id: Recipe the page was processed by, or None when the recipe was deleted or none ran yet.
    :ivar head_version_id: Last version of the recipe's steps, which is the current version of the stage, or None.
    :ivar state: Whether the current version matches the inputs of the stage.
    :ivar through_step: Index in the recipe of the last step the page was run through, when that is before the last
                        step that is on, so the page is not ready for the next stage. None for a page run through every
                        step that is on, and for a record no recipe made.
    :ivar updated_at: When the record last changed.
    """

    page_id: PageId
    stage: Stage
    recipe_id: RecipeId | None = None
    head_version_id: PageVersionId | None = None
    state: StageState = StageState.FRESH
    through_step: int | None = field(default=None, validator=validators.optional(validators.ge(0)))
    updated_at: datetime

    @property
    def key(self) -> PageStageKey:
        """The key the record is stored under."""
        return PageStageKey(self.page_id, self.stage)


@frozen(kw_only=True)
class BookPlace:
    """Where one account left one book: the stage, the page, the view and the position of the canvas.

    The place is replaced whole each time the reader moves, and read when the book is opened again, on any device. It
    holds the fields of ``NewBookPlace`` beside the account, the book and the time of the last write.

    :ivar account_id: Account the place belongs to.
    :ivar project_id: Book the place is in.
    :ivar mode: Whether the reader was working on a stage or reading.
    :ivar stage: Stage the reader was on, or last was on before reading.
    :ivar page_id: Page that was open, or None for the first page.
    :ivar scan_id: Scan that was open on the stages that work on scans, or None.
    :ivar source_id: File that was chosen on the stages that work on files, or None.
    :ivar view: How the canvas laid the pages out.
    :ivar compare: How the result of the stage was compared with the one before it.
    :ivar filter: Which pages the strip or the grid listed.
    :ivar canvas: Zoom and centre of the canvas, or None for the fitted view.
    :ivar strip_page_id: First page in sight in the strip or the grid, or None for the top.
    :ivar updated_at: When the place was last written.
    """

    account_id: AccountId
    project_id: ProjectId
    mode: PlaceMode
    stage: Stage
    page_id: PageId | None = None
    scan_id: ScanId | None = None
    source_id: SourceId | None = None
    view: ViewMode = ViewMode.PAGE
    compare: CompareMode = CompareMode.OFF
    filter: PageFilter = PageFilter.ALL
    canvas: CanvasPosition | None = None
    strip_page_id: PageId | None = None
    updated_at: datetime

    @property
    def key(self) -> BookPlaceKey:
        """The key the place is stored under."""
        return BookPlaceKey(self.account_id, self.project_id)


@frozen(kw_only=True)
class PageEdit:
    """A manual edit of a page that a processor reads as an input beside its parameters.

    A frame, an angle, a split line or the mask of an eraser is stored apart from the parameters of the step. The hash
    of the edit joins the identifier of the versions that read it, so a changed edit gives a new version and the old
    edit finds the old version again.

    :ivar page_id: Page the edit belongs to.
    :ivar stage: Stage of the step reading the edit.
    :ivar step_id: The step of a recipe reading the edit, so two steps of one processor keep their own edits.
    :ivar kind: Editor that made the edit.
    :ivar geometry: The shape the user drew, or None for an edit that is only a mask.
    :ivar mask_key: Storage key of the mask the user painted, or None for an edit without one.
    :ivar edit_hash: Hash of the geometry and of the mask, which joins the identifiers of the versions that read it.
    :ivar updated_at: When the edit was last saved.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId
    kind: EditorKind
    geometry: EditGeometry | None = None
    mask_key: StorageKey | None = None
    edit_hash: str = field(validator=validators.min_len(1))
    updated_at: datetime

    @property
    def key(self) -> PageStepKey:
        """The key the edit is stored under."""
        return PageStepKey(self.page_id, self.stage, self.step_id)

    def to_snapshot(self) -> dict[str, Any]:
        """Return the edit as the JSON object the history keeps for the manual layer of a step.

        The object holds what the edit is and not when it was saved, so two saves of the same shape give equal
        snapshots. Its values are JSON types only, which is what a stored snapshot reads back as.

        :returns: The editor, the shape, the key of the mask and the hash of the edit.
        :rtype: dict[str, Any]
        """
        shape = None if self.geometry is None else self.geometry.to_data()
        return json.loads(
            json.dumps(
                {
                    KIND_FIELD: self.kind.value,
                    GEOMETRY_FIELD: shape,
                    MASK_KEY_FIELD: self.mask_key,
                    EDIT_HASH_FIELD: self.edit_hash,
                }
            )
        )

    @classmethod
    def from_snapshot(cls, key: PageStepKey, data: MetadataMap, updated_at: datetime) -> Self:
        """Rebuild the edit a snapshot of the history holds, which is how an undo puts back an edit.

        :param key: The page, the stage and the step the edit belongs to.
        :type key: PageStepKey
        :param data: The snapshot ``to_snapshot`` made.
        :type data: MetadataMap
        :param updated_at: When the edit is put back.
        :type updated_at: datetime
        :returns: The edit, with the hash and the mask key it had.
        :rtype: Self
        """
        kind = EditorKind(data[KIND_FIELD])
        shape = data[GEOMETRY_FIELD]
        mask_key = data[MASK_KEY_FIELD]
        return cls(
            page_id=key.page_id,
            stage=key.stage,
            step_id=key.step_id,
            kind=kind,
            geometry=None if shape is None else geometry_from_data(kind, shape),
            mask_key=None if mask_key is None else StorageKey(mask_key),
            edit_hash=data[EDIT_HASH_FIELD],
            updated_at=updated_at,
        )

    @staticmethod
    def hash_of(geometry: EditGeometry | None, mask_sha256: str | None) -> str:
        """Return the hash of an edit, from its shape in canonical JSON and the SHA-256 digest of its mask.

        :param geometry: The shape the user drew, or None.
        :type geometry: EditGeometry | None
        :param mask_sha256: SHA-256 digest of the mask file, or None for an edit without one.
        :type mask_sha256: str | None
        :returns: The hash cut to 16 lower-case hexadecimal digits, as long as a version identifier.
        :rtype: str
        """
        shape = None if geometry is None else {KIND_FIELD: geometry.editor.value, **geometry.to_data()}
        text = json.dumps([shape, mask_sha256], sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(text.encode()).hexdigest()[:VERSION_ID_LENGTH]


@frozen(kw_only=True)
class PageStepState:
    """What a page keeps for one step of a recipe: its own settings of the step and the manual edit the step reads.

    The settings are only the fields of the parameters of the step that the user changed for this page. A run takes
    every other field from the odd or the even pages, from the group of the page, or from the step of the recipe, in
    that order of weakness, so a field changed in one of them reaches each page that did not change it itself. The
    values join the parameters the step runs with, which the identifier of a version hashes, so a page whose effective
    parameters equal those of another finds the same version in the cache.

    :ivar page_id: Page the state belongs to.
    :ivar stage: Stage of the step.
    :ivar step_id: The step of a recipe, so two steps of one processor keep their own state.
    :ivar params: The fields of the parameters of the step that this page changes, by name.
    :ivar edit: The manual edit the step reads on this page, or None.
    :ivar updated_at: When the state was last saved.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId
    params: MetadataMap = field(factory=dict)
    edit: PageEdit | None = None
    updated_at: datetime

    @property
    def key(self) -> PageStepKey:
        """The key the state is stored under."""
        return PageStepKey(self.page_id, self.stage, self.step_id)

    @property
    def is_empty(self) -> bool:
        """Whether the state holds neither a setting nor an edit, so there is nothing left to store."""
        return not self.params and self.edit is None

    def layer(self, layer: StepLayer) -> dict[str, Any] | None:
        """Return the content of one layer as the history keeps it.

        :param layer: The layer.
        :type layer: StepLayer
        :returns: The fields the page changes, or the snapshot of the manual edit, or None for an empty layer.
        :rtype: dict[str, Any] | None
        :raises ConflictError: For the layer of what the automatic run found, which no state keeps yet.
        """
        match layer:
            case StepLayer.SETTINGS:
                return dict(self.params) or None
            case StepLayer.HAND:
                return None if self.edit is None else self.edit.to_snapshot()
            case StepLayer.FOUND:
                raise ConflictError(LAYER_NOT_KEPT.format(layer=layer.label))

    def with_layer(self, layer: StepLayer, content: MetadataMap | None, updated_at: datetime) -> Self:
        """Return the state with one layer set to a content the history kept, which is how an undo writes it back.

        :param layer: The layer.
        :type layer: StepLayer
        :param content: What ``layer`` returned, or None to empty the layer.
        :type content: MetadataMap | None
        :param updated_at: When the layer is set.
        :type updated_at: datetime
        :returns: The state with the layer set.
        :rtype: Self
        :raises ConflictError: For the layer of what the automatic run found, which no state keeps yet.
        """
        match layer:
            case StepLayer.SETTINGS:
                return evolve(self, params=dict(content or {}), updated_at=updated_at)
            case StepLayer.HAND:
                edit = None if content is None else PageEdit.from_snapshot(self.key, content, updated_at)
                return evolve(self, edit=edit, updated_at=updated_at)
            case StepLayer.FOUND:
                raise ConflictError(LAYER_NOT_KEPT.format(layer=layer.label))


@frozen(kw_only=True)
class PageStepChange:
    """One change of a layer of a step on a page, which the history of the page keeps and never rewrites.

    The values are the whole content of the layer before and after the change, so undoing a change writes ``before``
    back without knowing how the layer is built. The layer of the settings holds the fields the page changes, by name.

    :ivar id: Identifier of the change.
    :ivar page_id: Page the change was made on.
    :ivar stage: Stage of the step.
    :ivar step_id: The step whose layer changed.
    :ivar layer: The layer that changed.
    :ivar scope: Whose settings the layer holds: those of the page itself, or those of the odd pages, the even pages or
                 a group the page takes values from, which the history of each page they change keeps a change of.
    :ivar group_label: Label of the group for the scope of a group, and empty for the others.
    :ivar before: Content of the layer before the change, or None when the layer was empty.
    :ivar after: Content of the layer after the change, or None when the change emptied it.
    :ivar source: What made the change.
    :ivar batch_id: Identifier shared by the changes of one batch, which are undone together, or None.
    :ivar undoes: The change this one takes back, for a change whose source is an undo, or None. The history is never
                  rewritten, so a change is undone when another change names it here.
    :ivar created_at: When the change was made.
    :ivar sequence: Place of the change in the history of its page, from one, which the repository gives it when it is
                    added, so changes made at the same instant keep the order they were written in.
    """

    id: PageStepChangeId
    page_id: PageId
    stage: Stage
    step_id: StepId
    layer: StepLayer
    scope: ValueScope = ValueScope.PAGES
    group_label: str = ''
    before: MetadataMap | None = None
    after: MetadataMap | None = None
    source: ChangeSource
    batch_id: ChangeBatchId | None = None
    undoes: PageStepChangeId | None = None
    created_at: datetime
    sequence: int = 0

    @property
    def key(self) -> PageStepKey:
        """The key of the state of the step on the page that the change was made on."""
        return PageStepKey(self.page_id, self.stage, self.step_id)

    @classmethod
    def between(cls, before: PageStepState, after: PageStepState, layer: StepLayer, source: ChangeSource) -> Self:
        """Describe what the change of one layer from a state to another did.

        :param before: The state of the step on the page before the change, which is empty for a page with none.
        :type before: PageStepState
        :param after: The state after the change, which carries the time of the change.
        :type after: PageStepState
        :param layer: The layer that changed.
        :type layer: StepLayer
        :param source: What made the change.
        :type source: ChangeSource
        :returns: The change, with a new identifier, no batch, nothing undone and no sequence yet.
        :rtype: Self
        """
        return cls(
            id=PageStepChangeId(uuid4()),
            page_id=after.page_id,
            stage=after.stage,
            step_id=after.step_id,
            layer=layer,
            before=before.layer(layer),
            after=after.layer(layer),
            source=source,
            created_at=after.updated_at,
        )

    def taken_back(self, moment: datetime, batch_id: ChangeBatchId | None) -> Self:
        """Describe the undo of this change: the same layer changed from what it left to what it replaced.

        :param moment: When the change is taken back.
        :type moment: datetime
        :param batch_id: The batch the undos of one action share, or None for an undo of one change.
        :type batch_id: ChangeBatchId | None
        :returns: The undo, with a new identifier, naming this change and with no sequence yet.
        :rtype: Self
        """
        return evolve(
            self,
            id=PageStepChangeId(uuid4()),
            before=self.after,
            after=self.before,
            source=ChangeSource.UNDO,
            batch_id=batch_id,
            undoes=self.id,
            created_at=moment,
            sequence=0,
        )

    @classmethod
    def of_values(cls, page_id: PageId, before: StepValues, after: StepValues, source: ChangeSource) -> Self:
        """Describe a change of the values of a part of the pages, as the history of one of its pages keeps it.

        :param page_id: The page whose history keeps the change, which is one that takes the values.
        :type page_id: PageId
        :param before: The values of the part before the change, which are empty for a part that had none.
        :type before: StepValues
        :param after: The values after the change, which carry the time of the change.
        :type after: StepValues
        :param source: What made the change.
        :type source: ChangeSource
        :returns: The change, with a new identifier, no batch, nothing undone and no sequence yet.
        :rtype: Self
        """
        return cls(
            id=PageStepChangeId(uuid4()),
            page_id=page_id,
            stage=after.stage,
            step_id=after.step_id,
            layer=StepLayer.SETTINGS,
            scope=after.scope,
            group_label=after.group_label,
            before=dict(before.params) or None,
            after=dict(after.params) or None,
            source=source,
            created_at=after.updated_at,
        )


@frozen(kw_only=True)
class ResultMarkChange:
    """One change of the mark or the comment of a result, which the log of the result keeps and never rewrites.

    :ivar id: Identifier of the change.
    :ivar version_id: Version the mark and the comment belong to.
    :ivar mark_before: Mark before the change, or None when the result had none.
    :ivar mark_after: Mark after the change, or None when the change took it off.
    :ivar comment_before: Comment before the change, or empty.
    :ivar comment_after: Comment after the change, or empty.
    :ivar created_at: When the change was made.
    :ivar sequence: Place of the change in the log of its version, from one, which the repository gives it when it is
                    added, so changes made at the same instant keep the order they were written in.
    """

    id: ResultMarkChangeId
    version_id: PageVersionId
    mark_before: ResultMark | None = None
    mark_after: ResultMark | None = None
    comment_before: str = ''
    comment_after: str = ''
    created_at: datetime
    sequence: int = 0
