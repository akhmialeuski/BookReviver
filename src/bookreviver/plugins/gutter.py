"""The search for the gutter of a spread, shared by ``split.spread`` and ``split.auto``.

The gutter is where the book was bound, and in a scan it need not be a vertical line: a spread laid on the glass at an
angle has a slanting gutter. ``GutterSearch`` looks for it in the central band of the scan, cut into horizontal strips.
Each strip gives one point of the gutter, and a straight line fitted through the points is the cut.

A strip is searched in two ways. Where the binding left a shadow, the darkest column of the strip is the gutter, as long
as the dip of brightness is deep enough. Where it left none, as on a sewn book or a flat bed scanner, the gutter is the
middle of the widest run of columns that hold no ink, which is the margins of the two pages next to each other. A strip
with neither, such as one a picture crosses, gives no point.

The line is fitted with ``cv2.fitLine`` and the Huber loss, so one strip that went wrong, a heading or a picture across
the gutter, does not pull the line away from the rest. The slant of the line is limited to ``max_slant_deg``.

The confidence of a cut is the product of two numbers: the share of the strips whose point lies near the line, and the
strength of the gutter, which is the depth of the shadow or the width of the ink-free run as a share of the band.

The module depends on OpenCV and NumPy alone and lives in the optional group ``bookreviver[cv]``.
"""

import math
from itertools import pairwise
from typing import TYPE_CHECKING

import cv2
import numpy as np
from attrs import frozen
from pydantic import Field

from bookreviver.plugins.base import Params
from bookreviver.plugins.cv_image import COLOR_PLANES, MANUAL_CONFIDENCE

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.plugins.cv_image import Floats, Samples

# A smoothing window as a fraction of the width of the scan, which evens out the specks and the letters in a column
SMOOTHING_FRACTION: float = 0.01
# How far above the darkest column, as a fraction of the range of the profile, a column is still part of the shadow
PLATEAU_FRACTION: float = 0.1
# The brightness of a column that stands for its paper, which the ink of the letters in it does not lower
PAPER_PERCENTILE: float = 75.0
# A column whose share of ink is at most this holds no ink worth the name, since a scan has specks
INK_FREE_SHARE: float = 0.02
# An ink-free run narrower than this fraction of the width of the scan is a gap between words and not a gutter
MIN_GAP_FRACTION: float = 0.01
# An ink-free run this much of the band is a band with no text in it at all, which has no gutter to find
BLANK_BAND_FRACTION: float = 0.9
# Decimal places of the place of a cut
CUT_DIGITS: int = 3
# A line needs this many points to be told from the point itself
MIN_POINTS: int = 2
# Parameters of ``cv2.fitLine``: the radial and angular accuracy, where 0.01 is the value the documentation advises
FIT_ACCURACY: float = 0.01


@frozen(kw_only=True)
class Cut:
    """The cut of a spread, a straight line in the pixels of the scan.

    :ivar top_x: Distance of the cut from the left edge at the top row.
    :ivar bottom_x: Distance of the cut from the left edge at the bottom row.
    :ivar confidence: How sure the cut is, from 0 to 1, which is 1 for a cut the user drew.
    """

    top_x: float
    bottom_x: float
    confidence: float = MANUAL_CONFIDENCE

    @property
    def middle_x(self) -> float:
        """The distance of the cut from the left edge halfway down the scan."""
        return (self.top_x + self.bottom_x) / 2

    def at_rows(self, height: int) -> Floats:
        """Give the place of the cut on every row.

        :param height: Height of the scan in rows.
        :type height: int
        :returns: The distance from the left edge of the cut, one number for each row.
        :rtype: Floats
        """
        return np.linspace(self.top_x, self.bottom_x, height)

    def slant_deg(self, height: int) -> float:
        """Give the angle of the cut from the vertical.

        :param height: Height of the scan in rows.
        :type height: int
        :returns: The angle in degrees, positive when the cut leans to the right going down.
        :rtype: float
        """
        return math.degrees(math.atan2(self.bottom_x - self.top_x, max(height - 1, 1)))


