"""Build sample PDFs, page images and DjVu documents in a test's temporary directory, so no binary fixture is committed.

The DjVu samples are encoded by the DjVuLibre tools from gradient images, so a test states the size, resolution and
colour mode of every page and compares the facts the format reports with them. One list of pages makes a bundled
document, an indirect document and single-page files alike.

A CMYK sample with an embedded colour profile is made by libvips, which carries a CMYK profile of its own, so the
repository holds no profile file: Pillow can only create sRGB, LAB and XYZ profiles. libvips also converts such a
sample back to sRGB by its embedded profile, which is the reference a test holds the application's conversion to.
"""

import io
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pymupdf
import pytest
import pyvips
from attrs import field, frozen
from PIL import Image, ImageOps, TiffImagePlugin

from bookreviver.adapters.imaging import DjvuLibreTools
from bookreviver.domain.enums import ColorMode

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from PIL import ExifTags

# Names libvips knows its built-in colour profiles by
VIPS_CMYK_PROFILE: str = 'cmyk'
VIPS_SRGB_PROFILE: str = 'srgb'
# Tools that encode and assemble the DjVu samples, beside the tools the format reads them with
DJVU_BUILD_TOOLS: tuple[str, ...] = ('c44', 'cjb2', 'djvm', 'djvmcvt', 'djvused')
requires_djvulibre = pytest.mark.skipif(
    DjvuLibreTools.locate() is None or any(shutil.which(tool) is None for tool in DJVU_BUILD_TOOLS),
    reason='DjVuLibre is not installed',
)
# The sample of every colour mode: the image mode Pillow writes, the file suffix, and the encoder of DjVuLibre
DJVU_SAMPLE_MODES: dict[ColorMode, tuple[str, str, str]] = {
    ColorMode.COLOR: ('RGB', '.ppm', 'c44'),
    ColorMode.GRAY: ('L', '.pgm', 'c44'),
    ColorMode.BILEVEL: ('1', '.pbm', 'cjb2'),
}
DJVU_SUFFIX: str = '.djvu'
DJVU_PAGE_PREFIX: str = 'p'
DJVU_PAGE_NAME: str = '{prefix}{number:04d}.djvu'
DJVU_BUNDLE_NAME: str = 'bundle.djvu'
DJVU_SCRIPT_NAME: str = 'script.txt'

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


def write_pdf_of_image(path: Path, *, image: Path, size_pt: tuple[float, float] = LETTER_SIZE_PT) -> Path:
    """Write a one-page PDF showing an image file over the whole page, embedded with the bytes the file holds.

    :param path: Where to write the PDF.
    :type path: Path
    :param image: Image file to embed, such as a CMYK JPEG with its colour profile.
    :type image: Path
    :param size_pt: Width and height of the page in points.
    :type size_pt: tuple[float, float]
    :returns: The written path.
    :rtype: Path
    """
    with pymupdf.open() as document:
        page = document.new_page(width=size_pt[0], height=size_pt[1])
        page.insert_image(page.rect, stream=image.read_bytes(), keep_proportion=False)
        document.save(path)
    return path


def write_cmyk_with_profile(path: Path, *, rgb: tuple[int, int, int], size: tuple[int, int] = (64, 64)) -> Path:
    """Write a flat CMYK image of one colour, with the CMYK profile of libvips embedded in it.

    The inks are what the colour ``rgb`` needs in that profile, so the colour a viewer with colour management shows for
    the file is close to ``rgb``, and ``icc_reference_color`` gives it exactly. The file suffix selects the format,
    which carries the profile: a ``.tif`` or a ``.jpg``.

    :param path: Where to write the file; its suffix selects the format.
    :type path: Path
    :param rgb: The sRGB colour the page shows.
    :type rgb: tuple[int, int, int]
    :param size: Width and height in pixels.
    :type size: tuple[int, int]
    :returns: The written path.
    :rtype: Path
    """
    flat = pyvips.Image.black(*size, bands=len(rgb)).new_from_image(list(rgb)).copy(interpretation=VIPS_SRGB_PROFILE)
    flat.icc_transform(VIPS_CMYK_PROFILE).write_to_file(str(path))
    return path


def icc_reference_color(path: Path) -> tuple[int, ...]:
    """Return the colour of the first pixel of an image file in sRGB, converted by its embedded profile by libvips.

    libvips converts with Little CMS, like Pillow does, but through code of its own, so it is an independent reference
    for the conversion the application makes with Pillow.

    :param path: Image file with an embedded colour profile.
    :type path: Path
    :returns: The red, green and blue values of the pixel.
    :rtype: tuple[int, ...]
    """
    converted = pyvips.Image.new_from_file(str(path)).icc_transform(VIPS_SRGB_PROFILE, embedded=True)
    return tuple(round(value) for value in converted(0, 0))


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


