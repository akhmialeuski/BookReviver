"""Tests for the split.spread processor, on spreads generated for the tests.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image, ImageDraw

from bookreviver.domain.entities import PageEdit
from bookreviver.domain.enums import EditorKind, ProcessorScope, ReviewReason, Stage, TransformKind, VersionData
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import Line, Point, SplitChoice
from bookreviver.domain.ids import PageId, StepId
from bookreviver.ports.processing import StepInput
from tests.helpers.builders import EPOCH
from tests.helpers.samples import GUTTER_SHADE, PAPER, find_mark, mark, save, spread
from tests.plugins.synthetic import draw_spread

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor, StepOutput

PAGE_WIDTH_PX: int = 900
HEIGHT_PX: int = 1_200
# How far the cut found may be from the middle of the generated gutter, in pixels
CUT_TOLERANCE_PX: float = 3.0
OVERLAP_PX: int = 25
# The angle a spread is laid on the glass at, and how far the ends of the found cut may be from the true gutter
SLANT_DEG: float = 3.0
SLANT_TOLERANCE_PX: float = 16.0
MARK_ON_LEFT: tuple[int, int] = (300, 500)
MARK_ON_RIGHT: tuple[int, int] = (1_300, 700)


def make_line_edit(start: Point, end: Point) -> PageEdit:
    """Build the cut line a user drew on a page.

    :param start: First point of the line.
    :type start: Point
    :param end: Second point of the line.
    :type end: Point
    :returns: An edit saved at the epoch.
    :rtype: PageEdit
    """
    geometry = Line(start=start, end=end)
    return PageEdit(
        page_id=PageId(uuid4()),
        stage=Stage.PAGE_SPLIT,
        step_id=StepId(uuid4()),
        kind=geometry.editor,
        geometry=geometry,
        edit_hash=PageEdit.hash_of(geometry, None),
        updated_at=EPOCH,
    )


def halves_of(processor: Processor, step_input: StepInput) -> tuple[StepOutput, StepOutput]:
    """Run the step and return its two outputs.

    :param processor: The processor under test.
    :type processor: Processor
    :param step_input: What the step reads.
    :type step_input: StepInput
    :returns: The left half and the right half.
    :rtype: tuple[StepOutput, StepOutput]
    """
    left, right = processor.run(step_input).outputs
    return left, right


class TestSplitSpread:
    """Tests for SplitSpread."""

    def test_cuts_the_scan_along_the_gutter_into_two_halves(self, fx_split_spread: Processor, tmp_path: Path) -> None:
        """Verify the cut falls in the middle of the generated gutter, so the halves add up to the scan.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(spread(PAGE_WIDTH_PX, HEIGHT_PX, tilt=1.5), tmp_path / 'scan.png')
        params = fx_split_spread.validate_params({})
        left, right = halves_of(fx_split_spread, StepInput(image=image, params=params, workdir=tmp_path))
        expect(abs(left.data[VersionData.CUT_X] - PAGE_WIDTH_PX) < CUT_TOLERANCE_PX)
        expect(left.data[VersionData.WIDTH_PX] + right.data[VersionData.WIDTH_PX] == 2 * PAGE_WIDTH_PX)
        expect((left.data[VersionData.HEIGHT_PX], right.data[VersionData.HEIGHT_PX]) == (HEIGHT_PX, HEIGHT_PX))
        expect((left.data[VersionData.OVERLAP_PX], right.data[VersionData.OVERLAP_PX]) == (0, 0))
        expect(left.data[VersionData.CONFIDENCE] == right.data[VersionData.CONFIDENCE] > params['min_confidence'])
        expect((left.review, right.review) == (None, None))
        assert_expectations()

    def test_a_second_dark_region_in_the_band_does_not_pull_the_cut_off_the_gutter(
        self, fx_split_spread: Processor, tmp_path: Path
    ) -> None:
        """Verify the cut is the middle of the gutter alone, though another strip as dark as it lies in the band.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = spread(PAGE_WIDTH_PX, HEIGHT_PX)
        ImageDraw.Draw(scan).rectangle((1_100, 0, 1_115, HEIGHT_PX), fill=(GUTTER_SHADE,) * 3)
        image = save(scan, tmp_path / 'scan.png')
        params = fx_split_spread.validate_params({})
        left, _right = halves_of(fx_split_spread, StepInput(image=image, params=params, workdir=tmp_path))
        assert abs(left.data[VersionData.CUT_X] - PAGE_WIDTH_PX) < CUT_TOLERANCE_PX

    def test_each_half_is_a_crop_whose_transform_maps_the_scan_to_it(
        self, fx_split_spread: Processor, tmp_path: Path
    ) -> None:
        """Verify a landmark of each half is where the transform of the half says, to a pixel.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = spread(PAGE_WIDTH_PX, HEIGHT_PX)
        mark(scan, MARK_ON_LEFT)
        mark(scan, MARK_ON_RIGHT)
        image = save(scan, tmp_path / 'scan.png')
        params = fx_split_spread.validate_params({})
        left, right = halves_of(fx_split_spread, StepInput(image=image, params=params, workdir=tmp_path))
        for output, landmark in ((left, MARK_ON_LEFT), (right, MARK_ON_RIGHT)):
            assert output.image is not None
            with Image.open(output.image) as half:
                found = find_mark(half)
            expected = output.transform.to_output(Point(x=landmark[0], y=landmark[1]))
            expect(output.transform.kind is TransformKind.CROP)
            expect(abs(found[0] - expected.x) <= 1)
            expect(abs(found[1] - expected.y) <= 1)
        assert_expectations()

    def test_overlap_lets_each_half_reach_over_the_cut(self, fx_split_spread: Processor, tmp_path: Path) -> None:
        """Verify the halves are wider by the overlap and the crop of the right half starts that much earlier.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(spread(PAGE_WIDTH_PX, HEIGHT_PX), tmp_path / 'scan.png')
        plain = fx_split_spread.validate_params({})
        wide = fx_split_spread.validate_params({'overlap_px': OVERLAP_PX})
        base_left, base_right = halves_of(fx_split_spread, StepInput(image=image, params=plain, workdir=tmp_path))
        left, right = halves_of(fx_split_spread, StepInput(image=image, params=wide, workdir=tmp_path))
        expect(left.data[VersionData.WIDTH_PX] - base_left.data[VersionData.WIDTH_PX] == OVERLAP_PX)
        expect(right.data[VersionData.WIDTH_PX] - base_right.data[VersionData.WIDTH_PX] == OVERLAP_PX)
        assert right.transform.quad is not None
        assert base_right.transform.quad is not None
        expect(base_right.transform.quad.top_left.x - right.transform.quad.top_left.x == OVERLAP_PX)
        assert_expectations()

    def test_line_edit_replaces_the_search_and_may_slant(self, fx_split_spread: Processor, tmp_path: Path) -> None:
        """Verify a slanted line cuts at its own place on every row, with white where the other half lay.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(spread(PAGE_WIDTH_PX, HEIGHT_PX), tmp_path / 'scan.png')
        edit = make_line_edit(Point(x=880, y=0), Point(x=920, y=HEIGHT_PX - 1))
        params = fx_split_spread.validate_params({})
        left, right = halves_of(fx_split_spread, StepInput(image=image, params=params, edit=edit, workdir=tmp_path))
        assert left.image is not None
        assert right.image is not None
        with Image.open(left.image) as left_half, Image.open(right.image) as right_half:
            # The gutter is gray in the scan. At the top the line is at 880, so the left half is white at 900
            expect(left_half.convert('L').getpixel((900, 0)) == PAPER)
            expect(left_half.convert('L').getpixel((900, HEIGHT_PX - 1)) == GUTTER_SHADE)
            assert right.transform.quad is not None
            origin = int(right.transform.quad.top_left.x)
            expect(right_half.convert('L').getpixel((900 - origin, HEIGHT_PX - 1)) == PAPER)
        expect(abs(left.data[VersionData.CUT_X] - PAGE_WIDTH_PX) < 1)
        assert_expectations()

    def test_line_edit_on_a_preview_is_scaled_to_the_image(self, fx_split_spread: Processor, tmp_path: Path) -> None:
        """Verify the line is drawn on the full image and cuts a preview of half its size at half the distance.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = spread(PAGE_WIDTH_PX, HEIGHT_PX).resize((PAGE_WIDTH_PX, HEIGHT_PX // 2))
        image = save(scan, tmp_path / 'preview.png')
        edit = make_line_edit(Point(x=1_000, y=0), Point(x=1_000, y=HEIGHT_PX - 1))
        params = fx_split_spread.validate_params({})
        left, _right = halves_of(
            fx_split_spread, StepInput(image=image, scale=0.5, params=params, edit=edit, workdir=tmp_path)
        )
        assert left.data[VersionData.CUT_X] == pytest.approx(500)

    def test_a_flat_scan_is_cut_in_the_middle(self, fx_split_spread: Processor, tmp_path: Path) -> None:
        """Verify a scan with no gutter to find is cut in the middle of the band that is searched.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('L', (1_000, 400), PAPER), tmp_path / 'blank.png')
        params = fx_split_spread.validate_params({})
        left, right = halves_of(fx_split_spread, StepInput(image=image, params=params, workdir=tmp_path))
        expect(abs(left.data[VersionData.CUT_X] - 500) <= 1)
        expect(left.data[VersionData.WIDTH_PX] + right.data[VersionData.WIDTH_PX] == 1_000)
        expect((left.data[VersionData.CONFIDENCE], right.data[VersionData.CONFIDENCE]) == (0.0, 0.0))
        expect((left.review, right.review) == (ReviewReason.LOW_CONFIDENCE, ReviewReason.LOW_CONFIDENCE))
        assert_expectations()

    def test_a_minimum_of_one_marks_even_a_clear_gutter_for_review(
        self, fx_split_spread: Processor, tmp_path: Path
    ) -> None:
        """Verify the minimum is what decides the mark: the cut is as good as before and both halves are marked.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(spread(PAGE_WIDTH_PX, HEIGHT_PX), tmp_path / 'scan.png')
        params = fx_split_spread.validate_params({'min_confidence': 1})
        left, right = halves_of(fx_split_spread, StepInput(image=image, params=params, workdir=tmp_path))
        expect(abs(left.data[VersionData.CUT_X] - PAGE_WIDTH_PX) < CUT_TOLERANCE_PX)
        expect((left.review, right.review) == (ReviewReason.LOW_CONFIDENCE, ReviewReason.LOW_CONFIDENCE))
        assert_expectations()

    def test_a_cut_the_user_drew_is_fully_confident_and_never_marked(
        self, fx_split_spread: Processor, tmp_path: Path
    ) -> None:
        """Verify a line edit has the confidence 1 and no mark, even with a minimum of 1 and a scan with no gutter.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('L', (1_000, 400), PAPER), tmp_path / 'blank.png')
        edit = make_line_edit(Point(x=500, y=0), Point(x=500, y=399))
        params = fx_split_spread.validate_params({'min_confidence': 1})
        left, right = halves_of(fx_split_spread, StepInput(image=image, params=params, edit=edit, workdir=tmp_path))
        expect((left.data[VersionData.CONFIDENCE], right.data[VersionData.CONFIDENCE]) == (1.0, 1.0))
        expect((left.review, right.review) == (None, None))
        assert_expectations()

    @pytest.mark.parametrize(
        ('start', 'end'),
        [(Point(x=0, y=100), Point(x=500, y=100)), (Point(x=-900, y=0), Point(x=-900, y=HEIGHT_PX - 1))],
        ids=['horizontal', 'outside'],
    )
    def test_a_cut_that_leaves_a_half_empty_is_refused(
        self, fx_split_spread: Processor, tmp_path: Path, start: Point, end: Point
    ) -> None:
        """Verify a horizontal line, or one outside the scan, fails the step instead of making an empty page.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param start: First point of the line.
        :type start: Point
        :param end: Second point of the line.
        :type end: Point
        """
        image = save(spread(PAGE_WIDTH_PX, HEIGHT_PX), tmp_path / 'scan.png')
        step_input = StepInput(
            image=image, params=fx_split_spread.validate_params({}), edit=make_line_edit(start, end), workdir=tmp_path
        )
        with pytest.raises(ConflictError):
            fx_split_spread.run(step_input)

    def test_a_missing_image_is_refused(self, fx_split_spread: Processor, tmp_path: Path) -> None:
        """Verify a step with no image says so.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=r'split\.spread'):
            fx_split_spread.run(StepInput(image=None, params=fx_split_spread.validate_params({}), workdir=tmp_path))

    @pytest.mark.parametrize(
        'raw',
        [{'search_band': 0}, {'search_band': 1.5}, {'overlap_px': -1}, {'width': 1}],
        ids=['no-band', 'band-too-wide', 'negative-overlap', 'unknown'],
    )
    def test_parameters_that_do_not_fit_the_schema_are_rejected(
        self, fx_split_spread: Processor, raw: MetadataMap
    ) -> None:
        """Reject a band that is empty or wider than the scan, a negative overlap, and a parameter it lacks.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=r'split\.spread'):
            fx_split_spread.validate_params(raw)

    def test_spec_says_it_splits_a_scan_and_reads_a_line(self, fx_split_spread: Processor) -> None:
        """Verify what the interface reads: the stage, the scope that makes two outputs, and the editor of the cut.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        """
        spec = fx_split_spread.spec
        expect((spec.key, spec.stage, spec.scope) == ('split.spread', Stage.PAGE_SPLIT, ProcessorScope.SPLIT))
        expect(spec.editor is EditorKind.LINE)
        expect(
            fx_split_spread.validate_params({})
            == {
                'search_band': 0.3,
                'strips': 12,
                'min_depth': 0.15,
                'max_slant_deg': 5.0,
                'tolerance': 0.005,
                'min_confidence': 0.1,
                'overlap_px': 0,
            }
        )
        expect(spec.version == '2')
        assert_expectations()

    def test_a_slanting_gutter_is_cut_along_its_slant(self, fx_split_spread: Processor, tmp_path: Path) -> None:
        """Verify the halves of a spread laid on the glass at an angle meet along the slanting gutter.

        :param fx_split_spread: The processor under test.
        :type fx_split_spread: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        drawn = draw_spread(slant_deg=SLANT_DEG)
        image = save(drawn.image, tmp_path / 'scan.png')
        params = fx_split_spread.validate_params({})
        left, _right = halves_of(fx_split_spread, StepInput(image=image, params=params, workdir=tmp_path))
        expect(abs(left.data[VersionData.CUT_TOP_X] - drawn.top_x) < SLANT_TOLERANCE_PX)
        expect(abs(left.data[VersionData.CUT_BOTTOM_X] - drawn.bottom_x) < SLANT_TOLERANCE_PX)
        expect(left.data[VersionData.PAGES] == SplitChoice.TWO_PAGES)
        assert_expectations()