class GutterParams(Params):
    """Where the gutter is looked for, and when the cut is marked for a check.

    :ivar search_band: Fraction of the width, around the middle of the scan, in which the gutter is looked for.
    :ivar strips: Number of horizontal strips the band is cut into.
    :ivar min_depth: Depth of the shadow, as a share of the paper brightness, from which a strip counts as shaded.
    :ivar max_slant_deg: Largest angle of the cut from the vertical in degrees.
    :ivar tolerance: Fraction of the width within which a strip point counts as lying on the line.
    :ivar min_confidence: Confidence of the found cut below which the halves are marked for review.
    """

    search_band: float = Field(
        default=0.3,
        gt=0,
        le=1,
        title='Search width',
        description='Fraction of the width, in the middle, to search',
    )
    strips: int = Field(
        default=12,
        ge=MIN_POINTS,
        le=200,
        title='Strips',
        description='Number of horizontal strips searched for the gutter',
    )
    min_depth: float = Field(
        default=0.15,
        gt=0,
        le=1,
        title='Least shadow depth',
        description='Depth of the shadow, as a share of the paper brightness, that counts as a gutter',
    )
    max_slant_deg: float = Field(
        default=5.0,
        ge=0,
        lt=45,
        title='Largest slant',
        description='Largest angle of the cut from the vertical, in degrees',
    )
    tolerance: float = Field(
        default=0.005,
        gt=0,
        le=1,
        title='Tolerance',
        description='Fraction of the width within which a strip point counts as lying on the cut',
    )
    min_confidence: float = Field(
        default=0.1,
        ge=0,
        le=1,
        title='Least confidence',
        description='Confidence of the found gutter below which the cut is marked for a check',
    )


