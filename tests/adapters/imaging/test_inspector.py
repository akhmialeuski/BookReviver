"""Tests for the PyMuPDF and Pillow source inspector."""

import hashlib
import json
from typing import TYPE_CHECKING, Any, NamedTuple
from unittest.mock import patch

import pytest
from delayed_assert import assert_expectations, expect
from PIL import ExifTags

from bookreviver.adapters.imaging.inspector import FILE_SIZE_KEY
from bookreviver.domain.enums import ColorMode, SourceKind
from bookreviver.domain.errors import UnsupportedSourceError
from bookreviver.domain.values import MetadataSuggestion
from tests.adapters.imaging.samples import PdfPage, ScanImage, encode_image, gradient_image, write_image, write_pdf

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
OUTLINE_TITLES: tuple[str, ...] = ('Preface', 'Chapter one')
PASSWORD: str = 'secret'
NOT_A_PDF_MATCH: str = 'is not a PDF'

NATURAL_ORDER_NAMES: tuple[str, ...] = ('page1.png', 'page2.png', 'page10.png')
EXIF_MAKE: str = 'Scanner Co'
EXIF_MODEL: str = 'Book Scanner 3000'


def _pdf_extra(*, image_count: int, text_chars: int) -> dict[str, Any]:
    """Return the ``extra`` expected for an unrotated PDF page."""
    return {'image_count': image_count, 'rotation': 0, 'text_chars': text_chars}


def _write_bytes(path: Path, content: bytes) -> Path:
    """Write raw bytes under a name whose suffix promises another format."""
    path.write_bytes(content)
    return path


