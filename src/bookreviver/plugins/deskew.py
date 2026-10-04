"""Straightening a page that was scanned at a slant, by one of three searches of the angle.

``geometry.deskew`` finds the angle the lines of text are turned by and turns the page back by it. The method says how
the angle is found, and the parameters of each method are its own.

The method ``projection`` shrinks the page and makes it black and white, then turns it through a set of angles, and for
each sums the ink along the rows. Lines of text turned level pile their ink into few rows, so the angle with the most
uneven sums is the one that levels them. A coarse set of angles over the whole range finds the neighbourhood of it, and
a fine set around that finds the angle. Its confidence is how far the best angle stands out of the rest of the coarse
set, from 0 for a page whose rows sum the same at every angle, such as a blank one, to nearly 1 for a page of clear
lines.

The method ``hough`` takes the long straight lines of the page, such as the rules and the frames of tables and
plates, which the Hough transform finds in the ink, and the angle is the median of their slopes weighted by
their length, folded so that a vertical line counts as a horizontal one. Its confidence is the share of the length of
the lines that lie within a third of a degree of that angle, times how much of the width of the page the lines cover.

The method ``baselines`` takes the lines of text as ``TextLineSearch`` finds them and the slope of each, and the angle
is the median of the slopes. The middle of the ink of a line is followed, which has the slope of its baseline. Its
confidence is the share of the lines within a quarter of a degree of the median, times how many lines there are against
the least the parameter ``min_lines`` asks.

A page whose tones do not part into ink and paper, such as the grain of a cover or of a blank leaf, has no lines to
follow and the confidence 0. A page whose confidence is below the parameter ``min_confidence`` is left as it is. The
version says it was skipped and is marked for review, so a picture or a blank leaf is not turned by a guess and is easy
to find. An angle the user gave as a rotation edit replaces the search and has the confidence 1.

The page keeps its size. What the turn leaves uncovered at the corners is white, and a bilevel page stays bilevel. The
transform is the rotation matrix OpenCV turned the page by, which maps a point of the input to the output, so the chain
of transforms gives the place of any point of the result in the scan.
"""

from typing import TYPE_CHECKING, Annotated, Literal, override

import cv2
import numpy as np
from pydantic import ConfigDict, Field, RootModel, Tag

from bookreviver.domain.enums import (
    ColorMode,
    DeskewMethod,
    ProcessorScope,
    ReviewReason,
    Stage,
    TransformKind,
    VersionData,
    VersionOutput,
)
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Rotation, Transform
from bookreviver.domain.values import OrderRule, ProcessorSpec
from bookreviver.plugins.base import METHOD_TITLE, ModelProcessor, Params, method_discriminator
from bookreviver.plugins.cv_image import (
    COLOR_PLANES,
    MANUAL_CONFIDENCE,
    MIN_TONE_CONTRAST,
    NO_IMAGE,
    WHITE,
    OtsuSplit,
    color_mode_of,
    image_data,
    read_samples,
    settle_review,
    to_bilevel,
    write_png,
)
from bookreviver.plugins.text_lines import TextLineSearch
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Floats, Samples
    from bookreviver.ports.processing import StepInput

DESKEWED_IMAGE_NAME: str = 'deskewed.png'
# The longer side in pixels the image is shrunk to for the search of the angle, since the ink of a page shows at it
SEARCH_LONG_SIDE_PX: int = 1_000
# How many angles each of the two sets of the search has
ANGLES_PER_SET: int = 21
# The angles of the Hough transform, in steps of a tenth of a degree, and the sizes the segments it takes are
# found by: the share of the longer side of the page a line has to be long enough to vote for, and the gap in pixels of
# the shrunk page a segment may bridge
HOUGH_ANGLE_STEPS_PER_DEGREE: int = 10
HOUGH_VOTE_SHARE: float = 0.15
HOUGH_GAP_PX: int = 3
# How far a line may be from the angle that is found, in degrees, to agree with it, for each of the two methods, and how
# far from the median the lines are that the angle of the Hough transform is averaged over
HOUGH_AGREEMENT_DEG: float = 0.33
HOUGH_REFINE_DEG: float = 1.0
BASELINE_AGREEMENT_DEG: float = 0.25
# A quarter of a turn folds a vertical line to a level one, so an angle is folded to a range of this many degrees, which
# reaches half of it to either side
QUARTER_TURN_DEG: float = 90.0
FOLD_HALF_DEG: float = QUARTER_TURN_DEG / 2
# How many times the least number of lines gives the full confidence of the baselines
FULL_LINES_FACTOR: float = 2.0


class AngleParams(Params):
    """How far and how surely the page is turned, whichever way the angle is found.

    :ivar max_angle: Largest angle in degrees the search looks for, to either side.
    :ivar min_confidence: Confidence below which the page is left as it is.
    """

    max_angle: float = Field(
        default=5.0,
        gt=0,
        le=45,
        title='Largest slant',
        description='Largest angle in degrees to look for, each way',
    )
    min_confidence: float = Field(
        default=0.3,
        ge=0,
        le=1,
        title='Least confidence',
        description='Confidence below which the page is left as it is',
    )


