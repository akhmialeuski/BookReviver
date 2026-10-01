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
WHITESPACE: Final = re.compile(r'\s')
# Longest shelfmark and longest URL of a copy the domain accepts
SHELFMARK_MAX_LENGTH: Final = 300
URL_MAX_LENGTH: Final = 2_048
URL_SCHEMES: Final = frozenset({'http', 'https'})
# The values and symbols of the Roman numerals in the order they are written, subtractive pairs included, and the
# largest number they can write
ROMAN_NUMERALS: Final = (
    (1000, 'M'),
    (900, 'CM'),
    (500, 'D'),
    (400, 'CD'),
    (100, 'C'),
    (90, 'XC'),
    (50, 'L'),
    (40, 'XL'),
    (10, 'X'),
    (9, 'IX'),
    (5, 'V'),
    (4, 'IV'),
    (1, 'I'),
)
ROMAN_MAX: Final = 3999


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

    @property
    def position(self) -> int:
        """The place of the stage in the pipeline from zero, by which a stage is earlier or later than another."""
        return list(type(self)).index(self)

    @property
    def manual(self) -> bool:
        """Whether the user does the stage by hand, so it is available whatever plugins are installed."""
        return self in {Stage.IMPORT, Stage.PAGE_ORDER}


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
                normalized = WHITESPACE.sub('', text).lower().partition('/')[0]
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
        if not 0 < len(text) <= URL_MAX_LENGTH or WHITESPACE.search(text):
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


class LabelStyle(LabeledStrEnum):
    """How the number of a page is written, which the numbering of a range of pages applies to its numbers.

    The domain imports only the standard library, so the Roman numerals are written here and not taken from the
    ``roman`` package, whose one function would be about a dozen lines of ours.
    """

    ARABIC = 'arabic', 'Arabic'
    ROMAN_LOWER = 'roman-lower', 'Roman, lower case'
    ROMAN_UPPER = 'roman-upper', 'Roman, upper case'
    NONE = 'none', 'No label'

    def write(self, number: int) -> str:
        """Write a page number in this style, which for ``none`` is the empty label that erases a numbering.

        The method is not called ``format``, since that name belongs to ``str``, whose signature it would break.

        :param number: Number of the page, from 1.
        :type number: int
        :returns: ``12``, ``xii`` or ``XII`` for the number 12, and an empty string for ``none``.
        :rtype: str
        :raises ValueError: If the number is below 1, or above 3999 in a Roman style, which has no numeral for it.
        """
        if self is LabelStyle.NONE:
            return ''
        if number < 1 or (self is not LabelStyle.ARABIC and number > ROMAN_MAX):
            err_msg = f'{number} cannot be written as a {self.label.lower()} page number.'
            raise ValueError(err_msg)
        if self is LabelStyle.ARABIC:
            return str(number)
        numeral, remaining = '', number
        for value, symbol in ROMAN_NUMERALS:
            repeats, remaining = divmod(remaining, value)
            numeral += symbol * repeats
        return numeral.lower() if self is LabelStyle.ROMAN_LOWER else numeral


class Side(LabeledStrEnum):
    """Which side of a page in the book a neighbour or a new position lies on."""

    BEFORE = 'before', 'Before the page'
    AFTER = 'after', 'After the page'


class PageChange(LabeledStrEnum):
    """What a use case did to the pages of a book, which the ``PagesChanged`` event reports."""

    MOVED = 'moved', 'Moved'
    EDITED = 'edited', 'Edited'
    ADDED = 'added', 'Added'
    REMOVED = 'removed', 'Removed'


class NewPageOrigin(LabeledStrEnum):
    """Where the image of a page the user adds comes from.

    Only a blank leaf and a placeholder are added by hand, since a page cut from a scan is made by the page split and
    by binding a scan to a placeholder.
    """

    BLANK = 'blank', 'Generated blank leaf'
    PLACEHOLDER = 'placeholder', 'Placeholder waiting for a scan'

    @property
    def page_origin(self) -> PageOrigin:
        """The origin of the page this one makes."""
        return PageOrigin(self.value)


class VersionData(LabeledStrEnum):
    """Keys of the data of a version: the size of its image, what its step found, and why it failed."""

    WIDTH_PX = 'width_px', 'Width of the image in pixels'
    HEIGHT_PX = 'height_px', 'Height of the image in pixels'
    DPI = 'dpi', 'Resolution of the image in dots per inch'
    ERROR = 'error', 'Why the version could not be made'
    ANGLE = 'angle', 'Angle a page was turned by, in degrees'
    CONFIDENCE = 'confidence', 'How sure the step is of what it found, from 0 to 1'
    SKIPPED = 'skipped', 'Whether the step left the image as it was'
    OVERLAP_PX = 'overlap_px', 'Width in pixels a half of a spread reaches over the cut'
    CUT_X = 'cut_x', 'Place of the cut in the scan, as the distance in pixels from its left edge'


