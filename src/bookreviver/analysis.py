"""Read the technical and bibliographic facts of an uploaded book.

Every function here is synchronous and CPU or disk bound; web handlers call them through a worker thread.
"""

import hashlib
from operator import attrgetter
from typing import TYPE_CHECKING, Any

import pymupdf
from attrs import evolve, field, frozen
from natsort import natsorted
from PIL import ExifTags, Image

from bookreviver.models import ColorMode, SourceKind

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

PDF_SUFFIXES: frozenset[str] = frozenset({'.pdf'})
IMAGE_SUFFIXES: frozenset[str] = frozenset({'.tif', '.tiff', '.jpg', '.jpeg', '.png'})

# Pillow refuses images above this many pixels as decompression bombs, and raises outright above twice as many.
# Its default of about 89 million pixels rejects a broadsheet newspaper scanned at 600 DPI, while an A1 sheet at
# 600 DPI is about 279 million pixels; this bound admits that and still rejects absurd headers.
MAX_IMAGE_PIXELS: int = 300_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

MM_PER_INCH: float = 25.4
POINTS_PER_INCH: float = 72.0
PDF_FILETYPE: str = 'pdf'
SHA256: str = 'sha256'
PNG_FORMAT: str = 'PNG'
TIFF_FORMAT: str = 'TIFF'
DPI_KEY: str = 'dpi'
NO_DPI: tuple[float, float] = (0.0, 0.0)
EXIF_KEY: str = 'exif'
FILE_SIZE_KEY: str = 'file_size_bytes'

# Human names of the PDF image filters; any other filter is shown as-is
PDF_FILTER_NAMES: Mapping[str, str] = {
    'DCTDecode': 'JPEG',
    'JPXDecode': 'JPEG 2000',
    'JBIG2Decode': 'JBIG2',
    'CCITTFaxDecode': 'CCITT',
    'FlateDecode': 'Flate',
}
# Index of the filter name in a ``Page.get_images(full=True)`` entry
PDF_IMAGE_FILTER_INDEX: int = 8
# Key of ``Document.metadata`` holding the PDF version rather than a document information entry
PDF_VERSION_KEY: str = 'format'
# Colour mode by the number of colour components of a PDF image; one component with one bit is bilevel
PDF_COMPONENT_COLOR_MODES: Mapping[int, ColorMode] = {1: ColorMode.GRAY, 3: ColorMode.COLOR, 4: ColorMode.COLOR}
# Key of the placement rectangle in a ``Page.get_image_info()`` entry
IMAGE_BBOX_KEY: str = 'bbox'

# Colour mode and bits per component of the Pillow modes a scan can have
PILLOW_MODE_DEPTHS: Mapping[str, tuple[ColorMode, int | None]] = {
    '1': (ColorMode.BILEVEL, 1),
    'L': (ColorMode.GRAY, 8),
    'LA': (ColorMode.GRAY, 8),
    'RGB': (ColorMode.COLOR, 8),
    'RGBA': (ColorMode.COLOR, 8),
    'CMYK': (ColorMode.COLOR, 8),
    'YCbCr': (ColorMode.COLOR, 8),
    'LAB': (ColorMode.COLOR, 8),
    'P': (ColorMode.COLOR, 8),
}
# 'I;16', 'I;16B', 'I;16L' and 'I;16N' are all 16-bit grayscale
PILLOW_16_BIT_MODE_PREFIX: str = 'I;16'
SIXTEEN_BIT_GRAY_DEPTH: tuple[ColorMode, int | None] = (ColorMode.GRAY, 16)
UNKNOWN_DEPTH: tuple[ColorMode, int | None] = (ColorMode.UNKNOWN, None)
EXIF_TAGS: Sequence[ExifTags.Base] = (
    ExifTags.Base.Make,
    ExifTags.Base.Model,
    ExifTags.Base.Software,
    ExifTags.Base.DateTime,
)


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
    try:
        document = pymupdf.open(path, filetype=PDF_FILETYPE)
    except pymupdf.FileDataError as error:
        err_msg = f'{path.name} cannot be read as a PDF: the file is damaged or is not a PDF. Upload an intact PDF.'
        raise UnsupportedSourceError(err_msg) from error
    with document:
        # PyMuPDF opens an image as a one-page document even when asked for a PDF
        if not document.is_pdf:
            err_msg = f'{path.name} is not a PDF. Upload a PDF, or upload page images as an image set.'
            raise UnsupportedSourceError(err_msg)
        if document.needs_pass:
            err_msg = f'{path.name} is protected by a password. Remove the password and upload the PDF again.'
            raise UnsupportedSourceError(err_msg)
        pages = [_pdf_page_facts(page) for page in document.pages()]
        metadata = document.metadata or {}
        outline = document.get_toc()
        file_metadata: dict[str, Any] = {
            'document_info': {key: value for key, value in metadata.items() if value and key != PDF_VERSION_KEY},
            'pdf_version': metadata.get(PDF_VERSION_KEY, ''),
            'page_count': document.page_count,
            'has_outline': bool(outline),
            'outline_entries': len(outline),
            'has_xmp_metadata': document.xref_xml_metadata() > 0,
            # MuPDF silently rebuilds a damaged cross-reference table; pages past the damage may be missing
            'repaired': document.is_repaired,
        }

    with path.open('rb') as file:
        file_metadata['sha256'] = hashlib.file_digest(file, SHA256).hexdigest()
    file_metadata[FILE_SIZE_KEY] = path.stat().st_size
    suggestion = MetadataSuggestion(
        title=(metadata.get('title') or '').strip(),
        authors=(metadata.get('author') or '').strip(),
    )
    return SourceAnalysis(kind=SourceKind.PDF, pages=pages, file_metadata=file_metadata, suggestion=suggestion)


