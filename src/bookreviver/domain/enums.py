"""Closed sets of values used across the application, each carrying a human label."""

import enum
from typing import Self


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
    GEOMETRY = 'geometry', 'Geometry'
    CLEANUP = 'cleanup', 'Cleanup'
    LAYOUT = 'layout', 'Layout'
    BACKGROUND = 'background', 'Background'
    RECOGNITION = 'recognition', 'Recognition'
    PROOFREADING = 'proofreading', 'Proofreading'
    TYPESETTING = 'typesetting', 'Typesetting'


class SourceKind(LabeledStrEnum):
    """What a book was imported from."""

    PDF = 'pdf', 'PDF document'
    IMAGES = 'images', 'Page images'


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


class WorkerPool(LabeledStrEnum):
    """Class of worker a job needs, which routes it to the right queue."""

    CPU = 'cpu', 'CPU'
    GPU = 'gpu', 'GPU'
    LLM = 'llm', 'Language model'


class UploadProblem(LabeledStrEnum):
    """Why an upload cannot become a source."""

    NO_FILES = 'no-files', 'Choose a PDF file or page images to upload.'
    EMPTY_NAME = 'empty-name', 'Every uploaded file needs a name.'
    DUPLICATE_NAME = 'duplicate-name', 'Two uploaded files have the same name.'
    MIXED_TYPES = 'mixed-types', 'Upload exactly one PDF, or one or more page images, without mixing them.'
    UNSUPPORTED_TYPE = 'unsupported-type', 'Only PDF, TIFF, JPEG and PNG files are accepted.'
    TOO_LARGE = 'too-large', 'The upload is larger than the allowed size.'


class FileType(LabeledStrEnum):
    """A file type accepted as a book source."""

    PDF = 'pdf', 'PDF'
    TIFF = 'tiff', 'TIFF'
    JPEG = 'jpeg', 'JPEG'
    PNG = 'png', 'PNG'

    @property
    def suffixes(self) -> frozenset[str]:
        """File name suffixes of this type, lower case with the dot."""
        return FILE_TYPE_SUFFIXES[self]

    @property
    def source_kind(self) -> SourceKind:
        """The kind of source a file of this type makes."""
        return SourceKind.PDF if self is FileType.PDF else SourceKind.IMAGES

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
    FileType.TIFF: frozenset({'.tif', '.tiff'}),
    FileType.JPEG: frozenset({'.jpg', '.jpeg'}),
    FileType.PNG: frozenset({'.png'}),
}


class PageAsset(LabeledStrEnum):
    """A derived file of an imported page, named by its value inside the page's asset directory."""

    FULL = 'full.jpg', 'Native resolution image'
    THUMBNAIL = 'thumb.jpg', 'Thumbnail'
    TILES = 'iiif', 'IIIF tile pyramid'