class ProjectionParams(AngleParams):
    """The angle that piles the ink of the page into the fewest rows.

    :ivar method: The projection of the ink.
    """

    model_config = ConfigDict(title='Projection of the ink')

    method: Literal[DeskewMethod.PROJECTION] = Field(
        default=DeskewMethod.PROJECTION,
        title=METHOD_TITLE,
        description='The angle that piles the ink of the lines into the fewest rows',
    )


class HoughParams(AngleParams):
    """The angle of the long straight lines of the page.

    :ivar method: The long straight lines.
    :ivar min_line_share: Shortest line that counts, as a share of the width of the page.
    """

    model_config = ConfigDict(title='Long straight lines')

    method: Literal[DeskewMethod.HOUGH] = Field(
        default=DeskewMethod.HOUGH,
        title=METHOD_TITLE,
        description='The slope of the long straight lines, such as the rules and the frames of tables',
    )
    min_line_share: float = Field(
        default=0.3,
        gt=0,
        le=1,
        title='Shortest line',
        description='Shortest line that counts, as a share of the width of the page',
    )


class BaselinesParams(AngleParams):
    """The median slope of the lines of text.

    :ivar method: The baselines of the text.
    :ivar min_lines: Fewest lines of text the angle is taken from.
    """

    model_config = ConfigDict(title='Baselines of the text')

    method: Literal[DeskewMethod.BASELINES] = Field(
        default=DeskewMethod.BASELINES,
        title=METHOD_TITLE,
        description='The median slope of the lines of text',
    )
    min_lines: int = Field(
        default=3,
        ge=1,
        le=100,
        title='Fewest lines',
        description='Fewest lines of text the angle is taken from, or the page is left as it is',
    )


DeskewChoice = Annotated[
    Annotated[ProjectionParams, Tag(DeskewMethod.PROJECTION)]
    | Annotated[HoughParams, Tag(DeskewMethod.HOUGH)]
    | Annotated[BaselinesParams, Tag(DeskewMethod.BASELINES)],
    method_discriminator(DeskewMethod.PROJECTION),
]


class DeskewParams(RootModel[DeskewChoice]):
    """The method of the search and its parameters, the projection of the ink when none is named."""


