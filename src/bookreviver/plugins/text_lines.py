"""Finding the lines of text of a page as curves, which the dewarping and the baseline slope of deskewing both follow.

The page is shrunk and made black and white, and the ink is smeared sideways so that the words of a line join into one
block. The blocks that are as wide as at least half of the page and not as tall as a picture are the lines. Each line is
cut into bands of 20 pixels of the shrunk page, and the middle of the ink in a band is a point of the line, so a line
that is bent or slanted gives points along its curve. Blocks shorter than half the width are headings, the last line of
a paragraph or noise, and taller blocks are pictures or lines that touch, and none of them is a line here.

The points are in the pixels of the page that was given, not of the shrunk one, so a caller never meets the scale.
"""

from typing import TYPE_CHECKING

import cv2
import numpy as np
from attrs import frozen

from bookreviver.plugins.cv_image import COLOR_PLANES, MIN_TONE_CONTRAST, WHITE, OtsuSplit, odd_size

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Floats, Samples

# The width in pixels the page is shrunk to for the search of the lines, since the lines show at it
SEARCH_WIDTH_PX: int = 1_200
# Width of a band of the shrunk page, in which the middle of a line is taken
BAND_WIDTH_PX: int = 20
# Width of the smear that joins the words of a line, as a share of the width of the page
SMEAR_WIDTH_SHARE: float = 0.06
# The least width of a line as a share of the width of the page, and the greatest height of one as a share of the height
MIN_LINE_WIDTH_SHARE: float = 0.5
MAX_LINE_HEIGHT_SHARE: float = 0.12
# The fewest bands a line has to be fitted by a curve
MIN_BANDS: int = 8
# The least ink of a band, in samples of darkness, below which the band has no middle worth taking
MIN_BAND_INK: float = 200.0
# A point farther than this many robust deviations from the curve of its line is an outlier of the fit, and the least
# deviation in pixels the rule is applied to, so a line that is exact to the pixel does not drop its good points
OUTLIER_DEVIATIONS: float = 3.0
MIN_OUTLIER_PX: float = 0.75
# How many times a line is fitted again without its outliers
FIT_PASSES: int = 3
# Scale that turns the median absolute deviation into the standard deviation of a normal distribution
MAD_TO_SIGMA: float = 1.4826


@frozen(kw_only=True)
class LineSample:
    """The points along one line of text.

    :ivar x: Horizontal places of the points in the pixels of the page, from left to right.
    :ivar y: Vertical places of the middle of the ink at those places.
    """

    x: Floats
    y: Floats

    @property
    def left(self) -> float:
        """The place of the leftmost point."""
        return float(self.x[0])

    @property
    def right(self) -> float:
        """The place of the rightmost point."""
        return float(self.x[-1])

    def fit(self, width: int, degree: int) -> tuple[Floats, float]:
        """Fit a polynomial through the points, again and again without the points far from it.

        :param width: Width of the page in pixels, by which the places are divided so the coefficients stay small.
        :type width: int
        :param degree: Degree of the polynomial, 3 for a curve and 1 for a straight line.
        :type degree: int
        :returns: The coefficients of the polynomial in the places divided by the width, highest power first, and the
                  root mean square deviation of the points that were kept from it, in pixels.
        :rtype: tuple[Floats, float]
        """
        keep = np.ones(len(self.x), dtype=bool)
        places = self.x / width
        coefficients = np.polyfit(places, self.y, degree)
        for _ in range(FIT_PASSES):
            deviation = np.abs(self.y - np.polyval(coefficients, places))
            limit = max(OUTLIER_DEVIATIONS * MAD_TO_SIGMA * float(np.median(deviation[keep])), MIN_OUTLIER_PX)
            kept = deviation <= limit
            if kept.sum() < MIN_BANDS or np.array_equal(kept, keep):
                break
            keep = kept
            coefficients = np.polyfit(places[keep], self.y[keep], degree)
        residual = self.y[keep] - np.polyval(coefficients, places[keep])
        return np.asarray(coefficients, dtype=np.float64), float(np.sqrt(np.mean(residual**2)))


class TextLineSearch:
    """Finds the lines of text of a page as the points along each of them."""

    def __init__(self, image: Samples) -> None:
        """Shrink the page and make it black and white.

        :param image: The samples of the page.
        :type image: Samples
        """
        height, width = image.shape[:2]
        shrink = min(1.0, SEARCH_WIDTH_PX / width)
        size = (max(1, round(width * shrink)), max(1, round(height * shrink)))
        small = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY) if small.ndim == COLOR_PLANES else small
        self._gray = np.asarray(gray, dtype=np.uint8)
        # The ratios that turn a place of the shrunk page into a place of the page
        self._to_page = np.array([width / size[0], height / size[1]])
        self._split = OtsuSplit.of(self._gray)
        is_ink = self._gray <= self._split.threshold
        self._ink = np.asarray(is_ink, dtype=np.uint8) * WHITE
        # The ink weighs by how dark it is, so the grays at the edge of a letter place its middle to a fraction of a
        # pixel
        self._darkness = (WHITE - self._gray.astype(np.float64)) * is_ink

    def find(self) -> list[LineSample]:
        """Search the lines.

        :returns: The lines from the top of the page to its bottom, none for a page whose tones do not part into ink and
                  paper.
        :rtype: list[LineSample]
        """
        if self._split.contrast < MIN_TONE_CONTRAST:
            return []
        height, width = self._gray.shape
        smeared = cv2.morphologyEx(
            self._ink,
            cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_RECT, (odd_size(SMEAR_WIDTH_SHARE * width), 1)),
        )
        count, labels, stats, _ = cv2.connectedComponentsWithStats(smeared, connectivity=8)
        lines: list[LineSample] = []
        for label in range(1, count):
            left, top, block_width, block_height = (int(stats[label, column]) for column in range(4))
            if block_width < MIN_LINE_WIDTH_SHARE * width or block_height > MAX_LINE_HEIGHT_SHARE * height:
                continue
            block = (labels[top : top + block_height, left : left + block_width] == label) * self._darkness[
                top : top + block_height, left : left + block_width
            ]
            if (points := self._points(block, left, top)) is not None:
                lines.append(points)
        return sorted(lines, key=lambda line: float(np.median(line.y)))

    def _points(self, block: Floats, left: int, top: int) -> LineSample | None:
        """Take the middle of the ink of a block in each band of columns.

        :param block: The darkness of the pixels of the block, zero where a pixel is not of it.
        :type block: Floats
        :param left: Place of the left edge of the block in the shrunk page.
        :type left: int
        :param top: Place of the top edge of the block in the shrunk page.
        :type top: int
        :returns: The points in the pixels of the page, or None when too few bands hold ink to follow a curve.
        :rtype: LineSample | None
        """
        rows = np.arange(block.shape[0], dtype=np.float64)
        places: list[float] = []
        middles: list[float] = []
        for start in range(0, block.shape[1] - BAND_WIDTH_PX + 1, BAND_WIDTH_PX):
            weights = block[:, start : start + BAND_WIDTH_PX].sum(axis=1)
            total = float(weights.sum())
            if total < MIN_BAND_INK:
                continue
            places.append(left + start + (BAND_WIDTH_PX - 1) / 2)
            middles.append(top + float(weights @ rows) / total)
        if len(places) < MIN_BANDS:
            return None
        # The centre of the shrunk pixel i is the centre of the stretch of the page that it covers, so the pixel centres
        # of the two pages are related by this half-pixel shift
        points = (np.column_stack([places, middles]) + 0.5) * self._to_page - 0.5
        return LineSample(x=points[:, 0], y=points[:, 1])