class GutterSearch:
    """Finds the gutter of a spread as a line fitted through the points the strips of its central band give."""

    def __init__(self, params: GutterParams) -> None:
        """Take the band, the strips and the thresholds of the search.

        :param params: The parameters of a processor that searches for a gutter.
        :type params: GutterParams
        """
        self._band = params.search_band
        self._strips = params.strips
        self._min_depth = params.min_depth
        self._max_slope = math.tan(math.radians(params.max_slant_deg))
        self._tolerance = params.tolerance

    def search(self, scan: Samples) -> Cut:
        """Find the cut through the gutter of a scan.

        :param scan: The samples of the scan, gray or blue, green and red.
        :type scan: Samples
        :returns: A line through the gutter whose slant is within the limit, or the vertical line through the middle of
                  the band with the confidence 0 when no strip shows a gutter. Otherwise the confidence is the share of
                  strips near the line times the strength of the gutter.
        :rtype: Cut
        """
        gray = np.asarray(cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY) if scan.ndim == COLOR_PLANES else scan, dtype=np.uint8)
        height, width = gray.shape
        first = int(width * (1 - self._band) / 2)
        last = max(first + 1, int(width * (1 + self._band) / 2))
        band = gray[:, first:last]
        # The ink is whatever Otsu's threshold puts on the dark side, which follows the paper of a yellowed scan too
        _threshold, thresholded = cv2.threshold(band, 0, 1, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        ink = np.asarray(thresholded, dtype=np.uint8)
        found: list[tuple[float, float, float]] = []
        for top, bottom in pairwise(np.linspace(0, height, self._strips + 1).astype(int)):
            point = self._shadow_point(band[top:bottom], width) or self._gap_point(ink[top:bottom], width)
            if point is not None:
                found.append((first + point[0], (top + bottom - 1) / 2, point[1]))
        if len(found) < MIN_POINTS:
            middle = (first + last) / 2
            return Cut(top_x=middle, bottom_x=middle, confidence=0.0)
        return self._fit(found, width, height)

    def _fit(self, found: Sequence[tuple[float, float, float]], width: int, height: int) -> Cut:
        """Fit the cut through the points of the strips, and work out how sure it is.

        :param found: The column, the row and the strength of the gutter, for every strip that showed one.
        :type found: Sequence[tuple[float, float, float]]
        :param width: Width of the scan in pixels.
        :type width: int
        :param height: Height of the scan in pixels.
        :type height: int
        :returns: The cut, whose confidence is the share of all strips near the line times the median strength of those.
        :rtype: Cut
        """
        xs, ys, strengths = (np.array(column, dtype=np.float64) for column in zip(*found, strict=True))
        # Huber loss keeps one wrong strip, a heading or a picture across the gutter, from pulling the line away
        points = np.column_stack((xs, ys)).astype(np.float32)
        direction_x, direction_y, *_ = cv2.fitLine(points, cv2.DIST_HUBER, 0, FIT_ACCURACY, FIT_ACCURACY).ravel()
        slope = float(direction_x) / float(direction_y) if direction_y else 0.0
        slope = max(-self._max_slope, min(self._max_slope, slope))
        # The line goes through the median of the points, which a wrong strip moves less than their mean
        intercept = float(np.median(xs - slope * ys))
        near = np.abs(xs - (intercept + slope * ys)) <= self._tolerance * width
        strength = float(np.median(strengths[near])) if near.any() else 0.0
        # Rounded to a thousandth of a pixel, so the noise of the fit does not move the edge of a half by a whole pixel
        return Cut(
            top_x=round(intercept, CUT_DIGITS),
            bottom_x=round(intercept + slope * (height - 1), CUT_DIGITS),
            confidence=min(1.0, float(near.sum()) / self._strips * strength),
        )

    def _shadow_point(self, strip: Samples, width: int) -> tuple[float, float] | None:
        """Find the shadow of the binding in one strip, as the middle of the darkest run of columns.

        :param strip: The brightness of the strip, cut to the band.
        :type strip: Samples
        :param width: Width of the whole scan in pixels, which the smoothing is a fraction of.
        :type width: int
        :returns: The column of the shadow in the band and its depth from 0 to 1, or None when the dip is too shallow.
        :rtype: tuple[float, float] | None
        """
        # A high percentile of a column is the paper, since letters cover only part of its rows, so a dense column of
        # text is not mistaken for a shadow as its mean would be
        profile = np.percentile(strip, PAPER_PERCENTILE, axis=0).astype(np.float32)
        window = max(1, int(width * SMOOTHING_FRACTION))
        # The ends repeat their last column, since padding with zeros would make the edges of the band the darkest
        smooth = cv2.blur(profile.reshape(1, -1), (window, 1), borderType=cv2.BORDER_REPLICATE).ravel()
        median = float(np.median(smooth))
        depth = (median - float(smooth.min())) / max(median, 1.0)
        if depth < self._min_depth:
            return None
        # The shadow is as wide as the binding, so the point is the middle of the run of columns around the darkest
        # one that are as dark as it. A second dark region of the band is another run and does not count
        as_dark = smooth <= smooth.min() + PLATEAU_FRACTION * float(np.ptp(smooth))
        darkest = int(smooth.argmin())
        lighter = np.flatnonzero(~as_dark)
        start = int(lighter[lighter < darkest].max(initial=-1)) + 1
        end = int(lighter[lighter > darkest].min(initial=len(smooth)))
        return (start + end) / 2, depth

    @staticmethod
    def _gap_point(ink: Samples, width: int) -> tuple[float, float] | None:
        """Find the gutter of a book with no shadow in one strip, as the middle of the widest ink-free run of columns.

        :param ink: One where the strip holds ink, and zero elsewhere, cut to the band.
        :type ink: Samples
        :param width: Width of the whole scan in pixels, which the narrowest gap is a fraction of.
        :type width: int
        :returns: The column of the gap in the band and its width as a share of the band, or None when no run of
                  columns is wide enough to be a gutter, or the whole band is free of ink.
        :rtype: tuple[float, float] | None
        """
        free = ink.mean(axis=0) <= INK_FREE_SHARE
        padded = np.concatenate(([False], free, [False]))
        changes = np.flatnonzero(padded[1:] != padded[:-1])
        starts, ends = changes[::2], changes[1::2]
        if not len(starts):
            return None
        widest = int((ends - starts).argmax())
        run = int((ends - starts)[widest])
        if run < MIN_GAP_FRACTION * width or run >= BLANK_BAND_FRACTION * len(free):
            return None
        return float(starts[widest] + ends[widest]) / 2, run / len(free)
