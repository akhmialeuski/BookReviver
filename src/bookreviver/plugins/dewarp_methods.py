"""The three ways ``geometry.dewarp`` finds how a page is bent: by its lines of text, by its edges, and by a network.

Each method makes a ``Flattening``: the field that straightens the page, and the curves it followed for the editor of
the mesh to start from. A method that cannot tell how the page is bent makes an ``Unfound`` instead, which says why.

The curves of the first two methods are cubic polynomials fitted to points along a line of text or an edge of the
sheet, which is how a page bends at its gutter. A page is flattened by making each curve a straight line at the height
it has at the middle of the page, so the field is the difference between a curve and that height, interpolated between
the curves. ``CurveSet`` holds the fitted curves and makes both the full mesh the field is made of and the five by five
summary the editor shows.
"""

from typing import TYPE_CHECKING

import cv2
import numpy as np

from bookreviver.domain.enums import ReviewReason
from bookreviver.domain.geometry import Mesh, Point
from bookreviver.plugins.cv_image import COLOR_PLANES, MIN_TONE_CONTRAST, OtsuSplit, sheet_of
from bookreviver.plugins.mesh_warp import CURVE_DEGREE, Flattening, FlatteningField, Unfound
from bookreviver.plugins.text_lines import BAND_WIDTH_PX, MIN_BANDS, SEARCH_WIDTH_PX, LineSample, TextLineSearch

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.plugins.cv_image import Floats, Samples
    from bookreviver.plugins.uvdoc import UvDocModel

# The nodes of each curve of the full mesh, which fit a cubic with room to spare
FULL_COLUMNS: int = 21
# The rows and the columns of the summary the editor of the mesh starts from, and so of its full grid
SUMMARY_ROWS: int = 5
SUMMARY_COLUMNS: int = 5
# How far a curve is followed beyond the first and the last point it was fitted to, as a share of the width of the page
EXTRAPOLATION_SHARE: float = 0.05
# The width of a page in pixels, over which a residual is stated per thousand
RESIDUAL_WIDTH_PX: float = 1_000.0
# A residual of this many times the limit gives the confidence 0
RESIDUAL_CONFIDENCE_FACTOR: float = 2.0
# The share of the columns of a band that have to be paper for a row to be of the sheet
PAPER_SHARE: float = 0.5
# The fewest curves a page is flattened between
MIN_CURVES: int = 2


class CurveSet:
    """Cubic curves fitted to the points along lines of a page, from the top of the page to its bottom."""

    def __init__(self, lines: Sequence[LineSample], width: int) -> None:
        """Fit a curve to the points of each line.

        :param lines: The lines, from the top of the page to the bottom, at least two.
        :type lines: Sequence[LineSample]
        :param width: Width of the page in pixels.
        :type width: int
        """
        self._width = width
        self._lines = lines
        fits = [line.fit(width, CURVE_DEGREE) for line in lines]
        self._coefficients = [coefficients for coefficients, _ in fits]
        # The deviations of the points from their curves, which are all the curves leave unexplained of the lines
        self._deviations = np.array([deviation for _, deviation in fits])

    @property
    def residual(self) -> float:
        """How far the points are from their curves, as a root mean square in pixels for each thousand of the width."""
        return float(np.sqrt(np.mean(self._deviations**2))) / self._width * RESIDUAL_WIDTH_PX

    def full(self) -> Mesh:
        """Make the mesh the field is made of, with each curve sampled along the stretch of the page it was fitted on.

        :returns: One row for each line, of the nodes of the curve.
        :rtype: Mesh
        """
        slack = EXTRAPOLATION_SHARE * self._width
        rows = []
        for line, coefficients in zip(self._lines, self._coefficients, strict=True):
            xs = np.linspace(max(0.0, line.left - slack), min(self._width - 1.0, line.right + slack), FULL_COLUMNS)
            rows.append(self._nodes(coefficients, xs, xs[0], xs[-1]))
        return Mesh(rows=tuple(rows))

    def summary(self) -> Mesh:
        """Make the five rows at most of five nodes that the editor of the mesh starts from.

        The rows are the first line, the last and those between that are the most evenly spread, and the nodes are at
        equal distances over the width of the page, a curve being followed a little beyond its own ends and then level.

        :returns: The first, the last and up to three lines between them, each as nodes.
        :rtype: Mesh
        """
        chosen = np.unique(np.round(np.linspace(0, len(self._lines) - 1, SUMMARY_ROWS)).astype(int))
        slack = EXTRAPOLATION_SHARE * self._width
        xs = np.linspace(0, self._width - 1, SUMMARY_COLUMNS)
        rows = [
            self._nodes(
                self._coefficients[index], xs, self._lines[index].left - slack, self._lines[index].right + slack
            )
            for index in chosen
        ]
        return Mesh(rows=tuple(rows))

    def _nodes(self, coefficients: Floats, xs: Floats, first: float, last: float) -> tuple[Point, ...]:
        """Place the nodes of a curve at some distances from the left.

        :param coefficients: The coefficients of the curve in the places divided by the width, highest power first.
        :type coefficients: Floats
        :param xs: Places of the nodes in pixels.
        :type xs: Floats
        :param first: The leftmost place the curve is followed to, and beyond it the curve goes on level.
        :type first: float
        :param last: The rightmost place the curve is followed to.
        :type last: float
        :returns: The nodes.
        :rtype: tuple[Point, ...]
        """
        heights = np.polyval(coefficients, np.clip(xs, first, last) / self._width)
        return tuple(Point(x=float(x), y=float(y)) for x, y in zip(xs, heights, strict=True))

    def flattening(self, max_residual: float) -> Flattening:
        """Make what the method reports of a page whose lines these curves follow.

        :param max_residual: Residual above which the page is marked for review, in pixels per thousand of the width.
        :type max_residual: float
        :returns: The flattening, with the field of the full mesh and the summary of it for the editor.
        :rtype: Flattening
        """
        residual = self.residual
        return Flattening(
            field=FlatteningField.from_mesh(self.full(), self._width),
            mesh=self.summary(),
            lines=len(self._lines),
            residual=residual,
            confidence=max(0.0, 1 - residual / (RESIDUAL_CONFIDENCE_FACTOR * max_residual)),
            review=ReviewReason.HIGH_RESIDUAL if residual > max_residual else None,
        )


