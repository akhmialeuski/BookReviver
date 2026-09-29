"""Native-resolution JPEG of one page: the embedded JPEG of a scanned PDF page, a rendered page, or a page image.

A scan stored as JPEG is copied byte for byte when the copy looks exactly like the page, because re-encoding a JPEG
loses quality and time. One predicate, ``_is_portable_jpeg``, decides that for PDF pages and page images alike: the
JPEG must be gray or RGB, which every reader shows the same, and carry no EXIF orientation other than upright, which
a browser applies while libvips ``dzsave`` and a PDF viewer do not. A PDF page must also show nothing but that one
image, upright over exactly its area, without a mask or a decode array, since ``extract_image`` returns the stored
stream without either.

Every other PDF page is rendered by PyMuPDF at the resolution of its dominant image, or at ``BORN_DIGITAL_DPI`` when it
has none. Every other page image is turned upright and encoded by Pillow with the same pixel values, 16-bit gray
scaled rather than clipped, and its colour profile kept unless the conversion changes the colour space.
"""

import io
import shutil
from typing import TYPE_CHECKING, assert_never, override

import pymupdf
from asyncer import asyncify
from PIL import ExifTags, Image, ImageOps

from bookreviver.adapters.imaging.inspector import PDF_IMAGE_FILTER_INDEX, PyMuPdfKey, natural_order, pdf_page_facts
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
# EXIF orientation of an image stored the way it is meant to be seen
UPRIGHT_ORIENTATION: int = 1
# JPEG mode for the Pillow modes that do not become RGB
GRAY_MODE: str = 'L'
GRAY_JPEG_MODES: Mapping[str, str] = {'1': GRAY_MODE, 'L': GRAY_MODE, 'LA': GRAY_MODE, 'F': GRAY_MODE}
RGB_MODE: str = 'RGB'
INT32_MODE: str = 'I'
# Both 'I;16*' and 'I' hold samples wider than 8 bits
WIDE_GRAY_MODE_PREFIX: str = 'I'
# Maps a 16-bit sample onto 0..255; a plain conversion to 'L' clips instead of scaling
SIXTEEN_TO_EIGHT_BIT_SCALE: float = 1 / 256
ICC_PROFILE_KEY: str = 'icc_profile'
# Modes whose pixels Pillow converts into another colour space, so their embedded profile no longer describes them
PROFILE_CHANGING_MODES: frozenset[str] = frozenset({'CMYK', 'LAB', 'YCbCr'})

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


class PdfImagePageRasterizer(PageRasterizer):
    """Writes a page as JPEG, copying the bytes of a scan when the page is nothing but that scan."""

    def __init__(self, *, jpeg_quality: int) -> None:
        """Encode rendered and converted pages at ``jpeg_quality``.

        :param jpeg_quality: JPEG quality from 1 to 100 for every page that is not copied.
        :type jpeg_quality: int
        """
        self._jpeg_quality = jpeg_quality

    @override
    async def extract(self, kind: SourceKind, files: Sequence[Path], index: int, target: Path) -> None:
        """Write one page as JPEG in a worker thread, since PyMuPDF and Pillow block.

        :param kind: Whether the files are one PDF or a set of page images.
        :type kind: SourceKind
        :param files: Local paths of the source files; a PDF source is its first file.
        :type files: Sequence[Path]
        :param index: Position of the page in book order, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
        """
        match kind:
            case SourceKind.PDF:
                await asyncify(self._extract_pdf_page)(files[0], index=index, target=target)
            case SourceKind.IMAGES:
                await asyncify(self._convert_image)(natural_order(files)[index], target=target)
            case _:
                assert_never(kind)

    def _extract_pdf_page(self, path: Path, *, index: int, target: Path) -> None:
        """Copy the page's embedded JPEG, or render the page at the resolution of its dominant image.

        :param path: PDF file of the source.
        :type path: Path
        :param index: Position of the page in the PDF, starting at 0.
        :type index: int
        :param target: Path to write the JPEG at.
        :type target: Path
        """
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
        """Copy an upright gray or RGB JPEG as it is, and encode any other page image upright as JPEG.

        The pixels keep their values and their colour profile, unless the conversion changes the colour space.

        :param path: Page image to write.
        :type path: Path
        :param target: Path to write the JPEG at.
        :type target: Path
        """
        with Image.open(path) as image:
            if _is_portable_jpeg(image):
                shutil.copyfile(path, target)
                return
            upright = ImageOps.exif_transpose(image)
            if upright.mode.startswith(WIDE_GRAY_MODE_PREFIX):
                scaled = upright.convert(INT32_MODE).point(lambda value: value * SIXTEEN_TO_EIGHT_BIT_SCALE)
                converted = scaled.convert(GRAY_MODE)
            else:
                converted = upright.convert(GRAY_JPEG_MODES.get(upright.mode, RGB_MODE))
            profile = None if image.mode in PROFILE_CHANGING_MODES else image.info.get(ICC_PROFILE_KEY)
            converted.save(target, format=JPEG_FORMAT, quality=self._jpeg_quality, icc_profile=profile)


def _is_portable_jpeg(image: Image.Image) -> bool:
    """Return whether every reader shows the JPEG as its pixels are stored: gray or RGB, and upright.

    A browser turns a JPEG by its EXIF orientation while libvips ``dzsave`` and a PDF viewer do not, so an oriented
    JPEG would give tiles, thumbnail and page that disagree.

    :param image: Open image, of which only the header is read.
    :type image: Image.Image
    :returns: True when the file can be copied as the page image.
    :rtype: bool
    """
    orientation = int(image.getexif().get(ExifTags.Base.Orientation, UPRIGHT_ORIENTATION))
    return image.format == JPEG_FORMAT and image.mode in PORTABLE_JPEG_MODES and orientation == UPRIGHT_ORIENTATION


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
        return jpeg if _is_portable_jpeg(image) else None
