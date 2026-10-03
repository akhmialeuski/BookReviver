"""Immutable value objects of the domain."""

import re
from collections.abc import Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Self, override
from uuid import UUID, uuid4

from attrs import evolve, field, fields_dict, frozen, validators

from bookreviver.domain.enums import (
    AppliesTo,
    CompareMode,
    ContributorRole,
    EditorKind,
    JobKind,
    NewPageOrigin,
    NumberDisplay,
    Orthography,
    PageFilter,
    ProcessorScope,
    Rendition,
    RightsStatus,
    Script,
    Stage,
    StepField,
    UploadProblem,
    VersionData,
    VersionOutput,
    ViewMode,
    WorkerPool,
)
from bookreviver.domain.errors import InvalidIdentifierError, InvalidParametersError, UploadRejectedError
from bookreviver.domain.ids import PageId, PageVersionId, RecipeId, StepId

if TYPE_CHECKING:
    from collections.abc import Sequence

    from attrs import Attribute

    from bookreviver.domain.enums import (
        ColorMode,
        FileType,
        IdentifierScheme,
        LabelStyle,
        PageKind,
        PlaceMode,
        RejectionReason,
        Side,
        SourceKind,
        VersionScale,
    )
    from bookreviver.domain.geometry import EditGeometry
    from bookreviver.domain.ids import AccountId, ProjectId, ScanId, SourceId

# JSON-compatible metadata as read from a source file
type MetadataMap = Mapping[str, Any]
# Key of the colour mode among the facts of a scan that a processor reads
COLOR_MODE_KEY: str = 'color_mode'
# The description field a suggestion never fills
TITLE_FIELD: str = 'title'
# A SHA-256 digest as lower-case hexadecimal digits
SHA256_PATTERN: str = r'[0-9a-f]{64}'
# Segments of an uploaded path that do not name a file or a folder
NAMELESS_SEGMENTS: frozenset[str] = frozenset({'', '.', '..'})
# A Windows drive letter opening a path, as in ``C:\scans`` or ``C:scans``
DRIVE_LETTER: re.Pattern[str] = re.compile(r'[A-Za-z]:')
CONTROL_CHARACTERS: re.Pattern[str] = re.compile(r'[\x00-\x1f\x7f]')
PIN_NEEDS_RECIPE: str = 'A run pins a recipe to its pages only when it names the recipe.'
# Key of the last step to run in the stored parameters of a ``run-stage`` job
THROUGH_STEP_KEY: str = 'through_step'
REMAKE_KEY: str = 'remake'
REMAKE_ONE_PAGE: str = 'A run that makes a version again names no recipe and exactly one page.'


@frozen(kw_only=True)
class Contributor:
    """A person who took part in the making of a book, in the role the title page or a catalogue gives them.

    The name is kept as printed, with the spelling and the initials of the book, because a catalogue of old books is
    searched by that form. The position of a contributor in the list of the description records the order of the
    title page.

    :ivar name: Name as printed in the book.
    :ivar role: Role of the person, a code of the MARC list of relators.
    """

    name: str = field(validator=validators.min_len(1))
    role: ContributorRole


def _is_normalized(identifier: BookIdentifier, _attribute: Attribute[str], value: str) -> None:
    """Check that the value follows the rules of the scheme of its identifier and is already normalized.

    :param identifier: The identifier being built, whose scheme the value must follow.
    :type identifier: BookIdentifier
    :param _attribute: The attribute being validated, which the rule does not need.
    :type _attribute: Attribute[str]
    :param value: The value to check.
    :type value: str
    :raises InvalidIdentifierError: If the value breaks the rules of the scheme or is not normalized.
    """
    if (normalized := identifier.scheme.normalize(value)) != value:
        err_msg = f'{value!r} is not normalized; the normalized {identifier.scheme.label} is {normalized!r}.'
        raise InvalidIdentifierError(err_msg)


@frozen(kw_only=True)
class BookIdentifier:
    """A number or an address that identifies a book or one copy of it, held in the normalized form of its scheme.

    :ivar scheme: Kind of the identifier, which decides how it is written and checked.
    :ivar value: The identifier in the normalized form of its scheme, so equal numbers written differently are equal.
    """

    scheme: IdentifierScheme
    value: str = field(validator=_is_normalized)

    @classmethod
    def parse(cls, scheme: IdentifierScheme, raw: str) -> Self:
        """Build an identifier from a value as a person wrote it or a file stored it.

        :param scheme: Kind of the identifier.
        :type scheme: IdentifierScheme
        :param raw: The value in any spelling the scheme accepts.
        :type raw: str
        :returns: The identifier with the value normalized.
        :rtype: Self
        :raises InvalidIdentifierError: If the value breaks the rules of the scheme.
        """
        return cls(scheme=scheme, value=scheme.normalize(raw))