class TextLinesMethod:
    """Finds how a page is bent by the curves of its lines of text."""

    def __init__(self, image: Samples, *, min_lines: int, max_residual: float) -> None:
        """Keep the page and the limits.

        :param image: The samples of the page.
        :type image: Samples
        :param min_lines: Fewest lines the bend is taken from.
        :type min_lines: int
        :param max_residual: Residual above which the page is marked for review, in pixels per thousand of the width.
        :type max_residual: float
        """
        self._image = image
        self._min_lines = min_lines
        self._max_residual = max_residual

    def find(self) -> Flattening | Unfound:
        """Search the lines of text and fit a curve to each.

        :returns: The flattening, or the report that too few lines were found to tell how the page is bent.
        :rtype: Flattening | Unfound
        """
        lines = TextLineSearch(self._image).find()
        width = self._image.shape[1]
        if len(lines) < max(self._min_lines, MIN_CURVES):
            mesh = CurveSet(lines, width).summary() if len(lines) >= MIN_CURVES else None
            return Unfound(lines=len(lines), reason=ReviewReason.FEW_LINES, mesh=mesh)
        return CurveSet(lines, width).flattening(self._max_residual)


class PageEdgesMethod:
    """Finds how a page is bent by the top and the bottom edge of its sheet, for pages with few lines of text."""

    def __init__(self, image: Samples, *, max_residual: float) -> None:
        """Shrink the page and keep the limit.

        :param image: The samples of the page.
        :type image: Samples
        :param max_residual: Residual above which the page is marked for review, in pixels per thousand of the width.
        :type max_residual: float
        """
        height, width = image.shape[:2]
        shrink = min(1.0, SEARCH_WIDTH_PX / width)
        size = (max(1, round(width * shrink)), max(1, round(height * shrink)))
        small = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        self._brightness = np.asarray(small.max(axis=2) if small.ndim == COLOR_PLANES else small, dtype=np.uint8)
        # The ratios that turn a place of the shrunk page into a place of the page
        self._to_page = np.array([width / size[0], height / size[1]])
        self._width = width
        self._max_residual = max_residual

    def find(self) -> Flattening | Unfound:
        """Search the sheet and fit a curve to its top edge and one to its bottom edge.

        The sheet is the largest region that is brighter than its background by the method of Otsu, with the holes of
        the text closed and the specks of the background opened. In each band of columns the top edge is the first row
        that is mostly sheet and the bottom edge the last.

        :returns: The flattening, or the report that no edges were found, which is a page with no background to part
                  the sheet from.
        :rtype: Flattening | Unfound
        """
        split = OtsuSplit.of(self._brightness)
        if split.contrast < MIN_TONE_CONTRAST:
            return Unfound(lines=0, reason=ReviewReason.NOT_APPLIED)
        sheet = sheet_of(self._brightness, split.threshold)
        if sheet is None:
            return Unfound(lines=0, reason=ReviewReason.NOT_APPLIED)
        tops: list[tuple[float, float]] = []
        bottoms: list[tuple[float, float]] = []
        for start in range(0, sheet.shape[1] - BAND_WIDTH_PX + 1, BAND_WIDTH_PX):
            rows = np.flatnonzero(sheet[:, start : start + BAND_WIDTH_PX].mean(axis=1) >= PAPER_SHARE)
            if len(rows):
                column = start + (BAND_WIDTH_PX - 1) / 2
                # An edge lies between the last row of the background and the first of the sheet
                tops.append((column, rows[0] - 0.5))
                bottoms.append((column, rows[-1] + 0.5))
        if len(tops) < MIN_BANDS:
            return Unfound(lines=0, reason=ReviewReason.NOT_APPLIED)
        edges = [self._edge(points) for points in (tops, bottoms)]
        return CurveSet(edges, self._width).flattening(self._max_residual)

    def _edge(self, points: Sequence[tuple[float, float]]) -> LineSample:
        """Turn the points of an edge in the shrunk page into the points of a line of the page.

        :param points: The places of the points as the column and the row of the shrunk page.
        :type points: Sequence[tuple[float, float]]
        :returns: The points in the pixels of the page.
        :rtype: LineSample
        """
        # The centre of the shrunk pixel i is the centre of the stretch of the page that it covers
        places = (np.array(points) + 0.5) * self._to_page - 0.5
        return LineSample(x=places[:, 0], y=places[:, 1])


class UvDocMethod:
    """Finds how a page is bent with the UVDoc network."""

    def __init__(self, image: Samples, model: UvDocModel) -> None:
        """Keep the page and the network.

        :param image: The samples of the page.
        :type image: Samples
        :param model: The network.
        :type model: UvDocModel
        """
        self._image = image
        self._model = model

    def find(self) -> Flattening:
        """Predict the grid of the page and make the field of it.

        :returns: The flattening, which has no curves, and so no residual, no confidence and no summary for the editor.
        :rtype: Flattening
        :raises ConflictError: If the model cannot be downloaded or is not the one that is expected.
        """
        height, width = self._image.shape[:2]
        return Flattening(field=FlatteningField.from_grid(self._model.grid(self._image), width, height))
