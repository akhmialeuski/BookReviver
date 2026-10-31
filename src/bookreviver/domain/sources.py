"""The sources of a book: the uploaded files, the scans they hold, and what an import makes of them."""

import re
from typing import TYPE_CHECKING, Any, Self, override

from attrs import field, frozen, validators

from bookreviver.domain.books import MetadataSuggestion
from bookreviver.domain.errors import UploadProblem, UploadRejectedError
from bookreviver.domain.images import Renditions
from bookreviver.domain.labels import LabeledStrEnum
from bookreviver.domain.versions import VersionData

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.ids import ScanId, SourceId
    from bookreviver.domain.images import ColorMode
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.page_label_rules import PageLabelRule


class SourceKind(LabeledStrEnum):
    """What one source of a book is, which selects the format that reads it."""

    PDF = 'pdf', 'PDF document'
    DJVU = 'djvu', 'DjVu document'
    IMAGE = 'image', 'Image file'


class SourceStatus(LabeledStrEnum):
    """What became of an uploaded file: it joined the book, an import refused it, or a cancelled import skipped it."""

    IMPORTED = 'imported', 'Imported'
    REJECTED = 'rejected', 'Rejected'
    SKIPPED = 'skipped', 'Skipped'


class DjvuDocumentKind(LabeledStrEnum):
    """How a DjVu file holds its pages, which decides how many sources it makes."""

    BUNDLED = 'bundled', 'Bundled document, every page in one file'
    INDIRECT = 'indirect', 'Indirect document, an index file with one file per page'
    SINGLE_PAGE = 'single-page', 'Single-page file'


class RejectionReason(LabeledStrEnum):
    """Why one file of an upload was not imported, while the other files of the upload were."""

    DUPLICATE = 'duplicate', 'The book already has this file.'
    UNREADABLE = 'unreadable', 'The file cannot be read as a source.'
    UNSUPPORTED_TYPE = 'unsupported-type', 'The type of the file is not accepted as a source.'
    SYSTEM_FILE = 'system-file', 'The file is a system file of the operating system, not a part of the book.'


class SystemFile(LabeledStrEnum):
    """A file an operating system adds to a folder, which a directory upload carries along but is not a book source.

    The value of a member is its name in lower case, and ``APPLE_DOUBLE`` is a prefix, since macOS writes the resource
    fork of ``001.tif`` as ``._001.tif`` and such a file has the suffix of the image it accompanies.
    """

    THUMBS_DB = 'thumbs.db', 'Windows thumbnail cache'
    DESKTOP_INI = 'desktop.ini', 'Windows folder settings'
    DS_STORE = '.ds_store', 'macOS folder settings'
    APPLE_DOUBLE = '._', 'macOS resource fork of another file'

    @classmethod
    def matches(cls, name: str) -> bool:
        """Tell whether a file is a system file, by the last segment of its name in any letter case.

        :param name: File name, or a relative path whose last segment is the file name, with a slash or a backslash
                     between segments.
        :type name: str
        :returns: Whether the name is one of the known system files, or starts with the prefix of one.
        :rtype: bool
        """
        folded = name.replace('\\', '/').rsplit('/', 1)[-1].casefold()
        return any(
            folded.startswith(member.value) if member is cls.APPLE_DOUBLE else folded == member.value for member in cls
        )


class FileType(LabeledStrEnum):
    """A file type accepted as a book source."""

    PDF = 'pdf', 'PDF'
    DJVU = 'djvu', 'DjVu'
    TIFF = 'tiff', 'TIFF'
    JPEG = 'jpeg', 'JPEG'
    JPEG_2000 = 'jpeg-2000', 'JPEG 2000'
    PNG = 'png', 'PNG'

    @property
    def suffixes(self) -> frozenset[str]:
        """File name suffixes of this type, lower case with the dot."""
        return FILE_TYPE_SUFFIXES[self]

    @property
    def source_kind(self) -> SourceKind:
        """The kind of source a file of this type makes."""
        match self:
            case FileType.PDF:
                return SourceKind.PDF
            case FileType.DJVU:
                return SourceKind.DJVU
            case _:
                return SourceKind.IMAGE

    @classmethod
    def from_name(cls, name: str) -> FileType | None:
        """Return the type of a file by its name, or None when the suffix is not accepted.

        :param name: File name, compared by its suffix in any letter case.
        :type name: str
        :returns: The accepted file type with this suffix, or None.
        :rtype: FileType | None
        """
        suffix = name[name.rfind('.') :].lower() if '.' in name else ''
        return next((file_type for file_type in cls if suffix in file_type.suffixes), None)


FILE_TYPE_SUFFIXES: dict[FileType, frozenset[str]] = {
    FileType.PDF: frozenset({'.pdf'}),
    FileType.DJVU: frozenset({'.djvu', '.djv'}),
    FileType.TIFF: frozenset({'.tif', '.tiff'}),
    FileType.JPEG: frozenset({'.jpg', '.jpeg'}),
    FileType.JPEG_2000: frozenset({'.jp2', '.j2k'}),
    FileType.PNG: frozenset({'.png'}),
}


# Key of the colour mode among the facts of a scan that a processor reads
COLOR_MODE_KEY: str = 'color_mode'


