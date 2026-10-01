"""Straightening a page that was scanned at a slant, with a search of the angle by the projection of its ink.

``geometry.deskew`` finds the angle the lines of text are turned by and turns the page back by it. The page is shrunk
and made black and white, then turned through a set of angles, and for each the ink is summed along the rows. Lines of
text turned level pile their ink into few rows, so the angle with the most uneven sums is the one that levels them. A
coarse set of angles over the whole range finds the neighbourhood of it, and a fine set around that finds the angle.

The confidence is how far the best angle stands out of the rest of the coarse set, from 0 for a page whose rows sum the
same at every angle, such as a blank one, to nearly 1 for a page of clear lines. A page whose confidence is below the
parameter ``min_confidence`` is left as it is, and the version says it was skipped, so a picture or a blank leaf is not
turned by a guess. An angle the user gave as a rotation edit replaces the search and has the confidence 1.

The page keeps its size. What the turn leaves uncovered at the corners is white, and a bilevel page stays bilevel. The
transform is the rotation matrix OpenCV turned the page by, which maps a point of the input to the output, so the chain
of transforms gives the place of any point of the result in the scan.
"""

from typing import TYPE_CHECKING, override

import cv2
import numpy as np
from pydantic import Field

from bookreviver.domain.enums import ColorMode, ProcessorScope, Stage, TransformKind, VersionData, VersionOutput
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Rotation, Transform
from bookreviver.domain.values import ProcessorSpec
from bookreviver.plugins.base import ModelProcessor, Params
from bookreviver.plugins.cv_image import (
    COLOR_PLANES,
    NO_IMAGE,
    WHITE,
    color_mode_of,
    image_data,
    read_samples,
    to_bilevel,
    write_png,
)
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Samples
    from bookreviver.ports.processing import StepInput

DESKEWED_IMAGE_NAME: str = 'deskewed.png'
# The longer side in pixels the image is shrunk to for the search of the angle, since the ink of a page shows at it
SEARCH_LONG_SIDE_PX: int = 1_000
# How many angles each of the two sets of the search has
ANGLES_PER_SET: int = 21
# The confidence of an angle the user gave
MANUAL_CONFIDENCE: float = 1.0


class DeskewParams(Params):
    """How far and how surely the page is turned.

    :ivar max_angle: Largest angle in degrees the search looks for, to either side.
    :ivar min_confidence: Confidence below which the page is left as it is.
    """

    max_angle: float = Field(default=5.0, gt=0, le=45, description='Largest angle in degrees to look for, each way')
    min_confidence: float = Field(
        default=0.3, ge=0, le=1, description='Confidence below which the page is left as it is'
    )


