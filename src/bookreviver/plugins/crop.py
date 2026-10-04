"""Cutting a page down to the frame of its content, with even margins of the colour of the paper.

``geometry.crop`` finds where the text and the pictures of a page lie and cuts the page to that frame plus a margin on
each side. The page is shrunk and made black and white, by the method of Otsu or by a threshold that follows the light
of the neighbourhood. The specks of the ink are cleaned away, and so are the lines that touch the edge of the sheet,
which are the edge of the paper and not its content. Then the ink is smeared with a rectangle whose sides are shares of
the page, so the step behaves the same at any resolution of the scan, and the dense blocks that come of it are joined
into the frame. A block is kept by the ink it holds and by how full it is, never by where it lies, so no glyph is cut.

A side of the sheet that lies on the edge of the scan, which ``geometry.perspective`` records in ``cut_edges``, may hold
text that the scanner cut, so the lines that touch it are kept, and a frame that comes to such a side is marked for
review, because the margin on that side is not known. A page with no ink to speak of is left as it is and marked for
review. A frame the user gave as a ``rect`` edit replaces the search.

By default the page is cut to the frame alone, since the margins of the book page are set by ``geometry.normalize``. A
recipe may still ask for a margin, a share of the width of the frame on each side, and what it reaches beyond the page
is filled with the median colour of the paper. The step also measures the distance between the lines of text of the
frame, from the profile of its ink along the rows, and records it as the line height of the page for
``geometry.normalize`` and for the measuring of the book. The transform is a translation by the corner of the cropped
area, which maps a point of the input to the output.
"""

import math
from typing import TYPE_CHECKING, Literal, override

import cv2
import numpy as np
from attrs import frozen
from pydantic import Field

