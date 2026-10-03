"""Straightening a bent page along a field of displacements, and the file that records the field.

A dewarping method finds how the page is bent, and this module turns that into the picture of a flat page. The field is
a backward map: for the pixels of a few rows of the flat page it holds the place in the bent page that the pixel is
taken from, and the pixels of the rows between are found by interpolating the displacement linearly from row to row. A
row that lies beyond the first or the last has the displacement of that one, so the page is moved as a whole there.

A field is made from the curves of a ``Mesh``, each a line that becomes straight at the height it has at the middle of
the page, or from the grid of a neural network, whose rows are evenly spaced. The curves are cubic polynomials, which is
how a page bends at its gutter, fitted to the nodes of a row. The image is remapped in strips of rows, so a page of
hundreds of megapixels needs the maps of one strip in memory and not of the whole page.

The grid of the mesh file is the field sampled at 45 rows and 31 columns of the flat page, the size of the grid the
UVDoc network gives, and holds for each node the place of the bent page it is taken from, in pixels. It is what the
transform ``mesh(key)`` of the version names, and the application does not read it back yet.
"""

import json
from typing import TYPE_CHECKING

import cv2
import numpy as np
from attrs import frozen

from bookreviver.domain.enums import ColorMode
from bookreviver.domain.errors import ConflictError
from bookreviver.plugins.cv_image import to_bilevel

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.domain.enums import ReviewReason
    from bookreviver.domain.geometry import Mesh
    from bookreviver.plugins.cv_image import Floats, Samples

# The places OpenCV takes the pixels of a remapped image from, one for each pixel
type Maps = NDArray[np.float32]

# The degree of the curve a row of a mesh is fitted by, which is how a page bends at its gutter
CURVE_DEGREE: int = 3
# The fewest rows a field has, between which the displacement is interpolated
MIN_ROWS: int = 2
# How many rows of the flat page are remapped at a time
STRIP_ROWS: int = 256
# The rows and columns of the grid of the mesh file
GRID_ROWS: int = 45
GRID_COLUMNS: int = 31
# Two rows of the flat page closer than this are one row, since the displacement between them has no length to follow
MIN_ROW_GAP_PX: float = 1.0
# The width over which the bend of a page is stated, in pixels
BEND_WIDTH_PX: float = 1_000.0
ROWS_ON_ONE_LINE: str = 'The curves of the mesh lie on one line, so there is nothing to straighten between them.'
UNWRITABLE_MESH: str = 'The mesh {path} cannot be written.'


@frozen(kw_only=True)
class Flattening:
    """What a dewarping method found of a page.

    :ivar field: The field that straightens the page.
    :ivar mesh: Five rows at most of five nodes each on the curves the field was made of, which the editor of the mesh
                starts from, or None for a field that is not made of curves.
    :ivar lines: How many lines or edges the curves follow.
    :ivar residual: How far the points the curves were fitted to are from them, in pixels per thousand of the width of
                    the page, or None for a method that has no such points.
    :ivar confidence: How sure the method is of the field, from 0 to 1, or None for a method that cannot tell.
    :ivar review: Why the page should be looked at though the field was found, or None.
    """

    field: FlatteningField
    mesh: Mesh | None = None
    lines: int = 0
    residual: float | None = None
    confidence: float | None = None
    review: ReviewReason | None = None


@frozen(kw_only=True)
class Unfound:
    """What a dewarping method reports when it cannot tell how a page is bent.

    :ivar lines: How many lines or edges it found, which were too few.
    :ivar reason: Why the page is left as it is and marked for review.
    :ivar mesh: The curves it did find, for the editor of the mesh to start from, or None when there are fewer than two.
    """

    lines: int
    reason: ReviewReason
    mesh: Mesh | None = None