class VersionState(LabeledStrEnum):
    """Lifecycle of a page version, from its creation to its result."""

    PENDING = 'pending', 'Pending'
    RUNNING = 'running', 'Running'
    READY = 'ready', 'Ready'
    FAILED = 'failed', 'Failed'


class StageState(LabeledStrEnum):
    """Whether the current version of a stage of a page still matches the inputs the stage would run on."""

    FRESH = 'fresh', 'Up to date'
    STALE = 'stale', 'Out of date'
    FAILED = 'failed', 'Failed'


class PageStageStatus(LabeledStrEnum):
    """Where one page stands in one stage: the state of its record, or that the stage has not run on it yet."""

    NOT_RUN = 'not-run', 'Not processed'
    FRESH = 'fresh', 'Up to date'
    STALE = 'stale', 'Out of date'
    FAILED = 'failed', 'Failed'

    @classmethod
    def of(cls, state: StageState | None) -> PageStageStatus:
        """Give the status of a page from the state of its record of a stage.

        :param state: State of the record, or None for a page the stage has no record of.
        :type state: StageState | None
        :returns: The status, which is not run for a page without a record.
        :rtype: PageStageStatus
        """
        return cls.NOT_RUN if state is None else cls(state.value)


class StageStatus(LabeledStrEnum):
    """Where a whole stage stands in a book, summed over its pages."""

    DONE = 'done', 'Done'
    ATTENTION = 'attention', 'Needs a look'
    RUNNING = 'running', 'Running'
    WAITING = 'waiting', 'Waiting'
    UNAVAILABLE = 'unavailable', 'Not available yet'


class ReviewReason(LabeledStrEnum):
    """Why a processed page is marked for a second look, though its step finished without an error."""

    LOW_CONFIDENCE = 'low-confidence', 'The step was not sure of its result'
    NOT_APPLIED = 'not-applied', 'The step left the page as it was, because it was not sure'


class RunOutcome(LabeledStrEnum):
    """What a run of a recipe came to on one page."""

    DONE = 'done', 'Processed'
    SKIPPED = 'skipped', 'Skipped, the page has no image to process'
    FAILED = 'failed', 'Failed'


class VersionScale(LabeledStrEnum):
    """The size of the image a step ran on, which tells a full run from a preview of its parameters."""

    FULL = 'full', 'Full image'
    PREVIEW = 'preview', 'Preview image'


class ProcessorScope(LabeledStrEnum):
    """How many outputs a processor makes from its input."""

    PAGE = 'page', 'One output for the page'
    SPLIT = 'split', 'One output for each part of a scan'


class VersionOutput(LabeledStrEnum):
    """What a processing step writes."""

    IMAGE = 'image', 'Page image'
    MASK = 'mask', 'Mask'
    REGIONS = 'regions', 'Regions'
    TEXT = 'text', 'Text'


class StepField(LabeledStrEnum):
    """Keys of a step of a recipe in the JSON it is stored as, in a recipe row and in the parameters of a job."""

    PROCESSOR_KEY = 'processor_key', 'Key of the processor that runs the step'
    PARAMS = 'params', 'Parameters the processor runs with'
    ENABLED = 'enabled', 'Whether a run and a preview run the step'


class EditorKind(LabeledStrEnum):
    """The editor a processor offers for the manual edit of its input."""

    NONE = 'none', 'No editor'
    RECT = 'rect', 'Frame'
    QUAD = 'quad', 'Quadrilateral'
    LINE = 'line', 'Line'
    ROTATION = 'rotation', 'Rotation'
    MESH = 'mesh', 'Mesh'
    BRUSH_MASK = 'brush-mask', 'Brush mask'
    REGIONS = 'regions', 'Regions'


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
    PREPARE_PAGES = 'prepare-pages', 'Prepare pages'
    RUN_STAGE = 'run-stage', 'Run a stage'
    PREVIEW_STEP = 'preview-step', 'Preview a step'
    CUT_TILES = 'cut-tiles', 'Cut tiles'
    COLLECT_VERSIONS = 'collect-versions', 'Collect old versions'

    @classmethod
    def processing(cls) -> frozenset[JobKind]:
        """Return the kinds of job that read and write the versions of pages, of which a project runs one at a time.

        A run, a preview and a tile cutting write the files of versions they may have found made already, and a
        collection deletes them, so no two of them may overlap, and two of one kind would write the same files.

        :returns: The kinds of the processing jobs.
        :rtype: frozenset[JobKind]
        """
        return frozenset({cls.RUN_STAGE, cls.PREVIEW_STEP, cls.CUT_TILES, cls.COLLECT_VERSIONS})


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
    MASK = 'mask.png', 'Mask of the areas a step removed'
