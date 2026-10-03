"""Removing the specks of dust from a page that is black and white, without taking the dots of its letters.

``cleanup.despeckle`` reads the ink of a page, which is every pixel that is black, joins it into components of touching
pixels and removes the small ones. What is small is not a number of pixels but a share of the height of a line of the
page, so the same strength does the same at any resolution of the scan. The height of a line is the upper quartile of
the heights of the components that are as tall as a letter or a mark, which is the band of heights between 0.5 % and 6 %
of the page, so the dots, which are many beside the stems of some pages, do not pull it down.

``strength`` is 1 to 3, as in ScanTailor. A component is a speck when its area is at most the square of a share of the
line height, a twentieth of it for strength 1, a eleventh for 2 and a seventh for 3. The dots of ``і`` and ``ї``,
the two dots of ``ё``, an accent, a quotation mark and a full stop are as small as dust, and a strong setting would take
them. With ``protect_diacritics`` a speck is kept when a letter lies within half a line height of it, above, below or
beside it, which is where every one of those marks stands. A speck farther from any letter than that, in a margin or a
wide gap, is removed.

Gray areas are left alone: only pure black is ink, so the tones of a picture on a mixed page are never taken for dust.
The pixels that were removed are painted white, and written to ``mask.png`` as white on black, so the interface can show
what the step did.
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
    from numpy.typing import NDArray

    from bookreviver.ports.processing import StepInput

MASK_IMAGE_NAME: str = 'mask.png'
DESPECKLED_IMAGE_NAME: str = 'despeckled.png'
# The strengths the step has, and the area of a speck of each as a share of the square of the height of a line
MIN_STRENGTH: int = 1
MAX_STRENGTH: int = 3
SPECK_AREA_SHARES: tuple[float, ...] = (0.0025, 0.008, 0.02)
# The heights a component has when it is a letter, as shares of the height of the page, and the line height of a page
# that has no such component
LETTER_MIN_SHARE: float = 0.005
LETTER_MAX_SHARE: float = 0.06
FALLBACK_LINE_SHARE: float = 0.015
UPPER_QUARTILE: float = 75.0
# How far a letter protects what is near it, as a share of the height of a line
PROTECT_REACH_SHARE: float = 0.5


class DespeckleParams(Params):
    """How much is taken for dust, and whether the marks that belong to the letters are kept.

    :ivar strength: 1 removes the smallest specks, 3 the largest.
    :ivar protect_diacritics: Whether a speck near a letter is kept.
    """

    strength: int = Field(
        default=2,
        ge=MIN_STRENGTH,
        le=MAX_STRENGTH,
        title='Strength',
        description='How large a speck may be to be removed: 1 is careful, 3 is aggressive',
    )
    protect_diacritics: bool = Field(
        default=True,
        title='Protect dots and accents',
        description='Keep a speck that lies within half a line of a letter, such as the dot over i or an accent',
    )


class SpeckFinder:
    """Finds the specks of a page that is black on white."""

    def __init__(self, ink: NDArray[np.bool_], params: DespeckleParams) -> None:
        """Join the ink into components and work out the line height of the page.

        :param ink: True where the pixel is black.
        :type ink: NDArray[np.bool_]
        :param params: The parameters of the step.
        :type params: DespeckleParams
        """
        self._params = params
        self._count, labels, stats, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
        self._labels = np.asarray(labels, dtype=np.intp)
        self._areas = stats[:, cv2.CC_STAT_AREA]
        heights = stats[:, cv2.CC_STAT_HEIGHT]
        page_height = ink.shape[0]
        letters = heights[(heights >= LETTER_MIN_SHARE * page_height) & (heights <= LETTER_MAX_SHARE * page_height)]
        self.line_height = (
            float(np.percentile(letters, UPPER_QUARTILE)) if letters.size else FALLBACK_LINE_SHARE * page_height
        )

    def find(self) -> tuple[NDArray[np.bool_], int]:
        """Find the specks to remove.

        :returns: True for each pixel of a speck to remove, and the number of specks.
        :rtype: tuple[NDArray[np.bool_], int]
        """
        limit = SPECK_AREA_SHARES[self._params.strength - MIN_STRENGTH] * self.line_height**2
        specks = self._areas <= limit
        specks[0] = False  # Label 0 is the background
        if self._params.protect_diacritics and specks.any():
            letters = (~specks)[self._labels] & (self._labels > 0)
            reach = 2 * round(PROTECT_REACH_SHARE * self.line_height) + 1
            near = cv2.dilate(letters.astype(np.uint8), np.ones((reach, reach), dtype=np.uint8))
            protected = np.bincount(self._labels.ravel(), weights=near.ravel(), minlength=self._count) > 0
            specks &= ~protected
        return np.asarray(specks[self._labels], dtype=np.bool_), int(specks.sum())


class Despeckle(ModelProcessor):
    """Removes the specks of dust from a black and white page."""

    params_model = DespeckleParams
    spec = ProcessorSpec(
        key='cleanup.despeckle',
        version='1',
        title='Despeckle',
        stage=Stage.CLEANUP,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE, VersionOutput.MASK}),
        parameters=DespeckleParams.model_json_schema(),
        requires_after=(
            OrderRule(
                processor_key='cleanup.binarize',
                reason=(
                    'Despeckle removes the black specks of a black and white page, '
                    'so it cannot come before Binarization, which makes the page black and white.'
                ),
            ),
        ),
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Remove the specks and write the mask of what was removed.

        :param step_input: The image of the page and the parameters.
        :type step_input: StepInput
        :returns: One output holding the page and the mask, with the number of specks removed in its data.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = DespeckleParams.model_validate(step_input.params)
        image = read_samples(step_input.image)
        ink = np.all(image == BLACK, axis=2) if image.ndim == COLOR_PLANES else image == BLACK
        removed, count = SpeckFinder(ink, params).find()
        cleaned = image.copy()
        cleaned[removed] = WHITE
        target = step_input.workdir / DESPECKLED_IMAGE_NAME
        write_png(cleaned, target)
        mask = step_input.workdir / MASK_IMAGE_NAME
        write_png(np.where(removed, WHITE, BLACK).astype(np.uint8), mask)
        color_mode = color_mode_of(cleaned, step_input.input_data)
        data = image_data(cleaned, step_input.input_data, color_mode)
        data[VersionData.SPECKS] = count
        if frame := content_frame_of(step_input.input_data):
            data[VersionData.CONTENT_FRAME] = frame.to_data()
        review = settle_review(data, None, step_input.input_data)
        return StepResult(
            outputs=[StepOutput(image=target, color_mode=color_mode, data=data, review=review, mask=mask)]
        )