class FlatteningField:
    """The displacements that make a bent page flat, kept for a few rows of the flat page."""

    def __init__(self, targets: Floats, offsets: Floats) -> None:
        """Keep the rows and what each is displaced by.

        :param targets: Heights of the rows in the flat page, increasing, at least two.
        :type targets: Floats
        :param offsets: For each row and each column of the page, how far the place the pixel is taken from lies from
                        the pixel, as the horizontal and then the vertical distance, in an array of the rows, the
                        columns and two.
        :type offsets: Floats
        """
        self._targets = targets
        self._offsets = offsets

    @classmethod
    def from_mesh(cls, mesh: Mesh, width: int) -> FlatteningField:
        """Make the field that straightens the curves of a mesh.

        :param mesh: The curves, in the pixels of the page.
        :type mesh: Mesh
        :param width: Width of the page in pixels.
        :type width: int
        :returns: The field.
        :rtype: FlatteningField
        :raises ConflictError: If the curves are so close at the middle of the page that fewer than two remain.
        """
        columns = np.arange(width, dtype=np.float64)
        middle = (width - 1) / 2
        heights: list[float] = []
        curves: list[Floats] = []
        for row in mesh.rows:
            xs = np.array([node.x for node in row])
            ys = np.array([node.y for node in row])
            coefficients = np.polyfit(xs / width, ys, min(CURVE_DEGREE, len(xs) - 1))
            # Beyond its first and last node a curve is not followed, so it goes on at the height of the end
            curves.append(np.asarray(np.polyval(coefficients, np.clip(columns, xs.min(), xs.max()) / width)))
            heights.append(float(np.polyval(coefficients, np.clip(middle, xs.min(), xs.max()) / width)))
        order = np.argsort(heights)
        targets = [heights[order[0]]]
        kept = [order[0]]
        for index in order[1:]:
            if heights[index] - targets[-1] >= MIN_ROW_GAP_PX:
                targets.append(heights[index])
                kept.append(index)
        if len(kept) < MIN_ROWS:
            raise ConflictError(ROWS_ON_ONE_LINE)
        offsets = np.zeros((len(kept), width, 2))
        for place, index in enumerate(kept):
            offsets[place, :, 1] = curves[index] - targets[place]
        return cls(np.array(targets), offsets)

    @classmethod
    def from_grid(cls, grid: Floats, width: int, height: int) -> FlatteningField:
        """Make the field of a grid of the places the pixels of the flat page are taken from.

        :param grid: For each node of an even grid over the flat page, from its top left to its bottom right corner, the
                     place of the bent page the node is taken from, as x and y in pixels, in an array of the rows, the
                     columns and two.
        :type grid: Floats
        :param width: Width of the page in pixels.
        :type width: int
        :param height: Height of the page in pixels.
        :type height: int
        :returns: The field.
        :rtype: FlatteningField
        """
        rows, nodes = grid.shape[:2]
        columns = np.arange(width, dtype=np.float64)
        node_columns = np.linspace(0, width - 1, nodes)
        targets = np.linspace(0, height - 1, rows)
        offsets = np.empty((rows, width, 2))
        for row in range(rows):
            offsets[row, :, 0] = np.interp(columns, node_columns, grid[row, :, 0]) - columns
            offsets[row, :, 1] = np.interp(columns, node_columns, grid[row, :, 1]) - targets[row]
        return cls(targets, offsets)

    def bend(self) -> float:
        """Measure how far the lines of the page are bent.

        A line that is only slanted is not bent, so the straight line that fits the displacement of a row best is taken
        away first. The middle of the rows is taken, which one odd row cannot move.

        :returns: How far a row departs from its straight line at the most, in pixels for each thousand of the width.
        :rtype: float
        """
        vertical = self._offsets[:, :, 1]
        width = int(vertical.shape[1])
        places = np.arange(width) / width
        fitted = np.polyfit(places, vertical.T, 1)
        straight = fitted[0][:, None] * places[None, :] + fitted[1][:, None]
        return float(np.median(np.abs(vertical - straight).max(axis=1))) / width * BEND_WIDTH_PX

    def remap(self, image: Samples, color_mode: ColorMode) -> Samples:
        """Take the pixels of the flat page from the bent one.

        :param image: The samples of the bent page.
        :type image: Samples
        :param color_mode: Colour mode of the page, a bilevel page being made bilevel again after the remapping.
        :type color_mode: ColorMode
        :returns: The samples of the flat page, of the size of the bent one.
        :rtype: Samples
        """
        height = image.shape[0]
        flat = np.empty_like(image)
        for top in range(0, height, STRIP_ROWS):
            rows = np.arange(top, min(top + STRIP_ROWS, height), dtype=np.float64)
            map_x, map_y = self._maps(rows)
            flat[top : top + len(rows)] = cv2.remap(
                image, map_x, map_y, interpolation=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
            )
        return to_bilevel(flat) if color_mode is ColorMode.BILEVEL else flat

    def grid(self, height: int) -> Floats:
        """Sample the field on the grid of the mesh file.

        :param height: Height of the page in pixels.
        :type height: int
        :returns: For each node of an even grid over the flat page the place of the bent page it is taken from, as x and
                  y in pixels, in an array of the rows, the columns and two.
        :rtype: Floats
        """
        width = self._offsets.shape[1]
        map_x, map_y = self._maps(np.linspace(0, height - 1, GRID_ROWS))
        nodes = np.round(np.linspace(0, width - 1, GRID_COLUMNS)).astype(np.intp)
        return np.stack([map_x[:, nodes], map_y[:, nodes]], axis=-1).astype(np.float64)

    def write(self, height: int, path: Path) -> None:
        """Write the mesh file of the field.

        :param height: Height of the page in pixels.
        :type height: int
        :param path: Local path of the file to make.
        :type path: Path
        :raises ConflictError: If the file cannot be written.
        """
        document = {
            'width': self._offsets.shape[1],
            'height': height,
            'grid': np.round(self.grid(height), 2).tolist(),
        }
        try:
            path.write_text(json.dumps(document, separators=(',', ':')), encoding='utf-8')
        except OSError as error:
            raise ConflictError(UNWRITABLE_MESH.format(path=path.name)) from error

    def _maps(self, rows: Floats) -> tuple[Maps, Maps]:
        """Work out where each pixel of some rows of the flat page is taken from.

        :param rows: Heights of the rows of the flat page, which may lie between pixels.
        :type rows: Floats
        :returns: The horizontal and the vertical places in the bent page, one for each pixel, as OpenCV remaps by.
        :rtype: tuple[Maps, Maps]
        """
        first = np.clip(np.searchsorted(self._targets, rows) - 1, 0, len(self._targets) - 2)
        share = np.clip((rows - self._targets[first]) / (self._targets[first + 1] - self._targets[first]), 0, 1)
        offsets = self._offsets[first] * (1 - share)[:, None, None] + self._offsets[first + 1] * share[:, None, None]
        columns = np.arange(self._offsets.shape[1], dtype=np.float64)
        map_x = np.asarray(columns[None, :] + offsets[:, :, 0], dtype=np.float32)
        map_y = np.asarray(rows[:, None] + offsets[:, :, 1], dtype=np.float32)
        return map_x, map_y
