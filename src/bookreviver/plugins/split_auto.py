"""The automatic page split: the scan stays one page or is cut into two, whichever it is.

``split.auto`` tells a single page from a spread by the proportions of the scan. A scan whose width over its height is
at least ``min_spread_ratio`` is a spread, and is cut along its gutter as ``split.spread`` cuts it, with the gutter
searched in strips so that it may slant. Any other scan is one page, which is written as ``split.none`` writes it,
unchanged. A single page of a book is about 0.7 times as wide as it is tall and a spread about 1.4 times, so the
default 1.1 lies between them.

The gutter is searched in a narrow scan too. If a strong one is found, the scan stays one page all the same and is
marked for review with the reason that it is narrow and has a gutter in the middle, since it may be a spread
photographed from too close or cut off at the sides. A spread whose gutter is found with a confidence below
``min_confidence`` is cut along the best line and both halves are marked for review.

The decision of the user is the ``SplitChoice`` edit and outranks the proportions. One page keeps the scan whole. Two
pages cut it along the line the user drew, or along the gutter the search finds when no line was drawn. What the user
decided has the confidence 1, except for the position of a cut the user left to the search.

The data of a version records ``pages``, the place of the cut at the top and at the bottom row, and ``confidence``, so
the interface can show the decision and the reason it was marked.
"""

from typing import TYPE_CHECKING, override

from pydantic import Field

from bookreviver.domain.enums import ProcessorScope, ReviewReason, Stage, VersionData, VersionOutput
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import SplitChoice
from bookreviver.domain.values import ProcessorSpec
from bookreviver.plugins.cv_image import MANUAL_CONFIDENCE, NO_IMAGE, color_mode_of, image_data, read_samples
from bookreviver.plugins.gutter import GutterSearch
from bookreviver.plugins.split_spread import SplitSpread, SpreadParams
from bookreviver.ports.processing import StepOutput, StepResult

if TYPE_CHECKING:
    from bookreviver.plugins.cv_image import Samples
    from bookreviver.ports.processing import StepInput


class AutoParams(SpreadParams):
    """How a spread is told from a page, on top of how the gutter is searched.

    :ivar min_spread_ratio: Width over height from which a scan is a spread of two pages.
    """

    min_spread_ratio: float = Field(
        default=1.1,
        gt=0,
        title='Least spread proportion',
        description='Width over height from which a scan is a spread',
    )


class SplitAuto(SplitSpread):
    """Keeps a scan as one page or cuts it into two, by its proportions and its gutter."""

    params_model = AutoParams
    spec = ProcessorSpec(
        key='split.auto',
        version='1',
        title='Automatic split',
        stage=Stage.PAGE_SPLIT,
        scope=ProcessorScope.SPLIT,
        outputs=frozenset({VersionOutput.IMAGE}),
        parameters=AutoParams.model_json_schema(),
        editor=SplitChoice.editor,
    )

    @override
    def run(self, step_input: StepInput) -> StepResult:
        """Decide between one page and two, and write the page or the two halves.

        :param step_input: The ``full`` image of the scan, the parameters, and the split choice if there is one.
        :type step_input: StepInput
        :returns: One output for a scan kept whole, else the left half and then the right half.
        :rtype: StepResult
        :raises ConflictError: If there is no image, it cannot be read, or the cut lies outside it.
        """
        if step_input.image is None:
            raise ConflictError(NO_IMAGE.format(key=self.spec.key))
        params = AutoParams.model_validate(step_input.params)
        scan = read_samples(step_input.image)
        height, width = scan.shape[:2]
        edit = step_input.edit
        choice = edit.geometry if edit is not None and isinstance(edit.geometry, SplitChoice) else None
        if choice is not None and choice.pages == SplitChoice.ONE_PAGE:
            return self._whole(step_input, scan, MANUAL_CONFIDENCE, None)
        if choice is not None and choice.line is not None:
            return self.halves(
                step_input, scan, self.line_cut(choice.line, step_input.scale, height), params.overlap_px, None
            )
        cut = GutterSearch(params).search(scan)
        found = cut.confidence >= params.min_confidence
        if choice is None and width / height < params.min_spread_ratio:
            return self._whole(step_input, scan, 1 - cut.confidence, ReviewReason.NARROW_GUTTER if found else None)
        review = None if found else ReviewReason.UNSURE_GUTTER
        return self.halves(step_input, scan, cut, params.overlap_px, review)

    @staticmethod
    def _whole(step_input: StepInput, scan: Samples, confidence: float, review: ReviewReason | None) -> StepResult:
        """Return the scan unchanged as the one page.

        :param step_input: What the step reads, whose image is returned as it is.
        :type step_input: StepInput
        :param scan: The samples of the scan.
        :type scan: Samples
        :param confidence: How sure the step is that the scan is one page.
        :type confidence: float
        :param review: Why the page is marked for a second look, or None.
        :type review: ReviewReason | None
        :returns: One output holding the scan's own file.
        :rtype: StepResult
        """
        color_mode = color_mode_of(scan, step_input.input_data)
        data = image_data(scan, step_input.input_data, color_mode) | {
            VersionData.PAGES: SplitChoice.ONE_PAGE,
            VersionData.CONFIDENCE: confidence,
        }
        return StepResult(outputs=[StepOutput(image=step_input.image, color_mode=color_mode, data=data, review=review)])
