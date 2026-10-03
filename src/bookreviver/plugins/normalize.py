"""Placing the block of text of a page on a page of the book, so every page has one size, text size and layout.

``geometry.normalize`` reads the page ``geometry.crop`` cut to its block of text and puts it on a blank page of the size
the parameters give. A size of 0, which a book has until it is measured, makes the page the block and its margins, so
the page is never mostly blank before the size of the book is known.

The block is scaled so that the distance between its lines becomes the target one, which makes the letters of a
photograph as large as those of a scan of the same book, and a page whose line height is farther from the
target than ``max_scale_change`` percent of it is placed unscaled and marked for review, since such a difference means
the lines were not measured right. The line height comes from the data ``geometry.crop`` wrote, or else it is
measured on the block here.

The page has a margin on each of its four sides. The two at the sides are told apart by the side of the book the page
lies on: the inner margin is the one at the gutter, on the left of a right page and on the right of a left page, so the
margins of the two pages of a spread mirror. A book without spreads asks for the left and the right margin instead,
which are the same on every page. A block narrower or lower than the work area inside the margins stands at the edge
the alignment names, which is how the last page of a chapter keeps its first line where the first line of every other
page is. The rest of the page is filled with the median colour of the paper of the block, or with white.

A frame the user gave as a ``rect`` edit replaces all of this for the page: the block is fitted to that rectangle of the
page, which sets its place and its scale at once. The transform is the scaling and the shift that carry a point of the
block to the page.
"""

import math
from typing import TYPE_CHECKING, Self, override

import cv2
import numpy as np
from pydantic import Field, model_validator

