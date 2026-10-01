"""Immutable value objects of the domain."""

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Self, override

from attrs import evolve, field, fields_dict, frozen, validators

from bookreviver.domain.enums import (
    ContributorRole,
    NewPageOrigin,
    Orthography,
    Rendition,
    RightsStatus,
    Script,
    TransformKind,
    UploadProblem,
    VersionData,
)
from bookreviver.domain.errors import InvalidIdentifierError, UploadRejectedError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from attrs import Attribute

    from bookreviver.domain.enums import (
        ColorMode,
        FileType,
        IdentifierScheme,
        LabelStyle,
        PageKind,
        RejectionReason,
        Side,
        SourceKind,
    )
    from bookreviver.domain.ids import PageId, SourceId, StorageKey

# JSON-compatible metadata as read from a source file
type MetadataMap = Mapping[str, Any]
# The description field a suggestion never fills
TITLE_FIELD: str = 'title'
# A SHA-256 digest as lower-case hexadecimal digits
SHA256_PATTERN: str = r'[0-9a-f]{64}'
# Segments of an uploaded path that do not name a file or a folder
NAMELESS_SEGMENTS: frozenset[str] = frozenset({'', '.', '..'})
# A Windows drive letter opening a path, as in ``C:\scans`` or ``C:scans``
DRIVE_LETTER: re.Pattern[str] = re.compile(r'[A-Za-z]:')
CONTROL_CHARACTERS: re.Pattern[str] = re.compile(r'[\x00-\x1f\x7f]')


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
class ProcessorRef:
    """The processor that made a page version, by key and version.

    :ivar key: Key of the processor, such as ``geometry.deskew``, which also names its storage directory.
    :ivar version: Version of the processor, such as ``1.2``; a new version computes new page versions.
    """

    key: str = field(validator=validators.min_len(1))
    version: str = field(validator=validators.min_len(1))


@frozen(kw_only=True)
class Point:
    """A point in the pixel coordinates of an image, whose origin is its top left corner.

    :ivar x: Distance from the left edge in pixels.
    :ivar y: Distance from the top edge in pixels.
    """

    x: float
    y: float


@frozen(kw_only=True)
class Quad:
    """A quadrilateral in the pixel coordinates of an image, such as a half of a spread or a skewed page.

    :ivar top_left: Corner at the top left of the area.
    :ivar top_right: Corner at the top right of the area.
    :ivar bottom_right: Corner at the bottom right of the area.
    :ivar bottom_left: Corner at the bottom left of the area.
    """

    top_left: Point
    top_right: Point
    bottom_right: Point
    bottom_left: Point


@frozen(kw_only=True)
class Transform:
    """The transform of coordinates a processing step applies from its input image to its output image.

    The chain of transforms from a scan to any page version maps coordinates of the version back to the scan. Each kind
    takes its own argument: a crop and a perspective correction a quadrilateral, a rotation an angle, and a dewarping
    the key of its stored mesh. The identity takes none.

    :ivar kind: Kind of the transform.
    :ivar quad: Area of the input that becomes the output, for a crop or a perspective correction.
    :ivar angle: Angle of a rotation in degrees, counter-clockwise.
    :ivar mesh_key: Storage key of the mesh a dewarping follows.
    """

    ARGUMENTS: ClassVar[Mapping[TransformKind, frozenset[str]]] = {
        TransformKind.IDENTITY: frozenset(),
        TransformKind.CROP: frozenset({'quad'}),
        TransformKind.ROTATE: frozenset({'angle'}),
        TransformKind.PERSPECTIVE: frozenset({'quad'}),
        TransformKind.MESH: frozenset({'mesh_key'}),
    }

    kind: TransformKind = TransformKind.IDENTITY
    quad: Quad | None = None
    angle: float | None = None
    mesh_key: StorageKey | None = None

    def __attrs_post_init__(self) -> None:
        """Check that exactly the arguments of the kind are given.

        :raises ValueError: If an argument of the kind is missing or an argument of another kind is given.
        """
        every_argument = frozenset[str]().union(*self.ARGUMENTS.values())
        given = {name for name in every_argument if getattr(self, name) is not None}
        if given != (expected := self.ARGUMENTS[self.kind]):
            err_msg = f'A {self.kind} transform takes {sorted(expected) or "no arguments"}, not {sorted(given)}.'
            raise ValueError(err_msg)


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
    """How to write the printed numbers of a range of pages, which is applied once and not stored.

    The range runs from one page to another in the order of the book. Pages kept out of the book, and pages of the kinds
    in ``skip_kinds``, take no number and keep their label, since the plates of an old book are usually not counted.

    :ivar first_page_id: First page of the range.
    :ivar last_page_id: Last page of the range, which may be the first page but not stand before it.
    :ivar style: How the numbers are written; ``none`` erases the labels of the range.
    :ivar start: Number of the first numbered page, from 1.
    :ivar bracketed: Whether the label is enclosed in square brackets, as a bibliographer marks a number that is not
                     printed in the book.
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

    def label(self, number: int) -> str:
        """Write the label of the page that takes ``number``.

        :param number: Number of the page, counted from ``start``.
        :type number: int
        :returns: The number in the style of the numbering, in square brackets when ``bracketed``, and empty for the
                  style ``none``.
        :rtype: str
        :raises ValueError: If the style cannot write the number, such as 4000 in Roman numerals.
        """
        text = self.style.write(number)
        return f'[{text}]' if self.bracketed and text else text


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
