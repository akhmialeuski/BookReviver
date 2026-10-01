"""Closed sets of values used across the application, each carrying a human label."""

import enum
import re
from itertools import cycle
from typing import Final, Self
from urllib.parse import urlsplit

from bookreviver.domain.errors import InvalidIdentifierError

# An ISBN without hyphens: ten characters whose last may be the check digit ``X``, or thirteen digits
ISBN_10: Final = re.compile(r'[0-9]{9}[0-9X]')
ISBN_13: Final = re.compile(r'[0-9]{13}')
ISBN_10_MODULUS: Final = 11
ISBN_13_MODULUS: Final = 10
# The check digit ``X`` of an ISBN-10 stands for ten
ISBN_10_X_VALUE: Final = 10
# Weights of the digits of an ISBN-13, which alternate
ISBN_13_WEIGHTS: Final = (1, 3)
# Digits the serial number of an LCCN is padded to after its hyphen
LCCN_SERIAL_LENGTH: Final = 6
# A normalized LCCN has up to four characters of prefix and year, and ends with eight digits
LCCN_NORMALIZED: Final = re.compile(r'[a-z0-9]{0,4}[0-9]{8}')
OCLC_NUMBER: Final = re.compile(r'[0-9]+')
# Longest shelfmark and longest URL of a copy the domain accepts
SHELFMARK_MAX_LENGTH: Final = 300
URL_MAX_LENGTH: Final = 2_048
URL_SCHEMES: Final = frozenset({'http', 'https'})


class LabeledStrEnum(enum.StrEnum):
    """A string enum whose members are declared as ``(value, label)`` pairs.

    :ivar label: Human-readable name of the member, shown in the interface and in error messages.
    """

    label: str

    def __new__(cls, value: str, label: str = '') -> Self:
        """Create a member whose string value is ``value`` and whose label is ``label``.

        Looking a member up by value, as in ``Stage('import')``, does not call this, so the default only keeps that
        call well-typed.

        :param value: String value of the member, stored and sent over the API.
        :type value: str
        :param label: Human-readable name of the member.
        :type label: str
        :returns: The new member.
        :rtype: Self
        """
        member = str.__new__(cls, value)
        member._value_ = value
        member.label = label
        return member


class Stage(LabeledStrEnum):
    """A step of the digitisation pipeline, in pipeline order."""

    IMPORT = 'import', 'Import'
    PAGE_SPLIT = 'page-split', 'Page split'
    PAGE_ORDER = 'page-order', 'Page order'
    GEOMETRY = 'geometry', 'Geometry'
    CLEANUP = 'cleanup', 'Cleanup'
    LAYOUT = 'layout', 'Layout'
    BACKGROUND = 'background', 'Background'
    RECOGNITION = 'recognition', 'Recognition'
    PROOFREADING = 'proofreading', 'Proofreading'
    TYPESETTING = 'typesetting', 'Typesetting'


class SourceKind(LabeledStrEnum):
    """What one source of a book is, which selects the format that reads it."""

    PDF = 'pdf', 'PDF document'
    DJVU = 'djvu', 'DjVu document'
    IMAGE = 'image', 'Image file'


class DjvuDocumentKind(LabeledStrEnum):
    """How a DjVu file holds its pages, which decides how many sources it makes."""

    BUNDLED = 'bundled', 'Bundled document, every page in one file'
    INDIRECT = 'indirect', 'Indirect document, an index file with one file per page'
    SINGLE_PAGE = 'single-page', 'Single-page file'


class ColorMode(LabeledStrEnum):
    """Colour depth of a page image as stored in the source."""

    BILEVEL = 'bilevel', 'Black and white'
    GRAY = 'gray', 'Grayscale'
    COLOR = 'color', 'Colour'
    UNKNOWN = 'unknown', 'Unknown'


class Orthography(LabeledStrEnum):
    """Spelling norm of the printed text."""

    UNKNOWN = 'unknown', 'Unknown'
    PRE_REFORM = 'pre-reform', 'Pre-reform'
    MODERN = 'modern', 'Modern'


class Script(LabeledStrEnum):
    """Writing system of the printed text, which is separate from its spelling norm."""

    UNKNOWN = 'unknown', 'Unknown'
    CYRILLIC = 'cyrillic', 'Cyrillic'
    LATIN = 'latin', 'Latin'
    MIXED = 'mixed', 'Mixed'


class RightsStatus(LabeledStrEnum):
    """Whether the result of the work on a book may be published."""

    UNKNOWN = 'unknown', 'Unknown'
    PUBLIC_DOMAIN = 'public-domain', 'Public domain'
    IN_COPYRIGHT = 'in-copyright', 'In copyright'


