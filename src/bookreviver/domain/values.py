"""Immutable value objects of the domain."""

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from attrs import field, frozen, validators

from bookreviver.domain.enums import Orthography

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.enums import ColorMode, SourceKind

# JSON-compatible metadata as read from a source file
type MetadataMap = Mapping[str, Any]


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
class SourceSummary:
    """The source a book was imported from.

    :ivar kind: Whether the source is one PDF or a set of page images.
    :ivar name: Name of the uploaded file, or of the first page image of a set.
    :ivar size_bytes: Total size of the upload in bytes.
    :ivar metadata: File metadata the inspector reported.
    :ivar imported_at: When the source became the project's source.
    """

    kind: SourceKind
    name: str
    size_bytes: int = field(validator=validators.ge(0))
    metadata: MetadataMap = field(factory=dict)
    imported_at: datetime


@frozen(kw_only=True)
class PageFacts:
    """Technical facts of one source page.

    :ivar width_px: Width of the page image in pixels.
    :ivar height_px: Height of the page image in pixels.
    :ivar color_mode: Whether the page is bilevel, gray or colour.
    :ivar dpi_x: Horizontal resolution in dots per inch, or None when the source does not record it.
    :ivar dpi_y: Vertical resolution in dots per inch, or None when the source does not record it.
    :ivar bits_per_component: Bit depth of one colour component, or None when unknown.
    :ivar image_format: Human name of the image format, such as ``JPEG`` or ``TIFF``.
    :ivar width_mm: Physical width in millimetres, or None without a resolution.
    :ivar height_mm: Physical height in millimetres, or None without a resolution.
    :ivar has_text_layer: Whether the page carries text, such as the OCR layer of a scanned PDF.
    :ivar source_file: File name inside an image set, empty for a PDF page.
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
    source_file: str = ''
    extra: MetadataMap = field(factory=dict)


@frozen(kw_only=True)
class PageAssets:
    """State of the derived images of a page: native image, thumbnail and tile pyramid.

    :ivar ready: Whether every derived image of the current version is published.
    :ivar version: Bumped whenever the assets are regenerated, so their URLs change and caches never go stale.
    """

    ready: bool = False
    version: int = 0


@frozen(kw_only=True)
class SourceAnalysis:
    """Everything an inspector learned from a source.

    :ivar kind: Whether the source is one PDF or a set of page images.
    :ivar pages: Facts of every page in book order.
    :ivar file_metadata: Facts of the source file or file set as a whole.
    :ivar suggestion: Description fields found in the source, offered to fill empty book details.
    """

    kind: SourceKind
    pages: Sequence[PageFacts]
    file_metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)


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
