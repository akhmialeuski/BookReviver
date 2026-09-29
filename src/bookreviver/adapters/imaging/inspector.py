"""Technical and bibliographic facts of a source: PDFs read with PyMuPDF, page images read from their headers."""

import enum
import hashlib
from operator import attrgetter
from typing import TYPE_CHECKING, Any, assert_never, override

import pymupdf
from asyncer import asyncify
from attrs import evolve
from natsort import natsorted
from PIL import ExifTags, Image

from bookreviver.domain.enums import ColorMode, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import MetadataSuggestion, PageFacts, SourceAnalysis
from bookreviver.ports.imaging import SourceInspector

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

type ModeDepth = tuple[ColorMode, int | None]

IMAGE_SUFFIXES: frozenset[str] = frozenset({'.tif', '.tiff', '.jpg', '.jpeg', '.png'})

# Pillow refuses images above this many pixels as decompression bombs, and raises outright above twice as many.
# Its default of about 89 million pixels rejects a broadsheet newspaper scanned at 600 DPI, while an A1 sheet at
# 600 DPI is about 279 million pixels; this bound admits that and still rejects absurd headers. Pillow keeps it
# process-wide, and the rasterizer imports this module, so the bound holds for every page it opens as well.
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
# Key of ``Image.info`` present when a PNG carries an EXIF chunk
PNG_EXIF_INFO_KEY: str = 'exif'
# EXIF orientations 5 to 8 turn the stored image a quarter, so the page is as wide as the stored image is high
QUARTER_TURN_ORIENTATIONS: frozenset[int] = frozenset({5, 6, 7, 8})

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
# Colour mode by the number of colour components of a PDF image; one component with one bit is bilevel
PDF_COMPONENT_COLOR_MODES: Mapping[int, ColorMode] = {1: ColorMode.GRAY, 3: ColorMode.COLOR, 4: ColorMode.COLOR}