from bookreviver.domain.enums import (
    Binarization,
    CropMethod,
    ProcessorScope,
    ReviewReason,
    SheetEdge,
    Stage,
    StepMeasure,
    TransformKind,
    VersionData,
    VersionOutput,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Point, Quad, Rect, Transform
from bookreviver.domain.values import OrderRule, ProcessorSpec
from bookreviver.plugins.base import METHOD_TITLE, ModelProcessor, Params
from bookreviver.plugins.cv_image import (
    COLOR_PLANES,
    MANUAL_CONFIDENCE,
    MIN_TONE_CONTRAST,
    NO_IMAGE,
    WHITE,
    OtsuSplit,
    color_mode_of,
    image_data,
    line_pitch,
    odd_size,
    read_samples,
    settle_review,
    source_size_data,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.domain.enums import ColorMode
    from bookreviver.plugins.cv_image import Indices, Samples
    from bookreviver.ports.processing import StepInput

CROPPED_IMAGE_NAME: str = 'cropped.png'
# The longer side in pixels the page is shrunk to for the search of the frame, since the blocks of text show at it
SEARCH_LONG_SIDE_PX: int = 1_000
# Size of the rectangle that smears the ink into blocks, as shares of the width and of the height of the page
SMEAR_WIDTH_SHARE: float = 0.015
SMEAR_HEIGHT_SHARE: float = 0.006
SMEAR_ITERATIONS: int = 2
# The least ink a block holds to be kept, as a share of the area of the page, and the least share of the smeared block
# that the ink fills, which drop the specks and the sparse bands the smear makes of them
MIN_BLOCK_INK_SHARE: float = 0.0001
MIN_BLOCK_FILL: float = 0.1
# Side of the neighbourhood of the adaptive threshold as a share of the longer side, and what the threshold lies below
# the mean of the neighbourhood, in samples
ADAPTIVE_BLOCK_SHARE: float = 0.03
MIN_ADAPTIVE_BLOCK: int = 3
ADAPTIVE_OFFSET: int = 10
# Room the frame keeps round the blocks, in pixels of the shrunk page, since the edge of the ink of the full page falls
# between two pixels of the shrunk one
FRAME_ROOM_PX: int = 2
# How close the frame is to a side of the page to come to it, as a share of the size of the page
EDGE_TOLERANCE_SHARE: float = 0.01
PERCENT: float = 100.0


class CropParams(Params):
    """How the content is found and how much margin is left round it.

    ``CropMethod.LAYOUT`` is not accepted here while no Layout stage exists to give the regions it takes the frame from.

    :ivar method: The blocks of ink, the one method offered.
    :ivar margin_percent: Margin on each side as a percent of the width of the frame, 0 to cut to the frame alone.
    :ivar binarization: How the page is made black and white to find its ink.
    :ivar noise_min_area: Ink smaller than this many pixels of the shrunk page is dust and is cleaned away.
    """

    method: Literal[CropMethod.INK_BLOCKS] = Field(
        default=CropMethod.INK_BLOCKS,
        title=METHOD_TITLE,
        description='The frame is the union of the dense blocks of ink',
    )
    margin_percent: float = Field(
        default=0.0,
        ge=0,
        le=50,
        title='Margin',
        description='Margin on each side, in percent of the width of the content; 0 cuts to the content alone',
    )
    binarization: Binarization = Field(
        default=Binarization.OTSU,
        title='Black and white',
        description='How the page is made black and white to find its ink',
    )
    noise_min_area: int = Field(
        default=4,
        ge=0,
        le=10_000,
        title='Smallest speck',
        description='Ink smaller than this, in pixels of the page shrunk to a thousand, is dust',
    )


@frozen(kw_only=True)
class Frame:
    """The frame of the content of a page.

    :ivar rect: The frame in the pixels of the image it was searched on.
    :ivar confidence: How sure the search is of the frame, from 0 to 1.
    """

    rect: Rect
    confidence: float


class FrameSearch:
    """Finds the frame of the content of a page by its ink."""

    def __init__(self, image: Samples, params: CropParams, cut_edges: frozenset[SheetEdge]) -> None:
        """Shrink the page and make it black and white.

        :param image: The samples of the page.
        :type image: Samples
        :param params: The parameters of the step.
        :type params: CropParams
        :param cut_edges: Sides of the sheet that lie on the edge of the scan.
        :type cut_edges: frozenset[SheetEdge]
        """
        height, width = image.shape[:2]
        shrink = min(1.0, SEARCH_LONG_SIDE_PX / max(height, width))
        size = (max(1, round(width * shrink)), max(1, round(height * shrink)))
        self._small = cv2.resize(image, size, interpolation=cv2.INTER_AREA)
        # The ratios that turn a place of the shrunk page into a place of the page
        self._to_page = np.array([width / size[0], height / size[1]])
        self._page_size = np.array([width, height])
        self._params = params
        self._cut_edges = cut_edges
        gray = cv2.cvtColor(self._small, cv2.COLOR_BGR2GRAY) if self._small.ndim == COLOR_PLANES else self._small
        self._gray = np.asarray(gray, dtype=np.uint8)
        self._split = OtsuSplit.of(self._gray)
        self._ink = self._binarize()

    def paper_colour(self) -> Samples:
        """Give the median colour of the paper, which is every pixel that is not ink.

        :returns: One sample for a gray page and three for a colour one, and white where the page is all ink.
        :rtype: Samples
        """
        paper = self._small[self._ink == 0]
        shape = self._small.shape[2:]
        if paper.size == 0:
            return np.full(shape, WHITE, dtype=np.uint8)
        return np.asarray(np.median(paper, axis=0), dtype=np.uint8)

    def find(self) -> Frame | None:
        """Search the frame.

        :returns: The frame in the pixels of the page, or None when the page has no ink that parts from its paper.
        :rtype: Frame | None
        """
        if self._split.contrast < MIN_TONE_CONTRAST:
            return None
        blocks = self._blocks()
        if blocks is None:
            return None
        boxes, confidence = blocks
        left, top = boxes[:, cv2.CC_STAT_LEFT].min(), boxes[:, cv2.CC_STAT_TOP].min()
        right = (boxes[:, cv2.CC_STAT_LEFT] + boxes[:, cv2.CC_STAT_WIDTH]).max()
        bottom = (boxes[:, cv2.CC_STAT_TOP] + boxes[:, cv2.CC_STAT_HEIGHT]).max()
        first = np.maximum((np.array([left, top]) - FRAME_ROOM_PX) * self._to_page, 0)
        last = np.minimum((np.array([right, bottom]) + FRAME_ROOM_PX) * self._to_page, self._page_size)
        rect = Rect(
            left=float(first[0]), top=float(first[1]), width=float(last[0] - first[0]), height=float(last[1] - first[1])
        )
        return Frame(rect=rect, confidence=confidence)

    def _blocks(self) -> tuple[Indices, float] | None:
        """Smear the ink into blocks and keep the ones that hold enough of it and are full enough.

        :returns: The statistics of the kept blocks, one row each as OpenCV gives them, and the share of the ink of the
                  page that the kept blocks hold; None when no block is kept.
        :rtype: tuple[Indices, float] | None
        """
        ink = self._cleaned()
        height, width = ink.shape
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (odd_size(SMEAR_WIDTH_SHARE * width), odd_size(SMEAR_HEIGHT_SHARE * height))
        )
        smeared = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, kernel, iterations=SMEAR_ITERATIONS)
        count, found, stats, _ = cv2.connectedComponentsWithStats(smeared, connectivity=8)
        labels = np.asarray(found, dtype=np.intp)
        ink_per_block = np.bincount(labels.ravel(), weights=(ink > 0).ravel(), minlength=count)
        areas = stats[:, cv2.CC_STAT_AREA]
        kept = (ink_per_block >= MIN_BLOCK_INK_SHARE * width * height) & (ink_per_block >= MIN_BLOCK_FILL * areas)
        kept[0] = False  # Label 0 is the background
        if not kept.any():
            return None
        total = float((ink > 0).sum())
        return np.asarray(stats[kept], dtype=np.int_), float(ink_per_block[kept].sum()) / total

    def _binarize(self) -> Samples:
        """Make the shrunk page black and white.

        :returns: The ink as 255 on 0.
        :rtype: Samples
        """
        if self._params.binarization is Binarization.ADAPTIVE:
            block = max(MIN_ADAPTIVE_BLOCK, odd_size(ADAPTIVE_BLOCK_SHARE * max(self._gray.shape)))
            ink = cv2.adaptiveThreshold(
                self._gray, WHITE, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, ADAPTIVE_OFFSET
            )
        else:
            ink = np.where(self._gray > self._split.threshold, 0, WHITE)
        return np.asarray(ink, dtype=np.uint8)

    def _cleaned(self) -> Samples:
        """Clean the ink of specks and of the lines that lie on the edge of the sheet.

        A piece of ink that touches a side of the page is the edge of the paper, unless the scanner cut the paper at
        that side, where it may be text and is kept.

        :returns: The ink as 255 on 0, without what was cleaned.
        :rtype: Samples
        """
        height, width = self._ink.shape
        _, found, stats, _ = cv2.connectedComponentsWithStats(self._ink, connectivity=8)
        labels = np.asarray(found, dtype=np.intp)
        left, top = stats[:, cv2.CC_STAT_LEFT], stats[:, cv2.CC_STAT_TOP]
        touches = {
            SheetEdge.TOP: top == 0,
            SheetEdge.LEFT: left == 0,
            SheetEdge.BOTTOM: top + stats[:, cv2.CC_STAT_HEIGHT] == height,
            SheetEdge.RIGHT: left + stats[:, cv2.CC_STAT_WIDTH] == width,
        }
        remove = stats[:, cv2.CC_STAT_AREA] < self._params.noise_min_area
        for edge, touching in touches.items():
            if edge not in self._cut_edges:
                remove |= touching
        remove[0] = False  # Label 0 is the background
        cleaned = self._ink.copy()
        cleaned[remove[labels]] = 0
        return cleaned