@frozen(kw_only=True)
class TiffFrame:
    """One frame of a multi-page TIFF, with the save options only this frame is written with.

    :ivar mode: Pillow mode of the frame.
    :ivar size_px: Width and height of the frame in pixels.
    :ivar options: Pillow save options of the frame, such as ``icc_profile``, ``dpi`` or raw tags under ``tiffinfo``.
    """

    mode: str
    size_px: tuple[int, int]
    options: Mapping[str, Any] = field(factory=dict)


def write_tiff(path: Path, *, frames: Sequence[TiffFrame]) -> Path:
    """Write a multi-page TIFF of gradient frames, each frame written with its own save options.

    Pillow takes the options of each appended frame from its ``encoderinfo``, over the options the whole file is saved
    with, which become the defaults of every frame. The first frame is therefore written without options, so that no
    option of one frame reaches another.

    :param path: Where to write the file.
    :type path: Path
    :param frames: Frames in page order, the first without options.
    :type frames: Sequence[TiffFrame]
    :returns: The written path.
    :rtype: Path
    :raises ValueError: If the first frame has options, which Pillow would give every frame.
    """
    first, *later = frames
    if first.options:
        err_msg = 'The options of the first frame would become the defaults of every frame; give it none.'
        raise ValueError(err_msg)
    images = [gradient_image(mode=frame.mode, size=frame.size_px) for frame in frames]
    for image, frame in zip(images[1:], later, strict=True):
        image.encoderinfo = dict(frame.options)
    images[0].save(path, save_all=True, append_images=images[1:])
    return path


def frame_pixel_span(path: Path, *, number: int) -> tuple[int, int]:
    """Return where the pixel data of one frame of a single-strip TIFF lies in the file.

    :param path: A TIFF whose frame stores its pixels in one strip, as ``write_tiff`` writes small frames.
    :type path: Path
    :param number: Number of the frame.
    :type number: int
    :returns: Offset of the first byte of the strip and its length in bytes.
    :rtype: tuple[int, int]
    :raises TypeError: If the file is not a TIFF.
    """
    with Image.open(path) as image:
        if not isinstance(image, TiffImagePlugin.TiffImageFile):
            err_msg = f'{path.name} is not a TIFF.'
            raise TypeError(err_msg)
        image.seek(number)
        tags = image.tag_v2
        return int(tags[TiffImagePlugin.STRIPOFFSETS][0]), int(tags[TiffImagePlugin.STRIPBYTECOUNTS][0])


@frozen(kw_only=True)
class DjvuPage:
    """One page of a DjVu sample: its size, resolution and colour mode.

    :ivar size_px: Width and height of the page in pixels.
    :ivar dpi: Resolution the page is encoded with.
    :ivar mode: Colour mode of the page, which selects the encoder and so the chunks of the page.
    """

    size_px: tuple[int, int] = (300, 400)
    dpi: int = 300
    mode: ColorMode = ColorMode.GRAY


def run_djvulibre(*command: str | Path) -> None:
    """Run one DjVuLibre tool and fail loudly when it does.

    :param command: The tool and its arguments.
    :type command: str | Path
    """
    subprocess.run([*command], check=True, capture_output=True)


def write_djvu_page(path: Path, *, page: DjvuPage) -> Path:
    """Encode a gradient page as a single-page DjVu file, bilevel pages with JB2 and the others with IW44.

    :param path: Where to write the file.
    :type path: Path
    :param page: Size, resolution and colour mode of the page.
    :type page: DjvuPage
    :returns: The written path.
    :rtype: Path
    """
    image_mode, suffix, encoder = DJVU_SAMPLE_MODES[page.mode]
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / f'page{suffix}'
        image = gradient_image(mode=image_mode, size=page.size_px)
        # A gray gradient converted to RGB holds no colour, so the colour sample runs from red to blue
        if page.mode is ColorMode.COLOR:
            image = ImageOps.colorize(gradient_image(mode=GRADIENT_MODE, size=page.size_px), black='red', white='blue')
        image.save(source)
        run_djvulibre(encoder, '-dpi', str(page.dpi), source, path)
    return path


