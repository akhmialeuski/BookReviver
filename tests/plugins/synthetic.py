"""Synthetic scans for the tests of the gutter search, drawn with Pillow and NumPy alone.

A spread is two columns of lines of words on paper, with or without the shadow the binding casts, laid on the glass at an
angle. The truth the search is measured against is the line the middle of the book takes once the scan is turned, which
is known exactly because the scan is made by turning a flat spread.
"""

import math
from typing import TYPE_CHECKING

import numpy as np
from attrs import frozen
from PIL import Image

from tests.helpers.samples import PAPER, text_page

if TYPE_CHECKING:
    from numpy.typing import NDArray

SPREAD_WIDTH_PX: int = 1_600
SPREAD_HEIGHT_PX: int = 1_100
SINGLE_PAGE_SIZE_PX: tuple[int, int] = (700, 1_000)
# Darkness of the shadow at its deepest, as a share of the paper brightness
SHADOW_DEPTH: float = 0.5
# Spread of the shadow, as the standard deviation of its bell in pixels
SHADOW_SIGMA_PX: float = 22.0
# The picture across the gutter, as fractions of the height and of the width
PICTURE_ROWS: tuple[float, float] = (0.38, 0.52)
PICTURE_COLUMNS: tuple[float, float] = (0.3, 0.7)
PICTURE_SEED: int = 11


@frozen(kw_only=True)
class SyntheticSpread:
    """A drawn scan and the place of its gutter.

    :ivar image: The scan, in gray.
    :ivar top_x: Distance of the true gutter from the left edge at the top row.
    :ivar bottom_x: Distance of the true gutter from the left edge at the bottom row.
    """

    image: Image.Image
    top_x: float
    bottom_x: float

    def truth_at_rows(self) -> NDArray[np.float64]:
        """Give the place of the true gutter on every row.

        :returns: The distance from the left edge, one number for each row.
        :rtype: NDArray[np.float64]
        """
        return np.linspace(self.top_x, self.bottom_x, self.image.height)


def draw_spread(
    *, slant_deg: float = 0.0, shadow: bool = True, picture: bool = False, width: int = SPREAD_WIDTH_PX
) -> SyntheticSpread:
    """Draw a spread turned on the glass.

    :param slant_deg: Angle the spread is turned by in degrees, counter-clockwise, so the gutter leans by it.
    :type slant_deg: float
    :param shadow: Whether the binding casts a shadow. Without one the gutter is the margins of the two pages.
    :type shadow: bool
    :param picture: Whether a dark picture crosses the gutter.
    :type picture: bool
    :param width: Width of the scan in pixels.
    :type width: int
    :returns: The scan and the line of its gutter.
    :rtype: SyntheticSpread
    """
    height = round(width * SPREAD_HEIGHT_PX / SPREAD_WIDTH_PX)
    flat = np.full((height, width), PAPER, dtype=np.float64)
    flat[:, : width // 2] = np.asarray(text_page(width // 2, height, seed=1))
    flat[:, width // 2 :] = np.asarray(text_page(width - width // 2, height, seed=2))
    if shadow:
        columns = np.arange(width) - width / 2
        flat *= 1 - SHADOW_DEPTH * np.exp(-(columns**2) / (2 * (SHADOW_SIGMA_PX * width / SPREAD_WIDTH_PX) ** 2))
    if picture:
        chance = np.random.default_rng(PICTURE_SEED)
        rows = slice(int(height * PICTURE_ROWS[0]), int(height * PICTURE_ROWS[1]))
        span = slice(int(width * PICTURE_COLUMNS[0]), int(width * PICTURE_COLUMNS[1]))
        flat[rows, span] = chance.integers(20, 160, size=flat[rows, span].shape)
    scan = Image.fromarray(flat.clip(0, 255).astype(np.uint8)).rotate(
        slant_deg, resample=Image.Resampling.BICUBIC, fillcolor=PAPER
    )
    # Turning counter-clockwise about the centre moves the top of the vertical middle line to the left
    shift = math.tan(math.radians(slant_deg)) * height / 2
    return SyntheticSpread(image=scan, top_x=width / 2 - shift, bottom_x=width / 2 + shift)


def as_samples(image: Image.Image) -> NDArray[np.uint8]:
    """Read a drawn scan as the 8-bit samples a processor reads.

    :param image: The scan.
    :type image: Image.Image
    :returns: The samples, with two dimensions for a gray scan.
    :rtype: NDArray[np.uint8]
    """
    return np.asarray(image, dtype=np.uint8)


def draw_blank(width: int = SPREAD_WIDTH_PX) -> Image.Image:
    """Draw an empty spread, paper with nothing on it.

    :param width: Width of the scan in pixels.
    :type width: int
    :returns: The scan, in gray.
    :rtype: Image.Image
    """
    return Image.new('L', (width, round(width * SPREAD_HEIGHT_PX / SPREAD_WIDTH_PX)), PAPER)


def draw_single_page() -> Image.Image:
    """Draw one page of text, which is taller than it is wide.

    :returns: The page, in gray.
    :rtype: Image.Image
    """
    return text_page(*SINGLE_PAGE_SIZE_PX, seed=3)
