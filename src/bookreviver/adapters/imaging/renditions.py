"""The files of a page version, written by libvips from the image a processor made.

A processor writes its image without loss, and this writer stores it the way the project's image policy asks. A bilevel
image becomes a 1-bit PNG, a gray or colour image a JPEG or a PNG as asked, and an image that already is a file of the
format asked for is copied byte for byte, so the base version of a page cut from a whole scan keeps the JPEG of its
scan without encoding it a second time. The preview and the thumbnail come from ``Image.thumbnail`` on the file, which
libvips documents as faster and better than shrinking an image already loaded, since it shrinks while it reads.

The source is opened with sequential access, as the tiler does, so libvips streams it instead of decoding the whole
page into memory.
"""

import shutil
from functools import partial
from typing import TYPE_CHECKING, override

import pyvips
from anyio import to_thread

from bookreviver.domain.enums import ColorMode, Rendition
from bookreviver.domain.values import RenditionInfo, Renditions
from bookreviver.ports.imaging import RenditionWriter

if TYPE_CHECKING:
    from pathlib import Path


# The depth of a pixel of a bilevel PNG in bits
BILEVEL_BIT_DEPTH: int = 1
# The value of a white pixel, and the first value that counts as white when a gray image is made bilevel
WHITE: int = 255
THRESHOLD: int = 128
JPEG_SUFFIXES: frozenset[str] = frozenset({'.jpg', '.jpeg'})
PNG_SUFFIX: str = '.png'


class VipsRenditionWriter(RenditionWriter):
    """Writes ``full``, ``preview`` and ``thumb`` of an image with libvips."""

    def __init__(self, *, preview_long_side_px: int, thumbnail_long_side_px: int, jpeg_quality: int) -> None:
        """Write previews and thumbnails of the configured sizes, and JPEG files at the configured quality.

        :param preview_long_side_px: Longer side of a preview in pixels; smaller images are not enlarged.
        :type preview_long_side_px: int
        :param thumbnail_long_side_px: Longer side of a thumbnail in pixels; smaller images are not enlarged.
        :type thumbnail_long_side_px: int
        :param jpeg_quality: JPEG quality from 1 to 100 of every JPEG file.
        :type jpeg_quality: int
        """
        self._preview_long_side_px = preview_long_side_px
        self._thumbnail_long_side_px = thumbnail_long_side_px
        self._jpeg_quality = jpeg_quality

    @override
    async def write(self, image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode) -> RenditionInfo:
        """Write the files in a worker thread, since libvips blocks.

        :param image: Image the processor made, a PNG or a JPEG.
        :type image: Path
        :param target_dir: Directory to create, which holds the files.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Whether the image is bilevel, gray or colour.
        :type color_mode: ColorMode
        :returns: The size of the image and the format of its ``full`` file.
        :rtype: RenditionInfo
        :raises ValueError: If ``full`` is not a format of the ``full`` image.
        """
        if full not in Renditions.FULL_FORMATS:
            err_msg = f'{full} is not a format of the full image.'
            raise ValueError(err_msg)
        return await to_thread.run_sync(partial(self._write, image, target_dir, full=full, color_mode=color_mode))

    def _write(self, image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode) -> RenditionInfo:
        """Write the three files of one image into a new directory.

        :param image: Image the processor made.
        :type image: Path
        :param target_dir: Directory to create.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Whether the image is bilevel, gray or colour.
        :type color_mode: ColorMode
        :returns: The size of the image and the format of its ``full`` file.
        :rtype: RenditionInfo
        """
        target_dir.mkdir(parents=True)
        source = pyvips.Image.new_from_file(str(image), access=pyvips.enums.Access.SEQUENTIAL)
        target = target_dir / full
        already_stored = image.suffix.lower() in (JPEG_SUFFIXES if full is Rendition.FULL_JPEG else {PNG_SUFFIX})
        if already_stored and color_mode is not ColorMode.BILEVEL:
            shutil.copyfile(image, target)
        elif full is Rendition.FULL_JPEG:
            source.jpegsave(str(target), Q=self._jpeg_quality)
        elif color_mode is ColorMode.BILEVEL:
            self._bilevel(source).pngsave(str(target), bitdepth=BILEVEL_BIT_DEPTH)
        else:
            source.pngsave(str(target))
        for rendition, long_side_px in (
            (Rendition.PREVIEW, self._preview_long_side_px),
            (Rendition.THUMBNAIL, self._thumbnail_long_side_px),
        ):
            shrunk = pyvips.Image.thumbnail(str(image), long_side_px, height=long_side_px, size=pyvips.enums.Size.DOWN)
            shrunk.jpegsave(str(target_dir / rendition), Q=self._jpeg_quality)
        return RenditionInfo(width_px=source.width, height_px=source.height, full=full)

    @staticmethod
    def _bilevel(source: pyvips.Image) -> pyvips.Image:
        """Turn an image into one band of exactly two values, black and white.

        :param source: Image of a bilevel page, as its file stores it.
        :type source: pyvips.Image
        :returns: One band of unsigned characters that are all 0 or 255, which ``pngsave`` writes at one bit.
        :rtype: pyvips.Image
        """
        gray = source.colourspace(pyvips.enums.Interpretation.B_W)
        if gray.bands > 1:
            gray = gray.extract_band(0)
        return (gray >= THRESHOLD).ifthenelse(WHITE, 0).cast(pyvips.enums.BandFormat.UCHAR)
