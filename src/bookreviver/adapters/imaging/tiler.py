"""IIIF Image API 3 level 0 tile pyramids and thumbnails, cut by libvips."""

import tempfile
from pathlib import Path
from typing import override

import pyvips
from asyncer import asyncify

from bookreviver.ports.imaging import Tiler


class VipsTiler(Tiler):
    """Cuts pyramids with ``dzsave`` in the IIIF 3 layout and shrinks thumbnails with ``thumbnail``."""

    def __init__(self, *, tile_size_px: int, thumbnail_long_side_px: int, jpeg_quality: int) -> None:
        self._tile_size_px = tile_size_px
        self._thumbnail_long_side_px = thumbnail_long_side_px
        self._jpeg_quality = jpeg_quality

    @override
    async def tile(self, image: Path, target_dir: Path) -> None:
        await asyncify(self._tile)(image, target_dir=target_dir)

    @override
    async def thumbnail(self, image: Path, target: Path) -> None:
        await asyncify(self._thumbnail)(image, target=target)

    def _tile(self, image: Path, *, target_dir: Path) -> None:
        """Cut the pyramid next to ``target_dir`` and move only the pyramid into place."""
        # dzsave also writes vips-properties.xml into the directory holding the pyramid, which must not leak
        with tempfile.TemporaryDirectory(dir=target_dir.parent) as scratch:
            pyramid = Path(scratch) / target_dir.name
            source = pyvips.Image.new_from_file(str(image), access=pyvips.enums.Access.SEQUENTIAL)
            source.dzsave(
                str(pyramid),
                layout=pyvips.enums.ForeignDzLayout.IIIF3,
                tile_size=self._tile_size_px,
                Q=self._jpeg_quality,
            )
            pyramid.rename(target_dir)

    def _thumbnail(self, image: Path, *, target: Path) -> None:
        """Shrink the image to fit a square of the thumbnail size, never enlarging it."""
        long_side = self._thumbnail_long_side_px
        preview = pyvips.Image.thumbnail(str(image), long_side, height=long_side, size=pyvips.enums.Size.DOWN)
        preview.jpegsave(str(target), Q=self._jpeg_quality)
