"""The margins of a page in millimetres, and the pixels of a page they make.

The margins of ``geometry.normalize`` are lengths on the paper of the book, so a page scanned at 300 dpi, one scanned at
600 dpi and a photograph all get the same margin, which a number of pixels cannot give. A scan carries its resolution,
and the scale of a page is that resolution after the step scaled the box. A page that has none takes the width of the
content box of the book for a text block of ``NOMINAL_BLOCK_MM`` millimetres, so its margins stay in proportion to the
text.

The step that places a page and the measure of the book, which sizes the page, work the pixels out here, so a page of
the size by the book holds the margins the step puts round its box.
"""

from typing import TYPE_CHECKING, Self

from attrs import frozen

from bookreviver.domain.enums import NormalizeParam

if TYPE_CHECKING:
    from collections.abc import Mapping

MM_PER_INCH: float = 25.4
# The width of the content box of a book, which a page of unknown resolution takes its millimetres from
NOMINAL_BLOCK_MM: float = 100.0
# Margins of a book page: a little more at the bottom and at the gutter, as books are set
DEFAULT_MARGINS_MM: Mapping[NormalizeParam, float] = {
    NormalizeParam.MARGIN_TOP: 10.0,
    NormalizeParam.MARGIN_BOTTOM: 15.0,
    NormalizeParam.MARGIN_INNER: 15.0,
    NormalizeParam.MARGIN_OUTER: 10.0,
}


@frozen
class MarginScale:
    """How many pixels of a page are a millimetre of its paper.

    :ivar pixels_per_mm: Pixels in a millimetre, more than zero.
    """

    pixels_per_mm: float

    @classmethod
    def from_dpi(cls, dpi: float) -> Self:
        """Take the scale of a page whose resolution is known.

        :param dpi: Pixels of the page in an inch of its paper.
        :type dpi: float
        :returns: The scale.
        :rtype: Self
        """
        return cls(dpi / MM_PER_INCH)

    @classmethod
    def from_block(cls, block_width: float) -> Self:
        """Take the scale of a page of unknown resolution from the width of its content box.

        :param block_width: Width in pixels of the content box, which is taken for ``NOMINAL_BLOCK_MM`` millimetres.
        :type block_width: float
        :returns: The scale.
        :rtype: Self
        """
        return cls(block_width / NOMINAL_BLOCK_MM)

    @classmethod
    def from_page(cls, page_width: float, side_margins_mm: float) -> Self:
        """Take the scale of a page of unknown resolution from the width of the page of the book.

        The page of the book holds the content box of the book and the two side margins, and the box is taken for
        ``NOMINAL_BLOCK_MM`` millimetres, so the pages of one book get one scale whatever their own boxes are.

        :param page_width: Width in pixels of the page of the book.
        :type page_width: float
        :param side_margins_mm: The two side margins together, in millimetres.
        :type side_margins_mm: float
        :returns: The scale.
        :rtype: Self
        """
        return cls(page_width / (NOMINAL_BLOCK_MM + side_margins_mm))

    def pixels(self, millimetres: float) -> float:
        """Give the pixels a length of the paper takes.

        :param millimetres: The length in millimetres.
        :type millimetres: float
        :returns: The length in pixels.
        :rtype: float
        """
        return millimetres * self.pixels_per_mm

    def millimetres(self, pixels: float) -> float:
        """Give the length of the paper that some pixels cover.

        :param pixels: The length in pixels.
        :type pixels: float
        :returns: The length in millimetres.
        :rtype: float
        """
        return pixels / self.pixels_per_mm