@frozen(kw_only=True)
class BookDetails:
    """Bibliographic description of a printed book, down to the copy that was scanned.

    The fields follow the `DCMI Metadata Terms <https://www.dublincore.org/specifications/dublin-core/dcmi-terms/>`_,
    so an export to library formats needs no translation of concepts. Text fields are empty when unknown, lists are
    tuples, and the title is the one field that may not be empty.

    :ivar title: Title of the book.
    :ivar subtitle: Words of the title page that explain the title.
    :ivar parallel_titles: Titles in other languages that the title page prints beside the title.
    :ivar original_title: Title of the original work, for a translation.
    :ivar contributors: People who made the book, each with a role, in the order of the title page.
    :ivar publisher: Publisher of the book.
    :ivar printer: Printing house, which old books name apart from the publisher.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed, which may be a range or an estimate.
    :ivar edition: Edition statement.
    :ivar censorship: Censor's permit printed in the book, whose date dates a book that names no year.
    :ivar series: Series the book belongs to.
    :ivar series_number: Number of the book in its series.
    :ivar volume: Volume or part within a multi-volume work.
    :ivar languages: ISO 639-3 codes of the languages of the text.
    :ivar orthography: Spelling system of the print, such as pre-reform Russian.
    :ivar script: Writing system of the print.
    :ivar printed_pagination: Pagination as a catalogue states it, such as ``XII, 340 p., 8 l. of plates``.
    :ivar height_cm: Height of the book in centimetres, or None when unknown.
    :ivar illustrations: Illustrations as a catalogue states them.
    :ivar binding: Binding or cover of the copy.
    :ivar identifiers: Numbers and addresses that identify the book or the copy, such as a shelfmark.
    :ivar subjects: Topics of the book.
    :ivar rights: Whether the result of the work may be published.
    :ivar copy_holder: Owner of the copy that was scanned.
    :ivar copy_notes: Marks of the copy that was scanned, such as bookplates and annotations.
    :ivar notes: Free-form notes of the owner.
    """

    title: str = field(validator=validators.min_len(1))
    subtitle: str = ''
    parallel_titles: tuple[str, ...] = ()
    original_title: str = ''
    contributors: tuple[Contributor, ...] = ()
    publisher: str = ''
    printer: str = ''
    publication_place: str = ''
    publication_year: str = ''
    edition: str = ''
    censorship: str = ''
    series: str = ''
    series_number: str = ''
    volume: str = ''
    languages: tuple[str, ...] = ()
    orthography: Orthography = Orthography.UNKNOWN
    script: Script = Script.UNKNOWN
    printed_pagination: str = ''
    height_cm: int | None = field(default=None, validator=validators.optional(validators.gt(0)))
    illustrations: str = ''
    binding: str = ''
    identifiers: tuple[BookIdentifier, ...] = ()
    subjects: tuple[str, ...] = ()
    rights: RightsStatus = RightsStatus.UNKNOWN
    copy_holder: str = ''
    copy_notes: str = ''
    notes: str = ''

    def fill_from(self, suggestion: MetadataSuggestion) -> BookDetails:
        """Return the description with every empty field the suggestion has a value for filled in.

        A field that holds a value, even a wrong one, is never replaced, and the title is never touched, because it is
        required when the project is created and a file can only guess it.

        :param suggestion: Description fields found in a source.
        :type suggestion: MetadataSuggestion
        :returns: The description with its empty fields filled, or an equal description when nothing was empty.
        :rtype: BookDetails
        """
        found = {
            name: value
            for name in fields_dict(MetadataSuggestion)
            if name != TITLE_FIELD and (value := getattr(suggestion, name)) and not getattr(self, name)
        }
        return evolve(self, **found)

    @property
    def primary_author(self) -> str:
        """The first author, else the first contributor of any role, else an empty string."""
        for person in self.contributors:
            if person.role is ContributorRole.AUTHOR:
                return person.name
        return self.contributors[0].name if self.contributors else ''


@frozen(kw_only=True)
class MetadataSuggestion:
    """Description fields found in the metadata of a source file; an empty string or list means nothing was found.

    Only the fields that file metadata can give are here, with the types they have in ``BookDetails``. A year comes
    only from metadata that states one, never from the date a file was created or changed.

    :ivar title: Title found in the source.
    :ivar contributors: People found in the source, each with the role the metadata gives them.
    :ivar publisher: Publisher found in the source.
    :ivar publication_year: Year of publication found in the source.
    :ivar languages: ISO 639-3 codes of the languages found in the source.
    :ivar identifiers: Valid ISBNs and web addresses found in the source.
    :ivar subjects: Topics found in the source.
    """

    title: str = ''
    contributors: tuple[Contributor, ...] = ()
    publisher: str = ''
    publication_year: str = ''
    languages: tuple[str, ...] = ()
    identifiers: tuple[BookIdentifier, ...] = ()
    subjects: tuple[str, ...] = ()