from bookreviver.domain.enums import (
    ColorMode,
    HorizontalAlign,
    MarginsBy,
    MarginsSource,
    PageSide,
    PaperFill,
    ProcessorScope,
    ReviewReason,
    Stage,
    TransformKind,
    VersionData,
    VersionOutput,
    VerticalAlign,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Rect, Transform
from bookreviver.domain.values import ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.plugins.cv_image import (
    MANUAL_CONFIDENCE,
    NO_IMAGE,
    WHITE,
    color_mode_of,
    image_data,
    line_pitch,
    paper_colour,
    read_samples,
    settle_review,
    to_bilevel,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Samples
    from bookreviver.ports.processing import StepInput

NORMALIZED_IMAGE_NAME: str = 'normalized.png'
# The name of the step and of the setting that says where its margins come from
MARGINS_TITLE: str = 'Margins'
PERCENT: float = 100.0
HALF: float = 2.0
# The bounds of the sizes in pixels, wide enough for a scan of a large page at a high resolution
MAX_PAGE_PX: int = 20_000
MIN_PAGE_PX: int = 64
MAX_MARGIN_PX: int = 10_000
MAX_LINE_HEIGHT_PX: float = 1_000.0
NO_ROOM: str = 'The margins leave no room for text on the page: they take {margins} of {page} pixels.'
TOO_SMALL: str = 'A page of {page} pixels is too small: give at least {smallest} pixels, or 0 to fit the block.'


class NormalizeParams(Params):
    """How the block of text is scaled and where it is put on the page.

    :ivar margins_source: Whether measuring the book writes the four margins, or the user set them and the measure
                          leaves them as they are. The step itself places the block by the margins either way.
    :ivar line_height: Target distance between the lines of text in pixels, 0 to keep the size of the text.
    :ivar max_scale_change: How far the line height of a page may be from the target, in percent of the target, before
                            the page is left unscaled and marked for review.
    :ivar page_width: Width of the page in pixels.
    :ivar page_height: Height of the page in pixels.
    :ivar margin_top: Margin at the top of the page in pixels.
    :ivar margin_bottom: Margin at the bottom of the page in pixels.
    :ivar margin_inner: Margin at the gutter in pixels, or the left margin when the margins are by left and right.
    :ivar margin_outer: Margin at the outer edge in pixels, or the right margin when the margins are by left and right.
    :ivar margins_by: Whether the side margins are inner and outer, by the side of the book, or left and right.
    :ivar align_vertical: Where a block lower than the work area stands.
    :ivar align_horizontal: Where a block narrower than the work area stands.
    :ivar fill: What fills the page round the block.
    """

    margins_source: MarginsSource = Field(
        default=MarginsSource.MEASURED,
        title=MARGINS_TITLE,
        description='measured lets Measure the book fill in the four margins, manual keeps the margins you set '
        'and the measure leaves them as they are',
    )
    line_height: float = Field(
        default=0.0,
        ge=0,
        le=MAX_LINE_HEIGHT_PX,
        title='Line height',
        description='Distance between the lines of text, in pixels; 0 keeps the size of the text as it is. '
        'Measure the book fills it in',
    )
    max_scale_change: float = Field(
        default=25.0,
        ge=0,
        le=PERCENT,
        title='Largest change of size',
        description='A page whose line height is farther than this many percent of the target from it '
        'is left as it is and marked for review',
    )
    page_width: int = Field(
        default=0,
        ge=0,
        le=MAX_PAGE_PX,
        title='Page width',
        description='Width of the page, in pixels; 0 makes it the block and the side margins. '
        'Measure the book fills it in',
    )
    page_height: int = Field(
        default=0,
        ge=0,
        le=MAX_PAGE_PX,
        title='Page height',
        description='Height of the page, in pixels; 0 makes it the block and the top and bottom margins. '
        'Measure the book fills it in',
    )
    margin_top: int = Field(
        default=150, ge=0, le=MAX_MARGIN_PX, title='Top margin', description='Margin at the top, in pixels'
    )
    margin_bottom: int = Field(
        default=200, ge=0, le=MAX_MARGIN_PX, title='Bottom margin', description='Margin at the bottom, in pixels'
    )
    margin_inner: int = Field(
        default=200,
        ge=0,
        le=MAX_MARGIN_PX,
        title='Inner margin',
        description='Margin at the gutter, in pixels; the left margin when the margins are by left and right',
    )
    margin_outer: int = Field(
        default=150,
        ge=0,
        le=MAX_MARGIN_PX,
        title='Outer margin',
        description='Margin at the outer edge, in pixels; the right margin when the margins are by left and right',
    )
    margins_by: MarginsBy = Field(
        default=MarginsBy.INNER_OUTER,
        title='Side margins',
        description='inner-outer mirrors the margins on the two pages of a spread, left-right keeps them as they are',
    )
    align_vertical: VerticalAlign = Field(
        default=VerticalAlign.TOP,
        title='Vertical alignment',
        description='Where a block lower than the room between the margins stands',
    )
    align_horizontal: HorizontalAlign = Field(
        default=HorizontalAlign.CENTER,
        title='Horizontal alignment',
        description='Where a block narrower than the room between the margins stands',
    )
    fill: PaperFill = Field(
        default=PaperFill.PAPER,
        title='Fill',
        description='What fills the page round the block: the colour of the paper, or white',
    )

    @model_validator(mode='after')
    def check_room_for_text(self) -> Self:
        """Check that a size of the page that is given is not too small and leaves some of it for the text.

        A size of 0 is the block and its margins, which always has room for the block.

        :returns: The parameters.
        :rtype: Self
        :raises ValueError: If a size of the page is below the smallest one, or the margins of a direction take the
                            whole page.
        """
        for margins, page in (
            (self.margin_top + self.margin_bottom, self.page_height),
            (self.margin_inner + self.margin_outer, self.page_width),
        ):
            if page == 0:
                continue
            if page < MIN_PAGE_PX:
                raise ValueError(TOO_SMALL.format(page=page, smallest=MIN_PAGE_PX))
            if margins >= page:
                raise ValueError(NO_ROOM.format(margins=margins, page=page))
        return self


class PagePlan:
    """Where a block of text stands on a page of the book, worked out in the pixels of the full page.

    :ivar size: Width and height of the page, as the parameters give them or else as the block and its margins make.
    :ivar area: The room the margins leave on the page.
    """

    def __init__(self, params: NormalizeParams, side: PageSide | None, block: tuple[float, float]) -> None:
        """Work out the size of the page of a side of the book and the room its margins leave.

        :param params: The parameters of the step.
        :type params: NormalizeParams
        :param side: Side of the book the page lies on, or None, which is taken for the right page.
        :type side: PageSide | None
        :param block: Width and height of the block as it is placed, which make a size of the page the parameters leave
                      at 0.
        :type block: tuple[float, float]
        """
        self._params = params
        # The gutter is on the left of a right page, and the left margin of a book without spreads is the inner one
        self._inner_left = params.margins_by is MarginsBy.LEFT_RIGHT or side is not PageSide.LEFT
        left = params.margin_inner if self._inner_left else params.margin_outer
        right = params.margin_outer if self._inner_left else params.margin_inner
        self._right = right
        width = params.page_width or math.ceil(left + block[0] + right)
        height = params.page_height or math.ceil(params.margin_top + block[1] + params.margin_bottom)
        self.size = (width, height)
        self.area = Rect(
            left=left,
            top=params.margin_top,
            width=width - left - right,
            height=height - params.margin_top - params.margin_bottom,
        )

    def size_around(self, frame: Rect) -> tuple[int, int]:
        """Give the size of a page that holds a block the user placed by hand, with the margins past it.

        :param frame: The place of the block on the page, from a rect edit.
        :type frame: Rect
        :returns: Width and height of the page, as the parameters give them or else as the frame and the margins make.
        :rtype: tuple[int, int]
        """
        return (
            self._params.page_width or math.ceil(frame.left + frame.width + self._right),
            self._params.page_height or math.ceil(frame.top + frame.height + self._params.margin_bottom),
        )

    def place(self, width: float, height: float) -> Rect:
        """Put a block on the page by the alignment of the parameters.

        :param width: Width of the block in pixels of the full page.
        :type width: float
        :param height: Height of the block in pixels of the full page.
        :type height: float
        :returns: The place of the block on the page, which reaches past the work area when the block is larger than it.
        :rtype: Rect
        """
        area = self.area
        free_x, free_y = area.width - width, area.height - height
        at_left = {
            HorizontalAlign.INNER: self._inner_left,
            HorizontalAlign.OUTER: not self._inner_left,
            HorizontalAlign.LEFT: True,
            HorizontalAlign.RIGHT: False,
        }.get(self._params.align_horizontal)
        left = area.left + free_x / HALF if at_left is None else area.left + (0.0 if at_left else free_x)
        match self._params.align_vertical:
            case VerticalAlign.TOP:
                top = area.top
            case VerticalAlign.BOTTOM:
                top = area.top + free_y
            case _:
                top = area.top + free_y / HALF
        return Rect(left=left, top=top, width=width, height=height)


class Normalize(ModelProcessor):
    """Scales a block of text to the size of the text of the book and puts it on a page of the book."""

    params_model = NormalizeParams
    spec = ProcessorSpec(
        key='geometry.normalize',
        version='1',
        title=MARGINS_TITLE,
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=NormalizeParams.model_json_schema(),
        editor=Rect.editor,
        by_page_side=True,
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Scale the block of the input and put it on a page.

        :param step_input: The image of the block, the parameters, the side of the page, and the rect edit if any.
        :type step_input: StepInput
        :returns: One output holding the page.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = NormalizeParams.model_validate(step_input.params)
        image = read_samples(step_input.image)
        color_mode = color_mode_of(image, step_input.input_data)
        scale = step_input.scale
        height, width = image.shape[:2]
        measured = self._measured(step_input, image)
        target, page_size, review = self._target(step_input, params, (width / scale, height / scale), measured)
        box = (
            round(target.left * scale),
            round(target.top * scale),
            max(1, round(target.width * scale)),
            max(1, round(target.height * scale)),
        )
        page = self._paint(image, color_mode, params, box, (round(page_size[0] * scale), round(page_size[1] * scale)))
        write_png(page, step_input.workdir / NORMALIZED_IMAGE_NAME)
        data = image_data(page, step_input.input_data, color_mode)
        edited = step_input.edit is not None and isinstance(step_input.edit.geometry, Rect)
        data |= {
            VersionData.SOURCE_WIDTH_PX: page_size[0],
            VersionData.SOURCE_HEIGHT_PX: page_size[1],
            VersionData.FRAME: target.to_data(),
            VersionData.CONFIDENCE: MANUAL_CONFIDENCE
            if edited
            else step_input.input_data.get(VersionData.CONFIDENCE, MANUAL_CONFIDENCE),
            VersionData.SKIPPED: False,
        }
        if measured is not None:
            data[VersionData.LINE_HEIGHT_PX] = measured * target.height / (height / scale)
        if isinstance(dpi := data.get(VersionData.DPI), int | float):
            # The block is scaled, and so is the number of its pixels in an inch
            data[VersionData.DPI] = dpi * target.width / (width / scale)
        matrix = (box[2] / width, 0.0, float(box[0]), 0.0, box[3] / height, float(box[1]), 0.0, 0.0, 1.0)
        return StepResult(
            outputs=[
                StepOutput(
                    image=step_input.workdir / NORMALIZED_IMAGE_NAME,
                    color_mode=color_mode,
                    transform=Transform(kind=TransformKind.PLACE, matrix=matrix),
                    data=data,
                    review=settle_review(data, review, step_input.input_data),
                )
            ]
        )

    @staticmethod
    def _measured(step_input: StepInput, image: Samples) -> float | None:
        """Give the distance between the lines of the block, as the input tells it or else as it is measured here.

        :param step_input: The facts of the input, which the crop fills in, and the scale of the image.
        :type step_input: StepInput
        :param image: The samples of the block, whose lines are measured when the input does not tell their distance.
        :type image: Samples
        :returns: The distance in pixels of the full image, or None when the block has no lines to measure.
        :rtype: float | None
        """
        stated = step_input.input_data.get(VersionData.LINE_HEIGHT_PX)
        if isinstance(stated, int | float):
            return float(stated)
        pitch = line_pitch(image)
        return None if pitch is None else pitch / step_input.scale

    @staticmethod
    def _target(
        step_input: StepInput, params: NormalizeParams, size: tuple[float, float], measured: float | None
    ) -> tuple[Rect, tuple[int, int], ReviewReason | None]:
        """Work out where the block stands on the page, how large the page is, and whether the page needs a look.

        :param step_input: The side of the page and the rect edit if there is one.
        :type step_input: StepInput
        :param params: The parameters of the step.
        :type params: NormalizeParams
        :param size: Width and height of the block in the pixels of the full image.
        :type size: tuple[float, float]
        :param measured: The distance between the lines of the block, or None when it has none to measure.
        :type measured: float | None
        :returns: The place of the block and the width and height of the page, in the pixels of the full page, and the
                  reason to review the page or None.
        :rtype: tuple[Rect, tuple[int, int], ReviewReason | None]
        """
        width, height = size
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, Rect):
            frame = edit.geometry
            return frame, PagePlan(params, step_input.side, (frame.width, frame.height)).size_around(frame), None
        factor, review = 1.0, None
        if (wanted := params.line_height) > 0:
            if measured is None or measured <= 0:
                review = ReviewReason.LOW_CONFIDENCE
            # How far the text of the page is from the size of the book, as a share of the size of the book
            elif abs(measured - wanted) / wanted * PERCENT > params.max_scale_change:
                review = ReviewReason.TEXT_SIZE
            else:
                factor = wanted / measured
        plan = PagePlan(params, step_input.side, (width * factor, height * factor))
        return plan.place(width * factor, height * factor), plan.size, review

    @staticmethod
    def _paint(
        image: Samples,
        color_mode: ColorMode,
        params: NormalizeParams,
        box: tuple[int, int, int, int],
        page_size: tuple[int, int],
    ) -> Samples:
        """Scale the block to its place and paint it on a page of the colour of the fill.

        :param image: The samples of the block.
        :type image: Samples
        :param color_mode: Colour mode of the block, which a bilevel block keeps after it is scaled.
        :type color_mode: ColorMode
        :param params: The parameters of the step, whose fill colours the page.
        :type params: NormalizeParams
        :param box: Left, top, width and height of the place of the block on the page, in pixels of this run.
        :type box: tuple[int, int, int, int]
        :param page_size: Width and height of the page, in pixels of this run.
        :type page_size: tuple[int, int]
        :returns: The samples of the page.
        :rtype: Samples
        """
        height, width = image.shape[:2]
        left, top, new_width, new_height = box
        if (new_width, new_height) != (width, height):
            shrinking = new_width * new_height < width * height
            image = np.asarray(
                cv2.resize(
                    image, (new_width, new_height), interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_CUBIC
                ),
                dtype=np.uint8,
            )
            if color_mode is ColorMode.BILEVEL:
                image = to_bilevel(image)
        page_width, page_height = page_size
        page = np.empty((page_height, page_width, *image.shape[2:]), dtype=np.uint8)
        page[:] = WHITE if params.fill is PaperFill.WHITE else paper_colour(image)
        inside = (max(left, 0), max(top, 0), min(left + new_width, page_width), min(top + new_height, page_height))
        if inside[0] < inside[2] and inside[1] < inside[3]:
            page[inside[1] : inside[3], inside[0] : inside[2]] = image[
                inside[1] - top : inside[3] - top, inside[0] - left : inside[2] - left
            ]
        return page
