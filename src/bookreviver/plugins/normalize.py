"""Placing the content box of a page on a page of the book, so every page has one size, text size and layout.

``geometry.normalize`` works on the box of the content of its input, never on the whole input. The box is the one the
user drew as a ``ContentBox`` edit, else the one ``geometry.crop`` recorded when its input carries it, else the one the
search of ``geometry.crop`` (``FrameSearch``) finds on the input here, so a recipe without Select content places the
block of text and not the sheet round it. A page with no ink to find a box by is placed whole and unscaled and marked
for review. ``geometry.crop`` leaves its page uncut, so the box lies in the pixels of the input, and this step is the
one that cuts it out and puts it on a blank page of the size the parameters give. An input that an older
``geometry.crop`` already cut is read as well, since its frame is brought into the pixels of the cut page. A size of 0
makes the page the box and its margins, which a run of the book replaces with the size by the book before the step runs.

The box is scaled so that the distance between its lines becomes the target one, which makes the letters of a
photograph as large as those of a scan of the same book, and a page whose line height is farther from the
target than ``max_scale_change`` percent of it is placed unscaled and marked for review, since such a difference means
the lines were not measured right. The line height comes from the data ``geometry.crop`` wrote when the box is its
own, or else it is measured on the box here.

The page has a margin on each of its four sides, in millimetres of the paper, which the step turns into pixels by the
resolution of the page after the box is scaled, so a scan at 300 dpi, one at 600 dpi and a photograph get the same
margin. A page whose resolution is unknown takes the width of the content box of the book for a block of
``NOMINAL_BLOCK_MM`` millimetres, which keeps the margins in proportion to the text. The two margins at the sides are
told apart by the side of the book the page lies on: the inner margin is the one at the gutter, on the left of a right
page and on the right of a left page, so the margins of the two pages of a spread mirror. A book without spreads asks
for the left and the right margin instead, which are the same on every page. A box narrower or lower than the work area
inside the margins stands at the edge the alignment names, which is how the last page of a chapter keeps its first line
where the first line of every other page is. The rest of the page is filled with the median colour of the paper of the
box, or with white.

The step records what a reader of the page and the measure of the book need: the box it placed and its place on the
page, the factor it scaled the box by, and the box grown by the margins of the page, all in the pixels of the full image
it read. The transform is the scaling and the shift that carry a point of the input to the page.
"""

import math
from typing import TYPE_CHECKING, Self, override

