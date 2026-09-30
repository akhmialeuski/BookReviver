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
# The pyramid is cut into a hidden partial directory, as the asset store hands one out, and published under its key
PARTIAL_PYRAMID_NAME: str = '.iiif-partial-0123'
# A path without a host, so the browser resolves it against the address the viewer was opened from
RESOURCE_ID: str = f'/api/v1/iiif/projects/book/pages/0/v1/{PYRAMID_NAME}'
THUMBNAIL_NAME: str = 'thumb.jpg'
FULL_NAME: str = 'full.jpg'
GRAY_MODE: str = 'L'
JPEG: str = 'JPEG'
# Other than the defaults, so the tests prove the provider passes the settings on
IMAGING: ImagingSettings = ImagingSettings(tile_size_px=256, preview_long_side_px=600, thumbnail_long_side_px=200)
# Larger than one tile, so the pyramid has several levels
PAGE_SIZE_PX: tuple[int, int] = (1200, 1500)
# Halved until the longer side fits one tile: 1500, 750, 375, 188
LEVEL_SCALE_FACTORS: list[int] = [1, 2, 4, 8]
# 600 over 1500 of the height, applied to the width
PREVIEW_SIZE_PX: tuple[int, int] = (480, 600)
PREVIEW_NAME: str = 'preview.jpg'
# 200 over 1500 of the height, applied to the width
THUMBNAIL_SIZE_PX: tuple[int, int] = (160, 200)
SMALL_SIZE_PX: tuple[int, int] = (120, 90)


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Override the suite's settings with the tile and thumbnail sizes of these tests.

    :param fx_settings: Settings of the whole suite, with in-memory persistence and a fresh data directory.
    :type fx_settings: Settings
    :returns: The same settings with ``IMAGING`` as their imaging group.
    :rtype: Settings
    """
    return fx_settings.model_copy(update={'imaging': IMAGING})


@pytest.fixture
def fx_page_image(tmp_path: Path) -> Path:
    """Write a gray page image larger than one tile.

    :param tmp_path: Temporary directory of the test.
    :type tmp_path: Path
    :returns: Path of the written ``full.jpg``.
    :rtype: Path
    """
    return write_image(tmp_path / FULL_NAME, mode=GRAY_MODE, size=PAGE_SIZE_PX)


class TestTile:
    """Tests for VipsTiler.tile()."""

    async def test_writes_iiif_3_pyramid(self, fx_tiler: Tiler, fx_page_image: Path, tmp_path: Path) -> None:
        """Verify info.json declares the IIIF 3 context, the public id, the image size and the configured tile size.

        :param fx_tiler: Tiler built by the application's imaging provider with the tile and thumbnail sizes of these tests.
        :type fx_tiler: Tiler
        :param fx_page_image: Gray page image larger than one tile.
        :type fx_page_image: Path
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        target_dir = tmp_path / PARTIAL_PYRAMID_NAME

        await fx_tiler.tile(fx_page_image, target_dir, resource_id=RESOURCE_ID)

        info = json.loads((target_dir / INFO_NAME).read_text())
        expect(info['@context'] == IIIF_3_CONTEXT)
        # A viewer builds every tile URL from the id, so it must be the public URL, not the partial directory
        expect(info['id'] == RESOURCE_ID)
        expect((info[WIDTH_KEY], info['height']) == PAGE_SIZE_PX)
        expect(info['tiles'] == [{'scaleFactors': LEVEL_SCALE_FACTORS, WIDTH_KEY: IMAGING.tile_size_px}])
        expect(any(target_dir.rglob('default.jpg')))
        assert_expectations()

    async def test_writes_nothing_beside_pyramid(self, fx_tiler: Tiler, fx_page_image: Path, tmp_path: Path) -> None:
        """Verify the properties file libvips writes next to a pyramid does not land beside the target.

        :param fx_tiler: Tiler built by the application's imaging provider with the tile and thumbnail sizes of these tests.
        :type fx_tiler: Tiler
        :param fx_page_image: Gray page image larger than one tile.
        :type fx_page_image: Path
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        await fx_tiler.tile(fx_page_image, tmp_path / PYRAMID_NAME, resource_id=RESOURCE_ID)

        assert sorted([path.name async for path in anyio.Path(tmp_path).iterdir()]) == [FULL_NAME, PYRAMID_NAME]


class TestPreview:
    """Tests for VipsTiler.preview()."""

    async def test_fits_long_side(self, fx_tiler: Tiler, fx_page_image: Path, tmp_path: Path) -> None:
        """Verify the preview is a JPEG whose longer side is the configured length, larger than a thumbnail.

        :param fx_tiler: Tiler built by the application's imaging provider with the sizes of these tests.
        :type fx_tiler: Tiler
        :param fx_page_image: Gray page image larger than one tile.
        :type fx_page_image: Path
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        target = tmp_path / PREVIEW_NAME

        await fx_tiler.preview(fx_page_image, target)

        with Image.open(target) as preview:
            expect(preview.format == JPEG)
            expect(preview.size == PREVIEW_SIZE_PX)
        assert_expectations()

    async def test_never_enlarges_small_image(self, fx_tiler: Tiler, tmp_path: Path) -> None:
        """Verify an image smaller than the preview keeps its own size.

        :param fx_tiler: Tiler built by the application's imaging provider with the sizes of these tests.
        :type fx_tiler: Tiler
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = write_image(tmp_path / FULL_NAME, mode=GRAY_MODE, size=SMALL_SIZE_PX)
        target = tmp_path / PREVIEW_NAME

        await fx_tiler.preview(image, target)

        with Image.open(target) as preview:
            assert preview.size == SMALL_SIZE_PX


class TestThumbnail:
    """Tests for VipsTiler.thumbnail()."""

    async def test_fits_long_side(self, fx_tiler: Tiler, fx_page_image: Path, tmp_path: Path) -> None:
        """Verify the thumbnail is a JPEG whose longer side is the configured length.

        :param fx_tiler: Tiler built by the application's imaging provider with the tile and thumbnail sizes of these tests.
        :type fx_tiler: Tiler
        :param fx_page_image: Gray page image larger than one tile.
        :type fx_page_image: Path
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        target = tmp_path / THUMBNAIL_NAME

        await fx_tiler.thumbnail(fx_page_image, target)

        with Image.open(target) as thumbnail:
            expect(thumbnail.format == JPEG)
            expect(thumbnail.size == THUMBNAIL_SIZE_PX)
        assert_expectations()

    async def test_never_enlarges_small_image(self, fx_tiler: Tiler, tmp_path: Path) -> None:
        """Verify an image smaller than the thumbnail keeps its own size.

        :param fx_tiler: Tiler built by the application's imaging provider with the tile and thumbnail sizes of these tests.
        :type fx_tiler: Tiler
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = write_image(tmp_path / FULL_NAME, mode=GRAY_MODE, size=SMALL_SIZE_PX)
        target = tmp_path / THUMBNAIL_NAME

        await fx_tiler.thumbnail(image, target)

        with Image.open(target) as thumbnail:
            assert thumbnail.size == SMALL_SIZE_PX