class Deskew(ModelProcessor):
    """Turns a page level by the angle of its lines of text."""

    params_model = DeskewParams
    spec = ProcessorSpec(
        key='geometry.deskew',
        version='1',
        title='Deskew',
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=DeskewParams.model_json_schema(),
        editor=Rotation.editor,
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Find the angle of the page and turn it back by it.

        :param step_input: The image of the page, the parameters, and the rotation edit if there is one.
        :type step_input: StepInput
        :returns: One output holding the straightened page, or the page as it was when it is skipped.
        :rtype: StepResult
        :raises ConflictError: If there is no image, or it cannot be read.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = DeskewParams.model_validate(step_input.params)
        image = read_samples(step_input.image)
        color_mode = color_mode_of(image, step_input.input_data)
        data = image_data(image, step_input.input_data, color_mode)
        angle, confidence = self._angle(image, params, step_input)
        if confidence < params.min_confidence:
            data |= {VersionData.ANGLE: 0.0, VersionData.CONFIDENCE: confidence, VersionData.SKIPPED: True}
            return StepResult(outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data)])
        turned, matrix = self._turn(image, angle, color_mode)
        target = step_input.workdir / DESKEWED_IMAGE_NAME
        write_png(turned, target)
        transform = Transform(kind=TransformKind.ROTATE, angle=angle, matrix=matrix)
        data |= {VersionData.ANGLE: angle, VersionData.CONFIDENCE: confidence, VersionData.SKIPPED: False}
        return StepResult(outputs=[StepOutput(image=target, color_mode=color_mode, transform=transform, data=data)])

    @staticmethod
    def _angle(image: Samples, params: DeskewParams, step_input: StepInput) -> tuple[float, float]:
        """Choose the angle to turn by: the one the user gave, or the one the search finds.

        :param image: The samples of the page.
        :type image: Samples
        :param params: The parameters of the step.
        :type params: DeskewParams
        :param step_input: The input of the step, which may hold a rotation edit.
        :type step_input: StepInput
        :returns: The angle in degrees, counter-clockwise, and its confidence.
        :rtype: tuple[float, float]
        """
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, Rotation):
            return edit.geometry.degrees, MANUAL_CONFIDENCE
        return Deskew._search(image, params.max_angle)

    @staticmethod
    def _search(image: Samples, max_angle: float) -> tuple[float, float]:
        """Search the angle by the projection of the ink on the rows.

        :param image: The samples of the page.
        :type image: Samples
        :param max_angle: Largest angle to look for in degrees, each way.
        :type max_angle: float
        :returns: The angle in degrees, counter-clockwise, and how far it stands out of the others, from 0 to 1.
        :rtype: tuple[float, float]
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == COLOR_PLANES else image
        height, width = gray.shape
        shrink = min(1.0, SEARCH_LONG_SIDE_PX / max(height, width))
        small = cv2.resize(gray, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA)
        _, thresholded = cv2.threshold(small, 0, WHITE, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        ink = np.asarray(thresholded, dtype=np.uint8)
        coarse = np.linspace(-max_angle, max_angle, ANGLES_PER_SET)
        coarse_scores = np.array([Deskew._unevenness(ink, angle) for angle in coarse])
        best = float(coarse[int(coarse_scores.argmax())])
        spacing = 2 * max_angle / (ANGLES_PER_SET - 1)
        fine = np.linspace(best - spacing, best + spacing, ANGLES_PER_SET)
        fine_scores = np.array([Deskew._unevenness(ink, angle) for angle in fine])
        top = float(fine_scores.max())
        if top <= 0:
            return 0.0, 0.0
        return float(fine[int(fine_scores.argmax())]), 1 - float(coarse_scores.mean()) / top

    @staticmethod
    def _unevenness(ink: Samples, angle: float) -> float:
        """Measure how unevenly the ink of a page lies on the rows once it is turned.

        :param ink: The page as white ink on black.
        :type ink: Samples
        :param angle: Angle to turn by in degrees, counter-clockwise.
        :type angle: float
        :returns: The variance of the row sums, which is greatest when the lines of text are level.
        :rtype: float
        """
        height, width = ink.shape
        matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        turned = np.asarray(cv2.warpAffine(ink, matrix, (width, height), flags=cv2.INTER_LINEAR), dtype=np.uint8)
        return float(turned.sum(axis=1, dtype=np.float64).var())

    @staticmethod
    def _turn(image: Samples, angle: float, color_mode: ColorMode) -> tuple[Samples, tuple[float, ...]]:
        """Turn the page about its centre, filling what the turn uncovers with white.

        :param image: The samples of the page.
        :type image: Samples
        :param angle: Angle to turn by in degrees, counter-clockwise.
        :type angle: float
        :param color_mode: Colour mode of the page, a bilevel page being made bilevel again after the turn.
        :type color_mode: ColorMode
        :returns: The turned samples and the matrix, nine numbers in rows, that maps a point of the page to the result.
        :rtype: tuple[Samples, tuple[float, ...]]
        """
        height, width = image.shape[:2]
        affine = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
        turned = np.asarray(
            cv2.warpAffine(
                image,
                affine,
                (width, height),
                flags=cv2.INTER_CUBIC,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(WHITE, WHITE, WHITE),
            ),
            dtype=np.uint8,
        )
        if color_mode is ColorMode.BILEVEL:
            turned = to_bilevel(turned)
        return turned, (*(float(value) for value in affine.ravel()), 0.0, 0.0, 1.0)
