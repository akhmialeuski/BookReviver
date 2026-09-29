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
# Keyword of Pillow's ``Image.save`` carrying the EXIF block
EXIF_OPTION: str = 'exif'


def gradient_image(*, mode: str, size: tuple[int, int]) -> Image.Image:
    """Return a horizontal gradient in the given mode.

    A varying image keeps its declared depth; PyMuPDF stores a uniform grey image as one bit per pixel.

    :param mode: Pillow mode of the returned image.
    :type mode: str
    :param size: Width and height in pixels.
    :type size: tuple[int, int]
    :returns: The gradient, dark on the left and light on the right.
    :rtype: Image.Image
    """
    gradient = Image.linear_gradient(GRADIENT_MODE).rotate(GRADIENT_ROTATION_DEGREES).resize(size)
    return gradient.convert(mode)


def exif_of(tags: Mapping[ExifTags.Base, str | int]) -> Image.Exif:
    """Return an EXIF block holding the given tags.

    :param tags: Tag values by tag, such as the maker as a string or the orientation as a number.
    :type tags: Mapping[ExifTags.Base, str | int]
    :returns: EXIF block Pillow writes into an image file.
    :rtype: Image.Exif
    """
    exif = Image.Exif()
    for tag, value in tags.items():
        exif[tag] = value
    return exif


def encode_image(
    image: Image.Image, *, image_format: str, exif: Mapping[ExifTags.Base, str | int] | None = None
) -> bytes:
    """Return the image encoded in a Pillow format such as ``JPEG`` or ``PNG``, with the given EXIF tags.

    :param image: Image to encode.
    :type image: Image.Image
    :param image_format: Pillow format name, such as ``JPEG`` or ``PNG``.
    :type image_format: str
    :param exif: EXIF tags to store in the file, or None to store no EXIF block.
    :type exif: Mapping[ExifTags.Base, str | int] | None
    :returns: The encoded file.
    :rtype: bytes
    """
    buffer = io.BytesIO()
    options: dict[str, Any] = {EXIF_OPTION: exif_of(exif)} if exif else {}
    image.save(buffer, format=image_format, **options)
    return buffer.getvalue()


@frozen(kw_only=True)
class ScanImage:
    """One raster image placed on a PDF page.

    :ivar mode: Pillow mode of the image.
    :ivar size_px: Width and height of the image in pixels.
    :ivar image_format: Pillow format the image is encoded in before embedding, such as ``JPEG``.
    :ivar rect: Placement in points as (x0, y0, x1, y1), or None to fill the whole page.
    :ivar rotate: Quarter turns of the placement in degrees, as PyMuPDF's ``insert_image`` takes them.
    :ivar decode: PDF decode array of the image object, such as ``[1 0]`` to invert gray, or None to leave it out.
    :ivar exif: EXIF tags stored inside the encoded image.
    """

    mode: str
    size_px: tuple[int, int]
    image_format: str
    rect: tuple[float, float, float, float] | None = None
    rotate: int = 0
    decode: str | None = None
    exif: Mapping[ExifTags.Base, str | int] = field(factory=dict)

    def encoded(self) -> bytes:
        """Return the image bytes exactly as they are embedded in the PDF.

        :returns: The gradient encoded in ``image_format`` with the ``exif`` tags.
        :rtype: bytes
        """
        image = gradient_image(mode=self.mode, size=self.size_px)
        return encode_image(image, image_format=self.image_format, exif=self.exif)


@frozen(kw_only=True)
class PdfPage:
    """One PDF page: its size, the images placed on it, its visible text and its invisible OCR text.

    :ivar size_pt: Width and height of the page in points.
    :ivar images: Images placed on the page, in drawing order.
    :ivar text: Visible text drawn on the page, or empty for none.
    :ivar ocr_text: Text drawn with render mode 3, which shows nothing, as the OCR layer of a scan.
    """

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

    :param path: Where to write the file.
    :type path: Path
    :param pages: Pages in order.
    :type pages: Sequence[PdfPage]
    :param metadata: PyMuPDF metadata keys such as ``title`` and ``author``, or None for none.
    :type metadata: Mapping[str, str] | None
    :param outline: Titles of top-level outline entries, all pointing at the first page.
    :type outline: Sequence[str]
    :param user_password: Encrypt the file so it needs this password to open, when not empty.
    :type user_password: str
    :returns: The written path.
    :rtype: Path
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

    :param path: Where to write the file; its suffix selects the format.
    :type path: Path
    :param mode: Pillow mode of the stored image.
    :type mode: str
    :param size: Width and height in pixels.
    :type size: tuple[int, int]
    :param dpi: Resolution recorded in the file, or None to record none.
    :type dpi: float | None
    :param exif: EXIF tags to record, or None to record none.
    :type exif: Mapping[ExifTags.Base, str | int] | None
    :returns: The written path.
    :rtype: Path
    """
    image = gradient_image(mode=mode, size=size)
    options: dict[str, Any] = {}
    if dpi:
        options['dpi'] = (dpi, dpi)
    if exif:
        options[EXIF_OPTION] = exif_of(exif)
    image.save(path, **options)
    return path
