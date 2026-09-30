"""Immutable value objects of the domain."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar

from attrs import field, frozen, validators

from bookreviver.domain.enums import Orthography, TransformKind

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.enums import ColorMode, FileType, RejectionReason, SourceKind
    from bookreviver.domain.ids import SourceId, StorageKey

# JSON-compatible metadata as read from a source file
type MetadataMap = Mapping[str, Any]
# A SHA-256 digest as lower-case hexadecimal digits
SHA256_PATTERN: str = r'[0-9a-f]{64}'


@frozen(kw_only=True)
class BookDetails:
    """Bibliographic description of a printed book.

    :ivar title: Title of the book, the one field that may not be empty.
    :ivar authors: Authors as printed, in one string.
    :ivar publisher: Publisher or printing house.
    :ivar publication_place: City of publication.
    :ivar publication_year: Year of publication as printed, which may be a range or an estimate.
    :ivar edition: Edition statement.
    :ivar series: Series the book belongs to.
    :ivar volume: Volume or part within a multi-volume work.
    :ivar language: Language of the text.
    :ivar orthography: Spelling system of the print, such as pre-reform Russian.
    :ivar notes: Free-form notes of the owner.
    """

    title: str = field(validator=validators.min_len(1))
    authors: str = ''
    publisher: str = ''
    publication_place: str = ''
    publication_year: str = ''
    edition: str = ''
    series: str = ''
    volume: str = ''
    language: str = ''
    orthography: Orthography = Orthography.UNKNOWN
    notes: str = ''


@frozen(kw_only=True)
class MetadataSuggestion:
    """Description fields found in a source; an empty string means nothing was found.

    :ivar title: Title found in the source.
    :ivar authors: Authors found in the source.
    :ivar publisher: Publisher found in the source.
    :ivar publication_year: Year of publication found in the source.
    :ivar language: Language found in the source.
    """

    title: str = ''
    authors: str = ''
    publisher: str = ''
    publication_year: str = ''
    language: str = ''


@frozen(kw_only=True)
class SourceFile:
    """One file of a source as uploaded, whether still staged or stored in the source's directory.

    :ivar name: Name of the file inside the source's directory.
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

    :ivar ready: Whether every derived file of the current version is published.
    :ivar version: Version of the derived files, part of their storage keys.
    """

    FIRST_VERSION: ClassVar[int] = 1

    ready: bool = False
    version: int = field(default=FIRST_VERSION, validator=validators.ge(FIRST_VERSION))


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
    :ivar file_metadata: Technical metadata of the source's format, such as the document information of a PDF.
    :ivar suggestion: Description fields found in the source, offered to fill empty book details.
    """

    kind: SourceKind
    scans: Sequence[ScanFacts]
    file_metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)


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
