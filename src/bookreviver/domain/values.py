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
    """Bibliographic description of a printed book."""

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
    """Description fields found in a source; an empty string means nothing was found."""

    title: str = ''
    authors: str = ''
    publisher: str = ''
    publication_year: str = ''
    language: str = ''


@frozen(kw_only=True)
class SourceSummary:
    """The source a book was imported from."""

    kind: SourceKind
    name: str
    size_bytes: int = field(validator=validators.ge(0))
    metadata: MetadataMap = field(factory=dict)
    imported_at: datetime


@frozen(kw_only=True)
class PageFacts:
    """Technical facts of one source page."""

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
    # File name inside an image set, empty for a PDF page
    source_file: str = ''
    extra: MetadataMap = field(factory=dict)


@frozen(kw_only=True)
class PageAssets:
    """State of the derived images of a page: native image, thumbnail and tile pyramid."""

    ready: bool = False
    # Bumped whenever the assets are regenerated, so their URLs change and caches never go stale
    version: int = 0


@frozen(kw_only=True)
class SourceAnalysis:
    """Everything an inspector learned from a source."""

    kind: SourceKind
    pages: Sequence[PageFacts]
    file_metadata: MetadataMap = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)


@frozen(kw_only=True)
class Progress:
    """How far a job has come."""

    done: int = field(default=0, validator=validators.ge(0))
    total: int = field(default=0, validator=validators.ge(0))

    @property
    def fraction(self) -> float:
        """The completed share between 0 and 1, zero while the total is unknown."""
        return self.done / self.total if self.total else 0.0


@frozen(kw_only=True)
class SliceRequest:
    """Which part of a collection to read."""

    offset: int = field(default=0, validator=validators.ge(0))
    limit: int = field(default=50, validator=validators.gt(0))


@frozen(kw_only=True)
class Slice[ItemT]:
    """A part of a collection together with the size of the whole collection."""

    items: Sequence[ItemT]
    total: int = field(validator=validators.ge(0))


@frozen(kw_only=True)
class MailMessage:
    """A plain-text message to one recipient."""

    to: str
    subject: str
    body: str
