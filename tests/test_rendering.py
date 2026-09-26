"""Tests for rendering cached page images."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.models import SourceKind
from bookreviver.rendering import RenderRequest, RenderVariant, render_page
from tests.helpers.samples import LETTER_SIZE_PT, PdfPage, ScanImage, write_image, write_pdf

if TYPE_CHECKING:
    from pathlib import Path

VARIANT_ARG: str = 'variant'
WEBP_FORMAT: str = 'WEBP'
GRAY_MODE: str = 'L'
PAGE_INDEX: int = 1
CACHE_DIR_NAME: str = 'cache'
PNG_NAME: str = 'page.png'
TIFF_NAME: str = 'page.tif'
LARGE_SCAN_SIZE_PX: tuple[int, int] = (3000, 2000)
SMALL_SCAN_SIZE_PX: tuple[int, int] = (120, 90)
SIXTEEN_BIT_SAMPLE: int = 40_000
# 40 000 / 256, allowing for lossy WebP encoding
EIGHT_BIT_SAMPLE: int = 156
SAMPLE_TOLERANCE: int = 3
DARK_LIMIT: int = 64
LIGHT_LIMIT: int = 192


def _image_request(source_path: Path, *, variant: RenderVariant, cache_dir: Path) -> RenderRequest:
    """Build a request for a page of an image set."""
    return RenderRequest(
        kind=SourceKind.IMAGES,
        source_path=source_path,
        page_index=PAGE_INDEX,
        variant=variant,
        cache_dir=cache_dir,
    )


def _gray_at(image: Image.Image, xy: tuple[int, int]) -> int:
    """Return the 8-bit gray value of one pixel of a rendered image."""
    value = image.convert(GRAY_MODE).getpixel(xy)
    assert isinstance(value, int)
    return value


class TestRenderPage:
    """Tests for render_page()."""

    @pytest.mark.parametrize(VARIANT_ARG, list(RenderVariant))
    def test_renders_pdf_page_to_long_side(self, tmp_path: Path, variant: RenderVariant) -> None:
        """Verify a PDF page is rendered as WebP with its longer side at the variant length."""
        pages = [PdfPage(), PdfPage(images=[ScanImage(mode='L', size_px=(80, 100), image_format='JPEG')])]
        pdf_path = write_pdf(tmp_path / 'book.pdf', pages=pages)
        cache_dir = tmp_path / CACHE_DIR_NAME
        request = RenderRequest(
            kind=SourceKind.PDF, source_path=pdf_path, page_index=PAGE_INDEX, variant=variant, cache_dir=cache_dir
        )

        output = render_page(request)

        with Image.open(output) as rendered:
            expect(rendered.format == WEBP_FORMAT)
            expect(rendered.height == variant.long_side_px)
            # PyMuPDF rounds the pixmap outwards, so the shorter side may gain a pixel
            short_side = variant.long_side_px * LETTER_SIZE_PT[0] / LETTER_SIZE_PT[1]
            expect(abs(rendered.width - short_side) <= 1)
        expect(output == cache_dir / f'{variant.value}-00001.webp')
        # The temporary file was renamed into place, not left beside it
        expect(list(cache_dir.iterdir()) == [output])
        assert_expectations()

    @pytest.mark.parametrize(VARIANT_ARG, list(RenderVariant))
    def test_shrinks_jpeg_to_long_side(self, tmp_path: Path, variant: RenderVariant) -> None:
        """Verify a large scan is shrunk so its longer side matches the variant, keeping the aspect ratio."""
        source = write_image(tmp_path / 'page.jpg', mode='RGB', size=LARGE_SCAN_SIZE_PX)

        output = render_page(_image_request(source, variant=variant, cache_dir=tmp_path / CACHE_DIR_NAME))

        with Image.open(output) as rendered:
            expect(rendered.format == WEBP_FORMAT)
            expect(rendered.size == (variant.long_side_px, round(variant.long_side_px * 2 / 3)))
        assert_expectations()

    def test_never_enlarges_small_image(self, tmp_path: Path) -> None:
        """Verify an image smaller than the variant keeps its own size."""
        source = write_image(tmp_path / PNG_NAME, mode='L', size=SMALL_SCAN_SIZE_PX)

        output = render_page(_image_request(source, variant=RenderVariant.PREVIEW, cache_dir=tmp_path))

        with Image.open(output) as rendered:
            assert rendered.size == SMALL_SCAN_SIZE_PX

    def test_renders_bilevel_image(self, tmp_path: Path) -> None:
        """Verify a bilevel scan renders with its black and white areas intact."""
        source = tmp_path / TIFF_NAME
        bilevel = Image.new('1', LARGE_SCAN_SIZE_PX, color=1)
        bilevel.paste(0, (0, 0, LARGE_SCAN_SIZE_PX[0] // 2, LARGE_SCAN_SIZE_PX[1]))
        bilevel.save(source)

        output = render_page(_image_request(source, variant=RenderVariant.THUMBNAIL, cache_dir=tmp_path))

        with Image.open(output) as rendered:
            width, height = rendered.size
            expect(_gray_at(rendered, (width // 4, height // 2)) < DARK_LIMIT)
            expect(_gray_at(rendered, (width * 3 // 4, height // 2)) > LIGHT_LIMIT)
        assert_expectations()

    def test_scales_sixteen_bit_samples(self, tmp_path: Path) -> None:
        """Verify 16-bit gray is scaled to 8 bits rather than clipped to white."""
        source = tmp_path / TIFF_NAME
        Image.new('I;16', SMALL_SCAN_SIZE_PX, color=SIXTEEN_BIT_SAMPLE).save(source)

        output = render_page(_image_request(source, variant=RenderVariant.THUMBNAIL, cache_dir=tmp_path))

        with Image.open(output) as rendered:
            sample = _gray_at(rendered, (0, 0))
        assert abs(sample - EIGHT_BIT_SAMPLE) <= SAMPLE_TOLERANCE

    def test_reuses_cached_file(self, tmp_path: Path) -> None:
        """Verify an existing cache entry is returned as-is without rendering again."""
        source = write_image(tmp_path / PNG_NAME, mode='L', size=SMALL_SCAN_SIZE_PX)
        request = _image_request(source, variant=RenderVariant.THUMBNAIL, cache_dir=tmp_path / CACHE_DIR_NAME)
        output = render_page(request)
        # Replace the rendered image with a marker a fresh render would overwrite
        output.write_bytes(b'cached')

        assert render_page(request) == output
        assert output.read_bytes() == b'cached'
