"""The base of the steps that change the black ink of a black and white page.

``cleanup.despeckle`` and ``cleanup.thickness`` both read a page whose ink is every pixel that is pure black, so the
tones of a picture on a mixed page are never taken for ink, change that ink, and hand the page on with the facts of its
input carried over: the size, the colour mode, the frame of the content and the reason an earlier step wants the page
looked at. This base does the reading and the handing on, and a step only says what it does to the ink.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, override

import numpy as np

from bookreviver.domain.enums import VersionData
from bookreviver.domain.errors import ConflictError
from bookreviver.plugins.base import ModelProcessor
from bookreviver.plugins.cv_image import (
    BLACK,
    COLOR_PLANES,
    NO_IMAGE,
    color_mode_of,
    content_frame_of,
    image_data,
    read_samples,
    settle_review,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.plugins.cv_image import Samples
    from bookreviver.ports.processing import StepInput


class InkProcessor(ModelProcessor, ABC):
    """A step that changes the black ink of a black and white page."""

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Read the page and let the step change its ink.

        :param step_input: The image of the page and the parameters.
        :type step_input: StepInput
        :returns: What the step made of the page.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        return self.process(step_input, read_samples(step_input.image))

    @abstractmethod
    def process(self, step_input: StepInput, image: Samples) -> StepResult:
        """Change the ink of the page.

        :param step_input: The image of the page and the parameters.
        :type step_input: StepInput
        :param image: The samples of the page.
        :type image: Samples
        :returns: What the step made of the page, as ``finish`` builds it.
        :rtype: StepResult
        """

    @staticmethod
    def ink_of(image: Samples) -> NDArray[np.bool_]:
        """Find the ink of a page, which is every pixel that is pure black.

        :param image: The samples of the page.
        :type image: Samples
        :returns: True where the pixel is black in every plane.
        :rtype: NDArray[np.bool_]
        """
        return np.all(image == BLACK, axis=2) if image.ndim == COLOR_PLANES else image == BLACK

    @staticmethod
    def finish(
        step_input: StepInput,
        *,
        shown: Path | None,
        samples: Samples,
        data: Mapping[str, object],
        mask: Path | None = None,
    ) -> StepResult:
        """Hand the page on with the facts of its input carried over.

        :param step_input: The input of the step, whose facts are carried over.
        :type step_input: StepInput
        :param shown: The file of the page the step made, or of its input when it left the page as it was.
        :type shown: Path | None
        :param samples: The samples that tell the colour mode and the size of the page.
        :type samples: Samples
        :param data: What the step records of its own, such as how many specks it removed.
        :type data: Mapping[str, object]
        :param mask: The file of the mask of what the step removed, or None.
        :type mask: Path | None
        :returns: One output holding the page.
        :rtype: StepResult
        """
        facts = step_input.input_data
        color_mode = color_mode_of(samples, facts)
        carried = image_data(samples, facts, color_mode)
        carried |= data
        if frame := content_frame_of(facts):
            carried[VersionData.CONTENT_FRAME] = frame.to_data()
        review = settle_review(carried, None, facts)
        return StepResult(
            outputs=[StepOutput(image=shown, color_mode=color_mode, data=carried, review=review, mask=mask)]
        )