# A SHA-256 digest as lower-case hexadecimal digits
SHA256_PATTERN: str = r'[0-9a-f]{64}'
# Segments of an uploaded path that do not name a file or a folder
NAMELESS_SEGMENTS: frozenset[str] = frozenset({'', '.', '..'})
# A Windows drive letter opening a path, as in ``C:\scans`` or ``C:scans``
DRIVE_LETTER: re.Pattern[str] = re.compile(r'[A-Za-z]:')
CONTROL_CHARACTERS: re.Pattern[str] = re.compile(r'[\x00-\x1f\x7f]')


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
    :ivar label_rules: Page label rules the file defines, which become the pagination sections of the book, in the
                       order of the scans, or empty for a format that carries none.
    :ivar file_metadata: Technical metadata of the source's format, such as the document information of a PDF.
    :ivar suggestion: Description fields found in the source, offered to fill empty book details.
    """

    kind: SourceKind
    scans: Sequence[ScanFacts]
    scan_labels: Sequence[str] = ()
    label_rules: Sequence[PageLabelRule] = ()
    file_metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)

    def __attrs_post_init__(self) -> None:
        """Check that the labels, when a format gives any, are one per scan, and the rules start in order at scans.

        :raises ValueError: If there are labels, but not as many as scans, or the rules do not start at increasing
                            positions of the scans.
        """
        if self.scan_labels and len(self.scan_labels) != len(self.scans):
            err_msg = f'{len(self.scan_labels)} page labels do not fit {len(self.scans)} scans.'
            raise ValueError(err_msg)
        starts = [rule.first_index for rule in self.label_rules]
        if starts != sorted(set(starts)) or any(start >= len(self.scans) for start in starts):
            err_msg = f'Page label rules at {starts} do not start at increasing positions of {len(self.scans)} scans.'
            raise ValueError(err_msg)

    def label_of(self, number: int) -> str:
        """Return the page label the file gives a scan.

        :param number: Position of the scan in the source, starting at 0.
        :type number: int
        :returns: The label, or an empty string for a format without labels or a scan without one.
        :rtype: str
        """
        return self.scan_labels[number] if self.scan_labels else ''


IMPORTED_NEEDS_TYPE: str = 'An imported source has a kind and a file type.'
REJECTION_NEEDS_STATUS: str = 'A source has a reason of rejection when, and only when, it is rejected.'


@frozen(kw_only=True)
class Source:
    """One uploaded file of a book, or the files of one indirect DjVu document, never changed after its import.

    Every file of an upload is recorded as a source. An imported one is part of the book and holds its scans. A
    rejected one was refused by the import with a reason, and a skipped one was never reached by a cancelled import.
    Both are kept until the reader closes the report of the import, hold no scan and have no stored file.

    :ivar id: Identifier of the source, which also names its storage directory.
    :ivar kind: Kind of the source, which selects the format that reads it, or None for a rejected or skipped file whose
                type is unknown.
    :ivar file_type: Exact format of the main file, or None for a rejected or skipped file whose type is unknown.
    :ivar file_name: Name the browser sent, with the relative path of a directory upload sanitised.
    :ivar files: Staged files, the main file first; more than one only for an indirect DjVu document.
    :ivar size_bytes: Total size of the files in bytes.
    :ivar sha256: SHA-256 digest of the main file, which refuses a second upload of the same file to the book.
    :ivar scan_count: Number of scans the source holds.
    :ivar metadata: Technical metadata of the format, which stays here and is never copied into the book.
    :ivar suggestion: Values of the book description found in the file, which fill only empty description fields.
    :ivar status: Whether the file was imported, rejected or skipped.
    :ivar rejection: Why a rejected file was refused, or None for a file that was not.
    :ivar detail: Text for the reader that says more than the reason, such as the name of the source a duplicate
                  repeats, or empty.
    :ivar imported_at: When the file was recorded, which for an imported source is when it became part of the book.
    """

    id: SourceId
    kind: SourceKind | None
    file_type: FileType | None
    file_name: str = field(validator=validators.min_len(1))
    files: Sequence[SourceFile] = field(validator=validators.min_len(1))
    size_bytes: int = field(validator=validators.ge(0))
    sha256: str = field(validator=validators.matches_re(SHA256_PATTERN))
    scan_count: int = field(validator=validators.ge(0))
    metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)
    status: SourceStatus = SourceStatus.IMPORTED
    rejection: RejectionReason | None = None
    detail: str = ''
    imported_at: datetime

    def __attrs_post_init__(self) -> None:
        """Check that the status agrees with the fields it decides.

        :raises ValueError: If an imported source has no kind or no type, or only a rejected source has a reason, or a
                            rejected source has none.
        """
        if self.status is SourceStatus.IMPORTED and (self.kind is None or self.file_type is None):
            raise ValueError(IMPORTED_NEEDS_TYPE)
        if (self.status is SourceStatus.REJECTED) != (self.rejection is not None):
            raise ValueError(REJECTION_NEEDS_STATUS)


@frozen(kw_only=True)
class Scan:
    """One image of a source as the file holds it: a page of a PDF or DjVu document, or a frame of an image file.

    A scan is created by the import and never changes, apart from the state of its derived files.

    :ivar id: Identifier of the scan.
    :ivar source_id: Source holding the scan.
    :ivar number: Position of the scan in its source, starting at 0.
    :ivar source_label: Page label the file itself gives, such as the PDF page label ``xii``, or empty.
    :ivar facts: Technical facts of the image.
    :ivar renditions: Readiness and version of the derived files.
    """

    id: ScanId
    source_id: SourceId
    number: int = field(validator=validators.ge(0))
    source_label: str = ''
    facts: ScanFacts
    renditions: Renditions = field(factory=Renditions)
