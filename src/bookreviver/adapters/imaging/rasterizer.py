"""Native-resolution JPEG of one page: the embedded JPEG of a scanned PDF page, a rendered page, or a page image."""

import shutil
from typing import TYPE_CHECKING, override

import pymupdf
from asyncer import asyncify
from PIL import Image

from bookreviver.adapters.imaging.inspector import PDF_IMAGE_FILTER_INDEX, natural_order, pdf_page_facts
from bookreviver.domain.enums import ColorMode, SourceKind
from bookreviver.ports.imaging import PageRasterizer

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

# Resolution of a page that has no raster image to take it from
BORN_DIGITAL_DPI: float = 300.0
JPEG_FORMAT: str = 'JPEG'
PYMUPDF_JPEG_OUTPUT: str = 'jpeg'
# Pillow modes a browser and libvips read from a JPEG file as they are
PORTABLE_JPEG_MODES: frozenset[str] = frozenset({'L', 'RGB'})
# JPEG mode for the Pillow modes that do not become RGB
GRAY_MODE: str = 'L'
GRAY_JPEG_MODES: Mapping[str, str] = {'1': GRAY_MODE, 'L': GRAY_MODE, 'LA': GRAY_MODE, 'F': GRAY_MODE}
RGB_MODE: str = 'RGB'
INT32_MODE: str = 'I'
# Both 'I;16*' and 'I' hold samples wider than 8 bits
WIDE_GRAY_MODE_PREFIX: str = 'I'
# Maps a 16-bit sample onto 0..255; a plain conversion to 'L' clips instead of scaling
SIXTEEN_TO_EIGHT_BIT_SCALE: float = 1 / 256

# Rules for copying the embedded JPEG of a PDF page instead of rendering the page
DCT_FILTER: str = 'DCTDecode'
# Colour components of a JPEG every reader shows as the page does: gray and RGB, not CMYK
PORTABLE_JPEG_COMPONENTS: frozenset[int] = frozenset({1, 3})
# Text render mode 3 draws nothing, as in the OCR layer of a scan
INVISIBLE_TEXT_TYPE: int = 3
# How far in points the image may fall short of a page edge and still count as covering it
COVER_TOLERANCE_PT: float = 1.0
COVER_MARGIN: tuple[float, float, float, float] = (
    -COVER_TOLERANCE_PT,
    -COVER_TOLERANCE_PT,
    COVER_TOLERANCE_PT,
    COVER_TOLERANCE_PT,
)


class PdfImagePageRasterizer(PageRasterizer):
    """Writes a page as JPEG, copying the bytes of a scan when the page is nothing but that scan."""

    def __init__(self, *, jpeg_quality: int) -> None:
        self._jpeg_quality = jpeg_quality

    @override
    async def extract(self, kind: SourceKind, files: Sequence[Path], index: int, target: Path) -> None:
        match kind:
            case SourceKind.PDF:
                await asyncify(self._extract_pdf_page)(files[0], index=index, target=target)
            case SourceKind.IMAGES:
                await asyncify(self._convert_image)(natural_order(files)[index], target=target)

    def _extract_pdf_page(self, path: Path, *, index: int, target: Path) -> None:
        """Copy the page's embedded JPEG, or render the page at the resolution of its dominant image."""
        with pymupdf.open(path) as document:
            page = document[index]
            if (jpeg := _embedded_jpeg(document, page)) is not None:
                target.write_bytes(jpeg)
                return
            facts = pdf_page_facts(page)
            dpi = max(filter(None, (facts.dpi_x, facts.dpi_y)), default=BORN_DIGITAL_DPI)
            gray = facts.color_mode in {ColorMode.GRAY, ColorMode.BILEVEL}
            pixmap = page.get_pixmap(dpi=round(dpi), colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB)
        pixmap.save(target, output=PYMUPDF_JPEG_OUTPUT, jpg_quality=self._jpeg_quality)

    def _convert_image(self, path: Path, *, target: Path) -> None:
        """Copy a gray or RGB JPEG as it is, and encode any other page image as JPEG with the same pixels."""
        with Image.open(path) as image:
            if image.format == JPEG_FORMAT and image.mode in PORTABLE_JPEG_MODES:
                shutil.copyfile(path, target)
                return
            if image.mode.startswith(WIDE_GRAY_MODE_PREFIX):
                scaled = image.convert(INT32_MODE).point(lambda value: value * SIXTEEN_TO_EIGHT_BIT_SCALE)
                converted = scaled.convert(GRAY_MODE)
            else:
                converted = image.convert(GRAY_JPEG_MODES.get(image.mode, RGB_MODE))
            converted.save(target, format=JPEG_FORMAT, quality=self._jpeg_quality)


def _embedded_jpeg(document: pymupdf.Document, page: pymupdf.Page) -> bytes | None:
    """Return the JPEG a page shows, when the page shows nothing else and shows it upright over the whole page.

    Anything drawn beside or over the image, a rotation, a mask or CMYK colour would make the copied bytes differ
    from what the page looks like, so the page is rendered instead.
    """
    placements = page.get_image_info(xrefs=True)
    if page.rotation or len(placements) != 1 or page.first_annot is not None or page.get_drawings():
        return None
    placement = placements[0]
    xref = placement['xref']
    scale_x, shear_y, shear_x, scale_y = placement['transform'][:4]
    bbox = pymupdf.Rect(placement['bbox']) + COVER_MARGIN
    filters = {image[0]: image[PDF_IMAGE_FILTER_INDEX] for image in page.get_images(full=True)}
    only_upright_jpeg = (
        scale_x > 0
        and scale_y > 0
        and not shear_x
        and not shear_y
        and bbox.contains(page.rect)
        and not placement['has-mask']
        and placement['colorspace'] in PORTABLE_JPEG_COMPONENTS
        and filters.get(xref) == DCT_FILTER
        and all(span['type'] == INVISIBLE_TEXT_TYPE for span in page.get_texttrace())
    )
    if not only_upright_jpeg:
        return None
    return bytes(document.extract_image(xref)['image'])