def write_djvu_bundle(path: Path, *, pages: Sequence[DjvuPage], prefix: str = DJVU_PAGE_PREFIX) -> Path:
    """Assemble pages into one bundled DjVu document, whose components are named after ``prefix``.

    :param path: Where to write the file.
    :type path: Path
    :param pages: Pages in order.
    :type pages: Sequence[DjvuPage]
    :param prefix: Start of the names of the components, which become the names of the page files of an indirect
                   document made of this one.
    :type prefix: str
    :returns: The written path.
    :rtype: Path
    """
    with tempfile.TemporaryDirectory() as directory:
        files = [
            write_djvu_page(Path(directory) / DJVU_PAGE_NAME.format(prefix=prefix, number=number), page=page)
            for number, page in enumerate(pages, start=1)
        ]
        run_djvulibre('djvm', '-c', path, *files)
    return path


def write_djvu_pages(directory: Path, *, pages: Sequence[DjvuPage], prefix: str = DJVU_PAGE_PREFIX) -> list[Path]:
    """Write pages as single-page DjVu files named ``p0001.djvu`` and so on.

    :param directory: Existing directory to write the files into.
    :type directory: Path
    :param pages: Pages in order.
    :type pages: Sequence[DjvuPage]
    :param prefix: Start of the names of the files.
    :type prefix: str
    :returns: The written files in page order.
    :rtype: list[Path]
    """
    return [
        write_djvu_page(directory / DJVU_PAGE_NAME.format(prefix=prefix, number=number), page=page)
        for number, page in enumerate(pages, start=1)
    ]


def write_djvu_indirect(
    directory: Path, *, pages: Sequence[DjvuPage], index_name: str = 'index.djvu', prefix: str = DJVU_PAGE_PREFIX
) -> list[Path]:
    """Write an indirect DjVu document: an index file, and one file per page named ``p0001.djvu`` and so on.

    :param directory: Existing directory to write the index and the page files into.
    :type directory: Path
    :param pages: Pages in order.
    :type pages: Sequence[DjvuPage]
    :param index_name: Name of the index file.
    :type index_name: str
    :param prefix: Start of the names of the page files, so that two documents in one upload share no name.
    :type prefix: str
    :returns: The index file first, then the page files in page order.
    :rtype: list[Path]
    """
    with tempfile.TemporaryDirectory() as scratch:
        bundle = write_djvu_bundle(Path(scratch) / DJVU_BUNDLE_NAME, pages=pages, prefix=prefix)
        run_djvulibre('djvmcvt', '-i', bundle, directory, index_name)
    return [
        directory / index_name,
        *(directory / DJVU_PAGE_NAME.format(prefix=prefix, number=number) for number in range(1, len(pages) + 1)),
    ]


def write_djvu_including(path: Path, *, shared_name: str) -> Path:
    """Write a single-page DjVu file that includes a shared data file, which is not written.

    :param path: Where to write the file.
    :type path: Path
    :param shared_name: Name of the shared file the page includes.
    :type shared_name: str
    :returns: The written path.
    :rtype: Path
    """
    with tempfile.TemporaryDirectory() as directory:
        page = write_djvu_page(Path(directory) / 'page.djvu', page=DjvuPage(mode=ColorMode.BILEVEL))
        mask = Path(directory) / 'mask.jb2'
        run_djvulibre('djvuextract', page, f'Sjbz={mask}')
        run_djvulibre('djvumake', path, 'INFO=300,400,300', f'Sjbz={mask}', f'INCL={shared_name}')
    return path


def edit_djvu(path: Path, *, command: str, script: str) -> None:
    """Change a DjVu document by a ``djvused`` command that reads its data from a script file.

    :param path: The document to change in place.
    :type path: Path
    :param command: The ``djvused`` command, such as ``set-meta`` or ``set-outline``.
    :type command: str
    :param script: What the command reads: metadata pairs, an outline or a text layer, in UTF-8.
    :type script: str
    """
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / DJVU_SCRIPT_NAME
        source.write_text(script, encoding='utf-8')
        run_djvulibre('djvused', '-s', '-e', f'{command} {source}', path)


def add_xmp(path: Path, *, packet: str) -> Path:
    """Embed an XMP packet in a PDF file that was written without one.

    :param path: The PDF to change in place.
    :type path: Path
    :param packet: The XMP packet, such as ``xmp_packet`` builds.
    :type packet: str
    :returns: The same path.
    :rtype: Path
    """
    with pymupdf.open(path) as document:
        document.set_xml_metadata(packet)
        document.saveIncr()
    return path


def xmp_packet(dublin_core: str) -> str:
    """Wrap Dublin Core elements into an XMP packet the way an editor of PDF metadata writes it.

    :param dublin_core: Markup of ``dc:`` elements, such as ``<dc:title>...</dc:title>``.
    :type dublin_core: str
    :returns: The packet, with its ``xpacket`` processing instructions and the RDF description holding the elements.
    :rtype: str
    """
    return (
        '<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'{dublin_core}'
        '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )
