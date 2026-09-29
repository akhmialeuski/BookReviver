"""PDF sources read with PyMuPDF: the facts of every page, and each page written as JPEG.

A scanned PDF page is described by its dominant raster image, the one covering the largest area, because its pixel
size and placement give the resolution the page was scanned at. A page without images is described by its own size
in points. The dictionaries PyMuPDF returns are read through the keys of ``PyMuPdfKey``, so no key is spelled twice.

A page's embedded JPEG is copied byte for byte when the copy looks exactly like the page: the page must show nothing
but that one image, upright over exactly its area, without a mask or a decode array, since ``extract_image`` returns
the stored stream without either, and the JPEG must pass ``is_portable_jpeg``. Every other page is rendered at the
resolution of its dominant image, or at ``BORN_DIGITAL_DPI`` when it has none, from the same ``_page_facts`` the
inspection reported.
"""

import enum
import hashlib
import io
from typing import TYPE_CHECKING, Any, override

import pymupdf
from attrs import evolve
from PIL import Image

from bookreviver.adapters.imaging.common import SHA256, FactKey, is_portable_jpeg, to_mm
from bookreviver.adapters.imaging.reader import SourceFormat
from bookreviver.domain.enums import ColorMode, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import MetadataSuggestion, PageFacts, SourceAnalysis

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

POINTS_PER_INCH: float = 72.0
PDF_FILETYPE: str = 'pdf'
# Resolution of a page that has no raster image to take it from
BORN_DIGITAL_DPI: float = 300.0
PYMUPDF_JPEG_OUTPUT: str = 'jpeg'

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

# Rules for copying the embedded JPEG of a PDF page instead of rendering the page
DCT_FILTER: str = 'DCTDecode'
DECODE_KEY: str = 'Decode'
# Type PyMuPDF reports for a key missing from a PDF dictionary
PDF_NULL_TYPE: str = 'null'
# Text render mode 3 draws nothing, as in the OCR layer of a scan
INVISIBLE_TEXT_TYPE: int = 3
# How far in points the image and the page edges may disagree and still count as the same area
COVER_TOLERANCE_PT: float = 1.0
COVER_MARGIN: tuple[float, float, float, float] = (
    -COVER_TOLERANCE_PT,
    -COVER_TOLERANCE_PT,
    COVER_TOLERANCE_PT,
    COVER_TOLERANCE_PT,
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


class PdfFormat(SourceFormat):
    """Describes a PDF page by its dominant raster image, and copies or renders it as JPEG."""

    kind = SourceKind.PDF

    def __init__(self, *, jpeg_quality: int) -> None:
        """Encode rendered pages at ``jpeg_quality``.

        :param jpeg_quality: JPEG quality from 1 to 100 for every page that is not copied.
        :type jpeg_quality: int
        """
        self._jpeg_quality = jpeg_quality

    @override
    def inspect(self, files: Sequence[Path]) -> SourceAnalysis:
        """Describe every page of the one PDF of a source and extract its document metadata.

        :param files: Local paths of the source files, which must be exactly one PDF.
        :type files: Sequence[Path]
        :returns: Facts of every page, the document information, outline and integrity facts, and the title and
                  authors found in the document information.
        :rtype: SourceAnalysis
        :raises UnsupportedSourceError: If the source is not exactly one readable PDF without a password.
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
            pages = [_page_facts(page) for page in document.pages()]
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

    @override
    def extract(self, files: Sequence[Path], *, index: int, target: Path) -> None:
        """Copy the page's embedded JPEG, or render the page at the resolution of its dominant image.

        :param files: Local paths of the source files, of which the first is the PDF.
        :type files: Sequence[Path]
        :param index: Position of the page in the PDF, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
        """
        with pymupdf.open(files[0]) as document:
            page = document[index]
            if (jpeg := _embedded_jpeg(document, page)) is not None:
                target.write_bytes(jpeg)
                return
            facts = _page_facts(page)
            dpi = max(filter(None, (facts.dpi_x, facts.dpi_y)), default=BORN_DIGITAL_DPI)
            gray = facts.color_mode in {ColorMode.GRAY, ColorMode.BILEVEL}
            pixmap = page.get_pixmap(dpi=round(dpi), colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB)
        pixmap.save(target, output=PYMUPDF_JPEG_OUTPUT, jpg_quality=self._jpeg_quality)


def _page_facts(page: pymupdf.Page) -> PageFacts:
    """Describe one PDF page by its dominant raster image, or by its own size when it has none.

    :param page: Page of an open PyMuPDF document.
    :type page: pymupdf.Page
    :returns: Pixel size, resolution, colour mode, bit depth and image format of the dominant image, the physical size
              of the page, whether it has a text layer, and its image count, rotation and character count.
    :rtype: PageFacts
    """
    rect = page.rect
    images = page.get_image_info(xrefs=True)
    text_chars = len(page.get_textpage(flags=pymupdf.TEXTFLAGS_TEXT).extractText().strip())
    facts = PageFacts(
        width_px=round(rect.width),
        height_px=round(rect.height),
        color_mode=ColorMode.UNKNOWN,
        width_mm=to_mm(rect.width, units_per_inch=POINTS_PER_INCH),
        height_mm=to_mm(rect.height, units_per_inch=POINTS_PER_INCH),
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


def _embedded_jpeg(document: pymupdf.Document, page: pymupdf.Page) -> bytes | None:
    """Return the JPEG a page shows, when the page shows nothing else and shows it upright over exactly its area.

    Anything drawn beside or over the image, an image reaching past the page edges, a rotation, a mask, a decode
    array or a JPEG that is not portable would make the copied bytes differ from what the page looks like, so the
    page is rendered instead.

    :param document: Open PDF holding the page.
    :type document: pymupdf.Document
    :param page: Page to examine.
    :type page: pymupdf.Page
    :returns: Bytes of the embedded JPEG to copy, or None when the page must be rendered.
    :rtype: bytes | None
    """
    placements = page.get_image_info(xrefs=True)
    if page.rotation or len(placements) != 1 or page.first_annot is not None or page.get_drawings():
        return None
    placement = placements[0]
    xref = placement[PyMuPdfKey.XREF]
    scale_x, shear_y, shear_x, scale_y = placement[PyMuPdfKey.TRANSFORM][:4]
    image_rect = pymupdf.Rect(placement[PyMuPdfKey.BBOX])
    filters = {image[0]: image[PDF_IMAGE_FILTER_INDEX] for image in page.get_images(full=True)}
    only_upright_jpeg = (
        scale_x > 0
        and scale_y > 0
        and not shear_x
        and not shear_y
        # The image covers the page and hides nothing past its edges, as a crop box would
        and (image_rect + COVER_MARGIN).contains(page.rect)
        and (page.rect + COVER_MARGIN).contains(image_rect)
        and not placement[PyMuPdfKey.HAS_MASK]
        and filters.get(xref) == DCT_FILTER
        # extract_image returns the stream as stored, without the decode array a viewer applies to it
        and document.xref_get_key(xref, DECODE_KEY)[0] == PDF_NULL_TYPE
        and all(span[PyMuPdfKey.SPAN_TYPE] == INVISIBLE_TEXT_TYPE for span in page.get_texttrace())
    )
    if not only_upright_jpeg:
        return None
    jpeg = bytes(document.extract_image(xref)[PyMuPdfKey.IMAGE])
    with Image.open(io.BytesIO(jpeg)) as image:
        return jpeg if is_portable_jpeg(image) else None
