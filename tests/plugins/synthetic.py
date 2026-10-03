"""Synthetic scans for the tests of the gutter, the sheet and the dewarping, drawn with Pillow and NumPy alone.

A spread is two columns of lines of words on paper, with or without the shadow the binding casts, laid on the glass at an
angle. The truth the search is measured against is the line the middle of the book takes once the scan is turned, which
is known exactly because the scan is made by turning a flat spread.

A sheet is one page of lines of words on paper laid on a dark binding, turned and seen from a slant. The truth is the
four corners of the paper, which are known exactly because the scan is made by moving the corners of a flat page.

A bent page is a flat one whose columns are moved up and down by a known shift, as a book bends a page at its gutter.
The truth is the shift, so a dewarped page is measured by how straight its lines are.
"""

import math
from typing import TYPE_CHECKING

import numpy as np
from attrs import frozen
from PIL import Image, ImageDraw, ImageOps

from tests.helpers.samples import (
    INK,
    MARGIN_PX,
    PAPER,
    WORD_GAP_RANGE_PX,
    WORD_HEIGHT_PX,
    WORD_WIDTH_RANGE_PX,
    text_page,
)

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
# The tone of the thin line along the edge of a sheet that parts from a background of its own colour
SHEET_EDGE: tuple[int, int, int] = (150, 135, 105)
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


def draw_sheet(
    *,
    rotation_deg: float = 0.0,
    perspective: float = 0.0,
    seed: int = 7,
    background: tuple[int, int, int] = SHEET_BACKGROUND,
    edge_px: int = 0,
) -> SyntheticSheet:
    """Draw a page of text on paper laid on a background, turned and seen from a slant.

    :param rotation_deg: Angle the sheet is turned by in degrees, counter-clockwise.
    :type rotation_deg: float
    :param perspective: How much narrower the top of the sheet is than its bottom, as a share of the width.
    :type perspective: float
    :param seed: Seed of the words of the page.
    :type seed: int
    :param background: Colour of the background, which is dark unless a test asks for paper of its own colour.
    :type background: tuple[int, int, int]
    :param edge_px: Width of the thin dark line that runs along the edge of the paper, such as a shadow or a worn edge,
                    or 0 for none.
    :type edge_px: int
    :returns: The scan and the corners of the sheet.
    :rtype: SyntheticSheet
    """
    width, height = SHEET_SIZE_PX
    page = ImageOps.colorize(text_page(width, height, seed=seed), black=SHEET_INK, white=SHEET_PAPER)
    if edge_px:
        ImageDraw.Draw(page).rectangle((0, 0, width - 1, height - 1), outline=SHEET_EDGE, width=edge_px)
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
    ground = Image.fromarray((np.array(background) + grain).clip(0, 255).astype(np.uint8))
    return SyntheticSheet(image=Image.composite(sheet, ground, mask), corners=corners)


# How a page bends at its gutter: a cubic and a quadratic of the share of the width, whose sum is the shift of a column
BEND_CUBIC_WEIGHT: float = 1.0
BEND_QUADRATIC_WEIGHT: float = 0.3
# The deepest bend a drawn page is given, as pixels for each thousand of its width, and a bend too slight to matter
GUTTER_BEND_PX: float = 30.0
SLIGHT_BEND_PX: float = 0.5
# The sheet of a page with a picture: its place on the scan, the picture on it, the lines under the picture, and tones
SHEET_BOX: tuple[int, int, int, int] = (50, 70, 950, 1_130)
PICTURE_BOX: tuple[int, int, int, int] = (150, 300, 850, 800)
PICTURE_LINE_TOPS: tuple[int, ...] = (860, 900, 940)
PICTURE_PAGE_SIZE_PX: tuple[int, int] = (1_000, 1_200)
BACKGROUND_TONE: int = 40
PICTURE_TONES: tuple[int, int] = (90, 170)
PICTURE_SHEET_TONE: int = 235
PICTURE_PAGE_SEED: int = 3


def bend_columns(
    image: NDArray[np.uint8], depth_px: float, fill: int = PAPER
) -> tuple[NDArray[np.uint8], NDArray[np.float64]]:
    """Bend a page as a book bends it at the gutter, by moving each column of it up or down.

    The shift grows along the page as a cubic and a quadratic of the share of the width, from none at the left edge to
    ``depth_px`` and a little more at the right edge, and does not depend on the row, so the lines of the page that was
    flat become the curves ``y + shift(x)`` and the shift is the truth a dewarping is measured against.

    :param image: The samples of a flat page, in gray.
    :type image: NDArray[np.uint8]
    :param depth_px: The shift of the right edge in pixels, for a page 1000 pixels wide, scaled with the width.
    :type depth_px: float
    :param fill: The tone of what a column that moves leaves uncovered at its top or its bottom.
    :type fill: int
    :returns: The bent page and the shift of each column in pixels, positive downwards.
    :rtype: tuple[NDArray[np.uint8], NDArray[np.float64]]
    """
    height, width = image.shape
    share = np.arange(width) / width
    shifts = depth_px * width / 1_000 * (BEND_CUBIC_WEIGHT * share**3 + BEND_QUADRATIC_WEIGHT * share**2)
    rows = np.arange(height, dtype=np.float64)
    bent = np.empty_like(image)
    for column in range(width):
        values = np.interp(rows - shifts[column], rows, image[:, column].astype(np.float64), left=fill, right=fill)
        bent[:, column] = np.round(values).astype(np.uint8)
    return bent, shifts


def draw_picture_page() -> NDArray[np.uint8]:
    """Draw a sheet laid on a dark background with a picture on it and three lines of text under the picture.

    :returns: The scan in gray: the sheet is light, the background dark and the picture of tones between them.
    :rtype: NDArray[np.uint8]
    """
    width, height = PICTURE_PAGE_SIZE_PX
    scan = np.full((height, width), BACKGROUND_TONE, dtype=np.uint8)
    left, top, right, bottom = SHEET_BOX
    scan[top:bottom, left:right] = PICTURE_SHEET_TONE
    left, top, right, bottom = PICTURE_BOX
    chance = np.random.default_rng(PICTURE_PAGE_SEED)
    scan[top:bottom, left:right] = chance.integers(*PICTURE_TONES, size=(bottom - top, right - left))
    image = Image.fromarray(scan)
    words = ImageDraw.Draw(image)
    for line_top in PICTURE_LINE_TOPS:
        position = SHEET_BOX[0] + MARGIN_PX
        while position < SHEET_BOX[2] - MARGIN_PX - WORD_WIDTH_RANGE_PX[1]:
            word = int(chance.integers(*WORD_WIDTH_RANGE_PX))
            words.rectangle((position, line_top, position + word, line_top + WORD_HEIGHT_PX), fill=INK)
            position += word + int(chance.integers(*WORD_GAP_RANGE_PX))
    return np.asarray(image, dtype=np.uint8)
