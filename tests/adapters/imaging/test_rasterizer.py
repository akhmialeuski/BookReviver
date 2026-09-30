"""Tests for the PyMuPDF and Pillow page rasterizer."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import ExifTags, Image, ImageCms

from bookreviver.domain.enums import SourceKind
from tests.adapters.imaging.samples import PdfPage, ScanImage, gradient_image, write_image, write_pdf

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.ports.imaging import PageRasterizer

pytestmark = pytest.mark.anyio

CASE_ARG: str = 'case'
MODE_ARG: str = 'mode'
PDF_NAME: str = 'book.pdf'
TARGET_NAME: str = 'full.jpg'
JPEG: str = 'JPEG'
PNG: str = 'PNG'
GRAY_MODE: str = 'L'
RGB_MODE: str = 'RGB'
CMYK_MODE: str = 'CMYK'
GRAY_16_MODE: str = 'I;16'
TIF_SUFFIX: str = '.tif'
PNG_SUFFIX: str = '.png'
JP2_SUFFIX: str = '.jp2'
TIFF_NAME: str = 'page.tif'
PAGE_STEM: str = 'page'

# 8 x 10 inches, so an image of 1200 x 1500 pixels filling it is exactly 150 DPI
SCAN_PAGE_SIZE_PT: tuple[float, float] = (576.0, 720.0)
LANDSCAPE_PAGE_SIZE_PT: tuple[float, float] = (720.0, 576.0)
SCAN_SIZE_PX: tuple[int, int] = (1200, 1500)
LANDSCAPE_SCAN_SIZE_PX: tuple[int, int] = (1500, 1200)
# The page rendered at 300 DPI, the resolution of the scan squeezed into its left half
DOUBLE_SCAN_SIZE_PX: tuple[int, int] = (2400, 3000)
LEFT_HALF_RECT_PT: tuple[float, float, float, float] = (0.0, 0.0, 288.0, 720.0)
# US Letter at the 300 DPI of a born-digital page
LETTER_AT_300_DPI_PX: tuple[int, int] = (2550, 3300)
QUARTER_TURN_DEGREES: int = 90
# One inch past every page edge, holding a 150 DPI scan of 10 x 12 inches
OVERHANG_RECT_PT: tuple[float, float, float, float] = (-72.0, -72.0, 648.0, 792.0)
OVERHANG_SCAN_SIZE_PX: tuple[int, int] = (1500, 1800)
INVERTING_DECODE: str = '[1 0]'
# EXIF orientation telling a viewer to turn the stored image a quarter clockwise
QUARTER_TURN_ORIENTATION: int = 6
UPRIGHT_ORIENTATION: int = 1
JPG_SUFFIX: str = '.jpg'
OCR_TEXT: str = 'Recognised text'
PAGE_TEXT: str = 'Printed text'
GRAY_SCAN: ScanImage = ScanImage(mode=GRAY_MODE, size_px=SCAN_SIZE_PX, image_format=JPEG)
# One inch by two, and two by one, rendered at 300 DPI as born-digital pages
PAGE_SIZES_PT: tuple[tuple[float, float], ...] = ((72.0, 144.0), (144.0, 72.0))
SECOND_PDF_PAGE_SIZE_PX: tuple[int, int] = (600, 300)
NUMBER_ARG: str = 'number'
# Frames of a multi-page TIFF, each of its own size
FRAME_SIZES_PX: tuple[tuple[int, int], ...] = ((30, 40), (32, 42), (34, 44))
SMALL_SIZE_PX: tuple[int, int] = (120, 90)
SIXTEEN_BIT_SAMPLE: int = 40_000
# 40 000 / 256, allowing for lossy JPEG encoding
EIGHT_BIT_SAMPLE: int = 156
SAMPLE_TOLERANCE: int = 3
DARK_LIMIT: int = 64
LIGHT_LIMIT: int = 192


class RenderedPageCase(NamedTuple):
    """A PDF page that is more than one upright JPEG, and the image rendered from it.

    :ivar page: Page to render.
    :ivar size_px: Width and height the rendered JPEG must have.
    :ivar mode: Pillow mode the rendered JPEG must have.
    """

    page: PdfPage
    size_px: tuple[int, int]
    mode: str


class ConvertedImageCase(NamedTuple):
    """A page image stored in some Pillow mode and format, and the JPEG mode it becomes.

    :ivar mode: Pillow mode of the stored page image.
    :ivar suffix: File suffix selecting the stored format.
    :ivar jpeg_mode: Pillow mode the written JPEG must have.
    """

    mode: str
    suffix: str
    jpeg_mode: str


def _gray_at(image: Image.Image, xy: tuple[int, int]) -> int:
    """Return the 8-bit gray value of one pixel of a written image.

    :param image: Written page image in any mode.
    :type image: Image.Image
    :param xy: Column and row of the pixel.
    :type xy: tuple[int, int]
    :returns: Gray value from 0 to 255.
    :rtype: int
    """
    value = image.convert(GRAY_MODE).getpixel(xy)
    assert isinstance(value, int)
    return value


class TestExtractPdf:
    """Tests for PageRasterizer.extract() of a PDF page, served by PdfFormat."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[evolve(GRAY_SCAN, mode=RGB_MODE)]),
            # The invisible OCR layer of a scan does not change what the page shows
            PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[GRAY_SCAN], ocr_text=OCR_TEXT),
        ],
        ids=['rgb', 'gray-with-ocr-layer'],
    )
    async def test_copies_embedded_jpeg_byte_for_byte(
        self, fx_rasterizer: PageRasterizer, tmp_path: Path, case: PdfPage
    ) -> None:
        """Verify a page showing only one upright JPEG over its whole area yields that JPEG unchanged.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: Page showing nothing but one upright gray or RGB JPEG over its whole area.
        :type case: PdfPage
        """
        pdf = write_pdf(tmp_path / PDF_NAME, pages=[case])
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.PDF, [pdf], 0, target)

        assert target.read_bytes() == case.images[0].encoded()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            RenderedPageCase(
                page=PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[GRAY_SCAN], text=PAGE_TEXT),
                size_px=SCAN_SIZE_PX,
                mode=GRAY_MODE,
            ),
            RenderedPageCase(
                page=PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[evolve(GRAY_SCAN, image_format=PNG)]),
                size_px=SCAN_SIZE_PX,
                mode=GRAY_MODE,
            ),
            RenderedPageCase(
                page=PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[evolve(GRAY_SCAN, rect=LEFT_HALF_RECT_PT)]),
                size_px=DOUBLE_SCAN_SIZE_PX,
                mode=GRAY_MODE,
            ),
            RenderedPageCase(
                page=PdfPage(size_pt=LANDSCAPE_PAGE_SIZE_PT, images=[evolve(GRAY_SCAN, rotate=QUARTER_TURN_DEGREES)]),
                size_px=LANDSCAPE_SCAN_SIZE_PX,
                mode=GRAY_MODE,
            ),
            RenderedPageCase(
                page=PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[evolve(GRAY_SCAN, mode=CMYK_MODE)]),
                size_px=SCAN_SIZE_PX,
                mode=RGB_MODE,
            ),
            RenderedPageCase(page=PdfPage(text=PAGE_TEXT), size_px=LETTER_AT_300_DPI_PX, mode=RGB_MODE),
            # A crop box hides the scan's margins, so its bytes hold more than the page shows
            RenderedPageCase(
                page=PdfPage(
                    size_pt=SCAN_PAGE_SIZE_PT,
                    images=[evolve(GRAY_SCAN, size_px=OVERHANG_SCAN_SIZE_PX, rect=OVERHANG_RECT_PT)],
                ),
                size_px=SCAN_SIZE_PX,
                mode=GRAY_MODE,
            ),
            # The stored samples are the negative of what the page shows
            RenderedPageCase(
                page=PdfPage(size_pt=SCAN_PAGE_SIZE_PT, images=[evolve(GRAY_SCAN, decode=INVERTING_DECODE)]),
                size_px=SCAN_SIZE_PX,
                mode=GRAY_MODE,
            ),
            # A browser would turn the copied JPEG while the page and the tiles would not
            RenderedPageCase(
                page=PdfPage(
                    size_pt=SCAN_PAGE_SIZE_PT,
                    images=[evolve(GRAY_SCAN, exif={ExifTags.Base.Orientation: QUARTER_TURN_ORIENTATION})],
                ),
                size_px=SCAN_SIZE_PX,
                mode=GRAY_MODE,
            ),
        ],
        ids=[
            'visible-text',
            'png-scan',
            'partial-cover',
            'quarter-turn',
            'cmyk',
            'born-digital',
            'overhang',
            'inverting-decode',
            'exif-orientation',
        ],
    )
    async def test_renders_page_at_native_resolution(
        self, fx_rasterizer: PageRasterizer, tmp_path: Path, case: RenderedPageCase
    ) -> None:
        """Verify a page that is not just one upright JPEG is rendered at its scan's resolution, or 300 DPI.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: A PDF page that is more than one upright JPEG, and the image rendered from it.
        :type case: RenderedPageCase
        """
        pdf = write_pdf(tmp_path / PDF_NAME, pages=[case.page])
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.PDF, [pdf], 0, target)

        with Image.open(target) as rendered:
            expect(rendered.format == JPEG)
            expect(rendered.size == case.size_px)
            expect(rendered.mode == case.mode)
        expect(target.read_bytes() not in {image.encoded() for image in case.page.images})
        assert_expectations()

    async def test_extracts_requested_page(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Verify the page with the given number is the one written.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        pages = [PdfPage(size_pt=size) for size in PAGE_SIZES_PT]
        pdf = write_pdf(tmp_path / PDF_NAME, pages=pages)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.PDF, [pdf], 1, target)

        with Image.open(target) as rendered:
            assert rendered.size == SECOND_PDF_PAGE_SIZE_PX

    async def test_rejects_number_past_last_page(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Reject a page number past the end of the PDF and write nothing.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        pdf = write_pdf(tmp_path / PDF_NAME, pages=[PdfPage(size_pt=size) for size in PAGE_SIZES_PT])
        target = tmp_path / TARGET_NAME

        with pytest.raises(IndexError, match=r'^book\.pdf has no page 2: it holds 2 pages'):
            await fx_rasterizer.extract(SourceKind.PDF, [pdf], len(PAGE_SIZES_PT), target)
        assert not target.exists()

    async def test_refuses_more_than_one_file(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Reject two PDF files as one source, since each PDF part is a source of its own.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        parts = [write_pdf(tmp_path / name, pages=[PdfPage()]) for name in ('part1.pdf', 'part2.pdf')]
        target = tmp_path / TARGET_NAME

        with pytest.raises(ValueError, match=r'^A source of kind pdf is one file, not 2'):
            await fx_rasterizer.extract(SourceKind.PDF, parts, 0, target)
        assert not target.exists()


class TestExtractImages:
    """Tests for PageRasterizer.extract() of an image file, served by ImageFormat."""

    async def test_extracts_requested_frame_of_a_tiff(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Verify the frame with the given number of a multi-page TIFF is the one written.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        first, *others = (gradient_image(mode=GRAY_MODE, size=size) for size in FRAME_SIZES_PX)
        source = tmp_path / TIFF_NAME
        first.save(source, save_all=True, append_images=others)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.IMAGE, [source], 1, target)

        with Image.open(target) as written:
            assert written.size == FRAME_SIZES_PX[1]

    @pytest.mark.parametrize(NUMBER_ARG, [1, -1])
    async def test_rejects_frame_the_file_lacks(
        self, fx_rasterizer: PageRasterizer, tmp_path: Path, number: int
    ) -> None:
        """Reject a frame number outside a single-image file and write nothing.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param number: Frame number the file does not have.
        :type number: int
        """
        source = write_image(tmp_path / f'{PAGE_STEM}{PNG_SUFFIX}', mode=GRAY_MODE, size=SMALL_SIZE_PX)
        target = tmp_path / TARGET_NAME

        with pytest.raises(IndexError, match=rf'^page\.png has no frame {number}: it holds 1 frames'):
            await fx_rasterizer.extract(SourceKind.IMAGE, [source], number, target)
        assert not target.exists()

    @pytest.mark.parametrize(MODE_ARG, [GRAY_MODE, RGB_MODE])
    async def test_copies_jpeg_byte_for_byte(self, fx_rasterizer: PageRasterizer, tmp_path: Path, mode: str) -> None:
        """Verify a gray or RGB JPEG page is copied without re-encoding.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param mode: Pillow mode of the page image.
        :type mode: str
        """
        source = write_image(tmp_path / 'page.jpg', mode=mode, size=SMALL_SIZE_PX)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.IMAGE, [source], 0, target)

        assert target.read_bytes() == source.read_bytes()

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            ConvertedImageCase(mode='1', suffix=TIF_SUFFIX, jpeg_mode=GRAY_MODE),
            ConvertedImageCase(mode=GRAY_MODE, suffix=PNG_SUFFIX, jpeg_mode=GRAY_MODE),
            ConvertedImageCase(mode='LA', suffix=PNG_SUFFIX, jpeg_mode=GRAY_MODE),
            ConvertedImageCase(mode=GRAY_16_MODE, suffix=TIF_SUFFIX, jpeg_mode=GRAY_MODE),
            ConvertedImageCase(mode=RGB_MODE, suffix=TIF_SUFFIX, jpeg_mode=RGB_MODE),
            ConvertedImageCase(mode='RGBA', suffix=PNG_SUFFIX, jpeg_mode=RGB_MODE),
            ConvertedImageCase(mode='P', suffix=PNG_SUFFIX, jpeg_mode=RGB_MODE),
            ConvertedImageCase(mode='LAB', suffix=TIF_SUFFIX, jpeg_mode=RGB_MODE),
            # A CMYK JPEG is re-encoded, since browsers disagree on how to show one
            ConvertedImageCase(mode=CMYK_MODE, suffix='.jpg', jpeg_mode=RGB_MODE),
            ConvertedImageCase(mode=GRAY_MODE, suffix=JP2_SUFFIX, jpeg_mode=GRAY_MODE),
            ConvertedImageCase(mode=RGB_MODE, suffix=JP2_SUFFIX, jpeg_mode=RGB_MODE),
        ],
        ids=lambda case: f'{case.mode}{case.suffix}',
    )
    async def test_converts_other_images_keeping_pixels(
        self, fx_rasterizer: PageRasterizer, tmp_path: Path, case: ConvertedImageCase
    ) -> None:
        """Verify any other page image becomes a gray or RGB JPEG of the same pixel size.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: A page image stored in some Pillow mode and format, and the JPEG mode it becomes.
        :type case: ConvertedImageCase
        """
        source = write_image(tmp_path / f'{PAGE_STEM}{case.suffix}', mode=case.mode, size=SMALL_SIZE_PX)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.IMAGE, [source], 0, target)

        with Image.open(target) as written:
            expect(written.format == JPEG)
            expect(written.mode == case.jpeg_mode)
            expect(written.size == SMALL_SIZE_PX)
        assert_expectations()

    async def test_keeps_bilevel_areas(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Verify a bilevel scan keeps its black and white areas.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = tmp_path / TIFF_NAME
        bilevel = Image.new('1', SMALL_SIZE_PX, color=1)
        bilevel.paste(0, (0, 0, SMALL_SIZE_PX[0] // 2, SMALL_SIZE_PX[1]))
        bilevel.save(source)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.IMAGE, [source], 0, target)

        with Image.open(target) as written:
            width, height = written.size
            expect(_gray_at(written, (width // 4, height // 2)) < DARK_LIMIT)
            expect(_gray_at(written, (width * 3 // 4, height // 2)) > LIGHT_LIMIT)
        assert_expectations()

    async def test_scales_sixteen_bit_samples(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Verify 16-bit gray is scaled to 8 bits rather than clipped to white.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = tmp_path / TIFF_NAME
        Image.new(GRAY_16_MODE, SMALL_SIZE_PX, color=SIXTEEN_BIT_SAMPLE).save(source)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.IMAGE, [source], 0, target)

        with Image.open(target) as written:
            sample = _gray_at(written, (0, 0))
        assert abs(sample - EIGHT_BIT_SAMPLE) <= SAMPLE_TOLERANCE

    async def test_turns_oriented_jpeg_upright(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Verify a JPEG with a quarter-turn EXIF orientation is written upright, so tiles and thumbnail agree.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        exif = {ExifTags.Base.Orientation: QUARTER_TURN_ORIENTATION}
        source = write_image(tmp_path / f'{PAGE_STEM}{JPG_SUFFIX}', mode=GRAY_MODE, size=SMALL_SIZE_PX, exif=exif)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.IMAGE, [source], 0, target)

        with Image.open(target) as written:
            expect(written.size == SMALL_SIZE_PX[::-1])
            expect(written.getexif().get(ExifTags.Base.Orientation, UPRIGHT_ORIENTATION) == UPRIGHT_ORIENTATION)
        assert_expectations()

    async def test_keeps_colour_profile(self, fx_rasterizer: PageRasterizer, tmp_path: Path) -> None:
        """Verify a re-encoded page keeps its embedded colour profile, so its colours are shown as scanned.

        :param fx_rasterizer: Page rasterizer built by the application's imaging provider.
        :type fx_rasterizer: PageRasterizer
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        profile = ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
        source = tmp_path / TIFF_NAME
        gradient_image(mode=RGB_MODE, size=SMALL_SIZE_PX).save(source, icc_profile=profile)
        target = tmp_path / TARGET_NAME

        await fx_rasterizer.extract(SourceKind.IMAGE, [source], 0, target)

        with Image.open(target) as written:
            assert written.info.get('icc_profile') == profile
