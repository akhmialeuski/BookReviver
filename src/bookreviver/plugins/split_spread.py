"""The page split of a spread: one scan of two facing pages becomes the left page and the right page.

``split.spread`` looks for the gutter, the dark strip where the book was bound, in the middle of the scan and cuts along
it. The mean brightness of every column is taken over the central ``search_band`` of the width, smoothed, and the
darkest column is the cut. A page whose columns are all of one brightness is cut at the middle of the band. The user may
draw the cut instead, as a line that need not be vertical, which is the ``line`` edit and replaces the search.

The step makes two outputs, the left half and then the right half. Each is the part of the scan on its side of the cut,
cut to the rectangle that holds it, with what lies on the other side of a slanting cut painted white. ``overlap_px``
lets each half reach that many pixels over the cut, so the margin of a page that was bound tight is not lost. The
transform of each output is the crop of its rectangle, which maps a point of the scan to the half by moving it.
"""

import math
from typing import TYPE_CHECKING, override

import cv2
import numpy as np
from attrs import frozen
from pydantic import Field

from bookreviver.domain.enums import ProcessorScope, Stage, TransformKind, VersionData, VersionOutput
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Line, Point, Quad, Transform
from bookreviver.domain.values import ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.plugins.cv_image import (
    COLOR_PLANES,
    NO_IMAGE,
    WHITE,
    color_mode_of,
    image_data,
    read_samples,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Floats, Indices, Samples
    from bookreviver.ports.processing import StepInput

HALF_IMAGE_NAMES: tuple[str, str] = ('left.png', 'right.png')
# A smoothing window as a fraction of the width of the scan, which evens out the specks and the letters in a column
SMOOTHING_FRACTION: float = 0.01
# Columns whose brightness differs by less than this have no gutter to find
MIN_CONTRAST: float = 2.0
# How far above the darkest column, as a fraction of the range of the profile, a column is still part of the gutter
PLATEAU_FRACTION: float = 0.1
HORIZONTAL_CUT: str = 'The cut line is horizontal, so it has no left side and no right side.'
CUT_OUTSIDE: str = 'The cut lies outside the scan, so a half of the spread would be empty.'


class SpreadParams(Params):
    """Where the gutter is looked for, and how far each half reaches over the cut.

    :ivar search_band: Fraction of the width, around the middle of the scan, in which the gutter is looked for.
    :ivar overlap_px: Pixels each half reaches over the cut into the other.
    """

    search_band: float = Field(default=0.3, gt=0, le=1, description='Fraction of the width, in the middle, to search')
    overlap_px: int = Field(default=0, ge=0, description='Pixels each half reaches over the cut')


@frozen(kw_only=True)
class Cut:
    """The cut of a spread, a straight line in the pixels of the scan.

    :ivar top_x: Distance of the cut from the left edge at the top row.
    :ivar bottom_x: Distance of the cut from the left edge at the bottom row.
    """

    top_x: float
    bottom_x: float

    def at_rows(self, height: int) -> Floats:
        """Give the place of the cut on every row.

        :param height: Height of the scan in rows.
        :type height: int
        :returns: The distance from the left edge of the cut, one number for each row.
        :rtype: Floats
        """
        return np.linspace(self.top_x, self.bottom_x, height)


class SplitSpread(ModelProcessor):
    """Cuts a spread along its gutter into the left page and the right page."""

    params_model = SpreadParams
    spec = ProcessorSpec(
        key='split.spread',
        version='1',
        title='Spread',
        stage=Stage.PAGE_SPLIT,
        scope=ProcessorScope.SPLIT,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=SpreadParams.model_json_schema(),
        editor=Line.editor,
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Find the cut and write the two halves.

        :param step_input: The ``full`` image of the scan, the parameters, and the line edit if there is one.
        :type step_input: StepInput
        :returns: The left half and then the right half, each with the crop it was cut by.
        :rtype: StepResult
        :raises ConflictError: If there is no image, it cannot be read, or the cut lies outside it.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = SpreadParams.model_validate(step_input.params)
        scan = read_samples(step_input.image)
        height, width = scan.shape[:2]
        cut = self._cut(scan, params, step_input)
        color_mode = color_mode_of(scan, step_input.input_data)
        columns = np.arange(width)[np.newaxis, :]
        edge = cut.at_rows(height)[:, np.newaxis]
        outputs = []
        for name, side in zip(HALF_IMAGE_NAMES, (-1, 1), strict=True):
            half, origin = self._half(scan, columns, edge, side * params.overlap_px, side)
            target = step_input.workdir / name
            write_png(half, target)
            data = image_data(half, step_input.input_data, color_mode) | {
                VersionData.OVERLAP_PX: params.overlap_px,
                VersionData.CUT_X: float((cut.top_x + cut.bottom_x) / 2),
            }
            transform = Transform(
                kind=TransformKind.CROP,
                quad=self._rectangle(origin, half),
                matrix=(1.0, 0.0, -float(origin), 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            )
            outputs.append(StepOutput(image=target, color_mode=color_mode, transform=transform, data=data))
        return StepResult(outputs=outputs)

    @staticmethod
    def _cut(scan: Samples, params: SpreadParams, step_input: StepInput) -> Cut:
        """Choose the cut: the line the user drew, or the gutter the search finds.

        :param scan: The samples of the scan.
        :type scan: Samples
        :param params: The parameters of the step.
        :type params: SpreadParams
        :param step_input: The input of the step, which may hold a line edit and the scale of the image.
        :type step_input: StepInput
        :returns: The cut in the pixels of the image the step reads.
        :rtype: Cut
        """
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, Line):
            line, scale = edit.geometry, step_input.scale
            return SplitSpread._line_cut(line, scale, scan.shape[0])
        return SplitSpread._search(scan, params.search_band)

    @staticmethod
    def _line_cut(line: Line, scale: float, height: int) -> Cut:
        """Turn the line of an edit into the cut at the top and the bottom row of the image.

        :param line: The line the user drew, in the pixels of the full image.
        :type line: Line
        :param scale: Size of the image the step reads over the size of the full image.
        :type scale: float
        :param height: Height of the image the step reads in rows.
        :type height: int
        :returns: The cut, which for a line that is not vertical reaches beyond the line's own points.
        :rtype: Cut
        :raises ConflictError: If the line is horizontal, so no side of it is the left.
        """
        start_x, start_y, end_x, end_y = (
            value * scale for value in (line.start.x, line.start.y, line.end.x, line.end.y)
        )
        if math.isclose(start_y, end_y):
            raise ConflictError(HORIZONTAL_CUT)
        slope = (end_x - start_x) / (end_y - start_y)
        return Cut(top_x=start_x - slope * start_y, bottom_x=start_x + slope * (height - 1 - start_y))

    @staticmethod
    def _search(scan: Samples, band: float) -> Cut:
        """Find the gutter as the darkest column in the central band of the scan.

        :param scan: The samples of the scan.
        :type scan: Samples
        :param band: Fraction of the width, around the middle, to search in.
        :type band: float
        :returns: A vertical cut through the darkest column, or through the middle when no column stands out.
        :rtype: Cut
        """
        gray = cv2.cvtColor(scan, cv2.COLOR_BGR2GRAY) if scan.ndim == COLOR_PLANES else scan
        width = gray.shape[1]
        first = int(width * (1 - band) / 2)
        last = max(first + 1, int(width * (1 + band) / 2))
        profile = gray[:, first:last].mean(axis=0, dtype=np.float32)
        window = max(1, int(width * SMOOTHING_FRACTION))
        # The ends repeat their last column, since padding with zeros would make the edges of the band the darkest
        smooth = cv2.blur(profile.reshape(1, -1), (window, 1), borderType=cv2.BORDER_REPLICATE).ravel()
        if float(np.ptp(smooth)) < MIN_CONTRAST:
            middle = (first + last) / 2
            return Cut(top_x=middle, bottom_x=middle)
        # The gutter is as wide as the shadow, so the cut goes through the middle of the columns that are as dark as it
        darkest = np.flatnonzero(smooth <= smooth.min() + PLATEAU_FRACTION * float(np.ptp(smooth)))
        gutter = first + float(darkest.mean()) + 0.5
        return Cut(top_x=gutter, bottom_x=gutter)

    @staticmethod
    def _half(scan: Samples, columns: Indices, edge: Floats, reach: int, side: int) -> tuple[Samples, int]:
        """Cut one half out of the scan and paint white what lies on the other side of the cut.

        :param scan: The samples of the scan.
        :type scan: Samples
        :param columns: The column numbers of the scan as one row.
        :type columns: Indices
        :param edge: The place of the cut on every row, as one column.
        :type edge: Floats
        :param reach: How far the half reaches over the cut, positive towards the right, negative towards the left.
        :type reach: int
        :param side: -1 for the left half and 1 for the right half.
        :type side: int
        :returns: The samples of the half and the distance of its left edge from the left edge of the scan.
        :rtype: tuple[Samples, int]
        :raises ConflictError: If the half would be empty.
        """
        width = scan.shape[1]
        limit = edge - reach
        inside = columns < limit if side < 0 else columns >= limit
        if side < 0:
            origin, end = 0, min(width, math.ceil(float(limit.max())))
        else:
            origin, end = max(0, math.floor(float(limit.min()))), width
        if end <= origin or not inside.any():
            raise ConflictError(CUT_OUTSIDE)
        half = np.where(inside[:, :, np.newaxis] if scan.ndim == COLOR_PLANES else inside, scan, WHITE).astype(np.uint8)
        return half[:, origin:end], origin

    @staticmethod
    def _rectangle(origin: int, half: Samples) -> Quad:
        """Name the part of the scan a half was cut to as a quadrilateral.

        :param origin: Distance of the left edge of the half from the left edge of the scan.
        :type origin: int
        :param half: The samples of the half.
        :type half: Samples
        :returns: The corners of the rectangle, in the pixels of the scan.
        :rtype: Quad
        """
        height, width = half.shape[:2]
        return Quad(
            top_left=Point(x=origin, y=0),
            top_right=Point(x=origin + width, y=0),
            bottom_right=Point(x=origin + width, y=height),
            bottom_left=Point(x=origin, y=height),
        )
