"""Tests for the PyMuPDF and Pillow source inspector."""

import json
from typing import TYPE_CHECKING, Any, NamedTuple
from unittest.mock import patch

import pytest
from delayed_assert import assert_expectations, expect
from PIL import ExifTags, TiffImagePlugin

from bookreviver.adapters.imaging.common import FactKey
from bookreviver.domain.enums import ColorMode, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import MetadataSuggestion
from tests.adapters.imaging.samples import (
    PdfPage,
    ScanImage,
    TiffFrame,
    encode_image,
    gradient_image,
    write_image,
    write_pdf,
    write_tiff,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path
    from unittest.mock import MagicMock

    from bookreviver.ports.imaging import SourceInspector

pytestmark = pytest.mark.anyio

IMAGE_FILE_LOAD_PATCH: str = 'PIL.ImageFile.ImageFile.load'

CASE_ARG: str = 'case'
SUFFIX_ARG: str = 'suffix'
PDF_NAME: str = 'book.pdf'
PAGE_STEM: str = 'page'
TIF_SUFFIX: str = '.tif'
PNG_SUFFIX: str = '.png'
JPG_SUFFIX: str = '.jpg'
JP2_SUFFIX: str = '.jp2'
RESOLUTION_SUFFIXES: tuple[str, ...] = (TIF_SUFFIX, PNG_SUFFIX, JPG_SUFFIX)
JPEG: str = 'JPEG'
PNG: str = 'PNG'
RGB_MODE: str = 'RGB'
CMYK_MODE: str = 'CMYK'
GRAY_16_MODE: str = 'I;16'

# 8 x 10 inches, so an image of 1200 x 1500 pixels filling it is exactly 150 DPI
SCAN_PAGE_SIZE_PT: tuple[float, float] = (576.0, 720.0)
# The same page in landscape, holding the same scan turned a quarter
LANDSCAPE_PAGE_SIZE_PT: tuple[float, float] = (720.0, 576.0)
QUARTER_TURN_DEGREES: int = 90
# EXIF orientation telling a viewer to turn the stored image a quarter clockwise
QUARTER_TURN_ORIENTATION: int = 6
SCAN_SIZE_PX: tuple[int, int] = (1200, 1500)
SCAN_DPI: float = 150.0
SCAN_PAGE_WIDTH_MM: float = 203.2
SCAN_PAGE_HEIGHT_MM: float = 254.0
SMALL_SIZE_PX: tuple[int, int] = (64, 48)
PAGE_TEXT: str = 'Printed text'

DOCUMENT_TITLE: str = 'Slutsk Gospels'
PADDED_DOCUMENT_TITLE: str = f'  {DOCUMENT_TITLE} '
DOCUMENT_AUTHOR: str = 'Unknown scribe'
TITLE_KEY: str = 'title'
SUBJECT_KEY: str = 'subject'
DOCUMENT_INFO_KEY: str = 'document_info'
HAS_OUTLINE_KEY: str = 'has_outline'
REPAIRED_KEY: str = 'repaired'
PAGE_COUNT_KEY: str = 'page_count'
OUTLINE_TITLES: tuple[str, ...] = ('Preface', 'Chapter one')
PASSWORD: str = 'secret'
NOT_A_PDF_MATCH: str = 'is not a PDF'

KIND_ARG: str = 'kind'
FILE_COUNT_ARG: str = 'file_count'
# Gray, colour and bilevel frames of a multi-page TIFF, each of its own size
FRAME_MODES: tuple[str, ...] = ('L', RGB_MODE, '1')
FRAME_SIZES_PX: tuple[tuple[int, int], ...] = ((64, 48), (80, 60), (96, 72))
MAX_FRAME_PIXELS_PATCH: str = 'bookreviver.adapters.imaging.images.MAX_FRAME_PIXELS'
TIFF_NAME: str = 'pages.tif'
TIFF_NAME_PATTERN: str = r'pages\.tif'
PLAIN_FRAME: TiffFrame = TiffFrame(mode='L', size_px=SMALL_SIZE_PX)
# A photometric interpretation TIFF does not define, which Pillow parses only on seeking to the frame
UNKNOWN_PIXEL_MODE_FRAME: TiffFrame = TiffFrame(
    mode='L', size_px=SMALL_SIZE_PX, options={'tiffinfo': {TiffImagePlugin.PHOTOMETRIC_INTERPRETATION: 99}}
)
# Admits the 64 x 48 frame and refuses the 128 x 96 one
SMALL_FRAME_PIXELS: int = 64 * 48
LARGE_FRAME: TiffFrame = TiffFrame(mode='L', size_px=(128, 96))
# Resolution unit 1 means no absolute unit, so the frame has no DPI
UNITLESS_RESOLUTION_TAGS: dict[int, int] = {
    TiffImagePlugin.RESOLUTION_UNIT: 1,
    TiffImagePlugin.X_RESOLUTION: 200,
    TiffImagePlugin.Y_RESOLUTION: 200,
}
# 100 dots per centimetre, resolution unit 3
CENTIMETRE_RESOLUTION_TAGS: dict[int, int] = {
    TiffImagePlugin.RESOLUTION_UNIT: 3,
    TiffImagePlugin.X_RESOLUTION: 100,
    TiffImagePlugin.Y_RESOLUTION: 100,
}
CENTIMETRE_DPI: float = 254.0
EXIF_MAKE: str = 'Scanner Co'
EXIF_MODEL: str = 'Book Scanner 3000'


def _pdf_extra(*, image_count: int, text_chars: int) -> dict[str, Any]:
    """Return the ``extra`` expected for an unrotated PDF page.

    :param image_count: Number of image placements on the page.
    :type image_count: int
    :param text_chars: Number of characters in the page's text layer.
    :type text_chars: int
    :returns: The extra facts the inspector must report, under the keys it stores.
    :rtype: dict[str, Any]
    """
    return {'image_count': image_count, 'rotation': 0, 'text_chars': text_chars}


def _write_bytes(path: Path, content: bytes) -> Path:
    """Write raw bytes under a name whose suffix promises another format.

    :param path: Where to write, with a misleading suffix.
    :type path: Path
    :param content: Bytes that are not of the format the suffix promises.
    :type content: bytes
    :returns: The written path.
    :rtype: Path
    """
    path.write_bytes(content)
    return path


def _write_truncated_pdf(path: Path, *, page: PdfPage) -> Path:
    """Write the first half of a valid one-page PDF.

    :param path: Where to write the file.
    :type path: Path
    :param page: The page of the PDF before truncation.
    :type page: PdfPage
    :returns: The written path.
    :rtype: Path
    """
    write_pdf(path, pages=[page])
    content = path.read_bytes()
    path.write_bytes(content[: len(content) // 2])
    return path


def _write_multi_frame_tiff(path: Path) -> Path:
    """Write a TIFF holding three pages of different sizes and modes: gray, colour and bilevel.

    :param path: Where to write the file.
    :type path: Path
    :returns: The written path.
    :rtype: Path
    """
    first, *others = (
        gradient_image(mode=mode, size=size) for mode, size in zip(FRAME_MODES, FRAME_SIZES_PX, strict=True)
    )
    first.save(path, save_all=True, append_images=others)
    return path


class RejectedPdfCase(NamedTuple):
    """A PDF source the inspector must refuse, and the message it must give.

    :ivar build: Function writing the source files into a directory and returning their paths.
    :ivar match: Regular expression the error message must match.
    """

    build: Callable[[Path], Sequence[Path]]
    match: str


class RejectedImagesCase(NamedTuple):
    """An image source the inspector must refuse, and the message it must give.

    :ivar build: Function writing the image file into a directory and returning its path as the source's files.
    :ivar match: Regular expression the error message must match.
    """

    build: Callable[[Path], Sequence[Path]]
    match: str


class ImageFormatCase(NamedTuple):
    """An image format written under its suffix, and the format name the inspector must report for it.

    :ivar suffix: File suffix selecting the image format.
    :ivar image_format: Name Pillow gives the format.
    """

    suffix: str
    image_format: str


class PdfImageCase(NamedTuple):
    """An image placed in a PDF, and the facts expected from it.

    :ivar mode: Pillow mode of the placed image.
    :ivar image_format: Pillow format the image is encoded in before placing it.
    :ivar color_mode: Colour mode the inspector must report.
    :ivar bits: Bits per component the inspector must report.
    :ivar filter_name: Human name of the PDF image filter the inspector must report.
    """

    mode: str
    image_format: str
    color_mode: ColorMode
    bits: int
    filter_name: str


class ImageModeCase(NamedTuple):
    """A Pillow mode stored in a file, and the facts expected from it.

    :ivar mode: Pillow mode of the stored image.
    :ivar suffix: File suffix selecting the image format.
    :ivar color_mode: Colour mode the inspector must report.
    :ivar bits_per_component: Bits per component the inspector must report, or None when unknown.
    """

    mode: str
    suffix: str
    color_mode: ColorMode
    bits_per_component: int | None


class TestInspectPdf:
    """Tests for SourceInspector.inspect() of a PDF source, served by PdfFormat."""

    async def test_describes_every_page_in_order(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify one entry per page, in page order.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sizes = [(300, 400), (320, 420), (340, 440)]
        pages = [PdfPage(images=[ScanImage(mode='L', size_px=size, image_format=JPEG)]) for size in sizes]
        path = write_pdf(tmp_path / PDF_NAME, pages=pages)

        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        expect(analysis.kind == SourceKind.PDF)
        expect([(page.width_px, page.height_px) for page in analysis.scans] == sizes)
        assert_expectations()

    async def test_describes_page_by_dominant_image(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify the largest image gives the pixel facts and effective DPI, and the page gives the size.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = ScanImage(mode='L', size_px=SCAN_SIZE_PX, image_format=JPEG)
        # A small colour stamp in the corner must not be taken for the page scan
        stamp = ScanImage(mode=RGB_MODE, size_px=SMALL_SIZE_PX, image_format=PNG, rect=(0, 0, 40, 30))
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[scan, stamp])])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).scans[0]

        expect((page.width_px, page.height_px) == SCAN_SIZE_PX)
        expect((page.dpi_x, page.dpi_y) == (SCAN_DPI, SCAN_DPI))
        expect(page.color_mode == ColorMode.GRAY)
        expect(page.bits_per_component == 8)
        expect(page.image_format == JPEG)
        expect((page.width_mm, page.height_mm) == (SCAN_PAGE_WIDTH_MM, SCAN_PAGE_HEIGHT_MM))
        expect(page.has_text_layer is False)
        expect(page.extra == _pdf_extra(image_count=2, text_chars=0))
        assert_expectations()

    async def test_measures_dpi_of_quarter_turned_scan(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a scan placed turned a quarter gets its DPI along its own axes, not the page's.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = ScanImage(mode='L', size_px=SCAN_SIZE_PX, image_format=JPEG, rotate=QUARTER_TURN_DEGREES)
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(size_pt=LANDSCAPE_PAGE_SIZE_PT, images=[scan])])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).scans[0]

        # Measured along the page axes the DPI would be 120 and 187.5
        assert (page.dpi_x, page.dpi_y) == (SCAN_DPI, SCAN_DPI)

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            # PyMuPDF stores inserted PNG data without a filter
            PdfImageCase(mode='1', image_format=PNG, color_mode=ColorMode.BILEVEL, bits=1, filter_name=''),
            PdfImageCase(mode='L', image_format=JPEG, color_mode=ColorMode.GRAY, bits=8, filter_name=JPEG),
            PdfImageCase(mode=RGB_MODE, image_format=JPEG, color_mode=ColorMode.COLOR, bits=8, filter_name=JPEG),
            PdfImageCase(mode=CMYK_MODE, image_format=JPEG, color_mode=ColorMode.COLOR, bits=8, filter_name=JPEG),
        ],
        ids=lambda case: case.mode,
    )
    async def test_maps_image_colour_space(
        self, fx_inspector: SourceInspector, tmp_path: Path, case: PdfImageCase
    ) -> None:
        """Verify colour mode, depth and format of the dominant image.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: An image placed in a PDF, and the facts expected from it.
        :type case: PdfImageCase
        """
        scan = ScanImage(mode=case.mode, size_px=SMALL_SIZE_PX, image_format=case.image_format)
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(images=[scan])])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).scans[0]

        expect(page.color_mode == case.color_mode)
        expect(page.bits_per_component == case.bits)
        expect(page.image_format == case.filter_name)
        assert_expectations()

    async def test_describes_born_digital_page_by_its_size(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a page without images reports its size in points and its text layer.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(text=PAGE_TEXT)])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).scans[0]

        expect((page.width_px, page.height_px) == (612, 792))
        expect((page.dpi_x, page.dpi_y) == (None, None))
        expect(page.color_mode == ColorMode.UNKNOWN)
        expect(page.image_format == '')
        expect((page.width_mm, page.height_mm) == (215.9, 279.4))
        expect(page.has_text_layer is True)
        expect(page.extra == _pdf_extra(image_count=0, text_chars=len(PAGE_TEXT)))
        assert_expectations()

    async def test_reports_file_metadata_and_suggestion(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify the document information, version, page count, outline and integrity facts, and the suggestion.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = write_pdf(
            tmp_path / PDF_NAME,
            pages=[PdfPage(text=PAGE_TEXT), PdfPage()],
            metadata={TITLE_KEY: PADDED_DOCUMENT_TITLE, 'author': f'{DOCUMENT_AUTHOR}\n', SUBJECT_KEY: ''},
            outline=OUTLINE_TITLES,
        )

        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])
        metadata = analysis.file_metadata

        expect(analysis.suggestion == MetadataSuggestion(title=DOCUMENT_TITLE, authors=DOCUMENT_AUTHOR))
        expect(metadata[DOCUMENT_INFO_KEY].get(TITLE_KEY) == PADDED_DOCUMENT_TITLE)
        expect(SUBJECT_KEY not in metadata[DOCUMENT_INFO_KEY])
        expect(metadata['pdf_version'].startswith('PDF '))
        expect(metadata[PAGE_COUNT_KEY] == 2)
        expect(metadata[HAS_OUTLINE_KEY] is True)
        expect(metadata['outline_entries'] == len(OUTLINE_TITLES))
        expect(metadata['has_xmp_metadata'] is False)
        expect(metadata[REPAIRED_KEY] is False)
        expect(json.loads(json.dumps(metadata)) == metadata)
        assert_expectations()

    async def test_suggests_nothing_without_metadata(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a PDF without a title or author yields an empty suggestion and no outline.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage()])

        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        expect(analysis.suggestion == MetadataSuggestion())
        expect(analysis.file_metadata[HAS_OUTLINE_KEY] is False)
        assert_expectations()

    async def test_flags_repaired_pdf(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a truncated PDF MuPDF can rebuild is inspected and flagged as repaired.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        # Truncation inside the content stream leaves enough structure for MuPDF to rebuild the page tree
        path = _write_truncated_pdf(tmp_path / PDF_NAME, page=PdfPage(text=PAGE_TEXT))

        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        expect(analysis.file_metadata[REPAIRED_KEY] is True)
        expect(len(analysis.scans) == 1)
        assert_expectations()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            RejectedPdfCase(build=lambda d: [_write_bytes(d / PDF_NAME, b'plain text')], match=NOT_A_PDF_MATCH),
            RejectedPdfCase(build=lambda d: [_write_bytes(d / PDF_NAME, b'')], match=NOT_A_PDF_MATCH),
            RejectedPdfCase(build=lambda d: [_write_truncated_pdf(d / PDF_NAME, page=PdfPage())], match='damaged'),
            RejectedPdfCase(
                build=lambda d: [
                    _write_bytes(
                        d / PDF_NAME, encode_image(gradient_image(mode='L', size=SMALL_SIZE_PX), image_format=PNG)
                    )
                ],
                match=NOT_A_PDF_MATCH,
            ),
            RejectedPdfCase(
                build=lambda d: [write_pdf(d / PDF_NAME, pages=[PdfPage()], user_password=PASSWORD)],
                match='protected by a password',
            ),
        ],
        ids=['text', 'empty-file', 'truncated', 'image', 'password'],
    )
    async def test_rejects_unreadable_pdf(
        self, fx_inspector: SourceInspector, tmp_path: Path, case: RejectedPdfCase
    ) -> None:
        """Reject files that are not an openable PDF with a message naming the file.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: A PDF source the inspector must refuse, and the message it must give.
        :type case: RejectedPdfCase
        """
        files = case.build(tmp_path)

        with pytest.raises(UnsupportedSourceError, match=rf'^book\.pdf .*{case.match}'):
            await fx_inspector.inspect(SourceKind.PDF, files)