@frozen(kw_only=True)
class UploadPath:
    """The relative path of an uploaded file inside the folder the user chose, checked to stay inside it.

    A browser sends ``vol1/001.tif`` for a file of a chosen directory and the bare name for a file chosen alone, and a
    client of the API may send anything, so the path is parsed before any file is written. It is always relative,
    separated by ``/``, and has no empty, ``.`` or ``..`` segment and no drive letter.

    :ivar segments: The folders of the path from the chosen directory down, then the file name last.
    """

    segments: tuple[str, ...] = field(validator=validators.min_len(1))

    @classmethod
    def parse(cls, raw: str) -> Self:
        """Check the path a client sent for a file and return it with ``/`` between its segments.

        :param raw: Path as sent, with a slash or a backslash between segments, or an empty string for no name.
        :type raw: str
        :returns: The checked path.
        :rtype: Self
        :raises UploadRejectedError: With ``EMPTY_NAME`` if the file name is empty, ``.`` or ``..``, and with
                                     ``UNSAFE_PATH`` if the path is absolute, starts with a drive letter, holds an
                                     empty, ``.`` or ``..`` folder segment or holds a control character.
        """
        segments = raw.replace('\\', '/').split('/')
        if segments[-1] in NAMELESS_SEGMENTS:
            raise UploadRejectedError(UploadProblem.EMPTY_NAME)
        if (
            DRIVE_LETTER.match(segments[0])
            or any(segment in NAMELESS_SEGMENTS for segment in segments)
            or CONTROL_CHARACTERS.search(raw)
        ):
            raise UploadRejectedError(UploadProblem.UNSAFE_PATH)
        return cls(segments=tuple(segments))

    @property
    def name(self) -> str:
        """The file name, the last segment, under which the file is stored in its source's directory."""
        return self.segments[-1]

    @property
    def folders(self) -> tuple[str, ...]:
        """The path of every folder the file lies in, from the outermost down, each as a ``/`` separated path."""
        return tuple('/'.join(self.segments[:end]) for end in range(1, len(self.segments)))

    @override
    def __str__(self) -> str:
        """Return the path with ``/`` between its segments.

        :returns: The path as it is shown and stored.
        :rtype: str
        """
        return '/'.join(self.segments)


@frozen(kw_only=True)
class SourceFile:
    """One file of a source as uploaded, whether still staged or stored in the source's directory.

    :ivar name: Relative path of the file in the upload, as ``UploadPath`` writes it; the source's directory stores
                the file under the last segment alone.
    :ivar size_bytes: Size of the file in bytes.
    :ivar sha256: SHA-256 digest of the file's content as lower-case hexadecimal digits.
    """

    name: str = field(validator=validators.min_len(1))
    size_bytes: int = field(validator=validators.ge(0))
    sha256: str = field(validator=validators.matches_re(SHA256_PATTERN))


@frozen(kw_only=True)
class ScanFacts:
    """Technical facts of one scan, the image a source holds; the file holding it is known from the source.

    :ivar width_px: Width of the image in pixels.
    :ivar height_px: Height of the image in pixels.
    :ivar color_mode: Whether the image is bilevel, gray or colour.
    :ivar dpi_x: Horizontal resolution in dots per inch, or None when the source does not record it.
    :ivar dpi_y: Vertical resolution in dots per inch, or None when the source does not record it.
    :ivar bits_per_component: Bit depth of one colour component, or None when unknown.
    :ivar image_format: Human name of the image format, such as ``JPEG`` or ``TIFF``.
    :ivar width_mm: Physical width in millimetres, or None without a resolution.
    :ivar height_mm: Physical height in millimetres, or None without a resolution.
    :ivar has_text_layer: Whether the scan carries text, such as the OCR layer of a scanned PDF page.
    :ivar extra: Further facts under the keys of the inspector that reported them.
    """

    width_px: int = field(validator=validators.gt(0))
    height_px: int = field(validator=validators.gt(0))
    color_mode: ColorMode
    dpi_x: float | None = None
    dpi_y: float | None = None
    bits_per_component: int | None = None
    image_format: str = ''
    width_mm: float | None = None
    height_mm: float | None = None
    has_text_layer: bool = False
    extra: MetadataMap = field(factory=dict)

    def as_data(self) -> dict[str, Any]:
        """Return the facts a processor that splits a scan reads, as the input data of its step.

        :returns: The size, the colour mode as its value, and the resolution when the source records one.
        :rtype: dict[str, Any]
        """
        data: dict[str, Any] = {
            VersionData.WIDTH_PX: self.width_px,
            VersionData.HEIGHT_PX: self.height_px,
            COLOR_MODE_KEY: self.color_mode.value,
        }
        if dpi := max(filter(None, (self.dpi_x, self.dpi_y)), default=None):
            data[VersionData.DPI] = dpi
        return data


@frozen(kw_only=True)
class Renditions:
    """State of the derived files of a scan or a page version: full image, preview, thumbnail and tile pyramid.

    A scan whose files are cut again gets the next version, so the keys of the new files differ from the old ones and
    no cache serves a stale image. A page version is written once into the directory of its own identifier, so its
    renditions stay at the first version.

    The format of ``full`` is chosen when the image is written, from its colour and the project's ``ImagePolicy``, and
    kept here. It is not computed from the policy when read, because a later change of the policy must not change the
    storage key of an image already stored. A page version copies the format of the scan it is cut from.

    :ivar ready: Whether every derived file of the current version is published.
    :ivar version: Version of the derived files, part of their storage keys.
    :ivar full: Format of the ``full`` image, ``Rendition.FULL_JPEG`` or ``Rendition.FULL_PNG``.
    """

    FIRST_VERSION: ClassVar[int] = 1
    FULL_FORMATS: ClassVar[frozenset[Rendition]] = frozenset({Rendition.FULL_JPEG, Rendition.FULL_PNG})

    ready: bool = False
    version: int = field(default=FIRST_VERSION, validator=validators.ge(FIRST_VERSION))
    full: Rendition = field(default=Rendition.FULL_JPEG, validator=validators.in_(FULL_FORMATS))