class Crop(ModelProcessor):
    """Cuts a page to the frame of its content and a margin of paper."""

    params_model = CropParams
    spec = ProcessorSpec(
        key='geometry.crop',
        version='1',
        title='Select content',
        summary='Finds the content box and drops the rest',
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=CropParams.model_json_schema(),
        editor=Rect.editor,
        measure=StepMeasure.FRAME_SIZE,
        after=(
            OrderRule(
                processor_key='geometry.perspective',
                reason=(
                    'Select content reads the sides of the sheet that Perspective records, '
                    'so it usually comes after Perspective.'
                ),
            ),
            OrderRule(
                processor_key='geometry.deskew',
                reason=(
                    'Select content finds the frame of the text on a page that is turned level, '
                    'so it usually comes after Deskew.'
                ),
            ),
            OrderRule(
                processor_key='geometry.dewarp',
                reason='Select content finds the frame of the text on a flat page, so it usually comes after Dewarp.',
            ),
        ),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Find the frame of the content and cut the page to it.

        :param step_input: The image of the page, the parameters, and the rect edit if there is one.
        :type step_input: StepInput
        :returns: One output holding the cropped page, or the page as it was when it holds no content.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = CropParams.model_validate(step_input.params)
        image = read_samples(step_input.image)
        color_mode = color_mode_of(image, step_input.input_data)
        stated = step_input.input_data.get(VersionData.CUT_EDGES)
        cut_edges = frozenset(SheetEdge(edge) for edge in stated) if isinstance(stated, list) else frozenset()
        search = FrameSearch(image, params, cut_edges)
        frame = self._frame(step_input, search)
        if frame is None:
            return self._unchanged(step_input, image, color_mode)
        bounds = self._bounds(frame.rect, params.margin_percent)
        cropped = self._cut(image, bounds, search.paper_colour())
        target = step_input.workdir / CROPPED_IMAGE_NAME
        write_png(cropped, target)
        data = image_data(cropped, step_input.input_data, color_mode) | source_size_data(image, step_input.scale)
        data |= {
            VersionData.FRAME: frame.rect.scaled(1 / step_input.scale).to_data(),
            VersionData.CONFIDENCE: frame.confidence,
            VersionData.SKIPPED: False,
        }
        block = image[
            max(0, math.floor(frame.rect.top)) : math.ceil(frame.rect.top + frame.rect.height),
            max(0, math.floor(frame.rect.left)) : math.ceil(frame.rect.left + frame.rect.width),
        ]
        if (pitch := line_pitch(block)) is not None:
            data[VersionData.LINE_HEIGHT_PX] = pitch / step_input.scale
        own = ReviewReason.CUT_BY_EDGE if self._reaches_cut_edge(frame.rect, image, cut_edges) else None
        review = settle_review(data, own, step_input.input_data)
        return StepResult(
            outputs=[
                StepOutput(
                    image=target, color_mode=color_mode, transform=self._transform(bounds), data=data, review=review
                )
            ]
        )

    @staticmethod
    def _frame(step_input: StepInput, search: FrameSearch) -> Frame | None:
        """Take the frame the user gave, or else search it.

        :param step_input: The rect edit of the page if there is one, and the scale of the image.
        :type step_input: StepInput
        :param search: The search of the frame on the page.
        :type search: FrameSearch
        :returns: The frame in the pixels of the image, or None when the page holds no content.
        :rtype: Frame | None
        """
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, Rect):
            return Frame(rect=edit.geometry.scaled(step_input.scale), confidence=MANUAL_CONFIDENCE)
        return search.find()

    @staticmethod
    def _unchanged(step_input: StepInput, image: Samples, color_mode: ColorMode) -> StepResult:
        """Give the result of a page with no content: the page as it was, marked for review.

        :param step_input: The image of the page and the data of its input version.
        :type step_input: StepInput
        :param image: The samples of the page.
        :type image: Samples
        :param color_mode: Colour mode of the page.
        :type color_mode: ColorMode
        :returns: One output holding the image of the input.
        :rtype: StepResult
        """
        data = image_data(image, step_input.input_data, color_mode) | source_size_data(image, step_input.scale)
        data |= {VersionData.CONFIDENCE: 0.0, VersionData.SKIPPED: True}
        review = settle_review(data, ReviewReason.NOT_APPLIED, step_input.input_data)
        return StepResult(outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data, review=review)])

    @staticmethod
    def _bounds(rect: Rect, margin_percent: float) -> tuple[int, int, int, int]:
        """Add the margin to the frame and round the area outwards to whole pixels.

        :param rect: The frame of the content.
        :type rect: Rect
        :param margin_percent: Margin on each side as a percent of the width of the frame.
        :type margin_percent: float
        :returns: Left, top, right and bottom of the area to cut, which may lie beyond the page.
        :rtype: tuple[int, int, int, int]
        """
        margin = margin_percent / PERCENT * rect.width
        return (
            math.floor(rect.left - margin),
            math.floor(rect.top - margin),
            math.ceil(rect.left + rect.width + margin),
            math.ceil(rect.top + rect.height + margin),
        )

    @staticmethod
    def _transform(bounds: tuple[int, int, int, int]) -> Transform:
        """Describe the cut as a transform: a translation by the corner of the area.

        :param bounds: Left, top, right and bottom of the area that was cut.
        :type bounds: tuple[int, int, int, int]
        :returns: The crop transform with its quadrilateral and its matrix.
        :rtype: Transform
        """
        left, top, right, bottom = bounds
        area = Quad.from_points(
            [Point(x=x, y=y) for x, y in ((left, top), (right, top), (right, bottom), (left, bottom))]
        )
        matrix = (1.0, 0.0, -float(left), 0.0, 1.0, -float(top), 0.0, 0.0, 1.0)
        return Transform(kind=TransformKind.CROP, quad=area, matrix=matrix)

    @staticmethod
    def _reaches_cut_edge(rect: Rect, image: Samples, cut_edges: frozenset[SheetEdge]) -> bool:
        """Tell whether the frame comes to a side of the sheet where the scanner cut the paper.

        :param rect: The frame in the pixels of the page.
        :type rect: Rect
        :param image: The samples of the page.
        :type image: Samples
        :param cut_edges: Sides of the sheet that lie on the edge of the scan.
        :type cut_edges: frozenset[SheetEdge]
        :returns: Whether the margin of the page on a side that was cut is not known.
        :rtype: bool
        """
        height, width = image.shape[:2]
        slack_x = EDGE_TOLERANCE_SHARE * width
        slack_y = EDGE_TOLERANCE_SHARE * height
        reaches = {
            SheetEdge.TOP: rect.top <= slack_y,
            SheetEdge.RIGHT: rect.left + rect.width >= width - slack_x,
            SheetEdge.BOTTOM: rect.top + rect.height >= height - slack_y,
            SheetEdge.LEFT: rect.left <= slack_x,
        }
        return any(reaches[edge] for edge in cut_edges)

    @staticmethod
    def _cut(image: Samples, bounds: tuple[int, int, int, int], paper: Samples) -> Samples:
        """Cut an area out of the page, filling what lies beyond the page with the colour of the paper.

        :param image: The samples of the page.
        :type image: Samples
        :param bounds: Left, top, right and bottom of the area in the pixels of the page, which may lie beyond it.
        :type bounds: tuple[int, int, int, int]
        :param paper: The colour of the paper.
        :type paper: Samples
        :returns: The samples of the area.
        :rtype: Samples
        """
        left, top, right, bottom = bounds
        height, width = image.shape[:2]
        cropped = np.empty((bottom - top, right - left, *image.shape[2:]), dtype=np.uint8)
        cropped[:] = paper
        inside = (max(left, 0), max(top, 0), min(right, width), min(bottom, height))
        if inside[0] < inside[2] and inside[1] < inside[3]:
            cropped[inside[1] - top : inside[3] - top, inside[0] - left : inside[2] - left] = image[
                inside[1] : inside[3], inside[0] : inside[2]
            ]
        return cropped