class ContributorRole(LabeledStrEnum):
    """Role of a person in the making of a book.

    The value of a member is its code in the `MARC Code List for Relators <https://www.loc.gov/marc/relators/>`_ and
    the label is the term of that list, so an export to library formats writes the codes without a translation table.
    """

    AUTHOR = 'aut', 'author'
    EDITOR = 'edt', 'editor'
    COMPILER = 'com', 'compiler'
    TRANSLATOR = 'trl', 'translator'
    ILLUSTRATOR = 'ill', 'illustrator'
    ENGRAVER = 'egr', 'engraver'
    LITHOGRAPHER = 'ltg', 'lithographer'
    PHOTOGRAPHER = 'pht', 'photographer'
    WRITER_OF_PREFACE = 'wpr', 'writer of preface'
    WRITER_OF_INTRODUCTION = 'win', 'writer of introduction'
    ANNOTATOR = 'ann', 'annotator'
    COMMENTATOR = 'cmm', 'commentator'
    DEDICATEE = 'dte', 'dedicatee'
    CONTRIBUTOR = 'ctb', 'contributor'
    OTHER = 'oth', 'other'


class IdentifierScheme(LabeledStrEnum):
    """Kind of number or address that identifies a book or one copy of it, each with its rule of writing."""

    ISBN = 'isbn', 'ISBN'
    OCLC = 'oclc', 'OCLC number'
    LCCN = 'lccn', 'LCCN'
    SHELFMARK = 'shelfmark', 'Shelfmark'
    URL = 'url', 'URL of a copy'

    def normalize(self, raw: str) -> str:
        """Return ``raw`` in the one form the scheme compares by, after checking it.

        The rules follow the standards of each scheme: an ISBN loses its hyphens and spaces and must carry a correct
        check digit, an OCLC number is digits, an LCCN is normalized as the Library of Congress describes, a shelfmark
        is free text, and a URL is an ``http`` or ``https`` address.

        :param raw: The identifier as a person wrote it or a file stored it.
        :type raw: str
        :returns: The identifier in normalized form.
        :rtype: str
        :raises InvalidIdentifierError: If the identifier breaks the rules of its scheme.
        """
        text = raw.strip()
        match self:
            case IdentifierScheme.ISBN:
                normalized = re.sub(r'[-\s]', '', text).upper()
                valid = self._has_isbn_check_digit(normalized)
            case IdentifierScheme.OCLC:
                normalized = text
                valid = OCLC_NUMBER.fullmatch(normalized) is not None
            case IdentifierScheme.LCCN:
                normalized = re.sub(r'\s', '', text).lower().partition('/')[0]
                head, hyphen, serial = normalized.partition('-')
                if hyphen:
                    normalized = head + serial.zfill(LCCN_SERIAL_LENGTH)
                valid = LCCN_NORMALIZED.fullmatch(normalized) is not None
            case IdentifierScheme.SHELFMARK:
                normalized = text
                valid = 0 < len(text) <= SHELFMARK_MAX_LENGTH
            case IdentifierScheme.URL:
                normalized = text
                valid = self._is_web_address(text)
        if not valid:
            err_msg = f'{raw!r} is not a valid {self.label}.'
            raise InvalidIdentifierError(err_msg)
        return normalized

    @staticmethod
    def _has_isbn_check_digit(isbn: str) -> bool:
        """Tell whether ``isbn`` is an ISBN-10 or an ISBN-13 whose weighted digit sum is a multiple of its modulus.

        :param isbn: The ISBN without hyphens and spaces, in upper case.
        :type isbn: str
        :returns: Whether it has the length, the digits and the check digit of an ISBN.
        :rtype: bool
        """
        if ISBN_10.fullmatch(isbn):
            values = [ISBN_10_X_VALUE if digit == 'X' else int(digit) for digit in isbn]
            weights = range(len(isbn), 0, -1)
            return sum(weight * value for weight, value in zip(weights, values, strict=True)) % ISBN_10_MODULUS == 0
        if ISBN_13.fullmatch(isbn):
            total = sum(weight * int(digit) for weight, digit in zip(cycle(ISBN_13_WEIGHTS), isbn, strict=False))
            return total % ISBN_13_MODULUS == 0
        return False

    @staticmethod
    def _is_web_address(text: str) -> bool:
        """Tell whether ``text`` is a single ``http`` or ``https`` address with a host.

        :param text: The address without surrounding whitespace.
        :type text: str
        :returns: Whether it is such an address.
        :rtype: bool
        """
        if not 0 < len(text) <= URL_MAX_LENGTH or re.search(r'\s', text):
            return False
        try:
            parts = urlsplit(text)
        except ValueError:
            return False
        return parts.scheme.lower() in URL_SCHEMES and bool(parts.hostname)


