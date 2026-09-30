"""Closed sets of values used across the application, each carrying a human label."""

import enum
from typing import TYPE_CHECKING, Self

from bookreviver.domain.errors import UploadRejectedError

if TYPE_CHECKING:
    from collections.abc import Collection


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
    IMAGES = 'image', 'Image file'

    @classmethod
    def of_files(cls, names: Collection[str]) -> SourceKind:
        """Return the kind of source an upload makes, judged by the suffixes of its file names.

        PDF files make a PDF source, whether one document holding the whole book or the book split into parts.
        DjVu files make a DjVu source, whether one bundled document holding the whole book, an indirect document
        split into an index file and one file per page, or a directory of single-page DjVu files. Any number of page
        images, in any mix of the accepted image types, makes an image set, such as the files of a directory of scans.

        :param names: Names of the uploaded files.
        :type names: Collection[str]
        :returns: The kind of source the files make.
        :rtype: SourceKind
        :raises UploadRejectedError: If there are no files, a file type is not accepted, or files of different kinds
                                     are mixed.
        """
        if not names:
            raise UploadRejectedError(UploadProblem.NO_FILES)
        file_types = [FileType.from_name(name) for name in names]
        if None in file_types:
            raise UploadRejectedError(UploadProblem.UNSUPPORTED_TYPE)
        kinds = {file_type.source_kind for file_type in file_types if file_type is not None}
        if len(kinds) > 1:
            raise UploadRejectedError(UploadProblem.MIXED_TYPES)
        return kinds.pop()


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


class ImagePolicy(LabeledStrEnum):
    """How a project stores the ``full`` image of its scans and page versions.

    A bilevel image is a lossless PNG under either policy. The policy decides only gray and colour images, and a change
    applies to images written afterwards, so nothing stored is encoded again.
    """

    COMPACT = 'compact', 'Compact: gray and colour images as JPEG'
    LOSSLESS = 'lossless', 'Lossless: every image as PNG'


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
    DUPLICATE_NAME = 'duplicate-name', 'Two uploaded files have the same name.'
    MIXED_TYPES = 'mixed-types', 'Upload the PDF files, the DjVu files or the page images of one book, not a mix.'
    UNSUPPORTED_TYPE = 'unsupported-type', 'Only PDF, DjVu, TIFF, JPEG, JPEG 2000 and PNG files are accepted.'
    TOO_LARGE = 'too-large', 'The upload is larger than the allowed size.'


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
                return SourceKind.IMAGES

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


class PageAsset(LabeledStrEnum):
    """A derived file of an imported page, named by its value inside the page's asset directory."""

    FULL = 'full.jpg', 'Native resolution image'
    THUMBNAIL = 'thumb.jpg', 'Thumbnail'
    TILES = 'iiif', 'IIIF tile pyramid'


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
