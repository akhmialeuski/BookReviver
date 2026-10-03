"""The light of a page and the pictures on it, which ``cleanup.binarize`` keeps in tones while it makes the text black.

The paper of a scan is lit unevenly and has yellowed, so a tone of paper in one corner is darker than the ink of the
other. ``level_light`` estimates the tone of the paper at every point and divides it out, so the paper comes out white
and the ink and the pictures keep their tone relative to it. The estimate is made from the paper alone: the text is
removed from a shrunk copy by a closing, a smooth quadratic surface is fitted to the cells, and the cells that lie well
under it, such as a plate that fills part of the page, are left out of the next round of the fit. What the surface does
not explain, such as the shadow of a binding, is smoothed from the paper cells round it, so a picture is never mistaken
for the shadow it would be.

``PictureZones`` finds the pictures. Text is made of strokes that are ink or paper with a thin edge between, and a
picture is made of continuous tones, so a place where a large share of the pixels is neither ink nor paper and the
tones vary is a picture. The zones are rectangles, since that is what the user changes with the ``regions`` editor: a
zone the user drew is added to those the search found, or cut out of them.
"""

from typing import TYPE_CHECKING

import cv2
import numpy as np

from bookreviver.domain.enums import ZoneMode
from bookreviver.domain.geometry import Point, Zone
from bookreviver.plugins.cv_image import COLOR_PLANES, WHITE, OtsuSplit, odd_size

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from bookreviver.domain.geometry import Regions
    from bookreviver.plugins.cv_image import Samples

type Planes = NDArray[np.float32]

# The longer side in pixels the page is shrunk to for the estimate of the light of the paper
LIGHT_LONG_SIDE_PX: int = 800
# Size of the closing that removes the text from the estimate, as a share of the longer side of the shrunk page
LIGHT_CLOSING_SHARE: float = 0.03
# Cells of the estimate across the longer side, and the width of their smoothing in cells
LIGHT_CELLS: int = 32
LIGHT_SMOOTHING_CELLS: float = 2.5
# A cell darker than this share of the surface fitted to the paper is not paper, and how many times the fit is made
# again without the cells it left out
PAPER_SHARE: float = 0.9
SURFACE_ROUNDS: int = 4
# The tones that are neither ink nor paper, of a page whose light is level: those that lie round the threshold of Otsu,
# from this share of it below to this share of the way from it to white above
MIDDLE_BELOW_SHARE: float = 0.6
MIDDLE_ABOVE_SHARE: float = 0.4
# The longer side in pixels the page is shrunk to for the search of the pictures
PICTURE_LONG_SIDE_PX: int = 2_000
# The size of the neighbourhood that is weighed, as a share of the longer side
PICTURE_WINDOW_SHARE: float = 0.02
# The share of the neighbourhood that has tones between ink and paper and the spread of its tones, from which a
# neighbourhood is a picture
PICTURE_TONE_SHARE: float = 0.4
PICTURE_MIN_SPREAD: float = 8.0
# The size of the closing that joins the neighbourhoods of one picture, as a multiple of the neighbourhood
PICTURE_JOIN_FACTOR: int = 2
# The least area of a picture as a share of the page, which leaves out the ornaments and the stains
PICTURE_MIN_AREA_SHARE: float = 0.02
# What a zone is painted with in a mask of the pictures
ZONE_VALUE: int = 1


def _planes_of(weight: Planes, grid: Planes) -> Planes:
    """Give a plane of weights the shape of the grid it weighs, with a copy for each colour plane.

    :param weight: One weight for each cell.
    :type weight: Planes
    :param grid: The cells, with or without colour planes.
    :type grid: Planes
    :returns: The weights, shaped to multiply or divide the grid.
    :rtype: Planes
    """
    return weight[..., np.newaxis] if grid.ndim == COLOR_PLANES else weight