@frozen(kw_only=True)
class RenditionInfo:
    """What a writer of renditions made of an image: its size and the format of its ``full`` file.

    :ivar width_px: Width of the image in pixels.
    :ivar height_px: Height of the image in pixels.
    :ivar full: Format the ``full`` image was written in.
    """

    width_px: int = field(validator=validators.gt(0))
    height_px: int = field(validator=validators.gt(0))
    full: Rendition = field(validator=validators.in_(Renditions.FULL_FORMATS))


@frozen(kw_only=True)
class ProcessorRef:
    """The processor that made a page version, by key and version.

    :ivar key: Key of the processor, such as ``geometry.deskew``, which also names its storage directory.
    :ivar version: Version of the processor, such as ``1.2``; a new version computes new page versions.
    """

    key: str = field(validator=validators.min_len(1))
    version: str = field(validator=validators.min_len(1))


@frozen(kw_only=True)
class Step:
    """One step of a recipe: a processor, the parameters it runs with and the pages it runs on.

    The processor key is not unique in a recipe, since a processor may be added twice with other parameters or another
    condition, so the step has an identifier of its own. A manual edit belongs to it, and it stays as the step is moved,
    saved, copied into a variant or kept in a profile.

    :ivar processor_key: Key of the processor, such as ``geometry.deskew``.
    :ivar params: Parameters of the step, following the processor's JSON Schema.
    :ivar enabled: Whether a run and a preview run the step. A step that is off stays in the recipe with its parameters,
                   so switching it on again loses nothing.
    :ivar step_id: Identifier of the step, made when the step is added.
    :ivar applies_to: Which pages the step processes. A page that does not meet the condition passes the step as it is.
    """

    processor_key: str = field(validator=validators.min_len(1))
    params: MetadataMap = field(factory=dict)
    enabled: bool = True
    step_id: StepId = field(factory=lambda: StepId(uuid4()))
    applies_to: AppliesTo = AppliesTo.ALL

    def to_map(self) -> dict[str, Any]:
        """Return the step as the JSON object a recipe and the parameters of a job store.

        :returns: The processor key, the parameters, whether the step is on, its identifier and its condition.
        :rtype: dict[str, Any]
        """
        return {
            StepField.PROCESSOR_KEY: self.processor_key,
            StepField.PARAMS: dict(self.params),
            StepField.ENABLED: self.enabled,
            StepField.STEP_ID: str(self.step_id),
            StepField.APPLIES_TO: self.applies_to.value,
        }

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the step from the JSON object ``to_map`` wrote.

        A step stored before the switch existed has no ``enabled`` key and is on, as every step was. One stored before
        the identifier and the condition existed has no ``step_id`` and gets a new one, and has no condition and is
        processing every page. The migration of the recipes gives the stored steps their identifiers, so only the
        parameters of a job queued before it can lack one.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The step.
        :rtype: Self
        :raises KeyError: If the object has no processor key or no parameters.
        :raises ValueError: If the identifier or the condition is not valid.
        """
        step_id = stored.get(StepField.STEP_ID)
        return cls(
            processor_key=stored[StepField.PROCESSOR_KEY],
            params=stored[StepField.PARAMS],
            enabled=stored.get(StepField.ENABLED, True),
            step_id=StepId(uuid4() if step_id is None else UUID(step_id)),
            applies_to=AppliesTo(stored.get(StepField.APPLIES_TO, AppliesTo.ALL)),
        )


@frozen
class PageStageKey:
    """The key of the record of a stage of a page, which a repository takes as one value.

    :ivar page_id: Page the stage belongs to.
    :ivar stage: The stage.
    """

    page_id: PageId
    stage: Stage


@frozen
class RecipeKey:
    """The address of a recipe: the stage it belongs to and its identifier, which a use case takes as one value.

    :ivar stage: The stage of the recipe.
    :ivar recipe_id: Identifier of the recipe.
    """

    stage: Stage
    recipe_id: RecipeId


@frozen
class PageEditKey:
    """The key of a manual edit: the input of a step of a recipe on a page.

    :ivar page_id: Page the edit belongs to.
    :ivar stage: Stage of the step reading the edit.
    :ivar step_id: The step reading the edit, which tells two steps of one processor apart.
    """

    page_id: PageId
    stage: Stage
    step_id: StepId


@frozen
class BookPlaceKey:
    """The key of the place of a book, which a repository takes as one value: the account that reads it and the book.

    :ivar account_id: Account the place belongs to.
    :ivar project_id: Book the place is in.
    """

    account_id: AccountId
    project_id: ProjectId


@frozen(kw_only=True)
class CanvasPosition:
    """Where the canvas looks at a view, in terms that do not depend on the size of the window.

    The zoom is a multiple of the zoom that fits the whole view into the canvas, so 1 is the fitted view and 2 shows
    half of it across. The centre is a point of the view in page heights, measured from its top left corner, so the
    same point is the centre on a phone and on a wide screen.

    :ivar zoom: Multiple of the zoom that fits the view, above zero.
    :ivar centre_x: Horizontal coordinate of the centre in page heights.
    :ivar centre_y: Vertical coordinate of the centre in page heights.
    """

    zoom: float = field(validator=validators.gt(0))
    centre_x: float
    centre_y: float


@frozen(kw_only=True)
class NewBookPlace:
    """Where a reader left a book, as the reader reports it; the account, the book and the time are added on storing.

    A workspace place names a stage and the page, or on the stages that work on files the file and the scan, the
    reader had open. A reading place names the page of the viewer, and keeps the stage the reader was working on, so
    the way back from reading is known. Pages, scans and files are kept by identifier and are not checked, since the
    reader may delete one afterwards and the place then falls back to the nearest view that still exists.

    :ivar mode: Whether the reader was working on a stage or reading.
    :ivar stage: Stage the reader was on, or last was on before reading.
    :ivar page_id: Page that was open, or None for the first page.
    :ivar scan_id: Scan that was open on the stages that work on scans, or None.
    :ivar source_id: File that was chosen on the stages that work on files, or None.
    :ivar view: How the canvas laid the pages out; the spread is the viewer's two-page spread as well.
    :ivar compare: How the result of the stage was compared with the one before it.
    :ivar filter: Which pages the strip or the grid listed.
    :ivar canvas: Zoom and centre of the canvas, or None for the fitted view.
    :ivar strip_page_id: First page in sight in the strip or the grid, or None for the top.
    """

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


@frozen(kw_only=True)
class PageAnchor:
    """A place in the book named by a page and the side of it, where moved or new pages are put.

    :ivar page_id: Page the place is next to.
    :ivar side: Whether the place lies before or after that page.
    """

    page_id: PageId
    side: Side


@frozen(kw_only=True)
class PageNumbering:
    """How to number a range of pages, which makes a pagination section of the book and is not stored itself.

    The range runs from one page to another in the order of the book. Numbering it makes a section of the main flow
    that starts at the first page, a series of the kinds in ``skip_kinds`` that does not count, and, when the book goes
    on after the last page, a section after the range that does not count either. Pages kept out of the book, and pages
    of the skipped kinds, take no number and keep their label, since the plates of an old book are usually not counted.

    :ivar first_page_id: First page of the range.
    :ivar last_page_id: Last page of the range, which may be the first page but not stand before it.
    :ivar style: How the numbers are written; ``none`` erases the labels of the range.
    :ivar start: Number of the first numbered page, from 1.
    :ivar bracketed: Whether the pages are counted and not printed, so the label is enclosed in square brackets, as a
                     bibliographer marks a number that is not printed in the book.
    :ivar skip_kinds: Kinds of page that are not numbered.
    """

    first_page_id: PageId
    last_page_id: PageId
    style: LabelStyle
    start: int = field(default=1, validator=validators.ge(1))
    bracketed: bool = False
    skip_kinds: frozenset[PageKind] = frozenset()

    def __attrs_post_init__(self) -> None:
        """Check that the first number can be written in the style.

        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        self.style.write(self.start)


