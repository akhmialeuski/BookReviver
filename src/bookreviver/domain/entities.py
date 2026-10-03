"""Entities: domain objects with an identity, frozen and changed through ``attrs.evolve``."""

import hashlib
import json
import re
from typing import TYPE_CHECKING, ClassVar

from attrs import field, frozen, validators

from bookreviver.domain.enums import (
    CompareMode,
    ImagePolicy,
    JobKind,
    JobState,
    NumberDisplay,
    PageFilter,
    PageKind,
    PageOrigin,
    RuleCondition,
    StageState,
    VersionScale,
    VersionState,
    ViewMode,
)
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.geometry import Transform
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.values import (
    SHA256_PATTERN,
    BookPlaceKey,
    MetadataSuggestion,
    PageEditKey,
    PageStageKey,
    Progress,
    Renditions,
    StageRun,
)

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.enums import (
        EditorKind,
        FileType,
        LabelStyle,
        PageSide,
        PlaceMode,
        ReviewReason,
        SourceKind,
        Stage,
    )
    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.ids import (
        AccountId,
        JobId,
        PageId,
        PaginationSectionId,
        ProjectId,
        RecipeId,
        RecipeProfileId,
        RecipeRuleId,
        ScanId,
        SourceId,
        StepId,
        StorageKey,
    )
    from bookreviver.domain.stage_summaries import BookProgress
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
GROUP_LABEL_MISSING: str = 'A rule on a manual group needs the label of the group.'
GROUP_LABEL_UNEXPECTED: str = 'The condition {condition} takes no group label.'


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
    :ivar origin: Where the image of the page comes from.
    :ivar scan_id: Scan the page was cut from, or None for a blank leaf, a placeholder, or a page whose source was
                   deleted.
    :ivar slot: Part of the scan the page shows: ``0`` the whole scan, ``1`` and ``2`` the halves of a spread, higher
                for a fold-out.
    :ivar included: Whether the page is part of the book; off for a colour chart or a duplicate.
    :ivar notes: Notes of the user.
    :ivar group_label: Label of the group the user put the page in by hand, or empty for no group, which a rule of a
                       stage may send to a variant of its recipe.
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
    origin: PageOrigin
    scan_id: ScanId | None = None
    slot: int = field(default=WHOLE_SCAN, validator=validators.ge(WHOLE_SCAN))
    included: bool = True
    notes: str = ''
    group_label: str = ''
    created_at: datetime
    updated_at: datetime
    revision: int = field(default=0, validator=validators.ge(0))

    def __attrs_post_init__(self) -> None:
        """Check that only a page cut from a scan names a scan.

        :raises ValueError: If a blank leaf or a placeholder names a scan.
        """
        if self.scan_id is not None and self.origin is not PageOrigin.SCAN:
            err_msg = f'A page of origin {self.origin} has no scan, but names scan {self.scan_id}.'
            raise ValueError(err_msg)


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
    variants of a recipe is one version.

    :ivar page_id: Page the step runs on. It is hashed because a base version has no input version, and two blank
                   leaves of one size would share an identifier without it.
    :ivar processor: Key and version of the processor running the step.
    :ivar params: Parameters of the step after validation, which must be JSON-compatible.
    :ivar input_id: Version the step reads, or None for a base version.
    :ivar edit_hash: Hash of the manual edit the step reads, or empty for none.
    :ivar scale: Whether the step runs on the full image or on the preview.
    :ivar side: Side of the book the page lies on, for a step that reads it, or None for a step that does not.
    :ivar skipped: Whether the page did not meet the condition of the step and passes it unchanged. Such a version
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

    @property
    def files_removed(self) -> bool:
        """Whether a collection removed the files of the version, which a run can make again.

        :returns: True from the moment the files were removed until they are made again.
        :rtype: bool
        """
        return self.files_removed_at is not None

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
    """

    page: Page
    position: int = field(validator=validators.ge(0))
    image_version: PageVersion | None = None
    source_id: SourceId | None = None


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
    """The ordered steps of one stage, saved in a project, which the pages of the stage are processed by.

    A stage has exactly one active recipe, by which a page without a choice of its own is processed. Every other recipe
    of the stage is a variant, which the user tries on some pages or compares with the active one.

    :ivar id: Identifier of the recipe.
    :ivar project_id: Project owning the recipe.
    :ivar stage: Stage the recipe processes.
    :ivar name: Name the user sees, such as ``Spread``.
    :ivar steps: The steps in the order they run, each a processor with its parameters.
    :ivar active: Whether the recipe is the one the stage runs by default, which no variant is.
    :ivar created_at: When the recipe was created.
    :ivar updated_at: When the recipe was last changed.
    """

    id: RecipeId
    project_id: ProjectId
    stage: Stage
    name: str = field(validator=validators.min_len(1))
    steps: tuple[Step, ...]
    active: bool = False
    created_at: datetime
    updated_at: datetime

    @property
    def enabled_steps(self) -> tuple[Step, ...]:
        """The steps a run and a preview run, in order, which leaves out the ones switched off."""
        return tuple(step for step in self.steps if step.enabled)

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
    account has at most one default profile for each stage, which a new book takes as the active recipe of the stage.

    :ivar id: Identifier of the profile.
    :ivar account_id: Account owning the profile, the only one that may read or change it.
    :ivar stage: Stage whose recipes the profile can be applied to.
    :ivar name: Name the user sees, such as ``Photographed book``.
    :ivar steps: The steps in the order they run, each a processor with its parameters.
    :ivar is_default: Whether a new book of the account starts the stage with this profile.
    :ivar created_at: When the profile was saved.
    :ivar updated_at: When the profile was last changed.
    """

    id: RecipeProfileId
    account_id: AccountId
    stage: Stage
    name: str = field(validator=validators.min_len(1))
    steps: tuple[Step, ...]
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
    :ivar pinned: Whether the user pinned ``recipe_id`` to the page, so that a run without a recipe processes the page
                  by it and not by a rule or the active recipe. A pin without a recipe holds nothing.
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
    pinned: bool = False
    through_step: int | None = field(default=None, validator=validators.optional(validators.ge(0)))
    updated_at: datetime

    @property
    def key(self) -> PageStageKey:
        """The key the record is stored under."""
        return PageStageKey(self.page_id, self.stage)

    @property
    def pinned_recipe_id(self) -> RecipeId | None:
        """The recipe pinned to the page, or None when the page is not pinned or the pinned recipe was deleted."""
        return self.recipe_id if self.pinned else None


