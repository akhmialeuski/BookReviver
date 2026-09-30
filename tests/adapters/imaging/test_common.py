"""Tests for the rules every source format of the imaging adapters shares."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from PIL import ExifTags, Image

from bookreviver.adapters.imaging.common import is_portable_jpeg
from tests.adapters.imaging.samples import write_image

if TYPE_CHECKING:
    from pathlib import Path

CASE_ARG: str = 'case'
SMALL_SIZE_PX: tuple[int, int] = (64, 48)
# EXIF orientation telling a viewer to turn the stored image a quarter clockwise
QUARTER_TURN_ORIENTATION: int = 6


class PortableJpegCase(NamedTuple):
    """An image file, and whether it may be copied as the image of its scan.

    :ivar name: File name, whose suffix selects the format.
    :ivar mode: Pillow mode of the stored image.
    :ivar orientation: EXIF orientation to record, or None to record none.
    :ivar portable: Whether ``is_portable_jpeg`` must accept the file.
    """

    name: str
    mode: str
    orientation: int | None
    portable: bool


class TestIsPortableJpeg:
    """Tests for is_portable_jpeg()."""

    @pytest.mark.parametrize(
        CASE_ARG,
        [
            PortableJpegCase(name='gray.jpg', mode='L', orientation=None, portable=True),
            PortableJpegCase(name='rgb.jpg', mode='RGB', orientation=1, portable=True),
            PortableJpegCase(name='cmyk.jpg', mode='CMYK', orientation=None, portable=False),
            PortableJpegCase(name='turned.jpg', mode='L', orientation=QUARTER_TURN_ORIENTATION, portable=False),
            PortableJpegCase(name='page.png', mode='L', orientation=None, portable=False),
        ],
        ids=lambda case: case.name,
    )
    def test_accepts_only_upright_gray_or_rgb_jpeg(self, tmp_path: Path, case: PortableJpegCase) -> None:
        """Verify only a gray or RGB JPEG without a turning orientation may be copied.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param case: An image file, and whether it may be copied.
        :type case: PortableJpegCase
        """
        exif = None if case.orientation is None else {ExifTags.Base.Orientation: case.orientation}
        path = write_image(tmp_path / case.name, mode=case.mode, size=SMALL_SIZE_PX, exif=exif)

        with Image.open(path) as image:
            assert is_portable_jpeg(image) is case.portable

    def test_does_not_read_past_the_header_of_another_format(self, tmp_path: Path) -> None:
        """Verify a PNG cut off in its pixel data is refused without reading it, since only a JPEG can be copied.

        Pillow reads a PNG to its end to look for EXIF placed after the pixel data, which would fail on this file.

        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = write_image(tmp_path / 'page.png', mode='RGB', size=SMALL_SIZE_PX)
        content = path.read_bytes()
        path.write_bytes(content[: len(content) // 2])

        with Image.open(path) as image:
            assert is_portable_jpeg(image) is False