@frozen(kw_only=True)
class PaginationSectionDraft:
    """What the user states of a pagination section, to make a section or to replace the rule of one.

    :ivar first_page_id: The page the section starts at.
    :ivar name: Name the user sees.
    :ivar style: How the numbers are written.
    :ivar start: Number of the first counted page, from 1.
    :ivar prefix: Text written before every number.
    :ivar display: Whether the pages count, and whether their numbers are printed or only implied.
    :ivar kinds: Kinds of page a series by kind takes; empty for a section of the main flow.
    """

    first_page_id: PageId
    name: str = ''
    style: LabelStyle
    start: int = field(default=1, validator=validators.ge(1))
    prefix: str = ''
    display: NumberDisplay = NumberDisplay.PRINTED
    kinds: frozenset[PageKind] = frozenset()

    def __attrs_post_init__(self) -> None:
        """Check that the first number can be written in the style.

        :raises ValueError: If the style is Roman and the first number is above 3999.
        """
        self.style.write(self.start)


@frozen(kw_only=True)
class NumberedPage:
    """A page and the label a numbering would give it, which a preview shows before anything is written.

    :ivar page_id: The page.
    :ivar label: The label the page would have, empty for the numbering style ``none``.
    """

    page_id: PageId
    label: str


@frozen(kw_only=True)
class PageSize:
    """The size of a page image in pixels and the resolution it was made at, as a base version records them.

    The median of these over the pages of a book gives the size of a generated blank leaf, so the leaf stands level
    with its neighbours in a spread.

    :ivar width_px: Width of the image in pixels.
    :ivar height_px: Height of the image in pixels.
    :ivar dpi: Resolution in dots per inch, or None when the page has none recorded.
    """

    width_px: int = field(validator=validators.gt(0))
    height_px: int = field(validator=validators.gt(0))
    dpi: float | None = field(default=None, validator=validators.optional(validators.gt(0)))

    @classmethod
    def from_data(cls, data: MetadataMap) -> Self | None:
        """Read the size a base version recorded in its data.

        :param data: Data of a page version.
        :type data: MetadataMap
        :returns: The size, or None when the data holds no usable width and height.
        :rtype: Self | None
        """
        width, height, dpi = (data.get(key) for key in (VersionData.WIDTH_PX, VersionData.HEIGHT_PX, VersionData.DPI))
        if not (isinstance(width, int) and isinstance(height, int) and width > 0 and height > 0):
            return None
        return cls(width_px=width, height_px=height, dpi=dpi if isinstance(dpi, int | float) and dpi > 0 else None)

    def as_data(self) -> dict[str, Any]:
        """Return the size as the data of a base version, which leaves out an unknown resolution.

        :returns: The width and height, and the resolution when it is known.
        :rtype: dict[str, Any]
        """
        data: dict[str, Any] = {VersionData.WIDTH_PX: self.width_px, VersionData.HEIGHT_PX: self.height_px}
        if self.dpi is not None:
            data[VersionData.DPI] = self.dpi
        return data


