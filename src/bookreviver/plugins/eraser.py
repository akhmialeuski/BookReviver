"""Painting out what the user brushed over, and the margins outside the frame of the content.

``cleanup.eraser`` is the manual step of the Cleanup stage. The ``brush-mask`` editor gives it a mask, white where the
user brushed, and the step paints those pixels with the ``fill`` colour: white, black, or the mean colour of the pixels
round the area, which is what ScanKromsator calls the eraser of the three colours. A mask that is not the size of the
page is resized, since a preview reads a smaller page than the one the user brushed on. The data of the version give the
size of the full image the step read, which the editor paints the mask at.

``fill_outside_frame`` makes everything outside the frame of the content white, which is the filling of the margins of
ScanTailor. The frame is the one ``geometry.crop`` found, carried through the steps of the stage in ``content_frame``.

A page that has neither a mask nor a frame to fill outside of is left as it is, and marked for review with the reason
``not-applied``, so a page the step could do nothing for is easy to find.
"""

from typing import TYPE_CHECKING, Annotated, override

import cv2
import numpy as np
from pydantic import Field

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    EraserFill,
    ProcessorScope,
    ReviewReason,
    Stage,
    VersionData,
    VersionOutput,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.values import OrderRule, ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params, labelled
from bookreviver.plugins.cv_image import (
    BILEVEL_THRESHOLD,
    BLACK,
    NO_IMAGE,
    UNREADABLE_IMAGE,
    WHITE,
    color_mode_of,
    content_frame_of,
    image_data,
    read_samples,
    settle_review,
    source_size_data,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from numpy.typing import NDArray

    from bookreviver.domain.geometry import Rect
    from bookreviver.plugins.cv_image import Samples
    from bookreviver.ports.processing import StepInput

ERASED_IMAGE_NAME: str = 'erased.png'
# The ring of the pixels the mean colour is taken over, as a share of the longer side of the page, and its least width
AROUND_RING_SHARE: float = 0.004
AROUND_RING_MIN_PX: int = 3


class EraserParams(Params):
    """What the erased area is painted with, and whether the margins outside the frame are made white.

    :ivar fill: Colour of the area the user brushed over.
    :ivar fill_outside_frame: Whether everything outside the frame of the content is made white.
    """

    fill: Annotated[EraserFill, labelled(EraserFill)] = Field(
        default=EraserFill.WHITE, title='Fill colour', description='What the area that was brushed over is painted with'
    )
    fill_outside_frame: bool = Field(
        default=True,
        title='Whiten outside the frame',
        description='Make everything outside the frame of the content white',
    )


class Eraser(ModelProcessor):
    """Paints out the area the user brushed over, and the margins outside the frame of the content."""

    params_model = EraserParams
    spec = ProcessorSpec(
        key='cleanup.eraser',
        version='1',
        title='Fill zones',
        stage=Stage.CLEANUP,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=EraserParams.model_json_schema(),
        editor=EditorKind.BRUSH_MASK,
        after=(
            OrderRule(
                processor_key='cleanup.binarize',
                reason=(
                    'Fill zones paints on the page as the cleaning left it, '
                    'and a Binarization after it would work on the fill again, '
                    'so it usually comes after Binarization.'
                ),
            ),
            OrderRule(
                processor_key='cleanup.despeckle',
                reason=(
                    'Fill zones paints on the page as the cleaning left it, '
                    'and a Despeckle after it would take the specks of the fill for dust, '
                    'so it usually comes after Despeckle.'
                ),
            ),
        ),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Paint out the brushed area and the margins.

        :param step_input: The image of the page, the parameters, and the mask of the brush edit if there is one.
        :type step_input: StepInput
        :returns: One output holding the page, or the page as it was when there was nothing to paint out.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or the image or the mask cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = EraserParams.model_validate(step_input.params)
        image = read_samples(step_input.image)
        facts = step_input.input_data
        color_mode = color_mode_of(image, facts)
        brushed = self._brushed(step_input, image.shape[:2])
        frame = content_frame_of(facts)
        data = image_data(image, facts, color_mode) | source_size_data(image, step_input.scale)
        if frame is not None:
            data[VersionData.CONTENT_FRAME] = frame.to_data()
        margins = frame if params.fill_outside_frame else None
        if not brushed.any() and margins is None:
            data[VersionData.SKIPPED] = True
            review = settle_review(data, ReviewReason.NOT_APPLIED, facts)
            return StepResult(
                outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data, review=review)]
            )
        result = image.copy()
        if brushed.any():
            self._paint(result, brushed, params.fill, color_mode)
        if margins is not None:
            self._whiten_outside(result, margins.scaled(step_input.scale))
        target = step_input.workdir / ERASED_IMAGE_NAME
        write_png(result, target)
        data[VersionData.SKIPPED] = False
        review = settle_review(data, None, facts)
        return StepResult(outputs=[StepOutput(image=target, color_mode=color_mode, data=data, review=review)])

    @staticmethod
    def _brushed(step_input: StepInput, shape: tuple[int, ...]) -> NDArray[np.bool_]:
        """Read the mask of the brush edit in the size of the page.

        :param step_input: The step input, whose ``edit_mask`` is the file of the mask, or None.
        :type step_input: StepInput
        :param shape: Height and width of the page.
        :type shape: tuple[int, ...]
        :returns: True where the user brushed, nowhere when there is no mask.
        :rtype: NDArray[np.bool_]
        :raises ConflictError: If the mask file cannot be read.
        """
        if step_input.edit_mask is None:
            return np.zeros(shape, dtype=np.bool_)
        mask = cv2.imread(str(step_input.edit_mask), cv2.IMREAD_GRAYSCALE)
        if mask is None:
            raise ConflictError(UNREADABLE_IMAGE.format(path=step_input.edit_mask.name))
        if mask.shape != shape:
            mask = cv2.resize(mask, (shape[1], shape[0]), interpolation=cv2.INTER_NEAREST)
        return np.asarray(mask >= BILEVEL_THRESHOLD, dtype=np.bool_)

    @staticmethod
    def _paint(result: Samples, brushed: NDArray[np.bool_], fill: EraserFill, color_mode: ColorMode) -> None:
        """Paint the brushed pixels in place.

        :param result: The samples of the page, changed.
        :type result: Samples
        :param brushed: True where the user brushed.
        :type brushed: NDArray[np.bool_]
        :param fill: What to paint with.
        :type fill: EraserFill
        :param color_mode: Colour mode of the page, a black and white page being painted black or white only.
        :type color_mode: ColorMode
        """
        if fill is not EraserFill.AROUND:
            result[brushed] = WHITE if fill is EraserFill.WHITE else BLACK
            return
        ring = max(AROUND_RING_MIN_PX, round(AROUND_RING_SHARE * max(brushed.shape)))
        kernel = np.ones((2 * ring + 1, 2 * ring + 1), dtype=np.uint8)
        count, labels, stats, _ = cv2.connectedComponentsWithStats(brushed.astype(np.uint8), connectivity=8)
        for label in range(1, count):
            left, top = stats[label, cv2.CC_STAT_LEFT], stats[label, cv2.CC_STAT_TOP]
            right, bottom = left + stats[label, cv2.CC_STAT_WIDTH], top + stats[label, cv2.CC_STAT_HEIGHT]
            window = (slice(max(top - ring, 0), bottom + ring), slice(max(left - ring, 0), right + ring))
            area = labels[window] == label
            around = cv2.dilate(area.astype(np.uint8), kernel).astype(np.bool_) & ~brushed[window]
            colour = result[window][around].mean(axis=0) if around.any() else WHITE
            if color_mode is ColorMode.BILEVEL:
                colour = WHITE if np.mean(colour) >= BILEVEL_THRESHOLD else BLACK
            result[window][area] = colour

    @staticmethod
    def _whiten_outside(result: Samples, frame: Rect) -> None:
        """Make everything outside a frame white, in place.

        :param result: The samples of the page, changed.
        :type result: Samples
        :param frame: The frame in the pixels of the page.
        :type frame: Rect
        """
        height, width = result.shape[:2]
        inside = np.zeros((height, width), dtype=np.bool_)
        inside[
            max(0, round(frame.top)) : max(0, round(frame.top + frame.height)),
            max(0, round(frame.left)) : max(0, round(frame.left + frame.width)),
        ] = True
        result[~inside] = WHITE
