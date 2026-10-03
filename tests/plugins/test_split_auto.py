"""Tests for the split.auto processor, on scans drawn for the tests.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import EditorKind, ProcessorScope, ReviewReason, Stage, TransformKind, VersionData
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Line, Point, SplitChoice
from bookreviver.domain.ids import PageId, StepId
from bookreviver.ports.processing import StepInput
from tests.helpers.builders import EPOCH
from tests.helpers.samples import save
from tests.plugins.synthetic import SPREAD_HEIGHT_PX, SPREAD_WIDTH_PX, draw_blank, draw_single_page, draw_spread

if TYPE_CHECKING:
    from pathlib import Path

    from PIL import Image

    from bookreviver.ports.processing import Processor, StepOutput

SCAN_NAME: str = 'scan.png'
MIN_SPREAD_RATIO: float = 1.1
SLANT_DEG: float = 3.0
# How far the ends of a cut may be from the true gutter, in pixels
CUT_TOLERANCE_PX: float = 16.0
# The part of a spread, as a fraction of its width, that makes a narrow scan with the gutter in the middle of it
NARROW_SHARE: float = 0.3


def make_choice(choice: SplitChoice) -> PageEdit:
    """Build the choice a user made on a page.

    :param choice: The number of pages and the line, if drawn.
    :type choice: SplitChoice
    :returns: An edit saved at the epoch.
    :rtype: PageEdit
    """
    return PageEdit(
        page_id=PageId(uuid4()),
        stage=Stage.PAGE_SPLIT,
        step_id=StepId(uuid4()),
        kind=choice.editor,
        geometry=choice,
        edit_hash=PageEdit.hash_of(choice, None),
        updated_at=EPOCH,
    )


def outputs_of(
    processor: Processor, image: Image.Image, workdir: Path, *, choice: SplitChoice | None = None, **params: float
) -> tuple[Path, list[StepOutput]]:
    """Run the step on a drawn scan.

    :param processor: The processor under test.
    :type processor: Processor
    :param image: The scan.
    :type image: Image.Image
    :param workdir: Directory the scan is saved in and the step writes into.
    :type workdir: Path
    :param choice: The choice of the user, or None for none.
    :type choice: SplitChoice | None
    :param params: Parameters that differ from the defaults.
    :type params: float
    :returns: The path of the scan and the outputs of the step.
    :rtype: tuple[Path, list[StepOutput]]
    """
    path = save(image, workdir / SCAN_NAME)
    step_input = StepInput(
        image=path,
        params=processor.validate_params(params),
        edit=None if choice is None else make_choice(choice),
        workdir=workdir,
    )
    return path, list(processor.run(step_input).outputs)


class TestSplitAuto:
    """Tests for SplitAuto."""

    def test_a_wide_scan_is_cut_along_its_slanting_gutter(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify a spread gives two halves, cut along the gutter with the slant, with the decision in their data.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        drawn = draw_spread(slant_deg=SLANT_DEG)
        _path, (left, right) = outputs_of(fx_split_auto, drawn.image, tmp_path)
        for half in (left, right):
            expect(half.data[VersionData.PAGES] == SplitChoice.TWO_PAGES)
            expect(abs(half.data[VersionData.CUT_TOP_X] - drawn.top_x) < CUT_TOLERANCE_PX)
            expect(abs(half.data[VersionData.CUT_BOTTOM_X] - drawn.bottom_x) < CUT_TOLERANCE_PX)
            expect(half.data[VersionData.CONFIDENCE] > 0)
            expect(half.review is None)
            expect(half.transform.kind is TransformKind.CROP)
        assert_expectations()

    def test_a_single_page_stays_whole_and_is_never_cut(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify a scan taller than wide is returned as the file it was, with the identity transform and no mark.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, outputs = outputs_of(fx_split_auto, draw_single_page(), tmp_path)
        (page,) = outputs
        expect(page.image == path)
        expect(page.transform.kind is TransformKind.IDENTITY)
        expect(page.data[VersionData.PAGES] == SplitChoice.ONE_PAGE)
        expect(page.review is None)
        expect(page.data[VersionData.CONFIDENCE] > 0.9)
        assert_expectations()

    def test_a_narrow_scan_with_a_gutter_stays_whole_and_is_marked(
        self, fx_split_auto: Processor, tmp_path: Path
    ) -> None:
        """Verify a scan narrower than a spread with a strong gutter in the middle is one page marked for a check.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        middle = SPREAD_WIDTH_PX // 2
        reach = int(SPREAD_WIDTH_PX * NARROW_SHARE)
        narrow = draw_spread().image.crop((middle - reach, 0, middle + reach, SPREAD_HEIGHT_PX))
        _path, outputs = outputs_of(fx_split_auto, narrow, tmp_path)
        (page,) = outputs
        expect(page.data[VersionData.PAGES] == SplitChoice.ONE_PAGE)
        expect(page.review is ReviewReason.NARROW_GUTTER)
        assert_expectations()

    def test_the_proportion_that_makes_a_spread_is_a_parameter(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify raising the least proportion above that of a spread keeps the spread whole.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        _path, outputs = outputs_of(fx_split_auto, draw_spread().image, tmp_path, min_spread_ratio=2.0)
        assert len(outputs) == 1

    def test_a_wide_scan_with_no_gutter_is_cut_and_marked(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify blank paper wider than a page is cut in the middle and both halves are marked as unsure.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        _path, (left, right) = outputs_of(fx_split_auto, draw_blank(), tmp_path)
        expect((left.review, right.review) == (ReviewReason.UNSURE_GUTTER, ReviewReason.UNSURE_GUTTER))
        expect(left.data[VersionData.CONFIDENCE] == 0)
        assert_expectations()

    def test_a_choice_of_one_page_keeps_a_spread_whole(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify the user's decision outranks the proportions, with full confidence and no mark.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        choice = SplitChoice(pages=SplitChoice.ONE_PAGE)
        _path, outputs = outputs_of(fx_split_auto, draw_spread().image, tmp_path, choice=choice)
        (page,) = outputs
        expect(page.data[VersionData.CONFIDENCE] == 1)
        expect(page.review is None)
        assert_expectations()

    def test_a_choice_of_two_pages_cuts_a_single_page(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify a scan taller than wide is cut when the user says it holds two pages.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        choice = SplitChoice(pages=SplitChoice.TWO_PAGES)
        _path, outputs = outputs_of(fx_split_auto, draw_single_page(), tmp_path, choice=choice)
        assert len(outputs) == 2

    def test_a_choice_with_a_line_cuts_along_the_line(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify the line of the choice replaces the search, with full confidence and no mark.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        line = Line(start=Point(x=700, y=0), end=Point(x=740, y=SPREAD_HEIGHT_PX - 1))
        choice = SplitChoice(pages=SplitChoice.TWO_PAGES, line=line)
        _path, (left, right) = outputs_of(fx_split_auto, draw_blank(), tmp_path, choice=choice)
        expect(left.data[VersionData.CUT_TOP_X] == pytest.approx(line.start.x))
        expect(left.data[VersionData.CUT_BOTTOM_X] == pytest.approx(line.end.x))
        expect((left.data[VersionData.CONFIDENCE], right.data[VersionData.CONFIDENCE]) == (1.0, 1.0))
        expect((left.review, right.review) == (None, None))
        assert_expectations()

    def test_a_missing_image_is_refused(self, fx_split_auto: Processor, tmp_path: Path) -> None:
        """Verify a step with no image says so.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=r'split\.auto'):
            fx_split_auto.run(StepInput(image=None, params=fx_split_auto.validate_params({}), workdir=tmp_path))

    def test_spec_says_it_splits_a_scan_and_reads_a_split_choice(self, fx_split_auto: Processor) -> None:
        """Verify what the interface reads: the stage, the scope and the editor of the choice.

        :param fx_split_auto: The processor under test.
        :type fx_split_auto: Processor
        """
        spec = fx_split_auto.spec
        expect((spec.stage, spec.scope, spec.editor) == (Stage.PAGE_SPLIT, ProcessorScope.SPLIT, EditorKind.SPLIT))
        expect(fx_split_auto.validate_params({})['min_spread_ratio'] == pytest.approx(MIN_SPREAD_RATIO))
        assert_expectations()
