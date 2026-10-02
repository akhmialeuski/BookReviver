"""Straightening the sheet of paper out of a scan, with a search of the sheet by the colour of the paper.

``geometry.perspective`` finds the four corners of the paper on a scan that shows more than the paper, such as a page
photographed on a table or scanned on a dark binding, and warps the paper into an upright rectangle. The scan is shrunk,
its brightness is split in two by the method of Otsu into paper and background, the holes the lines of text leave in the
paper are closed and the specks on the background are opened, and the largest region is the sheet. The convex hull of
the region is simplified to four corners, or else replaced by the smallest rectangle that holds it.

A sheet smaller than the parameter ``min_sheet_fraction`` of the scan is no sheet, as a cover or a blank leaf with no
clean paper on it, and the page is left as it is and marked for review. So is a scan whose two classes are too close in
tone for a split to mean anything. The confidence is how cleanly the paper parts from the background, times how well the
hull fills its four corners. A side of the sheet that lies on the edge of the scan means the scanner cut the paper
there, and the sides are written to the data of the version for the step that crops the page. The corners the user gave
as a ``quad`` edit replace the search and have the confidence 1.

The sheet is warped into a rectangle whose sides are the longer of each pair of opposite sides of the quadrilateral. The
transform is the perspective matrix, which maps a point of the scan to the warped page.
"""

from typing import TYPE_CHECKING, override

import cv2
import numpy as np
from attrs import frozen
from pydantic import Field