def _paper_surface(grid: Planes, tone: Planes) -> Planes:
    """Fit a smooth surface to the paper, leaving out the cells that are darker than paper is.

    The light of a scan falls off smoothly, so a quadratic surface follows it. A picture or a plate that fills a part of
    the page lies below that surface, and is dropped from the fit in the next round, so it does not drag the surface
    down to its own tone.

    :param grid: The cells of the shrunk page, with or without colour planes.
    :type grid: Planes
    :param tone: The brightness of each cell.
    :type tone: Planes
    :returns: The surface, of the shape of the grid.
    :rtype: Planes
    """
    rows, columns = tone.shape
    down, across = np.mgrid[0:rows, 0:columns]
    y = down.ravel() / max(rows - 1, 1)
    x = across.ravel() / max(columns - 1, 1)
    design = np.stack([np.ones_like(x), x, y, x * x, x * y, y * y], axis=1)
    values = grid.reshape(rows * columns, -1)
    keep = np.ones(rows * columns, dtype=np.bool_)
    fitted = values
    for _ in range(SURFACE_ROUNDS):
        coefficients = np.linalg.lstsq(design[keep], values[keep], rcond=None)[0]
        fitted = np.maximum(design @ coefficients, 1.0)
        keep = tone.ravel() / fitted.mean(axis=1) >= PAPER_SHARE
        if keep.sum() < design.shape[1]:
            break
    return np.asarray(fitted.reshape(grid.shape), dtype=np.float32)


def level_light(image: Samples) -> Samples:
    """Divide the tone of the paper out of a page, so the paper is white wherever the page is lit.

    :param image: Gray or colour samples.
    :type image: Samples
    :returns: The samples of the same shape, in which the estimate of the paper is white.
    :rtype: Samples
    """
    height, width = image.shape[:2]
    shrink = min(1.0, LIGHT_LONG_SIDE_PX / max(height, width))
    small = cv2.resize(image, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA)
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (odd_size(LIGHT_CLOSING_SHARE * max(small.shape[:2])),) * 2)
    cells = max(1, round(LIGHT_CELLS * min(height, width) / max(height, width)))
    grid_size = (LIGHT_CELLS, cells) if width >= height else (cells, LIGHT_CELLS)
    grid = cv2.resize(cv2.morphologyEx(small, cv2.MORPH_CLOSE, kernel), grid_size, interpolation=cv2.INTER_AREA)
    estimate = _light_estimate(np.asarray(grid, dtype=np.float32))
    background = np.asarray(cv2.resize(estimate, (width, height), interpolation=cv2.INTER_LINEAR), dtype=np.float32)
    leveled = image.astype(np.float32) * WHITE / np.maximum(background, 1.0)
    return np.asarray(np.clip(leveled, 0, WHITE), dtype=np.uint8)


def _light_estimate(grid: Planes) -> Planes:
    """Estimate the tone of the paper in each cell: the fitted surface, and the shadow it does not explain.

    :param grid: The cells of the shrunk page with the text closed away, with or without colour planes.
    :type grid: Planes
    :returns: The tone of the paper in each cell, of the shape of the grid.
    :rtype: Planes
    """
    tone = np.asarray(grid.mean(axis=2) if grid.ndim == COLOR_PLANES else grid, dtype=np.float32)
    surface = _paper_surface(grid, tone)
    # What the surface does not explain is the shadow of a fold or a binding, which the paper round a cell gives it
    surface_tone = surface.mean(axis=2) if grid.ndim == COLOR_PLANES else surface
    weight = (tone / surface_tone >= PAPER_SHARE).astype(np.float32)
    smoothed = np.asarray(cv2.GaussianBlur(weight, (0, 0), LIGHT_SMOOTHING_CELLS), dtype=np.float32)
    numerator = np.asarray(
        cv2.GaussianBlur(grid / surface * _planes_of(weight, grid), (0, 0), LIGHT_SMOOTHING_CELLS), dtype=np.float32
    )
    return np.asarray(
        surface * numerator / np.maximum(_planes_of(smoothed, grid), np.finfo(np.float32).eps), dtype=np.float32
    )


