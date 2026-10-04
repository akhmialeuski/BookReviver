"""Tests for the cleanup.thickness processor, on strokes of known width drawn for the tests.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import ColorMode, ProcessorScope, Stage, VersionData, VersionOutput
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import save
from tests.plugins.cleanup_pages import INK, PAPER, read_gray, run_step

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.ports.processing import Processor

PAGE_NAME: str = 'page.png'
KEY_PATTERN: str = r'cleanup\.thickness'
AMOUNT: str = 'amount'
MAX_AMOUNT: int = 3
# The stem drawn on the page, as rows then columns, and its width in pixels
STEM_ROWS: slice = slice(100, 300)
STEM_LEFT_PX: int = 200
STEM_WIDTH_PX: int = 5
# A row in the middle of the stem, which no end of it reaches
MIDDLE_ROW: int = 200
# A hairline beside the stem, one pixel wide
HAIRLINE_LEFT_PX: int = 400
# A grey spot of a picture, which is not ink
PICTURE_TONE: int = 90


def _page_with_stem() -> NDArray[np.uint8]:
    """Draw a black and white page with a stem and a hairline.

    :returns: The samples of the page.
    :rtype: NDArray[np.uint8]
    """
    page = np.full((400, 600), PAPER, dtype=np.uint8)
    page[STEM_ROWS, STEM_LEFT_PX : STEM_LEFT_PX + STEM_WIDTH_PX] = INK
    page[STEM_ROWS, HAIRLINE_LEFT_PX : HAIRLINE_LEFT_PX + 1] = INK
    return page


def _stem_width(page: NDArray[np.uint8]) -> int:
    """Count the ink in the middle row of the stem, between the hairline and the left edge.

    :param page: The samples of the page.
    :type page: NDArray[np.uint8]
    :returns: The width of the stem in pixels.
    :rtype: int
    """
    return int((page[MIDDLE_ROW, : HAIRLINE_LEFT_PX - 20] == INK).sum())


class TestThickness:
    """Tests for Thickness."""

    def test_spec_is_a_step_of_the_cleanup_stage_that_writes_an_image_and_stands_after_binarization(
        self, fx_thickness: Processor
    ) -> None:
        """Verify the spec: the key, the stage, the page scope, the outputs and the place the step requires.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        """
        spec = fx_thickness.spec
        expect(spec.key == 'cleanup.thickness')
        expect(spec.title == 'Thickness')
        expect(spec.stage is Stage.CLEANUP)
        expect(spec.scope is ProcessorScope.PAGE)
        expect(spec.outputs == {VersionOutput.IMAGE})
        expect([rule.processor_key for rule in spec.requires_after] == ['cleanup.binarize'])
        expect([rule.processor_key for rule in spec.after] == ['cleanup.despeckle'])
        assert_expectations()

    def test_the_amount_is_nothing_by_default(self, fx_thickness: Processor) -> None:
        """Verify a step added with no setting changes nothing.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        """
        assert fx_thickness.validate_params({}) == {AMOUNT: 0}

    @pytest.mark.parametrize(
        'raw',
        [{AMOUNT: MAX_AMOUNT + 1}, {AMOUNT: -MAX_AMOUNT - 1}, {AMOUNT: 1.5}, {'size': 3}],
        ids=['above', 'below', 'fraction', 'unknown'],
    )
    def test_an_amount_out_of_range_and_unknown_parameters_are_refused(
        self, fx_thickness: Processor, raw: dict[str, float]
    ) -> None:
        """Verify the amount is a whole number from -3 to 3 and a misspelt parameter is an error.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param raw: The parameters that do not fit.
        :type raw: dict[str, float]
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_thickness.validate_params(raw)

    def test_an_amount_of_nothing_leaves_the_page_as_it_is(self, fx_thickness: Processor, tmp_path: Path) -> None:
        """Verify the page passes unchanged and the step says it left the image alone, with no mark for review.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(_page_with_stem()), tmp_path / PAGE_NAME)
        output = run_step(fx_thickness, image, tmp_path, {AMOUNT: 0})
        expect(output.image == image)
        expect(output.data[VersionData.SKIPPED] is True)
        expect(output.review is None)
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    @pytest.mark.parametrize(AMOUNT, [1, 2, MAX_AMOUNT])
    def test_a_positive_amount_widens_a_stroke_by_the_amount_on_each_side(
        self, fx_thickness: Processor, tmp_path: Path, amount: int
    ) -> None:
        """Verify the ink grows: the stem is wider by twice the amount, and the page has more ink.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param amount: Pixels each edge moves by.
        :type amount: int
        """
        before = _page_with_stem()
        image = save(Image.fromarray(before), tmp_path / PAGE_NAME)
        output = run_step(fx_thickness, image, tmp_path, {AMOUNT: amount})
        assert output.image is not None
        after = read_gray(output.image)
        expect(_stem_width(after) == STEM_WIDTH_PX + 2 * amount)
        expect(int((after == INK).sum()) > int((before == INK).sum()))
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    @pytest.mark.parametrize(AMOUNT, [-1, -2])
    def test_a_negative_amount_narrows_a_stroke_by_the_amount_on_each_side(
        self, fx_thickness: Processor, tmp_path: Path, amount: int
    ) -> None:
        """Verify the ink shrinks: the stem is narrower by twice the amount, and the page has less ink.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param amount: Pixels each edge moves by, below zero.
        :type amount: int
        """
        before = _page_with_stem()
        image = save(Image.fromarray(before), tmp_path / PAGE_NAME)
        output = run_step(fx_thickness, image, tmp_path, {AMOUNT: amount})
        assert output.image is not None
        after = read_gray(output.image)
        expect(_stem_width(after) == max(STEM_WIDTH_PX + 2 * amount, 0))
        expect(int((after == INK).sum()) < int((before == INK).sum()))
        assert_expectations()

    def test_a_hairline_is_taken_by_the_thinning_and_kept_by_the_thickening(
        self, fx_thickness: Processor, tmp_path: Path
    ) -> None:
        """Verify a stroke one pixel wide vanishes when the ink is thinned and is a stroke of three when it is thickened.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(_page_with_stem()), tmp_path / PAGE_NAME)
        thinned = run_step(fx_thickness, image, tmp_path, {AMOUNT: -1})
        thickened = run_step(fx_thickness, image, tmp_path, {AMOUNT: 1})
        assert thinned.image is not None
        assert thickened.image is not None
        expect(int((read_gray(thinned.image)[MIDDLE_ROW, HAIRLINE_LEFT_PX - 5 :] == INK).sum()) == 0)
        expect(int((read_gray(thickened.image)[MIDDLE_ROW, HAIRLINE_LEFT_PX - 5 :] == INK).sum()) == 3)
        assert_expectations()

    def test_a_preview_moves_the_edge_by_the_amount_of_its_own_size(
        self, fx_thickness: Processor, tmp_path: Path
    ) -> None:
        """Verify a preview of half the size, which reads half as many pixels, moves the edge half as far.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(_page_with_stem()), tmp_path / PAGE_NAME)
        output = run_step(fx_thickness, image, tmp_path, {AMOUNT: 2}, scale=0.5)
        assert output.image is not None
        expect(_stem_width(read_gray(output.image)) == STEM_WIDTH_PX + 2)

    def test_the_tones_of_a_picture_are_never_taken_for_ink(self, fx_thickness: Processor, tmp_path: Path) -> None:
        """Verify only pure black is ink: a dark spot of a gray picture stays as it is, whichever way the ink moves.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = np.full((400, 600), PAPER, dtype=np.uint8)
        page[200:210, 300:310] = PICTURE_TONE
        image = save(Image.fromarray(page), tmp_path / PAGE_NAME)
        for amount in (-MAX_AMOUNT, MAX_AMOUNT):
            output = run_step(fx_thickness, image, tmp_path, {AMOUNT: amount})
            assert output.image is not None
            expect(bool((read_gray(output.image) == page).all()))
        assert_expectations()

    def test_the_content_frame_is_carried_to_the_next_step(self, fx_thickness: Processor, tmp_path: Path) -> None:
        """Verify the frame an earlier step recorded is in the data, since the eraser reads it from the step before.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        frame = {'left': 10.0, 'top': 20.0, 'width': 300.0, 'height': 400.0}
        image = save(Image.fromarray(_page_with_stem()), tmp_path / PAGE_NAME)
        output = run_step(fx_thickness, image, tmp_path, {AMOUNT: 1}, facts={VersionData.CONTENT_FRAME: frame})
        assert output.data[VersionData.CONTENT_FRAME] == frame

    def test_a_step_with_no_image_is_an_error(self, fx_thickness: Processor, tmp_path: Path) -> None:
        """Verify a step that reads an image fails with the key of the processor when it is given none.

        :param fx_thickness: The processor under test.
        :type fx_thickness: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        step_input = StepInput(image=None, params=fx_thickness.validate_params({}), workdir=tmp_path)
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_thickness.run(step_input)