from bookreviver.domain.enums import (
    ColorMode,
    ProcessorScope,
    ReviewReason,
    SheetEdge,
    Stage,
    TransformKind,
    VersionData,
    VersionOutput,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Point, Quad, Transform
from bookreviver.domain.values import ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.plugins.cv_image import (
    COLOR_PLANES,
    MANUAL_CONFIDENCE,
    MIN_TONE_CONTRAST,
    NO_IMAGE,
    WHITE,
    OtsuSplit,
    color_mode_of,
    image_data,
    odd_size,
    read_samples,
    settle_review,
    source_size_data,
    to_bilevel,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from bookreviver.plugins.cv_image import Floats, Samples
    from bookreviver.ports.processing import StepInput

# The points of a contour as OpenCV gives them, one row of one point each
type Hull = NDArray[np.int32]

STRAIGHTENED_IMAGE_NAME: str = 'straightened.png'
# The longer side in pixels the scan is shrunk to for the search of the sheet, since the paper shows at it
SEARCH_LONG_SIDE_PX: int = 600
# Sizes of the squares that close the holes of the lines of text and open the specks, as shares of the longer side
CLOSE_SIDE_SHARE: float = 0.03
OPEN_SIDE_SHARE: float = 0.01
# How far the corners of the hull may stray from a quadrilateral, as shares of its perimeter, tried from the least up
POLYGON_TOLERANCES: tuple[float, ...] = (0.01, 0.015, 0.02, 0.03, 0.04, 0.05)
# The corners of a quadrilateral
CORNER_COUNT: int = 4
# The number of labels of a scan whose regions are none, since the first label is the background
BACKGROUND_ONLY_LABELS: int = 1
# How close a corner of the sheet is to an edge of the scan to lie on it, as a share of the size of the scan
EDGE_TOLERANCE_SHARE: float = 0.01
# Confidence below which the sheet is found but the page is marked for review
LOW_CONFIDENCE_LIMIT: float = 0.5


class PerspectiveParams(Params):
    """How large a sheet has to be to be believed.

    :ivar min_sheet_fraction: Share of the area of the scan the sheet has to cover.
    """

    min_sheet_fraction: float = Field(
        default=0.25,
        gt=0,
        le=1,
        title='Smallest sheet',
        description='Share of the scan the sheet has to cover, or the page is left as it is',
    )


@frozen(kw_only=True)
class Sheet:
    """The sheet of paper a search found.

    :ivar corners: Corners in the pixels of the scan, top left, top right, bottom right and bottom left, one row each.
    :ivar confidence: How sure the search is of the sheet, from 0 to 1.
    """

    corners: Floats
    confidence: float

    def cut_edges(self, width: int, height: int) -> list[SheetEdge]:
        """Name the sides of the sheet that lie on the edge of the scan.

        :param width: Width of the scan in pixels.
        :type width: int
        :param height: Height of the scan in pixels.
        :type height: int
        :returns: The sides in the order top, right, bottom, left.
        :rtype: list[SheetEdge]
        """
        top_left, top_right, bottom_right, bottom_left = self.corners
        slack_x = EDGE_TOLERANCE_SHARE * width
        slack_y = EDGE_TOLERANCE_SHARE * height
        on_edge = {
            SheetEdge.TOP: max(top_left[1], top_right[1]) <= slack_y,
            SheetEdge.RIGHT: min(top_right[0], bottom_right[0]) >= width - slack_x,
            SheetEdge.BOTTOM: min(bottom_left[1], bottom_right[1]) >= height - slack_y,
            SheetEdge.LEFT: max(top_left[0], bottom_left[0]) <= slack_x,
        }
        return [edge for edge in SheetEdge if on_edge[edge]]


class SheetSearch:
    """Finds the sheet of paper on a scan by the colour of the paper."""

    def __init__(self, image: Samples, min_sheet_fraction: float) -> None:
        """Shrink the scan and keep what the search shares between its parts.

        :param image: The samples of the scan.
        :type image: Samples
        :param min_sheet_fraction: Share of the scan the sheet has to cover.
        :type min_sheet_fraction: float
        """
        height, width = image.shape[:2]
        shrink = min(1.0, SEARCH_LONG_SIDE_PX / max(height, width))
        size = (max(1, round(width * shrink)), max(1, round(height * shrink)))
        small = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        self._brightness = np.asarray(small.max(axis=2) if small.ndim == COLOR_PLANES else small, dtype=np.uint8)
        # The ratios that turn a place of the shrunk scan into a place of the scan
        self._to_scan = np.array([width / size[0], height / size[1]])
        self._min_fraction = min_sheet_fraction

    def find(self) -> Sheet | None:
        """Search the sheet.

        :returns: The sheet, or None when the scan holds no paper that parts from its background, or the paper is too
                  small to be a sheet.
        :rtype: Sheet | None
        """
        split = OtsuSplit.of(self._brightness)
        if split.contrast < MIN_TONE_CONTRAST:
            return None
        hull = self._paper_hull(split.threshold)
        if hull is None:
            return None
        corners = self._quadrilateral(hull)
        area = cv2.contourArea(corners.astype(np.float32))
        if area < self._min_fraction * self._brightness.size:
            return None
        fill = min(1.0, float(cv2.contourArea(hull)) / area)
        return Sheet(corners=corners * self._to_scan, confidence=split.separability * fill)

    def _paper_hull(self, threshold: float) -> Hull | None:
        """Take the paper of the scan as the largest bright region and give its convex hull.

        The holes the lines of text leave in the paper are closed and the specks on the background are opened first.

        :param threshold: Brightness above which a pixel is paper.
        :type threshold: float
        :returns: The hull, or None when nothing is brighter than the threshold.
        :rtype: Hull | None
        """
        paper = np.asarray(self._brightness > threshold, dtype=np.uint8) * WHITE
        long_side = max(self._brightness.shape)
        close = cv2.getStructuringElement(cv2.MORPH_RECT, (odd_size(CLOSE_SIDE_SHARE * long_side),) * 2)
        speck = cv2.getStructuringElement(cv2.MORPH_RECT, (odd_size(OPEN_SIDE_SHARE * long_side),) * 2)
        closed = cv2.morphologyEx(paper, cv2.MORPH_CLOSE, close)
        opened = np.asarray(cv2.morphologyEx(closed, cv2.MORPH_OPEN, speck), dtype=np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
        if count <= BACKGROUND_ONLY_LABELS:
            return None
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        contours, _ = cv2.findContours(
            np.asarray(labels == largest, dtype=np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )
        outline = contours[int(np.argmax([cv2.contourArea(contour) for contour in contours]))]
        return np.asarray(cv2.convexHull(outline), dtype=np.int32)

    @staticmethod
    def _quadrilateral(hull: Hull) -> Floats:
        """Simplify a convex hull to the four corners of a sheet.

        The corners of the hull are thinned out by a tolerance that grows until four are left, which keeps the slant
        of the sides that a perspective gives. A hull that never comes to four is fitted with the smallest rectangle.

        :param hull: The convex hull as the points OpenCV gives.
        :type hull: Hull
        :returns: The four corners, top left, top right, bottom right and bottom left, one row each.
        :rtype: Floats
        """
        perimeter = cv2.arcLength(hull, closed=True)
        points: Floats | None = None
        for tolerance in POLYGON_TOLERANCES:
            approximated = cv2.approxPolyDP(hull, tolerance * perimeter, closed=True)
            if len(approximated) == CORNER_COUNT:
                points = np.asarray(approximated, dtype=np.float64).reshape(CORNER_COUNT, 2)
                break
        if points is None:
            points = np.asarray(cv2.boxPoints(cv2.minAreaRect(hull)), dtype=np.float64)
        sums = points.sum(axis=1)
        differences = points[:, 1] - points[:, 0]
        return points[[sums.argmin(), differences.argmin(), sums.argmax(), differences.argmax()]]


class Perspective(ModelProcessor):
    """Warps the sheet of paper of a scan into an upright rectangle."""

    params_model = PerspectiveParams
    spec = ProcessorSpec(
        key='geometry.perspective',
        version='1',
        title='Perspective',
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=PerspectiveParams.model_json_schema(),
        editor=Quad.editor,
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Find the sheet and warp it upright.

        :param step_input: The image of the page, the parameters, and the quad edit if there is one.
        :type step_input: StepInput
        :returns: One output holding the straightened page, or the page as it was when no sheet is found.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        image = read_samples(step_input.image)
        color_mode = color_mode_of(image, step_input.input_data)
        sheet = self._sheet(step_input, image)
        if sheet is None:
            return self._unchanged(step_input, image, color_mode)
        warped, matrix = self._warp(image, sheet.corners, color_mode)
        target = step_input.workdir / STRAIGHTENED_IMAGE_NAME
        write_png(warped, target)
        transform = Transform(kind=TransformKind.PERSPECTIVE, quad=self._quad(sheet.corners), matrix=matrix)
        data = image_data(warped, step_input.input_data, color_mode) | source_size_data(image, step_input.scale)
        data |= {
            VersionData.QUAD: self._quad(sheet.corners / step_input.scale).to_data(),
            VersionData.CUT_EDGES: [edge.value for edge in sheet.cut_edges(image.shape[1], image.shape[0])],
            VersionData.CONFIDENCE: sheet.confidence,
            VersionData.SKIPPED: False,
        }
        own = ReviewReason.LOW_CONFIDENCE if sheet.confidence < LOW_CONFIDENCE_LIMIT else None
        review = settle_review(data, own, step_input.input_data)
        return StepResult(
            outputs=[StepOutput(image=target, color_mode=color_mode, transform=transform, data=data, review=review)]
        )

    def _sheet(self, step_input: StepInput, image: Samples) -> Sheet | None:
        """Take the sheet the user gave, or else search it.

        :param step_input: The parameters of the step, its quad edit if there is one, and the scale of the image.
        :type step_input: StepInput
        :param image: The samples of the scan.
        :type image: Samples
        :returns: The sheet, or None when the search finds none.
        :rtype: Sheet | None
        """
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, Quad):
            corners = np.array([[point.x, point.y] for point in edit.geometry.points()]) * step_input.scale
            return Sheet(corners=corners, confidence=MANUAL_CONFIDENCE)
        params = PerspectiveParams.model_validate(step_input.params)
        return SheetSearch(image, params.min_sheet_fraction).find()

    @staticmethod
    def _unchanged(step_input: StepInput, image: Samples, color_mode: ColorMode) -> StepResult:
        """Give the result of a scan with no sheet: the scan as it was, marked for review.

        :param step_input: The image of the scan and the data of its input version.
        :type step_input: StepInput
        :param image: The samples of the scan.
        :type image: Samples
        :param color_mode: Colour mode of the scan.
        :type color_mode: ColorMode
        :returns: One output holding the image of the input.
        :rtype: StepResult
        """
        data = image_data(image, step_input.input_data, color_mode) | source_size_data(image, step_input.scale)
        data |= {VersionData.CONFIDENCE: 0.0, VersionData.SKIPPED: True}
        review = settle_review(data, ReviewReason.NOT_APPLIED, step_input.input_data)
        return StepResult(outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data, review=review)])

    @staticmethod
    def _quad(corners: Floats) -> Quad:
        """Make the quadrilateral of four corners.

        :param corners: The corners, top left, top right, bottom right and bottom left, one row each.
        :type corners: Floats
        :returns: The quadrilateral.
        :rtype: Quad
        """
        return Quad.from_points([Point(x=float(x), y=float(y)) for x, y in corners])

    @staticmethod
    def _warp(image: Samples, corners: Floats, color_mode: ColorMode) -> tuple[Samples, tuple[float, ...]]:
        """Warp the quadrilateral of a scan into a rectangle.

        :param image: The samples of the scan.
        :type image: Samples
        :param corners: The corners of the sheet, top left, top right, bottom right and bottom left, one row each.
        :type corners: Floats
        :param color_mode: Colour mode of the page, a bilevel page being made bilevel again after the warp.
        :type color_mode: ColorMode
        :returns: The warped samples and the matrix, nine numbers in rows, that maps a point of the scan to the result.
        :rtype: tuple[Samples, tuple[float, ...]]
        :raises ConflictError: If the corners do not make a quadrilateral that can be warped.
        """
        top_left, top_right, bottom_right, bottom_left = corners
        width = round(max(np.linalg.norm(top_right - top_left), np.linalg.norm(bottom_right - bottom_left)))
        height = round(max(np.linalg.norm(bottom_left - top_left), np.linalg.norm(bottom_right - top_right)))
        if width < 1 or height < 1:
            err_msg = 'The corners of the sheet are too close together to make a page.'
            raise ConflictError(err_msg)
        target = np.array([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=np.float32)
        matrix = cv2.getPerspectiveTransform(corners.astype(np.float32), target)
        warped = np.asarray(
            cv2.warpPerspective(image, matrix, (width, height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE),
            dtype=np.uint8,
        )
        if color_mode is ColorMode.BILEVEL:
            warped = to_bilevel(warped)
        return warped, tuple(float(value) for value in matrix.ravel())
