"""Build sample PDFs and page images in a test's temporary directory, so no binary fixture is committed."""

import io
from typing import TYPE_CHECKING, Any

import pymupdf
from attrs import field, frozen
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from PIL import ExifTags

# US Letter in points
LETTER_SIZE_PT: tuple[float, float] = (612.0, 792.0)
GRADIENT_MODE: str = 'L'
GRADIENT_ROTATION_DEGREES: int = 90
FIRST_OUTLINE_LEVEL: int = 1
TEXT_ORIGIN_PT: tuple[float, float] = (72.0, 72.0)
# PDF text render mode that draws nothing, as the OCR layer of a scan does
INVISIBLE_RENDER_MODE: int = 3
DECODE_KEY: str = 'Decode'


def gradient_image(*, mode: str, size: tuple[int, int]) -> Image.Image:
    """Return a horizontal gradient in the given mode.

    A varying image keeps its declared depth; PyMuPDF stores a uniform grey image as one bit per pixel.
    """
    gradient = Image.linear_gradient(GRADIENT_MODE).rotate(GRADIENT_ROTATION_DEGREES).resize(size)
    return gradient.convert(mode)


def exif_of(tags: Mapping[ExifTags.Base, str | int]) -> Image.Exif:
    """Return an EXIF block holding the given tags."""
    exif = Image.Exif()
    for tag, value in tags.items():
        exif[tag] = value
    return exif


def encode_image(
    image: Image.Image, *, image_format: str, exif: Mapping[ExifTags.Base, str | int] | None = None
) -> bytes:
    """Return the image encoded in a Pillow format such as ``JPEG`` or ``PNG``, with the given EXIF tags."""
    buffer = io.BytesIO()
    options: dict[str, Any] = {'exif': exif_of(exif)} if exif else {}
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


@frozen(kw_only=True)
class ScanImage:
    """One raster image placed on a PDF page."""

    mode: str
    size_px: tuple[int, int]
    image_format: str
    # Placement in points as (x0, y0, x1, y1); None fills the whole page
    rect: tuple[float, float, float, float] | None = None
    # Quarter turns of the placement in degrees, as PyMuPDF's ``insert_image`` takes them
    rotate: int = 0
    # PDF decode array of the image object, such as ``[1 0]`` to invert gray; None leaves it out
    decode: str | None = None
    # EXIF tags stored inside the encoded image
    exif: Mapping[ExifTags.Base, str | int] = field(factory=dict)

    def encoded(self) -> bytes:
        """Return the image bytes exactly as they are embedded in the PDF."""
        image = gradient_image(mode=self.mode, size=self.size_px)
        return encode_image(image, image_format=self.image_format, exif=self.exif)


@frozen(kw_only=True)
class PdfPage:
    """One PDF page: its size, the images placed on it, its visible text and its invisible OCR text."""

    size_pt: tuple[float, float] = LETTER_SIZE_PT
    images: Sequence[ScanImage] = field(factory=tuple)
    text: str = ''
    ocr_text: str = ''


def write_pdf(
    path: Path,
    *,
    pages: Sequence[PdfPage],
    metadata: Mapping[str, str] | None = None,
    outline: Sequence[str] = (),
    user_password: str = '',
) -> Path:
    """Write a PDF with the given pages, document metadata and top-level outline titles.

    :param path:          Where to write the file.
    :param pages:         Pages in order.
    :param metadata:      PyMuPDF metadata keys such as ``title`` and ``author``.
    :param outline:       Titles of top-level outline entries, all pointing at the first page.
    :param user_password: Encrypt the file so it needs this password to open, when not empty.
    :return:              The written path.
    """
    with pymupdf.open() as document:
        for spec in pages:
            width, height = spec.size_pt
            page = document.new_page(width=width, height=height)
            for image in spec.images:
                rect = pymupdf.Rect(image.rect) if image.rect else page.rect
                xref = page.insert_image(rect, stream=image.encoded(), keep_proportion=False, rotate=image.rotate)
                if image.decode:
                    document.xref_set_key(xref, DECODE_KEY, image.decode)
            if spec.text:
                page.insert_text(TEXT_ORIGIN_PT, spec.text)
            if spec.ocr_text:
                page.insert_text(TEXT_ORIGIN_PT, spec.ocr_text, render_mode=INVISIBLE_RENDER_MODE)
        if metadata:
            document.set_metadata(dict(metadata))
        if outline:
            document.set_toc([[FIRST_OUTLINE_LEVEL, title, 1] for title in outline])
        if user_password:
            document.save(
                path, encryption=pymupdf.mupdf.PDF_ENCRYPT_AES_256, user_pw=user_password, owner_pw=user_password
            )
        else:
            document.save(path)
    return path


def write_image(
    path: Path,
    *,
    mode: str,
    size: tuple[int, int],
    dpi: float | None = None,
    exif: Mapping[ExifTags.Base, str | int] | None = None,
) -> Path:
    """Write a gradient image; the file suffix selects the format.

    :param path:   Where to write the file.
    :param mode:   Pillow mode of the stored image.
    :param size:   Width and height in pixels.
    :param dpi:    Resolution recorded in the file, or none recorded.
    :param exif:   EXIF tags to record.
    :return:       The written path.
    """
    image = gradient_image(mode=mode, size=size)
    options: dict[str, Any] = {}
    if dpi:
        options['dpi'] = (dpi, dpi)
    if exif:
        options['exif'] = exif_of(exif)
    image.save(path, **options)
    return path
