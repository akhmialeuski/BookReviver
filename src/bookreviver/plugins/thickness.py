"""Making the strokes of the text of a black and white page thinner or thicker.

``cleanup.thickness`` moves the edge of every stroke of ink by ``amount`` pixels: a positive amount grows the ink, which
mends the thin strokes of a faint print and joins the letters that binarization broke, and a negative amount takes the
edge away, which separates the letters that a heavy print or a low threshold ran together. An amount of 0 leaves the
page as it is. The change is a morphological dilation or erosion of the ink with a round kernel, so a stroke grows or
shrinks by the same number of pixels on every side.

The ink is every pixel that is pure black, as for ``cleanup.despeckle``, so the tones of a picture on a mixed page are
not taken for strokes. The pixels that the dilation reaches are painted black and the ones that the erosion takes away
are painted white.
"""

from typing import TYPE_CHECKING, override

import cv2
import numpy as np
from pydantic import Field

from bookreviver.domain.enums import ProcessorScope, Stage, VersionData, VersionOutput
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.values import OrderRule, ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.plugins.cv_image import (
    BLACK,
    COLOR_PLANES,
    NO_IMAGE,
    WHITE,
    color_mode_of,
    content_frame_of,
    image_data,
    read_samples,
    settle_review,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.ports.processing import StepInput

THICKENED_IMAGE_NAME: str = 'thickness.png'
# The most pixels an edge of a stroke moves by, either way
MAX_AMOUNT: int = 3


class ThicknessParams(Params):
    """How far the edge of every stroke moves.

    :ivar amount: Pixels the edge of a stroke moves by: positive makes the strokes thicker, negative thinner.
    """

    amount: int = Field(
        default=0,
        ge=-MAX_AMOUNT,
        le=MAX_AMOUNT,
        title='Amount',
        description='Pixels each edge of a stroke moves by: positive thickens the text, negative thins it, 0 keeps it',
    )


class Thickness(ModelProcessor):
    """Makes the strokes of the text of a black and white page thinner or thicker."""

    params_model = ThicknessParams
    spec = ProcessorSpec(
        key='cleanup.thickness',
        version='1',
        title='Thickness',
        summary='Makes the strokes of the text thinner or thicker',
        stage=Stage.CLEANUP,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=ThicknessParams.model_json_schema(),
        after=(
            OrderRule(
                processor_key='cleanup.despeckle',
                reason=(
                    'Thickness changes the strokes of the text, '
                    'and a Despeckle after it would measure the specks against strokes of another width, '
                    'so it usually comes after Despeckle.'
                ),
            ),
        ),
        requires_after=(
            OrderRule(
                processor_key='cleanup.binarize',
                reason=(
                    'Thickness moves the edge of the black ink of a black and white page, '
                    'so it cannot come before Binarization, which makes the page black and white.'
                ),
            ),
        ),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Move the edge of every stroke by the amount.

        :param step_input: The image of the page and the parameters.
        :type step_input: StepInput
        :returns: One output holding the page with the strokes changed, or the page as it was for an amount of 0.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = ThicknessParams.model_validate(step_input.params)
        image = read_samples(step_input.image)
        facts = step_input.input_data
        color_mode = color_mode_of(image, facts)
        data = image_data(image, facts, color_mode)
        if frame := content_frame_of(facts):
            data[VersionData.CONTENT_FRAME] = frame.to_data()
        if params.amount == 0:
            data[VersionData.SKIPPED] = True
            review = settle_review(data, None, facts)
            return StepResult(
                outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data, review=review)]
            )
        ink = np.all(image == BLACK, axis=2) if image.ndim == COLOR_PLANES else image == BLACK
        # A preview reads a smaller picture, so the reach is scaled to it, and is at least a pixel to show the change
        reach = max(1, round(abs(params.amount) * step_input.scale))
        size = 2 * reach + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size))
        changed = image.copy()
        if params.amount > 0:
            changed[cv2.dilate(ink.astype(np.uint8), kernel) > 0] = BLACK
        else:
            changed[ink & (cv2.erode(ink.astype(np.uint8), kernel) == 0)] = WHITE
        target = step_input.workdir / THICKENED_IMAGE_NAME
        write_png(changed, target)
        data[VersionData.SKIPPED] = False
        review = settle_review(data, None, facts)
        return StepResult(outputs=[StepOutput(image=target, color_mode=color_mode, data=data, review=review)])