def _write_truncated_pdf(path: Path, *, page: PdfPage) -> Path:
    """Write the first half of a valid one-page PDF."""
    write_pdf(path, pages=[page])
    content = path.read_bytes()
    path.write_bytes(content[: len(content) // 2])
    return path


def _write_multi_frame_tiff(path: Path) -> Path:
    """Write a TIFF holding three pages."""
    frame = gradient_image(mode='L', size=SMALL_SIZE_PX)
    frame.save(path, save_all=True, append_images=[frame.copy(), frame.copy()])
    return path


class RejectedPdfCase(NamedTuple):
    """A PDF source the inspector must refuse, and the message it must give."""

    build: Callable[[Path], Sequence[Path]]
    match: str


class RejectedImagesCase(NamedTuple):
    """An image set the inspector must refuse, and the message it must give."""

    build: Callable[[Path], Sequence[Path]]
    match: str


class PdfImageCase(NamedTuple):
    """An image placed in a PDF, and the facts expected from it."""

    mode: str
    image_format: str
    color_mode: ColorMode
    bits: int
    filter_name: str


class ImageModeCase(NamedTuple):
    """A Pillow mode stored in a file, and the facts expected from it."""

    mode: str
    suffix: str
    color_mode: ColorMode
    bits_per_component: int | None


class TestInspectPdf:
    """Tests for PdfImageSourceInspector.inspect() of a PDF source."""

    async def test_describes_every_page_in_order(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify one entry per page, in page order."""
        sizes = [(300, 400), (320, 420), (340, 440)]
        pages = [PdfPage(images=[ScanImage(mode='L', size_px=size, image_format=JPEG)]) for size in sizes]
        path = write_pdf(tmp_path / PDF_NAME, pages=pages)

        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        expect(analysis.kind == SourceKind.PDF)
        expect([(page.width_px, page.height_px) for page in analysis.pages] == sizes)
        assert_expectations()

    async def test_describes_page_by_dominant_image(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify the largest image gives the pixel facts and effective DPI, and the page gives the size."""
        scan = ScanImage(mode='L', size_px=SCAN_SIZE_PX, image_format=JPEG)
        # A small colour stamp in the corner must not be taken for the page scan
        stamp = ScanImage(mode=RGB_MODE, size_px=SMALL_SIZE_PX, image_format=PNG, rect=(0, 0, 40, 30))
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[scan, stamp])])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).pages[0]

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
        """Verify a scan placed turned a quarter gets its DPI along its own axes, not the page's."""
        scan = ScanImage(mode='L', size_px=SCAN_SIZE_PX, image_format=JPEG, rotate=QUARTER_TURN_DEGREES)
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(size_pt=LANDSCAPE_PAGE_SIZE_PT, images=[scan])])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).pages[0]

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
        """Verify colour mode, depth and format of the dominant image."""
        scan = ScanImage(mode=case.mode, size_px=SMALL_SIZE_PX, image_format=case.image_format)
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(images=[scan])])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).pages[0]

        expect(page.color_mode == case.color_mode)
        expect(page.bits_per_component == case.bits)
        expect(page.image_format == case.filter_name)
        assert_expectations()

    async def test_describes_born_digital_page_by_its_size(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a page without images reports its size in points and its text layer."""
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(text=PAGE_TEXT)])

        page = (await fx_inspector.inspect(SourceKind.PDF, [path])).pages[0]

        expect((page.width_px, page.height_px) == (612, 792))
        expect((page.dpi_x, page.dpi_y) == (None, None))
        expect(page.color_mode == ColorMode.UNKNOWN)
        expect(page.image_format == '')
        expect((page.width_mm, page.height_mm) == (215.9, 279.4))
        expect(page.has_text_layer is True)
        expect(page.extra == _pdf_extra(image_count=0, text_chars=len(PAGE_TEXT)))
        assert_expectations()

    async def test_reports_file_metadata_and_suggestion(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify document metadata, outline, checksum and the suggested title and authors."""
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
        expect(metadata['page_count'] == 2)
        expect(metadata[HAS_OUTLINE_KEY] is True)
        expect(metadata['outline_entries'] == len(OUTLINE_TITLES))
        expect(metadata['has_xmp_metadata'] is False)
        expect(metadata[REPAIRED_KEY] is False)
        expect(metadata[FILE_SIZE_KEY] == path.stat().st_size)
        expect(metadata['sha256'] == hashlib.sha256(path.read_bytes()).hexdigest())
        expect(json.loads(json.dumps(metadata)) == metadata)
        assert_expectations()

    async def test_suggests_nothing_without_metadata(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a PDF without a title or author yields an empty suggestion and no outline."""
        path = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage()])

        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        expect(analysis.suggestion == MetadataSuggestion())
        expect(analysis.file_metadata[HAS_OUTLINE_KEY] is False)
        assert_expectations()

    async def test_flags_repaired_pdf(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify a truncated PDF MuPDF can rebuild is inspected and flagged as repaired."""
        # Truncation inside the content stream leaves enough structure for MuPDF to rebuild the page tree
        path = _write_truncated_pdf(tmp_path / PDF_NAME, page=PdfPage(text=PAGE_TEXT))

        analysis = await fx_inspector.inspect(SourceKind.PDF, [path])

        expect(analysis.file_metadata[REPAIRED_KEY] is True)
        expect(len(analysis.pages) == 1)
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
        """Reject files that are not an openable PDF with a message naming the file."""
        files = case.build(tmp_path)

        with pytest.raises(UnsupportedSourceError, match=rf'^book\.pdf .*{case.match}'):
            await fx_inspector.inspect(SourceKind.PDF, files)

    async def test_rejects_several_files(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Reject a PDF source made of more than one file."""
        files = [write_pdf(tmp_path / name, pages=[PdfPage()]) for name in ('one.pdf', 'two.pdf')]

        with pytest.raises(UnsupportedSourceError, match=r'^A PDF source is exactly one PDF file, not 2 files'):
            await fx_inspector.inspect(SourceKind.PDF, files)


class TestInspectImages:
    """Tests for PdfImageSourceInspector.inspect() of an image set."""

    async def test_orders_pages_naturally(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify page2 comes before page10 whatever the upload order."""
        paths = [write_image(tmp_path / name, mode='L', size=SMALL_SIZE_PX) for name in reversed(NATURAL_ORDER_NAMES)]

        analysis = await fx_inspector.inspect(SourceKind.IMAGES, paths)

        expect(analysis.kind == SourceKind.IMAGES)
        expect(tuple(page.source_file for page in analysis.pages) == NATURAL_ORDER_NAMES)
        assert_expectations()

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
            # A 32-bit float scan has no mapping and must not be guessed
            ImageModeCase(mode='F', suffix=TIF_SUFFIX, color_mode=ColorMode.UNKNOWN, bits_per_component=None),
        ],
        ids=lambda case: f'{case.mode}{case.suffix}',
    )
    async def test_maps_pillow_mode(self, fx_inspector: SourceInspector, tmp_path: Path, case: ImageModeCase) -> None:
        """Verify colour mode and bits per component follow the stored Pillow mode."""
        path = write_image(tmp_path / f'{PAGE_STEM}{case.suffix}', mode=case.mode, size=SMALL_SIZE_PX)

        page = (await fx_inspector.inspect(SourceKind.IMAGES, [path])).pages[0]

        expect(page.extra['pillow_mode'] == case.mode)
        expect(page.color_mode == case.color_mode)
        expect(page.bits_per_component == case.bits_per_component)
        assert_expectations()

    @pytest.mark.parametrize(SUFFIX_ARG, RESOLUTION_SUFFIXES)
    async def test_reads_dpi_and_physical_size(
        self, fx_inspector: SourceInspector, tmp_path: Path, suffix: str
    ) -> None:
        """Verify the recorded DPI gives the physical page size."""
        path = write_image(tmp_path / f'{PAGE_STEM}{suffix}', mode='L', size=SCAN_SIZE_PX, dpi=SCAN_DPI)

        page = (await fx_inspector.inspect(SourceKind.IMAGES, [path])).pages[0]

        expect((page.width_px, page.height_px) == SCAN_SIZE_PX)
        expect((page.dpi_x, page.dpi_y) == (SCAN_DPI, SCAN_DPI))
        expect((page.width_mm, page.height_mm) == (SCAN_PAGE_WIDTH_MM, SCAN_PAGE_HEIGHT_MM))
        assert_expectations()

    @pytest.mark.parametrize(SUFFIX_ARG, RESOLUTION_SUFFIXES)
    async def test_leaves_dpi_empty_when_not_recorded(
        self, fx_inspector: SourceInspector, tmp_path: Path, suffix: str
    ) -> None:
        """Verify a file without a resolution gets no DPI and no size, even a TIFF Pillow calls 1 DPI."""
        path = write_image(tmp_path / f'{PAGE_STEM}{suffix}', mode='L', size=SMALL_SIZE_PX)

        page = (await fx_inspector.inspect(SourceKind.IMAGES, [path])).pages[0]

        expect((page.dpi_x, page.dpi_y) == (None, None))
        expect((page.width_mm, page.height_mm) == (None, None))
        assert_expectations()

    async def test_records_format_and_exif_subset(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify the format name, file size and the scanner EXIF tags are recorded as strings."""
        exif = {ExifTags.Base.Make: EXIF_MAKE, ExifTags.Base.Model: EXIF_MODEL, ExifTags.Base.Artist: 'Nobody'}
        path = write_image(tmp_path / 'page.jpg', mode=RGB_MODE, size=SMALL_SIZE_PX, exif=exif)

        page = (await fx_inspector.inspect(SourceKind.IMAGES, [path])).pages[0]

        expect(page.image_format == JPEG)
        expect(page.extra[FILE_SIZE_KEY] == path.stat().st_size)
        expect(page.extra['exif'] == {'Make': EXIF_MAKE, 'Model': EXIF_MODEL})
        assert_expectations()

    async def test_reports_file_metadata(self, fx_inspector: SourceInspector, tmp_path: Path) -> None:
        """Verify the file count, total size and the sorted set of formats."""
        names = ('1.tif', '2.png', '3.png', '4.jpg')
        paths = [write_image(tmp_path / name, mode='L', size=SMALL_SIZE_PX) for name in names]

        metadata = (await fx_inspector.inspect(SourceKind.IMAGES, paths)).file_metadata

        expect(metadata['file_count'] == len(paths))
        expect(metadata['total_size_bytes'] == sum(path.stat().st_size for path in paths))
        expect(metadata['formats'] == [JPEG, PNG, 'TIFF'])
        assert_expectations()

    @pytest.mark.parametrize(SUFFIX_ARG, RESOLUTION_SUFFIXES)
    @patch(IMAGE_FILE_LOAD_PATCH, autospec=True)
    async def test_reads_headers_only(
        self, mock_load: MagicMock, fx_inspector: SourceInspector, tmp_path: Path, suffix: str
    ) -> None:
        """Verify no pixel data is decoded, since a book holds hundreds of large scans."""
        path = write_image(tmp_path / f'{PAGE_STEM}{suffix}', mode='L', size=SMALL_SIZE_PX, dpi=SCAN_DPI)

        await fx_inspector.inspect(SourceKind.IMAGES, [path])

        mock_load.assert_not_called()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            RejectedImagesCase(build=lambda d: [], match=r'^No page images were uploaded'),
            RejectedImagesCase(
                build=lambda d: [write_image(d / 'page.gif', mode='P', size=SMALL_SIZE_PX)],
                match=r'^page\.gif is not a supported page image',
            ),
            RejectedImagesCase(
                build=lambda d: [_write_bytes(d / 'page.png', b'not an image')],
                match=r'^page\.png cannot be read as an image',
            ),
            RejectedImagesCase(
                build=lambda d: [_write_multi_frame_tiff(d / 'pages.tif')],
                match=r'^pages\.tif holds several pages',
            ),
        ],
        ids=['empty-set', 'suffix', 'unreadable', 'multi-frame'],
    )
    async def test_rejects_unsupported_images(
        self, fx_inspector: SourceInspector, tmp_path: Path, case: RejectedImagesCase
    ) -> None:
        """Reject an empty set, a wrong suffix, an unreadable file and a multi-frame image."""
        files = case.build(tmp_path)

        with pytest.raises(UnsupportedSourceError, match=case.match):
            await fx_inspector.inspect(SourceKind.IMAGES, files)