class TestInspectImages:
    """Tests for SourceInspector.inspect() of an image file, served by ImageFormat."""

    async def test_refuses_tiff_whose_later_frame_header_is_damaged(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Refuse a TIFF whose first frame reads but whose second has an unknown pixel mode, instead of crashing.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = write_tiff(tmp_path / TIFF_NAME, frames=[PLAIN_FRAME, UNKNOWN_PIXEL_MODE_FRAME])

        with pytest.raises(UnsupportedSourceError, match=rf'^{TIFF_NAME_PATTERN} cannot be read as an image'):
            await fx_inspector.inspect(SourceKind.IMAGE, [path])

    @patch(MAX_FRAME_PIXELS_PATCH, SMALL_FRAME_PIXELS)
    async def test_refuses_tiff_whose_later_frame_is_too_large(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Refuse a TIFF whose second frame has more pixels than the bound, which Pillow checks for the first alone.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = write_tiff(tmp_path / TIFF_NAME, frames=[PLAIN_FRAME, LARGE_FRAME])

        with pytest.raises(UnsupportedSourceError, match=rf'^{TIFF_NAME_PATTERN} .* frame 1 is damaged or too large'):
            await fx_inspector.inspect(SourceKind.IMAGE, [path])

    async def test_reads_resolution_of_each_tiff_frame_from_its_own_tags(
        self, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify a frame without an absolute resolution does not inherit the resolution of the frame before it.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        frames = [
            PLAIN_FRAME,
            TiffFrame(mode='L', size_px=SMALL_SIZE_PX, options={'dpi': (SCAN_DPI, SCAN_DPI)}),
            TiffFrame(mode='L', size_px=SMALL_SIZE_PX, options={'tiffinfo': UNITLESS_RESOLUTION_TAGS}),
            TiffFrame(mode='L', size_px=SMALL_SIZE_PX, options={'tiffinfo': CENTIMETRE_RESOLUTION_TAGS}),
        ]
        path = write_tiff(tmp_path / TIFF_NAME, frames=frames)

        scans = (await fx_inspector.inspect(SourceKind.IMAGE, [path])).scans

        assert [(scan.dpi_x, scan.dpi_y) for scan in scans] == [
            (None, None),
            (SCAN_DPI, SCAN_DPI),
            (None, None),
            (CENTIMETRE_DPI, CENTIMETRE_DPI),
        ]

    @patch(IMAGE_FILE_LOAD_PATCH, autospec=True)
    async def test_describes_every_frame_of_a_tiff(
        self, mock_load: MagicMock, fx_inspector: SourceInspector, tmp_path: Path
    ) -> None:
        """Verify a multi-page TIFF is one source with one scan per frame, read from the frame headers alone.

        :param mock_load: Autospec mock of Pillow's ``ImageFile.load``, which decodes pixel data.
        :type mock_load: MagicMock
        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = _write_multi_frame_tiff(tmp_path / 'pages.tif')

        analysis = await fx_inspector.inspect(SourceKind.IMAGE, [path])

        expect(analysis.kind == SourceKind.IMAGE)
        expect([(scan.width_px, scan.height_px) for scan in analysis.scans] == list(FRAME_SIZES_PX))
        expect([scan.color_mode for scan in analysis.scans] == [ColorMode.GRAY, ColorMode.COLOR, ColorMode.BILEVEL])
        expect(analysis.file_metadata == {'format': 'TIFF', 'frame_count': len(FRAME_SIZES_PX)})
        assert_expectations()
        mock_load.assert_not_called()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            ImageModeCase(mode='1', suffix=TIF_SUFFIX, color_mode=ColorMode.BILEVEL, bits_per_component=1),
            ImageModeCase(mode='L', suffix=PNG_SUFFIX, color_mode=ColorMode.GRAY, bits_per_component=8),
            ImageModeCase(mode='LA', suffix=PNG_SUFFIX, color_mode=ColorMode.GRAY, bits_per_component=8),
            ImageModeCase(mode=GRAY_16_MODE, suffix=TIF_SUFFIX, color_mode=ColorMode.GRAY, bits_per_component=16),
            ImageModeCase(mode=GRAY_16_MODE, suffix=PNG_SUFFIX, color_mode=ColorMode.GRAY, bits_per_component=16),
            ImageModeCase(mode=RGB_MODE, suffix=JPG_SUFFIX, color_mode=ColorMode.COLOR, bits_per_component=8),
            ImageModeCase(mode='RGBA', suffix=PNG_SUFFIX, color_mode=ColorMode.COLOR, bits_per_component=8),
            ImageModeCase(mode=CMYK_MODE, suffix='.jpeg', color_mode=ColorMode.COLOR, bits_per_component=8),
            ImageModeCase(mode='LAB', suffix='.tiff', color_mode=ColorMode.COLOR, bits_per_component=8),
            ImageModeCase(mode='P', suffix=PNG_SUFFIX, color_mode=ColorMode.COLOR, bits_per_component=8),
            ImageModeCase(mode='L', suffix=JP2_SUFFIX, color_mode=ColorMode.GRAY, bits_per_component=8),
            ImageModeCase(mode=RGB_MODE, suffix=JP2_SUFFIX, color_mode=ColorMode.COLOR, bits_per_component=8),
            # A 32-bit float scan has no mapping and must not be guessed
            ImageModeCase(mode='F', suffix=TIF_SUFFIX, color_mode=ColorMode.UNKNOWN, bits_per_component=None),
        ],
        ids=lambda case: f'{case.mode}{case.suffix}',
    )
    async def test_maps_pillow_mode(self, fx_inspector: SourceInspector, tmp_path: Path, case: ImageModeCase) -> None:
        """Verify colour mode and bits per component follow the stored Pillow mode.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: A Pillow mode stored in a file, and the facts expected from it.
        :type case: ImageModeCase
        """
        path = write_image(tmp_path / f'{PAGE_STEM}{case.suffix}', mode=case.mode, size=SMALL_SIZE_PX)

        page = (await fx_inspector.inspect(SourceKind.IMAGE, [path])).scans[0]

        expect(page.extra['pillow_mode'] == case.mode)
        expect(page.color_mode == case.color_mode)
        expect(page.bits_per_component == case.bits_per_component)
        assert_expectations()

    @pytest.mark.parametrize(SUFFIX_ARG, RESOLUTION_SUFFIXES)
    async def test_reads_dpi_and_physical_size(
        self, fx_inspector: SourceInspector, tmp_path: Path, suffix: str
    ) -> None:
        """Verify the recorded DPI gives the physical page size.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param suffix: File suffix selecting the image format.
        :type suffix: str
        """
        path = write_image(tmp_path / f'{PAGE_STEM}{suffix}', mode='L', size=SCAN_SIZE_PX, dpi=SCAN_DPI)

        page = (await fx_inspector.inspect(SourceKind.IMAGE, [path])).scans[0]

        expect((page.width_px, page.height_px) == SCAN_SIZE_PX)
        expect((page.dpi_x, page.dpi_y) == (SCAN_DPI, SCAN_DPI))
        expect((page.width_mm, page.height_mm) == (SCAN_PAGE_WIDTH_MM, SCAN_PAGE_HEIGHT_MM))
        assert_expectations()

    @pytest.mark.parametrize(SUFFIX_ARG, RESOLUTION_SUFFIXES)
    async def test_leaves_dpi_empty_when_not_recorded(
        self, fx_inspector: SourceInspector, tmp_path: Path, suffix: str
    ) -> None:
        """Verify a file without a resolution gets no DPI and no size, even a TIFF Pillow calls 1 DPI.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param suffix: File suffix selecting the image format.
        :type suffix: str
        """
        path = write_image(tmp_path / f'{PAGE_STEM}{suffix}', mode='L', size=SMALL_SIZE_PX)

        page = (await fx_inspector.inspect(SourceKind.IMAGE, [path])).scans[0]

        expect((page.dpi_x, page.dpi_y) == (None, None))
        expect((page.width_mm, page.height_mm) == (None, None))
        assert_expectations()

    async def test_records_format_and_exif_subset(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify the format name and the scanner EXIF tags are recorded, the tags as strings.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        exif = {ExifTags.Base.Make: EXIF_MAKE, ExifTags.Base.Model: EXIF_MODEL, ExifTags.Base.Artist: 'Nobody'}
        path = write_image(tmp_path / 'page.jpg', mode=RGB_MODE, size=SMALL_SIZE_PX, exif=exif)

        page = (await fx_inspector.inspect(SourceKind.IMAGE, [path])).scans[0]

        expect(page.image_format == JPEG)
        expect(page.extra[FactKey.EXIF] == {'Make': EXIF_MAKE, 'Model': EXIF_MODEL})
        assert_expectations()

    async def test_describes_oriented_image_as_shown(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a quarter-turn EXIF orientation swaps the size, DPI and physical size to those a viewer shows.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        exif = {ExifTags.Base.Orientation: QUARTER_TURN_ORIENTATION}
        path = write_image(tmp_path / f'{PAGE_STEM}{JPG_SUFFIX}', mode='L', size=SCAN_SIZE_PX, dpi=SCAN_DPI, exif=exif)

        page = (await fx_inspector.inspect(SourceKind.IMAGE, [path])).scans[0]

        expect((page.width_px, page.height_px) == SCAN_SIZE_PX[::-1])
        expect((page.width_mm, page.height_mm) == (SCAN_PAGE_HEIGHT_MM, SCAN_PAGE_WIDTH_MM))
        assert_expectations()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            ImageFormatCase(suffix=JPG_SUFFIX, image_format=JPEG),
            ImageFormatCase(suffix=PNG_SUFFIX, image_format=PNG),
            ImageFormatCase(suffix=JP2_SUFFIX, image_format='JPEG2000'),
            ImageFormatCase(suffix=TIF_SUFFIX, image_format='TIFF'),
        ],
        ids=lambda case: case.suffix,
    )
    async def test_reports_one_scan_and_the_format(
        self, fx_inspector: SourceInspector, tmp_path: Path, case: ImageFormatCase
    ) -> None:
        """Verify a single-image file is one scan, with the format and a frame count of one as its metadata.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: An image format, selected by its suffix, and the name Pillow gives it.
        :type case: ImageFormatCase
        """
        path = write_image(tmp_path / f'{PAGE_STEM}{case.suffix}', mode='L', size=SMALL_SIZE_PX)

        analysis = await fx_inspector.inspect(SourceKind.IMAGE, [path])

        expect(len(analysis.scans) == 1)
        expect(analysis.file_metadata == {'format': case.image_format, 'frame_count': 1})
        assert_expectations()

    @pytest.mark.parametrize(SUFFIX_ARG, RESOLUTION_SUFFIXES)
    @patch(IMAGE_FILE_LOAD_PATCH, autospec=True)
    async def test_reads_headers_only(
        self, mock_load: MagicMock, fx_inspector: SourceInspector, tmp_path: Path, suffix: str
    ) -> None:
        """Verify no pixel data is decoded, since a book holds hundreds of large scans.

        :param mock_load: Autospec mock of Pillow's ``ImageFile.load``, which decodes pixel data.
        :type mock_load: MagicMock
        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param suffix: File suffix selecting the image format.
        :type suffix: str
        """
        path = write_image(tmp_path / f'{PAGE_STEM}{suffix}', mode='L', size=SMALL_SIZE_PX, dpi=SCAN_DPI)

        await fx_inspector.inspect(SourceKind.IMAGE, [path])

        mock_load.assert_not_called()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            RejectedImagesCase(
                build=lambda d: [write_image(d / 'page.gif', mode='P', size=SMALL_SIZE_PX)],
                match=r'^page\.gif is not a supported image file',
            ),
            RejectedImagesCase(
                build=lambda d: [_write_bytes(d / 'page.png', b'not an image')],
                match=r'^page\.png cannot be read as an image',
            ),
        ],
        ids=['suffix', 'unreadable'],
    )
    async def test_rejects_unsupported_images(
        self, fx_inspector: SourceInspector, tmp_path: Path, case: RejectedImagesCase
    ) -> None:
        """Reject a file with a wrong suffix and a file that is not an image.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: An image source the inspector must refuse, and the message it must give.
        :type case: RejectedImagesCase
        """
        files = case.build(tmp_path)

        with pytest.raises(UnsupportedSourceError, match=case.match):
            await fx_inspector.inspect(SourceKind.IMAGE, files)


class TestInspectFileCount:
    """Tests for SourceInspector.inspect() given a number of files that no source of the kind has."""

    @pytest.mark.parametrize(KIND_ARG, [SourceKind.PDF, SourceKind.IMAGE])
    @pytest.mark.parametrize(FILE_COUNT_ARG, [0, 2])
    async def test_single_file_kind_refuses_other_counts(
        self, fx_inspector: SourceInspector, tmp_path: Path, kind: SourceKind, file_count: int
    ) -> None:
        """Reject no file or two files for a PDF or an image source, since each of those is one file.

        :param fx_inspector: Source inspector built by the application's imaging provider.
        :type fx_inspector: SourceInspector
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param kind: Kind of source whose every source is one file.
        :type kind: SourceKind
        :param file_count: Number of files passed as the source.
        :type file_count: int
        """
        files = [write_pdf(tmp_path / f'part{number}.pdf', pages=[PdfPage()]) for number in range(file_count)]

        with pytest.raises(ValueError, match=rf'^A source of kind {kind} is one file, not {file_count}'):
            await fx_inspector.inspect(kind, files)
