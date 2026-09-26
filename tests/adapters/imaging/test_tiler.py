"""Tests for the libvips tiler."""

import json
from typing import TYPE_CHECKING

import anyio
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.app.settings import ImagingSettings
from tests.adapters.imaging.samples import write_image

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.app.settings import Settings
    from bookreviver.ports.imaging import Tiler

pytestmark = pytest.mark.anyio

IIIF_3_CONTEXT: str = 'http://iiif.io/api/image/3/context.json'
INFO_NAME: str = 'info.json'
WIDTH_KEY: str = 'width'
PYRAMID_NAME: str = 'iiif'
THUMBNAIL_NAME: str = 'thumb.jpg'
FULL_NAME: str = 'full.jpg'
GRAY_MODE: str = 'L'
JPEG: str = 'JPEG'
# Other than the defaults, so the tests prove the provider passes the settings on
IMAGING: ImagingSettings = ImagingSettings(tile_size_px=256, thumbnail_long_side_px=200)
# Larger than one tile, so the pyramid has several levels
PAGE_SIZE_PX: tuple[int, int] = (1200, 1500)
# Halved until the longer side fits one tile: 1500, 750, 375, 188
LEVEL_SCALE_FACTORS: list[int] = [1, 2, 4, 8]
# 200 over 1500 of the height, applied to the width
THUMBNAIL_SIZE_PX: tuple[int, int] = (160, 200)
SMALL_SIZE_PX: tuple[int, int] = (120, 90)


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Override the suite's settings with the tile and thumbnail sizes of these tests."""
    return fx_settings.model_copy(update={'imaging': IMAGING})


@pytest.fixture
def fx_page_image(tmp_path: Path) -> Path:
    """Write a gray page image larger than one tile."""
    return write_image(tmp_path / FULL_NAME, mode=GRAY_MODE, size=PAGE_SIZE_PX)


class TestTile:
    """Tests for VipsTiler.tile()."""

    async def test_writes_iiif_3_pyramid(self, fx_tiler: Tiler, fx_page_image: Path, tmp_path: Path) -> None:
        """Verify info.json declares the IIIF 3 context, the image size and the configured tile size."""
        target_dir = tmp_path / PYRAMID_NAME

        await fx_tiler.tile(fx_page_image, target_dir)

        info = json.loads((target_dir / INFO_NAME).read_text())
        expect(info['@context'] == IIIF_3_CONTEXT)
        expect((info[WIDTH_KEY], info['height']) == PAGE_SIZE_PX)
        expect(info['tiles'] == [{'scaleFactors': LEVEL_SCALE_FACTORS, WIDTH_KEY: IMAGING.tile_size_px}])
        expect(any(target_dir.rglob('default.jpg')))
        assert_expectations()

    async def test_writes_nothing_beside_pyramid(self, fx_tiler: Tiler, fx_page_image: Path, tmp_path: Path) -> None:
        """Verify the properties file libvips writes next to a pyramid does not land beside the target."""
        await fx_tiler.tile(fx_page_image, tmp_path / PYRAMID_NAME)

        assert sorted([path.name async for path in anyio.Path(tmp_path).iterdir()]) == [FULL_NAME, PYRAMID_NAME]


class TestThumbnail:
    """Tests for VipsTiler.thumbnail()."""

    async def test_fits_long_side(self, fx_tiler: Tiler, fx_page_image: Path, tmp_path: Path) -> None:
        """Verify the thumbnail is a JPEG whose longer side is the configured length."""
        target = tmp_path / THUMBNAIL_NAME

        await fx_tiler.thumbnail(fx_page_image, target)

        with Image.open(target) as thumbnail:
            expect(thumbnail.format == JPEG)
            expect(thumbnail.size == THUMBNAIL_SIZE_PX)
        assert_expectations()

    async def test_never_enlarges_small_image(self, fx_tiler: Tiler, tmp_path: Path) -> None:
        """Verify an image smaller than the thumbnail keeps its own size."""
        image = write_image(tmp_path / FULL_NAME, mode=GRAY_MODE, size=SMALL_SIZE_PX)
        target = tmp_path / THUMBNAIL_NAME

        await fx_tiler.thumbnail(image, target)

        with Image.open(target) as thumbnail:
            assert thumbnail.size == SMALL_SIZE_PX
