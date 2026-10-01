"""Tests for the libvips maker of blank leaves."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.adapters.imaging import VipsBlankPageMaker, VipsTiler
from bookreviver.app.settings import ImagingSettings

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.anyio

PNG_BIT_DEPTH_OFFSET: int = 24
PNG_COLOR_TYPE_OFFSET: int = 25
GRAY_COLOR_TYPE: int = 0
WHITE: int = 255
RESOLUTION_TOLERANCE_DPI: float = 0.01


class TestMake:
    """Tests for VipsBlankPageMaker.make()."""

    @pytest.mark.parametrize('size', [(1, 1), (64, 48), (2200, 3000)], ids=['one-pixel', 'small', 'page'])
    async def test_writes_a_white_gray_png_of_one_bit_per_pixel_and_exactly_the_size_asked(
        self, tmp_path: Path, size: tuple[int, int]
    ) -> None:
        """Verify the leaf has the pixel size asked, a bit depth of 1 in a gray PNG, and no pixel that is not white.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param size: Width and height of the leaf in pixels.
        :type size: tuple[int, int]
        """
        target = tmp_path / 'full.png'

        await VipsBlankPageMaker().make(target, width_px=size[0], height_px=size[1], dpi=None)

        header = target.read_bytes()
        with Image.open(target) as leaf:
            mode, leaf_size, extrema = leaf.mode, leaf.size, leaf.getextrema()
        expect(header[PNG_BIT_DEPTH_OFFSET] == 1)
        expect(header[PNG_COLOR_TYPE_OFFSET] == GRAY_COLOR_TYPE)
        expect((mode, leaf_size, extrema) == ('1', size, (WHITE, WHITE)))
        assert_expectations()

    async def test_records_the_resolution_asked(self, tmp_path: Path) -> None:
        """Verify the dots per inch given are the dots per inch of the file, within the rounding of pixels per metre.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        target = tmp_path / 'full.png'

        await VipsBlankPageMaker().make(target, width_px=300, height_px=400, dpi=300.0)

        with Image.open(target) as leaf:
            dpi = leaf.info['dpi']
        assert all(abs(axis - 300.0) < RESOLUTION_TOLERANCE_DPI for axis in dpi)

    async def test_the_tiler_cuts_the_leaf_as_it_cuts_any_page(self, tmp_path: Path) -> None:
        """Verify the written PNG is an image the tiler reads, so the leaf gets a preview, a thumbnail and a pyramid.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        imaging = ImagingSettings()
        tiler = VipsTiler(
            tile_size_px=imaging.tile_size_px,
            preview_long_side_px=imaging.preview_long_side_px,
            thumbnail_long_side_px=imaging.thumbnail_long_side_px,
            jpeg_quality=imaging.jpeg_quality,
        )
        target = tmp_path / 'full.png'
        await VipsBlankPageMaker().make(target, width_px=800, height_px=1000, dpi=None)

        await tiler.thumbnail(target, tmp_path / 'thumb.jpg')
        await tiler.tile(target, tmp_path / 'iiif', resource_id='/api/v1/iiif/leaf/iiif')

        expect((tmp_path / 'thumb.jpg').is_file())
        expect((tmp_path / 'iiif' / 'info.json').is_file())
        assert_expectations()
