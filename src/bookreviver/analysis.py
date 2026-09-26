"""Read the technical and bibliographic facts of an uploaded book.

Every function here is synchronous and CPU or disk bound; web handlers call them through a worker thread.
"""

from typing import TYPE_CHECKING, Any

from attrs import field, frozen

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from bookreviver.models import ColorMode, SourceKind

PDF_SUFFIXES: frozenset[str] = frozenset({'.pdf'})
IMAGE_SUFFIXES: frozenset[str] = frozenset({'.tif', '.tiff', '.jpg', '.jpeg', '.png'})


class UnsupportedSourceError(ValueError):
    """The uploaded files are not a readable PDF or image set."""


@frozen(kw_only=True)
class PageFacts:
    """Technical facts of one page, mirroring the columns of ``models.Page``."""

    source_file: str = ''
    width_px: int
    height_px: int
    dpi_x: float | None = None
    dpi_y: float | None = None
    color_mode: ColorMode
    bits_per_component: int | None = None
    image_format: str = ''
    width_mm: float | None = None
    height_mm: float | None = None
    has_text_layer: bool = False
    extra: Mapping[str, Any] = field(factory=dict)


@frozen(kw_only=True)
class MetadataSuggestion:
    """Bibliographic fields found in the source; an empty string means nothing was found."""

    title: str = ''
    authors: str = ''
    publisher: str = ''
    publication_year: str = ''
    language: str = ''


@frozen(kw_only=True)
class SourceAnalysis:
    """Everything learned from a source: its pages, raw file metadata and suggested description."""

    kind: SourceKind
    pages: Sequence[PageFacts]
    # JSON-serialisable, shown to the user as-is
    file_metadata: Mapping[str, Any] = field(factory=dict)
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)


def analyze_pdf(path: Path) -> SourceAnalysis:
    """Describe every page of a PDF and extract its document metadata.

    :raises UnsupportedSourceError: If the file is not a readable PDF.
    """
    raise NotImplementedError


def analyze_images(paths: Sequence[Path]) -> SourceAnalysis:
    """Describe a set of page images, ordered by natural sort of their file names.

    :raises UnsupportedSourceError: If the set is empty or a file is not a readable image.
    """
    raise NotImplementedError