@frozen(kw_only=True)
class NewPage:
    """A page the user adds to the book without a scan: a blank leaf or a placeholder.

    :ivar origin: Whether the page is a generated blank leaf or a placeholder that waits for a scan.
    :ivar kind: Role of the page in the book.
    :ivar label: Printed number of the page, or empty.
    :ivar notes: Notes of the user.
    :ivar anchor: Place the page is put at, or None for the end of the book.
    :ivar size: Size of a blank leaf, or None for the median size of the book's pages; never given for a placeholder.
    """

    origin: NewPageOrigin
    kind: PageKind
    label: str = ''
    notes: str = ''
    anchor: PageAnchor | None = None
    size: PageSize | None = None

    def __attrs_post_init__(self) -> None:
        """Check that only a blank leaf has a size.

        :raises ValueError: If a placeholder is given a size, since it has no image.
        """
        if self.size is not None and self.origin is not NewPageOrigin.BLANK:
            err_msg = f'A {self.origin.label.lower()} has no image, so it has no size.'
            raise ValueError(err_msg)


@frozen(kw_only=True)
class UploadedSource:
    """The staged files of an upload that make one source, as the source inspector groups them.

    :ivar kind: Kind of the source, which selects the format that reads it.
    :ivar file_type: Exact format of the main file.
    :ivar names: Names of the staged files, the main file first; more than one only for an indirect DjVu document.
    """

    kind: SourceKind
    file_type: FileType
    names: Sequence[str] = field(validator=validators.min_len(1))


@frozen(kw_only=True)
class SourceAnalysis:
    """Everything an inspector learned from one source.

    :ivar kind: Kind of the source that was inspected.
    :ivar scans: Facts of every scan in the order of the source, which gives the scans their numbers.
    :ivar scan_labels: Page label the file gives each scan, such as the PDF page label ``xii``, aligned with ``scans``,
                       or empty for a format that carries no labels.
    :ivar file_metadata: Technical metadata of the source's format, such as the document information of a PDF.
    :ivar suggestion: Description fields found in the source, offered to fill empty book details.
    """

    kind: SourceKind
    scans: Sequence[ScanFacts]
    scan_labels: Sequence[str] = ()
    file_metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)

    def __attrs_post_init__(self) -> None:
        """Check that the labels, when a format gives any, are one per scan.

        :raises ValueError: If there are labels, but not as many as scans.
        """
        if self.scan_labels and len(self.scan_labels) != len(self.scans):
            err_msg = f'{len(self.scan_labels)} page labels do not fit {len(self.scans)} scans.'
            raise ValueError(err_msg)

    def label_of(self, number: int) -> str:
        """Return the page label the file gives a scan.

        :param number: Position of the scan in the source, starting at 0.
        :type number: int
        :returns: The label, or an empty string for a format without labels or a scan without one.
        :rtype: str
        """
        return self.scan_labels[number] if self.scan_labels else ''


@frozen(kw_only=True)
class ImportRequest:
    """The files an import job was asked to import, as they were staged.

    :ivar files: Name, size and SHA-256 digest of every staged file, in upload order.
    """

    files: Sequence[SourceFile]


@frozen(kw_only=True)
class RejectedFile:
    """A file of an upload that was not imported, with the reason shown to the user.

    :ivar file_name: Name of the staged file, or of the main file of its source.
    :ivar reason: Why the file was not imported.
    :ivar detail: Text for the user that says more than the reason, such as the name of the existing source.
    """

    file_name: str = field(validator=validators.min_len(1))
    reason: RejectionReason
    detail: str = ''


@frozen(kw_only=True)
class ImportResult:
    """What an import job did with the files of its upload.

    :ivar imported: Sources the job committed to the project, in book order.
    :ivar rejected: Files no check let through, each with its reason.
    :ivar skipped: Names of the files a cancelled job never reached, which no check rejected.
    """

    imported: Sequence[SourceId] = ()
    rejected: Sequence[RejectedFile] = ()
    skipped: Sequence[str] = ()


@frozen(kw_only=True)
class Progress:
    """How far a job has come.

    :ivar done: Steps completed.
    :ivar total: Steps in all, zero while unknown.
    """

    done: int = field(default=0, validator=validators.ge(0))
    total: int = field(default=0, validator=validators.ge(0))

    @property
    def fraction(self) -> float:
        """The completed share between 0 and 1, zero while the total is unknown."""
        return self.done / self.total if self.total else 0.0