class ImagePolicy(LabeledStrEnum):
    """How a project stores the ``full`` image of its scans and page versions.

    A bilevel image is a lossless PNG under either policy. The policy decides only gray and colour images, and a change
    applies to images written afterwards, so nothing stored is encoded again.
    """

    COMPACT = 'compact', 'Compact: gray and colour images as JPEG'
    LOSSLESS = 'lossless', 'Lossless: every image as PNG'

    def full_format(self, color_mode: ColorMode) -> Rendition:
        """Return the format of the ``full`` image of a page of ``color_mode``, by decision 22 of the book model.

        A bilevel page is a 1-bit PNG whatever the policy, since JPEG rings around strokes and a 1-bit PNG is smaller.
        Any other page, one of unknown colour included, is a JPEG under ``compact`` and a PNG under ``lossless``.

        :param color_mode: Colour mode of the page, as its scan reports it.
        :type color_mode: ColorMode
        :returns: ``Rendition.FULL_PNG`` or ``Rendition.FULL_JPEG``.
        :rtype: Rendition
        """
        if color_mode is ColorMode.BILEVEL or self is ImagePolicy.LOSSLESS:
            return Rendition.FULL_PNG
        return Rendition.FULL_JPEG


class PageKind(LabeledStrEnum):
    """Role of a page in the printed book."""

    COVER = 'cover', 'Cover'
    BACK_COVER = 'back-cover', 'Back cover'
    ENDPAPER = 'endpaper', 'Endpaper'
    FRONTISPIECE = 'frontispiece', 'Frontispiece'
    TITLE = 'title', 'Title page'
    TEXT = 'text', 'Text page'
    PLATE = 'plate', 'Plate'
    BLANK = 'blank', 'Blank page'
    OTHER = 'other', 'Other'


class PageOrigin(LabeledStrEnum):
    """Where the image of a page comes from."""

    SCAN = 'scan', 'Copy of a part of a scan'
    BLANK = 'blank', 'Generated blank leaf'
    PLACEHOLDER = 'placeholder', 'Placeholder waiting for a scan'


class VersionState(LabeledStrEnum):
    """Lifecycle of a page version, from its creation to its result."""

    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    READY = 'ready', 'Ready'
    FAILED = 'failed', 'Failed'


class TransformKind(LabeledStrEnum):
    """Kind of the coordinate transform a processing step applies from its input to its output."""

    IDENTITY = 'identity', 'Unchanged coordinates'
    CROP = 'crop', 'Crop to a quadrilateral'
    ROTATE = 'rotate', 'Rotation by an angle'
    PERSPECTIVE = 'perspective', 'Perspective correction of a quadrilateral'
    MESH = 'mesh', 'Dewarping along a stored mesh'


class JobKind(LabeledStrEnum):
    """What a background job does."""

    IMPORT_SOURCE = 'import-source', 'Import source'


class JobState(LabeledStrEnum):
    """Lifecycle of a background job."""

    QUEUED = 'queued', 'Queued'
    RUNNING = 'running', 'Running'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    CANCELLED = 'cancelled', 'Cancelled'

    @property
    def is_final(self) -> bool:
        """Whether the job will not change state any more."""
        return self in {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED}

    @classmethod
    def active(cls) -> frozenset[JobState]:
        """Return the states of a job that has not finished, which a worker or a cancellation may still leave.

        :returns: Every state that is not final.
        :rtype: frozenset[JobState]
        """
        return frozenset(state for state in cls if not state.is_final)


class WorkerPool(LabeledStrEnum):
    """Class of worker a job needs, which routes it to the right queue."""

    CPU = 'cpu', 'CPU'
    GPU = 'gpu', 'GPU'
    LLM = 'llm', 'Language model'


class UploadProblem(LabeledStrEnum):
    """Why an upload cannot become a source."""

    NO_FILES = 'no-files', 'Choose PDF files, DjVu files or page images to upload.'
    EMPTY_NAME = 'empty-name', 'Every uploaded file needs a name.'
    UNSAFE_PATH = 'unsafe-path', 'An uploaded file has a path that leaves its folder, such as an absolute path or ..'
    DUPLICATE_NAME = 'duplicate-name', 'Two uploaded files have the same path.'
    UNSUPPORTED_TYPE = 'unsupported-type', 'Only PDF, DjVu, TIFF, JPEG, JPEG 2000 and PNG files are accepted.'
    TOO_LARGE = 'too-large', 'The upload is larger than the allowed size.'
    TOO_MANY_FILES = 'too-many-files', 'The upload has more files than allowed.'


class RejectionReason(LabeledStrEnum):
    """Why one file of an upload was not imported, while the other files of the upload were."""

    DUPLICATE = 'duplicate', 'The project already has this file.'
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


class Rendition(LabeledStrEnum):
    """One of the derived files of a scan or a page version, named by its value inside their directory.

    ``full`` is a JPEG or a PNG as the project's ``ImagePolicy`` and the colour of the image decide, so it has one
    member per format. ``preview`` and ``thumb`` are always JPEG.
    """

    FULL_JPEG = 'full.jpg', 'Native resolution image as JPEG'
    FULL_PNG = 'full.png', 'Native resolution image as PNG'
    PREVIEW = 'preview.jpg', 'Preview, 2048 px on the longer side'
    THUMBNAIL = 'thumb.jpg', 'Thumbnail'
    TILES = 'iiif', 'IIIF tile pyramid'