@frozen(kw_only=True)
class RecipeRule:
    """A rule of a stage that sends the pages meeting a condition to a variant of its recipe.

    The rules of a stage are tried in the order of ``order``, and the first that matches a page wins, so a page that
    meets no rule is processed by the active recipe. A page that has a recipe pinned to it skips the rules.

    :ivar id: Identifier of the rule.
    :ivar project_id: Project owning the rule.
    :ivar stage: Stage whose pages the rule sends to a variant.
    :ivar condition: What a page must be for the rule to match it.
    :ivar group_label: The group the page must be in, for the condition ``group``, and empty for any other.
    :ivar recipe_id: Recipe of the stage that processes the pages the rule matches.
    :ivar order: Place of the rule among the rules of the stage, from zero, the lowest being tried first.
    """

    id: RecipeRuleId
    project_id: ProjectId
    stage: Stage
    condition: RuleCondition
    group_label: str = ''
    recipe_id: RecipeId
    order: int = field(validator=validators.ge(0))

    def __attrs_post_init__(self) -> None:
        """Check that a group label is given exactly for the condition on the group.

        :raises ValueError: If the condition on the group has no label, or another condition has one.
        """
        if self.condition is RuleCondition.GROUP and not self.group_label:
            raise ValueError(GROUP_LABEL_MISSING)
        if self.condition is not RuleCondition.GROUP and self.group_label:
            raise ValueError(GROUP_LABEL_UNEXPECTED.format(condition=self.condition.label.lower()))

    def matches(self, page: Page, position: int) -> bool:
        """Tell whether the page meets the condition of the rule.

        The condition on illustrations matches no page, since the Layout stage that finds them does not exist yet.

        :param page: The page.
        :type page: Page
        :param position: Place of the page in the book counted from 1, which the parity of the page is read from.
        :type position: int
        :returns: Whether the rule applies to the page.
        :rtype: bool
        """
        match self.condition:
            case RuleCondition.ODD:
                return position % 2 == 1
            case RuleCondition.EVEN:
                return position % 2 == 0
            case RuleCondition.GROUP:
                return page.group_label == self.group_label
            case RuleCondition.ILLUSTRATED:
                return False
            case _:
                return page.kind in self.condition.kinds


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
    def key(self) -> PageEditKey:
        """The key the edit is stored under."""
        return PageEditKey(self.page_id, self.stage, self.step_id)

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
        shape = None if geometry is None else {'kind': geometry.editor.value, **geometry.to_data()}
        text = json.dumps([shape, mask_sha256], sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(text.encode()).hexdigest()[:VERSION_ID_LENGTH]