@frozen(kw_only=True)
class SliceRequest:
    """Which part of a collection to read.

    :ivar offset: Number of items to skip from the start.
    :ivar limit: Largest number of items to return.
    """

    offset: int = field(default=0, validator=validators.ge(0))
    limit: int = field(default=50, validator=validators.gt(0))


@frozen(kw_only=True)
class Slice[ItemT]:
    """A part of a collection together with the size of the whole collection.

    :ivar items: Items of the requested part, in collection order.
    :ivar total: Number of items in the whole collection.
    """

    items: Sequence[ItemT]
    total: int = field(validator=validators.ge(0))


@frozen(kw_only=True)
class MailMessage:
    """A plain-text message to one recipient.

    :ivar to: Email address of the recipient.
    :ivar subject: Subject line.
    :ivar body: Plain-text body.
    """

    to: str
    subject: str
    body: str


@frozen(kw_only=True)
class ProcessorSpec:
    """What a processor says about itself, which the catalogue lists without running it.

    The spec is a plain value declared on the processor's class, so the API server lists every processor without
    importing what a processor needs to run.

    :ivar key: Key of the processor, ``<stage>.<name>`` or ``split.<name>``, such as ``geometry.deskew``.
    :ivar version: Version of the algorithm, which joins the identifier of the page versions it makes.
    :ivar title: Name the interface shows.
    :ivar stage: Stage whose recipe the step may be put into.
    :ivar scope: Whether the step makes one output for the page or one for each part of a scan.
    :ivar outputs: What the step writes.
    :ivar parameters: JSON Schema of the parameters, from which the interface builds the settings form.
    :ivar editor: Editor of the manual edit the step reads.
    :ivar pool: Class of worker the step runs on.
    :ivar by_page_side: Whether the step reads the side of the book its page lies on, so a page that moves to the other
                        side is made again.
    """

    key: str = field(validator=validators.min_len(1))
    version: str = field(validator=validators.min_len(1))
    title: str = field(validator=validators.min_len(1))
    stage: Stage
    scope: ProcessorScope = ProcessorScope.PAGE
    outputs: frozenset[VersionOutput] = frozenset({VersionOutput.IMAGE})
    parameters: MetadataMap = field(factory=dict)
    editor: EditorKind = EditorKind.NONE
    pool: WorkerPool = WorkerPool.CPU
    by_page_side: bool = False

    @property
    def ref(self) -> ProcessorRef:
        """The key and the version of the processor, as a page version records them."""
        return ProcessorRef(key=self.key, version=self.version)


def _params_error(kind: JobKind, error: Exception) -> InvalidParametersError:
    """Build the error of the parameters of a job that its value class cannot read.

    :param kind: Kind of the job whose parameters were read.
    :type kind: JobKind
    :param error: The error the reading raised.
    :type error: Exception
    :returns: The error to raise.
    :rtype: InvalidParametersError
    """
    err_msg = f'The parameters of a {kind} job are not valid: {error}.'
    return InvalidParametersError(err_msg)


@frozen(kw_only=True)
class NewPageEdit:
    """A manual edit as the user sends it, before it is stored with its hash.

    :ivar kind: Editor that made the edit.
    :ivar geometry: The shape the user drew, or None for an edit that is only a mask.
    """

    kind: EditorKind
    geometry: EditGeometry | None = None

    def __attrs_post_init__(self) -> None:
        """Check that the shape is the one the editor draws.

        :raises ValueError: If the shape belongs to another editor.
        """
        if self.geometry is not None and self.geometry.editor is not self.kind:
            err_msg = f'The {self.kind.label.lower()} editor does not draw a {self.geometry.editor.label.lower()}.'
            raise ValueError(err_msg)


@frozen(kw_only=True)
class VersionFilter:
    """Which versions of a page a listing returns.

    :ivar stage: Stage whose versions are listed, or None for every stage.
    :ivar scale: Scale of the runs listed, or None for both full runs and previews.
    """

    stage: Stage | None = None
    scale: VersionScale | None = None


