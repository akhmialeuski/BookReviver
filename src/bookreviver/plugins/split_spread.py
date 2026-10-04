"""The page split of a spread: one scan of two facing pages becomes the left page and the right page.

``split.spread`` looks for the gutter, the place where the book was bound, in the middle of the scan and cuts along it.
``GutterSearch`` finds it in strips of the central ``search_band`` of the width and fits a line through them, so the cut
follows a gutter that slants because the book lay crooked on the glass. A scan with no gutter to find is cut at the
middle of the band. The user may draw the cut instead, as a line that need not be vertical, which is the ``line`` edit
and replaces the search.

The confidence of a found cut is how many strips agree on the line times how strong the gutter is in them, as the module
``gutter`` works it out. A cut below ``min_confidence`` is still made, since a spread has to be cut somewhere, but both
halves are marked for review so the user checks it. A cut the user drew has the confidence 1.

The step makes two outputs, the left half and then the right half. Each is the part of the scan on its side of the cut,
cut to the rectangle that holds it, with what lies on the other side of a slanting cut painted white. ``overlap_px``
lets each half reach that many pixels over the cut, so the margin of a page that was bound tight is not lost. The
transform of each output is the crop of its rectangle, which maps a point of the scan to the half by moving it.

Version 2 of the step searches in strips and cuts along a slanting line. The versions of version 1 stay in the history
of a page and can be chosen as before.
"""

import math
from typing import TYPE_CHECKING, override

import numpy as np
from pydantic import Field

from bookreviver.domain.enums import (
    ProcessorScope,
    ReviewReason,
    Stage,
    TransformKind,
    VersionData,
    VersionOutput,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Line, Point, Quad, SplitChoice, Transform
from bookreviver.domain.values import ProcessorSpec
from bookreviver.plugins.base import ModelProcessor
from bookreviver.plugins.cv_image import (
    COLOR_PLANES,
    NO_IMAGE,
    WHITE,
    color_mode_of,
    image_data,
    read_samples,
    write_png,
)
from bookreviver.plugins.gutter import Cut, GutterParams, GutterSearch
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Floats, Indices, Samples
    from bookreviver.ports.processing import StepInput

HALF_IMAGE_NAMES: tuple[str, str] = ('left.png', 'right.png')
HORIZONTAL_CUT: str = 'The cut line is horizontal, so it has no left side and no right side.'
CUT_OUTSIDE: str = 'The cut lies outside the scan, so a half of the spread would be empty.'


class SpreadParams(GutterParams):
    """Where the gutter is looked for, and how far each half reaches over the cut.

    :ivar overlap_px: Pixels each half reaches over the cut into the other.
    """

    overlap_px: int = Field(
        default=0,
        ge=0,
        title='Overlap',
        description='Pixels each half reaches over the cut',
    )


class SplitSpread(ModelProcessor):
    """Cuts a spread along its gutter into the left page and the right page."""

    params_model = SpreadParams
    spec = ProcessorSpec(
        key='split.spread',
        version='2',
        title='Spread',
        summary='Cuts a spread into two pages at a fixed place',
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
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, Line):
            cut = self.line_cut(edit.geometry, step_input.scale, scan.shape[0])
        else:
            cut = GutterSearch(params).search(scan)
        review = ReviewReason.LOW_CONFIDENCE if cut.confidence < params.min_confidence else None
        return self.halves(step_input, scan, cut, params.overlap_px, review)

    @staticmethod
    def line_cut(line: Line, scale: float, height: int) -> Cut:
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

    @classmethod
    def halves(
        cls, step_input: StepInput, scan: Samples, cut: Cut, overlap_px: int, review: ReviewReason | None
    ) -> StepResult:
        """Write the left half and the right half of a scan cut along a line.

        :param step_input: What the step reads, whose work directory the halves are written into.
        :type step_input: StepInput
        :param scan: The samples of the scan.
        :type scan: Samples
        :param cut: The cut.
        :type cut: Cut
        :param overlap_px: Pixels each half reaches over the cut into the other.
        :type overlap_px: int
        :param review: Why both halves are marked for a second look, or None.
        :type review: ReviewReason | None
        :returns: The left half and then the right half, each with the crop it was cut by.
        :rtype: StepResult
        :raises ConflictError: If the cut lies outside the scan, so a half would be empty.
        """
        height, width = scan.shape[:2]
        color_mode = color_mode_of(scan, step_input.input_data)
        columns = np.arange(width)[np.newaxis, :]
        edge = cut.at_rows(height)[:, np.newaxis]
        outputs = []
        for name, side in zip(HALF_IMAGE_NAMES, (-1, 1), strict=True):
            half, origin = cls._half(scan, columns, edge, side * overlap_px, side)
            target = step_input.workdir / name
            write_png(half, target)
            data = image_data(half, step_input.input_data, color_mode) | {
                VersionData.PAGES: SplitChoice.TWO_PAGES,
                VersionData.OVERLAP_PX: overlap_px,
                VersionData.CUT_X: float(cut.middle_x),
                VersionData.CUT_TOP_X: cut.top_x,
                VersionData.CUT_BOTTOM_X: cut.bottom_x,
                VersionData.CONFIDENCE: cut.confidence,
            }
            transform = Transform(
                kind=TransformKind.CROP,
                quad=cls._rectangle(origin, half),
                matrix=(1.0, 0.0, -float(origin), 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),
            )
            outputs.append(
                StepOutput(image=target, color_mode=color_mode, transform=transform, data=data, review=review)
            )
        return StepResult(outputs=outputs)

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