class Deskew(ModelProcessor):
    """Turns a page level by the angle of its lines of text."""

    params_model = DeskewParams
    spec = ProcessorSpec(
        key='geometry.deskew',
        version='1',
        title='Deskew',
        summary='Turns the page so its lines of text run level',
        stage=Stage.GEOMETRY,
        scope=ProcessorScope.PAGE,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=DeskewParams.model_json_schema(),
        editor=Rotation.editor,
        after=(
            OrderRule(
                processor_key='geometry.perspective',
                reason=(
                    'Deskew reads the slant of the lines on an upright sheet, so it usually comes after Perspective.'
                ),
            ),
        ),
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
        params = DeskewParams.model_validate(step_input.params).root
        image = read_samples(step_input.image)
        color_mode = color_mode_of(image, step_input.input_data)
        data = image_data(image, step_input.input_data, color_mode)
        angle, confidence = self._angle(image, params, step_input)
        if confidence < params.min_confidence:
            data |= {VersionData.ANGLE: 0.0, VersionData.CONFIDENCE: confidence, VersionData.SKIPPED: True}
            review = settle_review(data, ReviewReason.NOT_APPLIED, step_input.input_data)
            return StepResult(
                outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data, review=review)]
            )
        turned, matrix = self._turn(image, angle, color_mode)
        target = step_input.workdir / DESKEWED_IMAGE_NAME
        write_png(turned, target)
        transform = Transform(kind=TransformKind.ROTATE, angle=angle, matrix=matrix)
        data |= {VersionData.ANGLE: angle, VersionData.CONFIDENCE: confidence, VersionData.SKIPPED: False}
        review = settle_review(data, None, step_input.input_data)
        return StepResult(
            outputs=[StepOutput(image=target, color_mode=color_mode, transform=transform, data=data, review=review)]
        )

    @staticmethod
    def _angle(
        image: Samples, params: ProjectionParams | HoughParams | BaselinesParams, step_input: StepInput
    ) -> tuple[float, float]:
        """Choose the angle to turn by: the one the user gave, or the one the method of the step finds.

        :param image: The samples of the page.
        :type image: Samples
        :param params: The parameters of the step, which name its method.
        :type params: ProjectionParams | HoughParams | BaselinesParams
        :param step_input: The input of the step, which may hold a rotation edit.
        :type step_input: StepInput
        :returns: The angle in degrees, counter-clockwise, and its confidence.
        :rtype: tuple[float, float]
        """
        edit = step_input.edit
        if edit is not None and isinstance(edit.geometry, Rotation):
            return edit.geometry.degrees, MANUAL_CONFIDENCE
        match params:
            case HoughParams():
                return Deskew._hough(image, params)
            case BaselinesParams():
                return Deskew._baselines(image, params)
            case _:
                return Deskew._search(image, params.max_angle)

    @staticmethod
    def _hough(image: Samples, params: HoughParams) -> tuple[float, float]:
        """Find the angle by the long straight lines of the page.

        :param image: The samples of the page.
        :type image: Samples
        :param params: The parameters of the method.
        :type params: HoughParams
        :returns: The angle in degrees, counter-clockwise, and the confidence from 0 to 1.
        :rtype: tuple[float, float]
        """
        found = Deskew._long_lines(image, params)
        if found is None:
            return 0.0, 0.0
        angles, lengths, width = found
        order = np.argsort(angles)
        reach = np.cumsum(lengths[order])
        median = float(angles[order][np.searchsorted(reach, reach[-1] / 2)])
        # The ends of a segment fall on whole pixels, so one slope is only so exact, and the mean of the many near the
        # median is nearer the truth than any of them
        near = np.abs(angles - median) <= HOUGH_REFINE_DEG
        angle = float(np.average(angles[near], weights=lengths[near]))
        agreeing = float(lengths[np.abs(angles - angle) <= HOUGH_AGREEMENT_DEG].sum())
        coverage = min(1.0, float(lengths.sum()) / width)
        return angle, agreeing / float(lengths.sum()) * coverage

    @staticmethod
    def _long_lines(image: Samples, params: HoughParams) -> tuple[Floats, Floats, int] | None:
        """Find the long straight segments of a page with the Hough transform.

        :param image: The samples of the page.
        :type image: Samples
        :param params: The parameters of the method, of which the range of the angle and the shortest line are read.
        :type params: HoughParams
        :returns: The angle of each segment within the range, folded so that a vertical one counts as a level one, its
                  length, and the width of the shrunk page they are measured on; None when the page has no ink to part
                  from its paper or no segment within the range.
        :rtype: tuple[Floats, Floats, int] | None
        """
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == COLOR_PLANES else image
        shrink = min(1.0, SEARCH_LONG_SIDE_PX / max(gray.shape))
        small = np.asarray(cv2.resize(gray, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA), dtype=np.uint8)
        split = OtsuSplit.of(small)
        if split.contrast < MIN_TONE_CONTRAST:
            return None
        # The ink itself is taken and not its edges, since a thin rule that is a little slanted has edges that step from
        # one row to the next and fall in different bins of the transform, while its ink falls in one
        segments = cv2.HoughLinesP(
            np.asarray(small <= split.threshold, dtype=np.uint8) * WHITE,
            rho=1,
            theta=np.pi / 180 / HOUGH_ANGLE_STEPS_PER_DEGREE,
            threshold=round(HOUGH_VOTE_SHARE * max(small.shape)),
            minLineLength=params.min_line_share * small.shape[1],
            maxLineGap=HOUGH_GAP_PX,
        )
        if segments is None:
            return None
        steps = np.diff(segments.reshape(-1, 2, 2).astype(np.float64), axis=1)[:, 0]
        # A line is as far from level as it is from upright, whichever is nearer, and the angle is folded to that
        angles = (np.degrees(np.arctan2(steps[:, 1], steps[:, 0])) + FOLD_HALF_DEG) % QUARTER_TURN_DEG - FOLD_HALF_DEG
        within = np.abs(angles) <= params.max_angle
        if not within.any():
            return None
        return angles[within], np.hypot(steps[within, 0], steps[within, 1]), small.shape[1]

    @staticmethod
    def _baselines(image: Samples, params: BaselinesParams) -> tuple[float, float]:
        """Find the angle by the median slope of the lines of text.

        :param image: The samples of the page.
        :type image: Samples
        :param params: The parameters of the method.
        :type params: BaselinesParams
        :returns: The angle in degrees, counter-clockwise, and the confidence from 0 to 1.
        :rtype: tuple[float, float]
        """
        width = image.shape[1]
        lines = TextLineSearch(image).find()
        if len(lines) < params.min_lines:
            return 0.0, 0.0
        slopes = np.array([np.degrees(np.arctan(line.fit(width, 1)[0][0] / width)) for line in lines])
        angle = float(np.median(slopes))
        if abs(angle) > params.max_angle:
            return 0.0, 0.0
        agreeing = float((np.abs(slopes - angle) <= BASELINE_AGREEMENT_DEG).mean())
        enough = min(1.0, len(lines) / (FULL_LINES_FACTOR * params.min_lines))
        return angle, agreeing * enough

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
        small = np.asarray(cv2.resize(gray, None, fx=shrink, fy=shrink, interpolation=cv2.INTER_AREA), dtype=np.uint8)
        if OtsuSplit.of(small).contrast < MIN_TONE_CONTRAST:
            # The page has no ink to part from its paper, only grain, which has no lines to follow
            return 0.0, 0.0
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