@frozen(kw_only=True)
class StageRun:
    """What a ``run-stage`` job runs: a stage over some pages by a recipe.

    :ivar stage: Stage to run.
    :ivar recipe_id: Recipe to run it by, or None for the active recipe of the stage.
    :ivar page_ids: Pages to run it on, or None for every page with an image.
    :ivar confirm_unsplit: Whether the user confirmed that a page split that is undone deletes the right half of a
                           spread, which a run that would do so refuses without it.
    :ivar pin: Whether the recipe is pinned to the pages of the run, so that a later run without a recipe keeps it. A
               run without a recipe chooses the recipe of each page and pins nothing.
    :ivar through_step: Index in the recipe of the last step to run, or None to run through the last step that is on.
                        The steps before it are found in the cache of versions when their inputs did not change.
    :ivar remake: Version whose files a collection removed, which the run makes again by the steps its chain stored
                  instead of by a recipe, over the one page named, and makes current. It names no recipe.
    """

    stage: Stage
    recipe_id: RecipeId | None = None
    page_ids: tuple[PageId, ...] | None = None
    confirm_unsplit: bool = False
    pin: bool = False
    through_step: int | None = field(default=None, validator=validators.optional(validators.ge(0)))
    remake: PageVersionId | None = None

    def __attrs_post_init__(self) -> None:
        """Check that only a run by a recipe pins, and that a run that makes a version again names one page.

        :raises ValueError: If a run without a recipe asks to pin, or a run that makes a version again names a recipe
                            or not exactly one page.
        """
        if self.pin and self.recipe_id is None:
            raise ValueError(PIN_NEEDS_RECIPE)
        if self.remake is not None and (self.recipe_id is not None or self.page_ids is None or len(self.page_ids) != 1):
            raise ValueError(REMAKE_ONE_PAGE)

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The stage, the recipe, the pages as text, the confirmation, the pin and the last step.
        :rtype: dict[str, Any]
        """
        return {
            'stage': self.stage.value,
            'recipe_id': None if self.recipe_id is None else str(self.recipe_id),
            'page_ids': None if self.page_ids is None else [str(page_id) for page_id in self.page_ids],
            'confirm_unsplit': self.confirm_unsplit,
            'pin': self.pin,
            THROUGH_STEP_KEY: self.through_step,
            REMAKE_KEY: self.remake,
        }

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``run-stage`` job.
        """
        try:
            page_ids = stored['page_ids']
            return cls(
                stage=Stage(stored['stage']),
                recipe_id=None if stored['recipe_id'] is None else RecipeId(UUID(stored['recipe_id'])),
                page_ids=None if page_ids is None else tuple(PageId(UUID(page_id)) for page_id in page_ids),
                confirm_unsplit=bool(stored.get('confirm_unsplit', False)),
                pin=bool(stored.get('pin', False)),
                through_step=stored.get(THROUGH_STEP_KEY),
                remake=None if stored.get(REMAKE_KEY) is None else PageVersionId(stored[REMAKE_KEY]),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.RUN_STAGE, error) from error


@frozen(kw_only=True)
class StepPreview:
    """What a ``preview-step`` job previews: the steps of a form on one page, up to one of them.

    :ivar page_id: Page to preview on.
    :ivar stage: Stage of the steps.
    :ivar steps: The steps as the form has them, with parameters not yet saved in a recipe.
    :ivar step_index: Index of the last step whose result is wanted; the steps before it run first.
    """

    page_id: PageId
    stage: Stage
    steps: tuple[Step, ...] = field(validator=validators.min_len(1))
    step_index: int = field(validator=validators.ge(0))

    def __attrs_post_init__(self) -> None:
        """Check that the index names one of the steps.

        :raises ValueError: If the index is past the last step.
        """
        if self.step_index >= len(self.steps):
            err_msg = f'The step index {self.step_index} is past the {len(self.steps)} steps of the preview.'
            raise ValueError(err_msg)

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The page, the stage, the steps and the index.
        :rtype: dict[str, Any]
        """
        return {
            'page_id': str(self.page_id),
            'stage': self.stage.value,
            'steps': [step.to_map() for step in self.steps],
            'step_index': self.step_index,
        }

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``preview-step`` job.
        """
        try:
            return cls(
                page_id=PageId(UUID(stored['page_id'])),
                stage=Stage(stored['stage']),
                steps=tuple(Step.from_map(step) for step in stored['steps']),
                step_index=stored['step_index'],
            )
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.PREVIEW_STEP, error) from error


@frozen(kw_only=True)
class TileCut:
    """What a ``cut-tiles`` job cuts: the tile pyramids of some versions.

    :ivar version_ids: Versions to cut.
    """

    version_ids: tuple[PageVersionId, ...] = field(validator=validators.min_len(1))

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The identifiers of the versions.
        :rtype: dict[str, Any]
        """
        return {'version_ids': list(self.version_ids)}

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``cut-tiles`` job.
        """
        try:
            return cls(version_ids=tuple(PageVersionId(version_id) for version_id in stored['version_ids']))
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.CUT_TILES, error) from error


@frozen(kw_only=True)
class VersionCollection:
    """What a ``collect-versions`` job deletes: the versions older than two moments that nothing needs.

    :ivar older_than: Full runs created before this moment may be deleted.
    :ivar previews_older_than: Previews created before this moment may be deleted.
    """

    older_than: datetime
    previews_older_than: datetime

    def to_map(self) -> dict[str, Any]:
        """Return the value as the JSON object a job stores.

        :returns: The two moments as ISO 8601 text.
        :rtype: dict[str, Any]
        """
        return {
            'older_than': self.older_than.isoformat(),
            'previews_older_than': self.previews_older_than.isoformat(),
        }

    @classmethod
    def from_map(cls, stored: MetadataMap) -> Self:
        """Read the value from the JSON object ``to_map`` wrote.

        :param stored: The stored object.
        :type stored: MetadataMap
        :returns: The value.
        :rtype: Self
        :raises InvalidParametersError: If the object is not one of a ``collect-versions`` job.
        """
        try:
            return cls(
                older_than=datetime.fromisoformat(stored['older_than']),
                previews_older_than=datetime.fromisoformat(stored['previews_older_than']),
            )
        except (KeyError, ValueError, TypeError) as error:
            raise _params_error(JobKind.COLLECT_VERSIONS, error) from error
