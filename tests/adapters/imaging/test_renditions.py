"""Tests for the libvips rendition writer."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.app.settings import ImagingSettings
from bookreviver.domain.enums import ColorMode, Rendition
from tests.adapters.imaging.samples import write_image

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.app.settings import Settings
    from bookreviver.ports.imaging import RenditionWriter

pytestmark = pytest.mark.anyio

GRAY_MODE: str = 'L'
RGB_MODE: str = 'RGB'
BILEVEL_MODE: str = '1'
JPEG: str = 'JPEG'
PNG: str = 'PNG'
# Other than the defaults, so the tests prove the provider passes the settings on
IMAGING: ImagingSettings = ImagingSettings(preview_long_side_px=600, thumbnail_long_side_px=200)
PAGE_SIZE_PX: tuple[int, int] = (1200, 1500)
# 600 over 1500 of the height, applied to the width, and 200 over 1500
PREVIEW_SIZE_PX: tuple[int, int] = (480, 600)
THUMBNAIL_SIZE_PX: tuple[int, int] = (160, 200)
SMALL_SIZE_PX: tuple[int, int] = (120, 90)


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Override the suite's settings with the preview and thumbnail sizes of these tests.

    :param fx_settings: Settings of the whole suite, with in-memory persistence and a fresh data directory.
    :type fx_settings: Settings
    :returns: The same settings with ``IMAGING`` as their imaging group.
    :rtype: Settings
    """
    return fx_settings.model_copy(update={'imaging': IMAGING})


class TestVipsRenditionWriter:
    """Tests for writing ``full``, ``preview`` and ``thumb`` from the image of a processor."""

    async def test_color_page_as_jpeg_gets_a_jpeg_and_the_two_smaller_files(
        self, fx_renditions: RenditionWriter, tmp_path: Path
    ) -> None:
        """Verify a PNG of a colour page becomes a JPEG ``full`` with a preview and a thumbnail of the set sizes.

        :param fx_renditions: The application's rendition writer.
        :type fx_renditions: RenditionWriter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = write_image(tmp_path / 'step.png', mode=RGB_MODE, size=PAGE_SIZE_PX)
        info = await fx_renditions.write(source, tmp_path / 'out', full=Rendition.FULL_JPEG, color_mode=ColorMode.COLOR)
        with (
            Image.open(tmp_path / 'out' / Rendition.FULL_JPEG) as full,
            Image.open(tmp_path / 'out' / Rendition.PREVIEW) as preview,
            Image.open(tmp_path / 'out' / Rendition.THUMBNAIL) as thumbnail,
        ):
            expect((full.format, full.size) == (JPEG, PAGE_SIZE_PX))
            expect((preview.format, preview.size) == (JPEG, PREVIEW_SIZE_PX))
            expect((thumbnail.format, thumbnail.size) == (JPEG, THUMBNAIL_SIZE_PX))
        expect((info.width_px, info.height_px, info.full) == (*PAGE_SIZE_PX, Rendition.FULL_JPEG))
        assert_expectations()

    async def test_jpeg_that_is_already_the_format_asked_for_is_copied_byte_for_byte(
        self, fx_renditions: RenditionWriter, tmp_path: Path
    ) -> None:
        """Verify the JPEG of a scan is not encoded a second time, which would lose detail again.

        :param fx_renditions: The application's rendition writer.
        :type fx_renditions: RenditionWriter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = write_image(tmp_path / 'scan.jpg', mode=GRAY_MODE, size=PAGE_SIZE_PX)
        await fx_renditions.write(source, tmp_path / 'out', full=Rendition.FULL_JPEG, color_mode=ColorMode.GRAY)
        assert (tmp_path / 'out' / Rendition.FULL_JPEG).read_bytes() == source.read_bytes()

    async def test_gray_page_as_png_keeps_its_pixels(self, fx_renditions: RenditionWriter, tmp_path: Path) -> None:
        """Verify a lossless policy writes a gray page into a PNG of the same pixels.

        :param fx_renditions: The application's rendition writer.
        :type fx_renditions: RenditionWriter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = write_image(tmp_path / 'step.png', mode=GRAY_MODE, size=SMALL_SIZE_PX)
        await fx_renditions.write(source, tmp_path / 'out', full=Rendition.FULL_PNG, color_mode=ColorMode.GRAY)
        with Image.open(source) as expected, Image.open(tmp_path / 'out' / Rendition.FULL_PNG) as written:
            assert (written.format, list(written.get_flattened_data())) == (PNG, list(expected.get_flattened_data()))

    async def test_bilevel_page_is_a_one_bit_png_of_two_values(
        self, fx_renditions: RenditionWriter, tmp_path: Path
    ) -> None:
        """Verify a bilevel page is written at one bit per pixel, and a gray source is cut to black and white.

        :param fx_renditions: The application's rendition writer.
        :type fx_renditions: RenditionWriter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = write_image(tmp_path / 'step.png', mode=GRAY_MODE, size=SMALL_SIZE_PX)
        await fx_renditions.write(source, tmp_path / 'out', full=Rendition.FULL_PNG, color_mode=ColorMode.BILEVEL)
        with Image.open(tmp_path / 'out' / Rendition.FULL_PNG) as written:
            expect((written.format, written.mode) == (PNG, BILEVEL_MODE))
            expect(set(written.convert(GRAY_MODE).get_flattened_data()) <= {0, 255})
        assert_expectations()

    async def test_small_page_is_not_enlarged(self, fx_renditions: RenditionWriter, tmp_path: Path) -> None:
        """Verify the preview and the thumbnail of a page smaller than their sizes keep its size.

        :param fx_renditions: The application's rendition writer.
        :type fx_renditions: RenditionWriter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = write_image(tmp_path / 'step.png', mode=GRAY_MODE, size=SMALL_SIZE_PX)
        await fx_renditions.write(source, tmp_path / 'out', full=Rendition.FULL_JPEG, color_mode=ColorMode.GRAY)
        with Image.open(tmp_path / 'out' / Rendition.PREVIEW) as preview:
            assert preview.size == SMALL_SIZE_PX

    @pytest.mark.parametrize('full', [Rendition.PREVIEW, Rendition.THUMBNAIL, Rendition.TILES])
    async def test_format_that_is_not_a_full_format_is_rejected(
        self, fx_renditions: RenditionWriter, tmp_path: Path, full: Rendition
    ) -> None:
        """Reject a ``full`` that names a preview, a thumbnail or a pyramid.

        :param fx_renditions: The application's rendition writer.
        :type fx_renditions: RenditionWriter
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param full: Rendition that is not a format of the ``full`` image.
        :type full: Rendition
        """
        source = write_image(tmp_path / 'step.png', mode=GRAY_MODE, size=SMALL_SIZE_PX)
        with pytest.raises(ValueError, match='format of the full image'):
            await fx_renditions.write(source, tmp_path / 'out', full=full, color_mode=ColorMode.GRAY)