import cv2
import numpy as np
from attrs import frozen
from pydantic import Field, model_validator

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    HorizontalAlign,
    MarginsBy,
    MarginsSource,
    NormalizeParam,
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
from bookreviver.domain.geometry import ContentBox, Rect, Transform
from bookreviver.domain.margins import DEFAULT_MARGINS_MM, MarginScale
from bookreviver.domain.text_scale import DEFAULT_MAX_SCALE_CHANGE, scale_factor
from bookreviver.domain.values import OrderRule, ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.plugins.crop import CropParams, FrameSearch, cut_edges_of
from bookreviver.plugins.cv_image import (
    MANUAL_CONFIDENCE,
    NO_IMAGE,
    WHITE,
    color_mode_of,
    content_frame_of,
    image_data,
    line_pitch,
    paper_colour,
    read_samples,
    settle_review,
    source_size_data,
    to_bilevel,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.domain.values import MetadataMap
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
MAX_MARGIN_MM: float = 50.0
MAX_LINE_HEIGHT_PX: float = 1_000.0
NO_ROOM: str = 'The margins leave no room for text on the page: they take {margins} of {page} pixels.'
TOO_SMALL: str = 'A page of {page} pixels is too small: give at least {smallest} pixels, or 0 to size it by the book.'


class NormalizeParams(Params):
    """How the content box is scaled and where it is put on the page.

    :ivar margins_source: Whether measuring the book writes the four margins, or the user set them and the measure
                          leaves them as they are. The step itself places the box by the margins either way.
    :ivar line_height: Target distance between the lines of text in pixels, 0 to take the one of the book, or to keep
                       the size of the text where the book has none.
    :ivar max_scale_change: How far the line height of a page may be from the target, in percent of the target, before
                            the page is left unscaled and marked for review.
    :ivar page_width: Width of the page in pixels, 0 for the width by the book.
    :ivar page_height: Height of the page in pixels, 0 for the height by the book.
    :ivar margin_top: Margin at the top of the page in millimetres.
    :ivar margin_bottom: Margin at the bottom of the page in millimetres.
    :ivar margin_inner: Margin at the gutter in millimetres, or the left margin when the margins are by left and right.
    :ivar margin_outer: Margin at the outer edge in millimetres, or the right margin when the margins are by left and
                        right.
    :ivar margins_by: Whether the side margins are inner and outer, by the side of the book, or left and right.
    :ivar align_vertical: Where a box lower than the work area stands.
    :ivar align_horizontal: Where a box narrower than the work area stands.
    :ivar fill: What fills the page round the box.
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
        description='Distance between the lines of text, in pixels; 0 takes the median of the pages of the book when '
        'the book is run, and keeps the size of the text when one page is. Measure the book fills it in',
    )
    max_scale_change: float = Field(
        default=DEFAULT_MAX_SCALE_CHANGE,
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
        description='Width of the page, in pixels; 0 takes the largest content box of the book and the side margins. '
        'Measure the book fills it in',
    )
    page_height: int = Field(
        default=0,
        ge=0,
        le=MAX_PAGE_PX,
        title='Page height',
        description='Height of the page, in pixels; 0 takes the largest content box of the book and the top and '
        'bottom margins. Measure the book fills it in',
    )
    margin_top: float = Field(
        default=DEFAULT_MARGINS_MM[NormalizeParam.MARGIN_TOP],
        ge=0,
        le=MAX_MARGIN_MM,
        title='Top margin, mm',
        description='Margin at the top, in millimetres of the paper',
    )
    margin_bottom: float = Field(
        default=DEFAULT_MARGINS_MM[NormalizeParam.MARGIN_BOTTOM],
        ge=0,
        le=MAX_MARGIN_MM,
        title='Bottom margin, mm',
        description='Margin at the bottom, in millimetres of the paper',
    )
    margin_inner: float = Field(
        default=DEFAULT_MARGINS_MM[NormalizeParam.MARGIN_INNER],
        ge=0,
        le=MAX_MARGIN_MM,
        title='Inner margin, mm',
        description='Margin at the gutter, in millimetres of the paper; the left margin when the margins are by left '
        'and right',
    )
    margin_outer: float = Field(
        default=DEFAULT_MARGINS_MM[NormalizeParam.MARGIN_OUTER],
        ge=0,
        le=MAX_MARGIN_MM,
        title='Outer margin, mm',
        description='Margin at the outer edge, in millimetres of the paper; the right margin when the margins are by '
        'left and right',
    )
    margins_by: MarginsBy = Field(
        default=MarginsBy.INNER_OUTER,
        title='Side margins',
        description='inner-outer mirrors the margins on the two pages of a spread, left-right keeps them as they are',
    )
    align_vertical: VerticalAlign = Field(
        default=VerticalAlign.TOP,
        title='Vertical alignment',
        description='Where a box lower than the room between the margins stands',
    )
    align_horizontal: HorizontalAlign = Field(
        default=HorizontalAlign.CENTER,
        title='Horizontal alignment',
        description='Where a box narrower than the room between the margins stands',
    )
    fill: PaperFill = Field(
        default=PaperFill.PAPER,
        title='Fill',
        description='What fills the page round the box: the colour of the paper, or white',
    )

    @model_validator(mode='after')
    def check_page_size(self) -> Self:
        """Check that a size of the page that is given is not too small.

        A size of 0 is the one by the book, which always has room for the box. Whether the margins leave room on a page
        of the size given depends on the resolution of the page, so the step checks that when it places the box.

        :returns: The parameters.
        :rtype: Self
        :raises ValueError: If a size of the page is below the smallest one.
        """
        for page in (self.page_height, self.page_width):
            if 0 < page < MIN_PAGE_PX:
                raise ValueError(TOO_SMALL.format(page=page, smallest=MIN_PAGE_PX))
        return self


@frozen(kw_only=True)
class Content:
    """The box of the content of a page that the step places, and where it came from.

    :ivar rect: The box in the pixels of the image the step reads.
    :ivar confidence: How sure the step is of the box, from 0 to 1.
    :ivar edited: Whether the user drew the box.
    :ivar from_input: Whether the box is the one an earlier step found, which also measured the lines of the box.
    """

    rect: Rect
    confidence: float
    edited: bool = False
    from_input: bool = False

    def bounds(self, width: int, height: int) -> tuple[int, int, int, int]:
        """Round the box outwards to whole pixels of the image, and keep it on the image.

        :param width: Width of the image in pixels.
        :type width: int
        :param height: Height of the image in pixels.
        :type height: int
        :returns: Left, top, right and bottom, at least a pixel apart, the right and the bottom being outside the box.
        :rtype: tuple[int, int, int, int]
        """
        left = min(max(math.floor(self.rect.left), 0), width - 1)
        top = min(max(math.floor(self.rect.top), 0), height - 1)
        right = min(max(math.ceil(self.rect.left + self.rect.width), left + 1), width)
        bottom = min(max(math.ceil(self.rect.top + self.rect.height), top + 1), height)
        return left, top, right, bottom


class PagePlan:
    """Where a box stands on a page of the book, worked out in the pixels of the full page.

    :ivar size: Width and height of the page, as the parameters give them or else as the box and its margins make.
    :ivar area: The room the margins leave on the page.
    :ivar margins: Left, top, right and bottom margin of the page in pixels, by the side of the book the page lies on.
    :ivar margin_names: The parameter that holds the margin of each side of the page, by the name of the side.
    """

    def __init__(
        self, params: NormalizeParams, side: PageSide | None, block: tuple[float, float], dpi: float | None
    ) -> None:
        """Work out the size of the page of a side of the book and the room its margins leave.

        :param params: The parameters of the step.
        :type params: NormalizeParams
        :param side: Side of the book the page lies on, or None, which is taken for the right page.
        :type side: PageSide | None
        :param block: Width and height of the box as it is placed, which make a size of the page the parameters leave
                      at 0.
        :type block: tuple[float, float]
        :param dpi: Resolution of the page after the box is scaled, or None when the page has none.
        :type dpi: float | None
        :raises ConflictError: If the margins take the whole of a size of the page that the parameters give.
        """
        self._params = params
        # The gutter is on the left of a right page, and the left margin of a book without spreads is the inner one
        self._inner_left = params.margins_by is MarginsBy.LEFT_RIGHT or side is not PageSide.LEFT
        left_mm = params.margin_inner if self._inner_left else params.margin_outer
        right_mm = params.margin_outer if self._inner_left else params.margin_inner
        if dpi is not None:
            scale = MarginScale.from_dpi(dpi)
        elif params.page_width:
            scale = MarginScale.from_page(params.page_width, left_mm + right_mm)
        else:
            scale = MarginScale.from_block(block[0])
        self.pixels_per_mm = scale.pixels_per_mm
        left, top, right, bottom = (
            scale.pixels(mm) for mm in (left_mm, params.margin_top, right_mm, params.margin_bottom)
        )
        self.margins = (left, top, right, bottom)
        left_name, right_name = (
            (NormalizeParam.MARGIN_INNER, NormalizeParam.MARGIN_OUTER)
            if self._inner_left
            else (NormalizeParam.MARGIN_OUTER, NormalizeParam.MARGIN_INNER)
        )
        self.margin_names = {
            'left': left_name.value,
            'top': NormalizeParam.MARGIN_TOP.value,
            'right': right_name.value,
            'bottom': NormalizeParam.MARGIN_BOTTOM.value,
        }
        width = params.page_width or math.ceil(left + block[0] + right)
        height = params.page_height or math.ceil(top + block[1] + bottom)
        for taken, page in ((left + right, width), (top + bottom, height)):
            if taken >= page:
                raise ConflictError(NO_ROOM.format(margins=math.ceil(taken), page=page))
        self.size = (width, height)
        self.area = Rect(left=left, top=top, width=width - left - right, height=height - top - bottom)

    def place(self, width: float, height: float) -> Rect:
        """Put a box on the page by the alignment of the parameters.

        :param width: Width of the box in pixels of the full page.
        :type width: float
        :param height: Height of the box in pixels of the full page.
        :type height: float
        :returns: The place of the box on the page, which reaches past the work area when the box is larger than it.
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


class Placing:
    """One content box on its way to the page: its pixels, its scale and its place, worked out once.

    :ivar bounds: Left, top, right and bottom of the box in the pixels of the image read.
    :ivar target: The place of the box on the page, in the pixels of the full page.
    :ivar factor: The factor the box is scaled by.
    :ivar review: Why the page needs a look, or None.
    """

    def __init__(self, step_input: StepInput, params: NormalizeParams, image: Samples, content: Content | None) -> None:
        """Cut the box out of the image, measure its lines, and work out the page and the place of the box on it.

        :param step_input: The data of the input version, the side of the page and the scale of the image.
        :type step_input: StepInput
        :param params: The parameters of the step.
        :type params: NormalizeParams
        :param image: The samples of the input.
        :type image: Samples
        :param content: The content box, or None for a page with no ink, which is placed whole.
        :type content: Content | None
        """
        self._params = params
        self._content = content
        self._image = image
        self._scale = step_input.scale
        height, width = image.shape[:2]
        self.bounds = (0, 0, width, height) if content is None else content.bounds(width, height)
        left, top, right, bottom = self.bounds
        self._block = image[top:bottom, left:right]
        known = step_input.input_data.get(VersionData.LINE_HEIGHT_PX)
        if content is not None and content.from_input and isinstance(known, int | float):
            self._measured: float | None = float(known)
        else:
            pitch = line_pitch(self._block)
            self._measured = None if pitch is None else pitch / self._scale
        self.factor, self.review = 1.0, None if content is not None else ReviewReason.NOT_APPLIED
        if content is not None and (wanted := params.line_height) > 0:
            if self._measured is None or self._measured <= 0:
                self.review = ReviewReason.LOW_CONFIDENCE
            elif (factor := scale_factor(self._measured, wanted, params.max_scale_change)) is None:
                self.review = ReviewReason.TEXT_SIZE
            else:
                self.factor = factor
        # The box as it is placed, in the pixels of the full page
        block = ((right - left) / self._scale * self.factor, (bottom - top) / self._scale * self.factor)
        dpi = step_input.input_data.get(VersionData.DPI)
        # The resolution of the box changes with its size, and the margins of the page are lengths of the paper
        self._plan = PagePlan(
            params, step_input.side, block, dpi * self.factor if isinstance(dpi, int | float) and dpi > 0 else None
        )
        self.target = self._plan.place(*block)
        # The place of the box and the size of the page in the pixels of this run
        self._box = (
            round(self.target.left * self._scale),
            round(self.target.top * self._scale),
            max(1, round(self.target.width * self._scale)),
            max(1, round(self.target.height * self._scale)),
        )

    def paint(self, color_mode: ColorMode) -> Samples:
        """Scale the box to its place and paint it on a page of the colour of the fill.

        :param color_mode: Colour mode of the box, which a bilevel box keeps after it is scaled.
        :type color_mode: ColorMode
        :returns: The samples of the page.
        :rtype: Samples
        """
        image = self._block
        height, width = image.shape[:2]
        left, top, new_width, new_height = self._box
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
        page_width, page_height = (round(side * self._scale) for side in self._plan.size)
        page = np.empty((page_height, page_width, *image.shape[2:]), dtype=np.uint8)
        page[:] = WHITE if self._params.fill is PaperFill.WHITE else paper_colour(image)
        inside = (max(left, 0), max(top, 0), min(left + new_width, page_width), min(top + new_height, page_height))
        if inside[0] < inside[2] and inside[1] < inside[3]:
            page[inside[1] : inside[3], inside[0] : inside[2]] = image[
                inside[1] - top : inside[3] - top, inside[0] - left : inside[2] - left
            ]
        return page

    def transform(self) -> Transform:
        """Describe the placing as a transform: the scaling of the box and the shift to its place.

        :returns: The place transform that carries a point of the input to the page.
        :rtype: Transform
        """
        left, top, right, bottom = self.bounds
        a, d = self._box[2] / (right - left), self._box[3] / (bottom - top)
        return Transform(
            kind=TransformKind.PLACE,
            matrix=(a, 0.0, self._box[0] - left * a, 0.0, d, self._box[1] - top * d, 0.0, 0.0, 1.0),
        )

    def data(self, page: Samples, color_mode: ColorMode, facts: MetadataMap) -> dict[str, object]:
        """Record what the page, the reader of it and the measure of the book need to know of the placing.

        :param page: The samples of the page.
        :type page: Samples
        :param color_mode: Colour mode of the page.
        :type color_mode: ColorMode
        :param facts: Data of the input version.
        :type facts: MetadataMap
        :returns: The size of the page, the box and its place, the scale, the box grown by the margins, and the line
                  height of the page, in the pixels of the full image.
        :rtype: dict[str, object]
        """
        content = self._content
        data = image_data(page, facts, color_mode) | source_size_data(self._image, self._scale)
        data |= {
            VersionData.FRAME: self.target.to_data(),
            # The block stands on the page at its place, which is where the steps after this one find the content
            VersionData.CONTENT_FRAME: self.target.to_data(),
            VersionData.CONFIDENCE: 0.0 if content is None else content.confidence,
            VersionData.SKIPPED: False,
        }
        if content is not None:
            left, top, right, bottom = self.bounds
            found = Rect(left=left, top=top, width=right - left, height=bottom - top).scaled(1 / self._scale)
            margin_left, margin_top, margin_right, margin_bottom = self._plan.margins
            data |= {
                VersionData.CONTENT_BOX: found.to_data(),
                VersionData.BLOCK_SCALE: self.factor,
                VersionData.MARGIN_PARAMS: self._plan.margin_names,
                # A distance on the image read is this many pixels to a millimetre of margin, which the editor needs
                VersionData.MARGIN_PIXELS_PER_MM: self._plan.pixels_per_mm / self.factor,
                # The margins are on the page, so they are brought back to the image the box was found on
                VersionData.MARGIN_BOX: Rect(
                    left=found.left - margin_left / self.factor,
                    top=found.top - margin_top / self.factor,
                    width=found.width + (margin_left + margin_right) / self.factor,
                    height=found.height + (margin_top + margin_bottom) / self.factor,
                ).to_data(),
            }
        if self._measured is not None:
            data[VersionData.LINE_HEIGHT_PX] = self._measured * self.factor
        if isinstance(dpi := data.get(VersionData.DPI), int | float):
            # The box is scaled, and so is the number of its pixels in an inch
            data[VersionData.DPI] = dpi * self.factor
        return data


class Normalize(ModelProcessor):
    """Scales the content box of a page to the size of the text of the book and puts it on a page of the book."""

    params_model = NormalizeParams
    spec = ProcessorSpec(
        key='geometry.normalize',
        version='3',
        title=MARGINS_TITLE,
        summary='One page size, scale and margins for the book',
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=NormalizeParams.model_json_schema(),
        editor=EditorKind.CONTENT_BOX,
        by_page_side=True,
        after=(
            OrderRule(
                processor_key='geometry.perspective',
                reason=(
                    'Margins scales the content box by the height of its lines, '
                    'which is measured on an upright sheet, '
                    'so it usually comes after Perspective.'
                ),
            ),
            OrderRule(
                processor_key='geometry.deskew',
                reason='Margins puts the content box on the page level, so it usually comes after Deskew.',
            ),
            OrderRule(
                processor_key='geometry.dewarp',
                reason=(
                    'Margins scales the content box by the height of its lines, '
                    'which is measured on a flat page, '
                    'so it usually comes after Dewarp.'
                ),
            ),
        ),
        requires_after=(
            OrderRule(
                processor_key='geometry.crop',
                reason=(
                    'Margins places on the page the content box that Select content finds, '
                    'so it cannot come before Select content.'
                ),
            ),
        ),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Find the content box of the input, scale it and put it on a page.

        :param step_input: The image, the parameters, the side of the page, and the content box edit if any.
        :type step_input: StepInput
        :returns: One output holding the page.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        image = read_samples(step_input.image)
        color_mode = color_mode_of(image, step_input.input_data)
        params = NormalizeParams.model_validate(step_input.params)
        placing = Placing(step_input, params, image, self._content(step_input, image))
        page = placing.paint(color_mode)
        write_png(page, step_input.workdir / NORMALIZED_IMAGE_NAME)
        data = placing.data(page, color_mode, step_input.input_data)
        return StepResult(
            outputs=[
                StepOutput(
                    image=step_input.workdir / NORMALIZED_IMAGE_NAME,
                    color_mode=color_mode,
                    transform=placing.transform(),
                    data=data,
                    review=settle_review(data, placing.review, step_input.input_data),
                )
            ]
        )

    @staticmethod
    def _content(step_input: StepInput, image: Samples) -> Content | None:
        """Find the content box: the user's, else the one an earlier step found, else the one searched here.

        :param step_input: The edit, the data of the input version and the scale of the image.
        :type step_input: StepInput
        :param image: The samples of the input.
        :type image: Samples
        :returns: The box in the pixels of the image, or None when the page has no ink that parts from its paper.
        :rtype: Content | None
        """
        scale = step_input.scale
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, ContentBox):
            return Content(rect=edit.geometry.scaled(scale), confidence=MANUAL_CONFIDENCE, edited=True)
        facts = step_input.input_data
        if (frame := content_frame_of(facts, scale)) is not None:
            confidence = facts.get(VersionData.CONFIDENCE)
            return Content(
                rect=frame.scaled(scale),
                confidence=float(confidence) if isinstance(confidence, int | float) else MANUAL_CONFIDENCE,
                from_input=True,
            )
        if (found := FrameSearch(image, CropParams(), cut_edges_of(facts)).find()) is None:
            return None
        return Content(rect=found.rect, confidence=found.confidence)