class PictureZones:
    """Finds the pictures of a page by their tones, and applies the zones the user drew to them."""

    def __init__(self, gray: Samples) -> None:
        """Shrink the page for the search.

        :param gray: Single-plane samples of the page whose light is level.
        :type gray: Samples
        """
        height, width = gray.shape
        self._shrink = min(1.0, PICTURE_LONG_SIDE_PX / max(height, width))
        self._small = np.asarray(
            cv2.resize(gray, None, fx=self._shrink, fy=self._shrink, interpolation=cv2.INTER_AREA), dtype=np.uint8
        )
        self._size = (width, height)

    def _cells(self, window: int) -> Samples:
        """Mark the neighbourhoods that are made of continuous tones.

        :param window: Side of the neighbourhood in pixels of the shrunk page.
        :type window: int
        :returns: A plane that is 1 where the neighbourhood is a picture and 0 elsewhere.
        :rtype: Samples
        """
        small = self._small
        floats = small.astype(np.float32)
        threshold = OtsuSplit.of(small).threshold
        low = MIDDLE_BELOW_SHARE * threshold
        high = threshold + MIDDLE_ABOVE_SHARE * (WHITE - threshold)
        tone_share = cv2.blur(((small > low) & (small < high)).astype(np.float32), (window, window))
        mean = cv2.blur(floats, (window, window))
        spread = np.sqrt(np.maximum(cv2.blur(floats * floats, (window, window)) - mean * mean, 0.0))
        return np.asarray((tone_share >= PICTURE_TONE_SHARE) & (spread >= PICTURE_MIN_SPREAD), dtype=np.uint8)

    def find(self) -> list[Zone]:
        """Search the pictures.

        :returns: One rectangular zone to add for each picture, in the pixels of the page.
        :rtype: list[Zone]
        """
        window = odd_size(PICTURE_WINDOW_SHARE * max(self._small.shape))
        join = cv2.getStructuringElement(cv2.MORPH_RECT, (odd_size(PICTURE_JOIN_FACTOR * window),) * 2)
        joined = cv2.morphologyEx(self._cells(window), cv2.MORPH_CLOSE, join)
        count, _, stats, _ = cv2.connectedComponentsWithStats(joined, connectivity=8)
        least = PICTURE_MIN_AREA_SHARE * self._small.shape[0] * self._small.shape[1]
        zones: list[Zone] = []
        for label in range(1, count):
            if stats[label, cv2.CC_STAT_AREA] < least:
                continue
            left, top = int(stats[label, cv2.CC_STAT_LEFT]), int(stats[label, cv2.CC_STAT_TOP])
            right = left + int(stats[label, cv2.CC_STAT_WIDTH])
            bottom = top + int(stats[label, cv2.CC_STAT_HEIGHT])
            corners = ((left, top), (right, top), (right, bottom), (left, bottom))
            zones.append(
                Zone(mode=ZoneMode.ADD, points=tuple(Point(x=x / self._shrink, y=y / self._shrink) for x, y in corners))
            )
        return zones

    def mask(self, found: list[Zone], edit: Regions | None) -> Samples:
        """Paint the pictures of a page, the ones found and then the zones the user drew.

        :param found: The zones the search found.
        :type found: list[Zone]
        :param edit: What the user drew, in the pixels of the page, or None.
        :type edit: Regions | None
        :returns: A plane of the size of the page that is 1 in a picture and 0 elsewhere.
        :rtype: Samples
        """
        width, height = self._size
        mask = np.zeros((height, width), dtype=np.uint8)
        for zone in [*found, *(() if edit is None else edit.zones)]:
            polygon = np.array([[round(point.x), round(point.y)] for point in zone.points], dtype=np.int32)
            value = ZONE_VALUE if zone.mode is ZoneMode.ADD else 0
            cv2.fillPoly(mask, [polygon], value)
        return mask