def analyze_images(paths: Sequence[Path]) -> SourceAnalysis:
    """Describe a set of page images, ordered by natural sort of their file names.

    :raises UnsupportedSourceError: If the set is empty or a file is not a readable image.
    """
    if not paths:
        err_msg = 'No page images were uploaded. Upload at least one TIFF, JPEG or PNG file.'
        raise UnsupportedSourceError(err_msg)
    ordered = natsorted(paths, key=attrgetter('name'))
    pages = [_image_page_facts(path) for path in ordered]
    file_metadata = {
        'file_count': len(ordered),
        'total_size_bytes': sum(path.stat().st_size for path in ordered),
        'formats': sorted({page.image_format for page in pages}),
    }
    return SourceAnalysis(kind=SourceKind.IMAGES, pages=pages, file_metadata=file_metadata)


def _to_mm(length: float, *, units_per_inch: float) -> float:
    """Convert a length in points or pixels to millimetres, rounded to 0.1 mm."""
    return round(length / units_per_inch * MM_PER_INCH, 1)


def _pdf_page_facts(page: pymupdf.Page) -> PageFacts:
    """Describe one PDF page by its dominant raster image, or by its own size when it has none."""
    rect = page.rect
    images = page.get_image_info(xrefs=True)
    text_chars = len(page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT).extractText().strip())
    facts = PageFacts(
        width_px=round(rect.width),
        height_px=round(rect.height),
        color_mode=ColorMode.UNKNOWN,
        width_mm=_to_mm(rect.width, units_per_inch=POINTS_PER_INCH),
        height_mm=_to_mm(rect.height, units_per_inch=POINTS_PER_INCH),
        has_text_layer=text_chars > 0,
        extra={'image_count': len(images), 'rotation': page.rotation, 'text_chars': text_chars},
    )
    if not images:
        return facts

    dominant = max(images, key=lambda info: pymupdf.Rect(info[IMAGE_BBOX_KEY]).get_area())
    width_px, height_px, bits = dominant['width'], dominant['height'], dominant['bpc']
    bbox = pymupdf.Rect(dominant[IMAGE_BBOX_KEY])
    scale_x, shear_y = dominant['transform'][:2]
    # A quarter-turned placement lays the image width along the page's vertical axis
    box_width, box_height = (bbox.height, bbox.width) if abs(shear_y) > abs(scale_x) else (bbox.width, bbox.height)
    filters = {image[0]: image[PDF_IMAGE_FILTER_INDEX] for image in page.get_images(full=True)}
    image_filter = filters.get(dominant['xref'], '')
    return evolve(
        facts,
        width_px=width_px,
        height_px=height_px,
        dpi_x=round(width_px * POINTS_PER_INCH / box_width, 1) if box_width else None,
        dpi_y=round(height_px * POINTS_PER_INCH / box_height, 1) if box_height else None,
        color_mode=(
            ColorMode.BILEVEL
            if dominant['colorspace'] == 1 and bits == 1
            else PDF_COMPONENT_COLOR_MODES.get(dominant['colorspace'], ColorMode.UNKNOWN)
        ),
        bits_per_component=bits,
        image_format=PDF_FILTER_NAMES.get(image_filter, image_filter),
    )


def _image_page_facts(path: Path) -> PageFacts:
    """Describe one page image from its header, without decoding the pixel data.

    :raises UnsupportedSourceError: If the file has a wrong suffix, cannot be read, or holds several frames.
    """
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        err_msg = f'{path.name} is not a supported page image. Upload TIFF, JPEG or PNG files.'
        raise UnsupportedSourceError(err_msg)
    try:
        image = Image.open(path)
    except (OSError, Image.DecompressionBombError) as error:
        err_msg = f'{path.name} cannot be read as an image: the file is damaged or too large. Replace this file.'
        raise UnsupportedSourceError(err_msg) from error

    with image:
        if getattr(image, 'n_frames', 1) > 1:
            err_msg = f'{path.name} holds several pages. Split it into one image file per page and upload again.'
            raise UnsupportedSourceError(err_msg)
        color_mode, bits = (
            SIXTEEN_BIT_GRAY_DEPTH
            if image.mode.startswith(PILLOW_16_BIT_MODE_PREFIX)
            else PILLOW_MODE_DEPTHS.get(image.mode, UNKNOWN_DEPTH)
        )
        # Pillow decodes a whole PNG to look for EXIF placed after the pixel data, so read only the header chunk
        exif = image.getexif() if image.format != PNG_FORMAT or EXIF_KEY in image.info else {}
        # Pillow reports 1 DPI for a TIFF without resolution tags, which would make the page metres wide
        dpi = image.info.get(DPI_KEY) if image.format != TIFF_FORMAT or ExifTags.Base.XResolution in exif else None
        dpi_x, dpi_y = (round(float(value), 1) if value > 0 else None for value in dpi or NO_DPI)
        width_px, height_px = image.size
        return PageFacts(
            source_file=path.name,
            width_px=width_px,
            height_px=height_px,
            dpi_x=dpi_x,
            dpi_y=dpi_y,
            color_mode=color_mode,
            bits_per_component=bits,
            image_format=image.format or '',
            width_mm=_to_mm(width_px, units_per_inch=dpi_x) if dpi_x else None,
            height_mm=_to_mm(height_px, units_per_inch=dpi_y) if dpi_y else None,
            extra={
                'pillow_mode': image.mode,
                FILE_SIZE_KEY: path.stat().st_size,
                EXIF_KEY: {tag.name: str(exif[tag]) for tag in EXIF_TAGS if tag in exif},
            },
        )