# Colour mode and bits per component of the Pillow modes a scan can have
PILLOW_MODE_DEPTHS: Mapping[str, ModeDepth] = {
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
SIXTEEN_BIT_GRAY_DEPTH: ModeDepth = (ColorMode.GRAY, 16)
UNKNOWN_DEPTH: ModeDepth = (ColorMode.UNKNOWN, None)
EXIF_TAGS: Sequence[ExifTags.Base] = (
    ExifTags.Base.Make,
    ExifTags.Base.Model,
    ExifTags.Base.Software,
    ExifTags.Base.DateTime,
)


class PyMuPdfKey(enum.StrEnum):
    """Keys of the dictionaries PyMuPDF returns for image placements, extracted images, text spans and metadata."""

    BBOX = 'bbox'
    WIDTH = 'width'
    HEIGHT = 'height'
    BITS_PER_COMPONENT = 'bpc'
    COLORSPACE = 'colorspace'
    TRANSFORM = 'transform'
    XREF = 'xref'
    HAS_MASK = 'has-mask'
    IMAGE = 'image'
    SPAN_TYPE = 'type'
    # ``Document.metadata`` holds the PDF version under this key, beside the document information entries
    PDF_VERSION = 'format'
    TITLE = 'title'
    AUTHOR = 'author'


class FactKey(enum.StrEnum):
    """Keys of the file metadata and the page extras this inspector reports."""

    DOCUMENT_INFO = 'document_info'
    PDF_VERSION = 'pdf_version'
    PAGE_COUNT = 'page_count'
    HAS_OUTLINE = 'has_outline'
    OUTLINE_ENTRIES = 'outline_entries'
    HAS_XMP_METADATA = 'has_xmp_metadata'
    REPAIRED = 'repaired'
    SHA256 = 'sha256'
    FILE_SIZE_BYTES = 'file_size_bytes'
    FILE_COUNT = 'file_count'
    TOTAL_SIZE_BYTES = 'total_size_bytes'
    FORMATS = 'formats'
    IMAGE_COUNT = 'image_count'
    ROTATION = 'rotation'
    TEXT_CHARS = 'text_chars'
    PILLOW_MODE = 'pillow_mode'
    EXIF = 'exif'


class PdfImageSourceInspector(SourceInspector):
    """Describes a PDF page by its dominant raster image, and a page image by its header alone."""

    @override
    async def inspect(self, kind: SourceKind, files: Sequence[Path]) -> SourceAnalysis:
        match kind:
            case SourceKind.PDF:
                return await asyncify(self._inspect_pdf)(files)
            case SourceKind.IMAGES:
                return await asyncify(self._inspect_images)(files)
            case _:
                assert_never(kind)

    def _inspect_pdf(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe every page of the one PDF of a source and extract its document metadata.

        :raises UnsupportedSourceError: If the source is not exactly one readable PDF.
        """
        if len(files) != 1:
            err_msg = f'A PDF source is exactly one PDF file, not {len(files)} files. Upload a single PDF.'
            raise UnsupportedSourceError(err_msg)
        path = files[0]
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
            pages = [pdf_page_facts(page) for page in document.pages()]
            metadata = document.metadata or {}
            outline = document.get_toc()
            file_metadata: dict[str, Any] = {
                FactKey.DOCUMENT_INFO: {
                    key: value for key, value in metadata.items() if value and key != PyMuPdfKey.PDF_VERSION
                },
                FactKey.PDF_VERSION: metadata.get(PyMuPdfKey.PDF_VERSION, ''),
                FactKey.PAGE_COUNT: document.page_count,
                FactKey.HAS_OUTLINE: bool(outline),
                FactKey.OUTLINE_ENTRIES: len(outline),
                FactKey.HAS_XMP_METADATA: document.xref_xml_metadata() > 0,
                # MuPDF silently rebuilds a damaged cross-reference table; pages past the damage may be missing
                FactKey.REPAIRED: document.is_repaired,
            }

        with path.open('rb') as file:
            file_metadata[FactKey.SHA256] = hashlib.file_digest(file, SHA256).hexdigest()
        file_metadata[FactKey.FILE_SIZE_BYTES] = path.stat().st_size
        suggestion = MetadataSuggestion(
            title=(metadata.get(PyMuPdfKey.TITLE) or '').strip(),
            authors=(metadata.get(PyMuPdfKey.AUTHOR) or '').strip(),
        )
        return SourceAnalysis(kind=SourceKind.PDF, pages=pages, file_metadata=file_metadata, suggestion=suggestion)

    def _inspect_images(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe a set of page images in the natural order of their file names.

        :raises UnsupportedSourceError: If the set is empty or a file is not a readable single-page image.
        """
        if not files:
            err_msg = 'No page images were uploaded. Upload at least one TIFF, JPEG or PNG file.'
            raise UnsupportedSourceError(err_msg)
        ordered = natural_order(files)
        pages = [_image_page_facts(path) for path in ordered]
        file_metadata: dict[str, Any] = {
            FactKey.FILE_COUNT: len(ordered),
            FactKey.TOTAL_SIZE_BYTES: sum(path.stat().st_size for path in ordered),
            FactKey.FORMATS: sorted({page.image_format for page in pages}),
        }
        return SourceAnalysis(kind=SourceKind.IMAGES, pages=pages, file_metadata=file_metadata)


def natural_order(files: Sequence[Path]) -> list[Path]:
    """Return page images in book order, the natural sort of their names, so ``page2`` precedes ``page10``."""
    return natsorted(files, key=attrgetter('name'))


def pdf_page_facts(page: pymupdf.Page) -> PageFacts:
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
        extra={FactKey.IMAGE_COUNT: len(images), FactKey.ROTATION: page.rotation, FactKey.TEXT_CHARS: text_chars},
    )
    if not images:
        return facts

    dominant = max(images, key=lambda info: pymupdf.Rect(info[PyMuPdfKey.BBOX]).get_area())
    width_px, height_px = dominant[PyMuPdfKey.WIDTH], dominant[PyMuPdfKey.HEIGHT]
    bits, components = dominant[PyMuPdfKey.BITS_PER_COMPONENT], dominant[PyMuPdfKey.COLORSPACE]
    bbox = pymupdf.Rect(dominant[PyMuPdfKey.BBOX])
    scale_x, shear_y = dominant[PyMuPdfKey.TRANSFORM][:2]
    # A quarter-turned placement lays the image width along the page's vertical axis
    box_width, box_height = (bbox.height, bbox.width) if abs(shear_y) > abs(scale_x) else (bbox.width, bbox.height)
    image_filter = next(
        (
            image[PDF_IMAGE_FILTER_INDEX]
            for image in page.get_images(full=True)
            if image[0] == dominant[PyMuPdfKey.XREF]
        ),
        '',
    )
    return evolve(
        facts,
        width_px=width_px,
        height_px=height_px,
        dpi_x=round(width_px * POINTS_PER_INCH / box_width, 1) if box_width else None,
        dpi_y=round(height_px * POINTS_PER_INCH / box_height, 1) if box_height else None,
        color_mode=(
            ColorMode.BILEVEL
            if components == 1 and bits == 1
            else PDF_COMPONENT_COLOR_MODES.get(components, ColorMode.UNKNOWN)
        ),
        bits_per_component=bits,
        image_format=PDF_FILTER_NAMES.get(image_filter, image_filter),
    )


def _image_page_facts(path: Path) -> PageFacts:
    """Describe one page image from its header, without decoding the pixel data.

    The size, resolution and physical size are those of the page as a viewer shows it, so an EXIF orientation that
    turns the stored image a quarter swaps them.

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
        exif = image.getexif() if image.format != PNG_FORMAT or PNG_EXIF_INFO_KEY in image.info else {}
        # Pillow reports 1 DPI for a TIFF without resolution tags, which would make the page metres wide
        dpi = image.info.get(DPI_KEY) if image.format != TIFF_FORMAT or ExifTags.Base.XResolution in exif else None
        dpi_x, dpi_y = (round(float(value), 1) if value > 0 else None for value in dpi or NO_DPI)
        width_px, height_px = image.size
        if exif.get(ExifTags.Base.Orientation) in QUARTER_TURN_ORIENTATIONS:
            width_px, height_px, dpi_x, dpi_y = height_px, width_px, dpi_y, dpi_x
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
                FactKey.PILLOW_MODE: image.mode,
                FactKey.FILE_SIZE_BYTES: path.stat().st_size,
                FactKey.EXIF: {tag.name: str(exif[tag]) for tag in EXIF_TAGS if tag in exif},
            },
        )


def _to_mm(length: float, *, units_per_inch: float) -> float:
    """Convert a length in points or pixels to millimetres, rounded to 0.1 mm."""
    return round(length / units_per_inch * MM_PER_INCH, 1)
