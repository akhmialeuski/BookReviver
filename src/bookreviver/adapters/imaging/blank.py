"""A blank leaf written by libvips: a white page of a given size as a 1-bit PNG.

``Image.black`` makes a black image of unsigned characters, so adding 255 makes it white and keeps the single band. A
white page has two colours at most, so it is a bilevel page whatever the project's image policy, and ``pngsave`` with a
bit depth of 1 writes it as the PNG of a bilevel page, which the PNG specification allows for a gray image. The pixel
size is exact and the resolution is written in pixels per millimetre. libvips always writes a resolution, so a leaf made
without one carries its default of one pixel per millimetre, and the resolution the book keeps is the one in the data of
the base version, which says it is unknown.
"""

from typing import TYPE_CHECKING, override

import pyvips
from asyncer import asyncify

from bookreviver.ports.imaging import BlankPageMaker

if TYPE_CHECKING:
    from pathlib import Path

MM_PER_INCH: float = 25.4
WHITE: int = 255
# The depth of a pixel of a bilevel PNG in bits
BILEVEL_BIT_DEPTH: int = 1


class VipsBlankPageMaker(BlankPageMaker):
    """Writes a white page of a given size as a 1-bit PNG with libvips."""

    @override
    async def make(self, target: Path, *, width_px: int, height_px: int, dpi: float | None) -> None:
        """Write the page in a worker thread, since libvips blocks.

        :param target: Path to write the PNG at.
        :type target: Path
        :param width_px: Width of the page in pixels.
        :type width_px: int
        :param height_px: Height of the page in pixels.
        :type height_px: int
        :param dpi: Resolution to record in dots per inch, or None for the default of libvips.
        :type dpi: float | None
        """
        await asyncify(self._write)(target, width_px=width_px, height_px=height_px, dpi=dpi)

    @staticmethod
    def _write(target: Path, *, width_px: int, height_px: int, dpi: float | None) -> None:
        """Build the white image and save it as a PNG of one bit per pixel.

        :param target: Path to write the PNG at.
        :type target: Path
        :param width_px: Width of the page in pixels.
        :type width_px: int
        :param height_px: Height of the page in pixels.
        :type height_px: int
        :param dpi: Resolution to record in dots per inch, or None to leave libvips its default.
        :type dpi: float | None
        """
        page = (pyvips.Image.black(width_px, height_px) + WHITE).cast(pyvips.enums.BandFormat.UCHAR)
        if dpi is not None:
            per_mm = dpi / MM_PER_INCH
            page = page.copy(xres=per_mm, yres=per_mm)
        page.pngsave(str(target), bitdepth=BILEVEL_BIT_DEPTH)
