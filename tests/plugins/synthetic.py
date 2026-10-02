"""Synthetic scans for the tests of the gutter search and of the search of the sheet, drawn with Pillow and NumPy alone.

A spread is two columns of lines of words on paper, with or without the shadow the binding casts, laid on the glass at an
angle. The truth the search is measured against is the line the middle of the book takes once the scan is turned, which
is known exactly because the scan is made by turning a flat spread.

A sheet is one page of lines of words on paper laid on a dark binding, turned and seen from a slant. The truth is the
four corners of the paper, which are known exactly because the scan is made by moving the corners of a flat page.
"""

import math
from typing import TYPE_CHECKING

import numpy as np
from attrs import frozen
from PIL import Image, ImageOps

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


# Tones of the sheet of paper laid on a dark binding: the ink, the paper and the background, as colours
SHEET_INK: tuple[int, int, int] = (35, 30, 28)
SHEET_PAPER: tuple[int, int, int] = (228, 208, 164)
SHEET_BACKGROUND: tuple[int, int, int] = (46, 42, 40)
# Grain of the background, as the standard deviation of its samples
BACKGROUND_NOISE: float = 9.0
# How much larger than the sheet the scan is, so the background shows on every side
SCAN_ROOM: float = 1.2
SHEET_SIZE_PX: tuple[int, int] = (800, 1_100)
BACKGROUND_SEED: int = 5
# The corners of the sheet that are at its top, which come in when it is seen from a slant
TOP_CORNERS: int = 2


@frozen(kw_only=True)
class SyntheticSheet:
    """A drawn scan of a sheet of paper on a dark background, and where the corners of the sheet are.

    :ivar image: The scan, in colour.
    :ivar corners: Top left, top right, bottom right and bottom left corner of the sheet in the pixels of the scan.
    """

    image: Image.Image
    corners: tuple[tuple[float, float], ...]

    @property
    def diagonal(self) -> float:
        """Give the length of the diagonal of the scan.

        :returns: The diagonal in pixels.
        :rtype: float
        """
        return math.hypot(*self.image.size)


def perspective_coefficients(
    scan_corners: tuple[tuple[float, float], ...], page_corners: tuple[tuple[float, float], ...]
) -> tuple[float, ...]:
    """Work out the eight numbers Pillow takes for a perspective transform, which map the scan back to the page.

    :param scan_corners: Corners of the sheet in the scan.
    :type scan_corners: tuple[tuple[float, float], ...]
    :param page_corners: The same corners in the flat page.
    :type page_corners: tuple[tuple[float, float], ...]
    :returns: The coefficients of the transform from the pixels of the scan to those of the page.
    :rtype: tuple[float, ...]
    """
    rows: list[list[float]] = []
    targets: list[float] = []
    for (x_scan, y_scan), (x_page, y_page) in zip(scan_corners, page_corners, strict=True):
        rows.extend(
            [
                [x_scan, y_scan, 1, 0, 0, 0, -x_scan * x_page, -y_scan * x_page],
                [0, 0, 0, x_scan, y_scan, 1, -x_scan * y_page, -y_scan * y_page],
            ]
        )
        targets.extend([x_page, y_page])
    return tuple(float(value) for value in np.linalg.solve(np.array(rows), np.array(targets)))


def draw_sheet(*, rotation_deg: float = 0.0, perspective: float = 0.0, seed: int = 7) -> SyntheticSheet:
    """Draw a page of text on paper laid on a dark background, turned and seen from a slant.

    :param rotation_deg: Angle the sheet is turned by in degrees, counter-clockwise.
    :type rotation_deg: float
    :param perspective: How much narrower the top of the sheet is than its bottom, as a share of the width.
    :type perspective: float
    :param seed: Seed of the words of the page.
    :type seed: int
    :returns: The scan and the corners of the sheet.
    :rtype: SyntheticSheet
    """
    width, height = SHEET_SIZE_PX
    page = ImageOps.colorize(text_page(width, height, seed=seed), black=SHEET_INK, white=SHEET_PAPER)
    scan_size = (round(width * SCAN_ROOM), round(height * SCAN_ROOM))
    flat = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
    angle = math.radians(rotation_deg)
    corners = tuple(
        (
            scan_size[0] / 2 + x_in * math.cos(angle) + y_in * math.sin(angle),
            scan_size[1] / 2 - x_in * math.sin(angle) + y_in * math.cos(angle),
        )
        # The top corners come towards the middle, then the sheet is turned counter-clockwise about its centre
        for x_in, y_in in (
            ((x - width / 2) * (1 - perspective if index < TOP_CORNERS else 1), y - height / 2)
            for index, (x, y) in enumerate(flat)
        )
    )
    transform = perspective_coefficients(corners, flat)
    sheet = page.transform(scan_size, Image.Transform.PERSPECTIVE, transform, Image.Resampling.BICUBIC)
    mask = Image.new('L', (width, height), 255).transform(
        scan_size, Image.Transform.PERSPECTIVE, transform, Image.Resampling.BILINEAR
    )
    grain = np.random.default_rng(BACKGROUND_SEED).normal(0, BACKGROUND_NOISE, (scan_size[1], scan_size[0], 1))
    background = Image.fromarray((np.array(SHEET_BACKGROUND) + grain).clip(0, 255).astype(np.uint8))
    return SyntheticSheet(image=Image.composite(sheet, background, mask), corners=corners)
