"""IIIF Image API 3 level 0 tile pyramids, previews and thumbnails, cut by libvips.

``dzsave`` with ``layout=IIIF3`` writes the whole static pyramid, ``info.json`` included, so no tile or descriptor is
written by hand. Two of its habits shape this module. It writes ``vips-properties.xml`` into the directory holding the
pyramid whatever ``keep`` says, so the pyramid is cut in a temporary directory next to the target and only the pyramid
is moved out. The ``id`` it writes into ``info.json`` is left at its default, since the API replaces it with the
address the document is asked at.

The source is opened with sequential access, so libvips streams it instead of decoding the whole page into memory.
"""

import tempfile
from functools import partial
from pathlib import Path
from typing import override

import pyvips
from anyio import to_thread

from bookreviver.ports.imaging import Tiler


class VipsTiler(Tiler):
    """Cuts pyramids with ``dzsave`` in the IIIF 3 layout and shrinks thumbnails with ``thumbnail``."""

    def __init__(
        self, *, tile_size_px: int, preview_long_side_px: int, thumbnail_long_side_px: int, jpeg_quality: int
    ) -> None:
        """Cut tiles, previews and thumbnails of the configured sizes and quality.

        :param tile_size_px: Side of a square tile in pixels.
        :type tile_size_px: int
        :param preview_long_side_px: Longer side of a preview in pixels; smaller images are not enlarged.
        :type preview_long_side_px: int
        :param thumbnail_long_side_px: Longer side of a thumbnail in pixels; smaller images are not enlarged.
        :type thumbnail_long_side_px: int
        :param jpeg_quality: JPEG quality from 1 to 100 of tiles, previews and thumbnails.
        :type jpeg_quality: int
        """
        self._tile_size_px = tile_size_px
        self._preview_long_side_px = preview_long_side_px
        self._thumbnail_long_side_px = thumbnail_long_side_px
        self._jpeg_quality = jpeg_quality

    @override
    async def tile(self, image: Path, target_dir: Path) -> None:
        """Cut the pyramid in a worker thread, since libvips blocks.

        :param image: Page image to cut.
        :type image: Path
        :param target_dir: Directory to create for the pyramid.
        :type target_dir: Path
        """
        await to_thread.run_sync(partial(self._tile, image, target_dir=target_dir))

    @override
    async def preview(self, image: Path, target: Path) -> None:
        """Shrink the preview in a worker thread, since libvips blocks.

        :param image: Page image to shrink.
        :type image: Path
        :param target: Path to write the JPEG preview at.
        :type target: Path
        """
        await to_thread.run_sync(partial(self._shrink, image, target=target, long_side_px=self._preview_long_side_px))

    @override
    async def thumbnail(self, image: Path, target: Path) -> None:
        """Shrink the thumbnail in a worker thread, since libvips blocks.

        :param image: Page image to shrink.
        :type image: Path
        :param target: Path to write the JPEG thumbnail at.
        :type target: Path
        """
        await to_thread.run_sync(partial(self._shrink, image, target=target, long_side_px=self._thumbnail_long_side_px))

    def _tile(self, image: Path, *, target_dir: Path) -> None:
        """Cut the pyramid next to ``target_dir`` and move only the pyramid into place.

        :param image: Page image to cut.
        :type image: Path
        :param target_dir: Directory to create for the pyramid.
        :type target_dir: Path
        """
        # dzsave writes vips-properties.xml into the directory holding the pyramid, which must not leak
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

    def _shrink(self, image: Path, *, target: Path, long_side_px: int) -> None:
        """Shrink the image to fit a square of the given side, never enlarging it.

        :param image: Page image to shrink.
        :type image: Path
        :param target: Path to write the JPEG at.
        :type target: Path
        :param long_side_px: Longest side the result may have in pixels.
        :type long_side_px: int
        """
        shrunk = pyvips.Image.thumbnail(str(image), long_side_px, height=long_side_px, size=pyvips.enums.Size.DOWN)
        shrunk.jpegsave(str(target), Q=self._jpeg_quality)
