"""Tests for the cleanup.despeckle processor, on lines of letters with dots drawn for the tests.

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
from tests.plugins.cleanup_pages import INK, PAPER, marks_line, read_gray, run_step

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.ports.processing import Processor

PAGE_NAME: str = 'page.png'
KEY_PATTERN: str = r'cleanup\.despeckle'
STRENGTH: str = 'strength'
PROTECT: str = 'protect_diacritics'
# A speck of dust in the margin, far from every letter, as rows then columns, and its side in pixels
DUST_AT: tuple[int, int] = (60, 560)
DUST_SIDE_PX: int = 3
MAX_STRENGTH: int = 3
# A black speck inside a gray picture, whose tones are not ink
PICTURE_TONE: int = 90


def _paint_dust(page: np.ndarray, *, side: int = DUST_SIDE_PX) -> None:
    """Draw a speck of dust in the margin of a page.

    :param page: The samples of the page, changed.
    :type page: np.ndarray
    :param side: Side of the speck in pixels.
    :type side: int
    """
    top, left = DUST_AT
    page[top : top + side, left : left + side] = INK


class TestDespeckle:
    """Tests for Despeckle."""

    def test_spec_is_a_step_of_the_cleanup_stage_that_writes_an_image_and_a_mask(self, fx_despeckle: Processor) -> None:
        """Verify the spec: the key, the stage, the page scope and the outputs.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        """
        spec = fx_despeckle.spec
        expect(spec.key == 'cleanup.despeckle')
        expect(spec.stage is Stage.CLEANUP)
        expect(spec.scope is ProcessorScope.PAGE)
        expect(spec.outputs == {VersionOutput.IMAGE, VersionOutput.MASK})
        assert_expectations()

    @pytest.mark.parametrize('raw', [{STRENGTH: 0}, {STRENGTH: 4}, {'size': 3}], ids=['below', 'above', 'unknown'])
    def test_strength_out_of_range_and_unknown_parameters_are_refused(
        self, fx_despeckle: Processor, raw: dict[str, int]
    ) -> None:
        """Verify the strength is 1 to 3 and a misspelt parameter is an error.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param raw: The parameters that do not fit.
        :type raw: dict[str, int]
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_despeckle.validate_params(raw)

    def test_the_dots_of_i_and_yo_survive_the_strongest_setting_when_they_are_protected(
        self, fx_despeckle: Processor, tmp_path: Path
    ) -> None:
        """Verify strength 3 with the protection on keeps every dot over a stem, and still removes dust in the margin.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        line, dots = marks_line()
        _paint_dust(line)
        image = save(Image.fromarray(line), tmp_path / PAGE_NAME)
        output = run_step(fx_despeckle, image, tmp_path, {STRENGTH: MAX_STRENGTH, PROTECT: True})
        assert output.image is not None
        cleaned = read_gray(output.image)
        expect(all(cleaned[row, column] == INK for row, column in dots))
        expect(cleaned[DUST_AT] == PAPER)
        expect(output.data[VersionData.SPECKS] == 1)
        assert_expectations()

    def test_the_same_setting_takes_the_dots_when_the_protection_is_off(
        self, fx_despeckle: Processor, tmp_path: Path
    ) -> None:
        """Verify the dots are as small as dust, which is why the protection exists.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        line, dots = marks_line()
        image = save(Image.fromarray(line), tmp_path / PAGE_NAME)
        output = run_step(fx_despeckle, image, tmp_path, {STRENGTH: MAX_STRENGTH, PROTECT: False})
        assert output.image is not None
        cleaned = read_gray(output.image)
        expect(all(cleaned[row, column] == PAPER for row, column in dots))
        expect(output.data[VersionData.SPECKS] == len(dots))
        assert_expectations()

    def test_a_careful_setting_leaves_what_an_aggressive_one_takes(
        self, fx_despeckle: Processor, tmp_path: Path
    ) -> None:
        """Verify the strength is a size: a speck of 3 pixels goes at strength 3 and stays at strength 1.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        line, _ = marks_line()
        _paint_dust(line, side=DUST_SIDE_PX)
        image = save(Image.fromarray(line), tmp_path / PAGE_NAME)
        careful = run_step(fx_despeckle, image, tmp_path, {STRENGTH: 1})
        assert careful.image is not None
        careful_dust = read_gray(careful.image)[DUST_AT]
        aggressive = run_step(fx_despeckle, image, tmp_path, {STRENGTH: MAX_STRENGTH})
        assert aggressive.image is not None
        expect(careful_dust == INK)
        expect(read_gray(aggressive.image)[DUST_AT] == PAPER)
        assert_expectations()

    def test_the_mask_marks_what_was_removed_and_nothing_else(self, fx_despeckle: Processor, tmp_path: Path) -> None:
        """Verify ``mask.png`` is white on the pixels of the removed specks and black elsewhere.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        line, _ = marks_line()
        _paint_dust(line)
        image = save(Image.fromarray(line), tmp_path / PAGE_NAME)
        output = run_step(fx_despeckle, image, tmp_path, {STRENGTH: MAX_STRENGTH})
        assert output.mask is not None
        assert output.mask.name == 'mask.png'
        mask = read_gray(output.mask)
        expect(int((mask == PAPER).sum()) == DUST_SIDE_PX**2)
        expect(mask[DUST_AT] == PAPER)
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    def test_the_tones_of_a_picture_are_never_taken_for_dust(self, fx_despeckle: Processor, tmp_path: Path) -> None:
        """Verify only pure black is ink: a small dark spot of a gray picture stays.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = np.full((800, 600), PAPER, dtype=np.uint8)
        page[300:303, 300:303] = PICTURE_TONE
        image = save(Image.fromarray(page), tmp_path / PAGE_NAME)
        output = run_step(fx_despeckle, image, tmp_path, {STRENGTH: MAX_STRENGTH, PROTECT: False})
        assert output.image is not None
        expect(read_gray(output.image)[300, 300] == PICTURE_TONE)
        expect(output.data[VersionData.SPECKS] == 0)
        assert_expectations()

    def test_the_content_frame_is_carried_to_the_next_step(self, fx_despeckle: Processor, tmp_path: Path) -> None:
        """Verify the frame an earlier step recorded is in the data, since the eraser reads it from the step before.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        line, _ = marks_line()
        frame = {'left': 10.0, 'top': 20.0, 'width': 300.0, 'height': 400.0}
        image = save(Image.fromarray(line), tmp_path / PAGE_NAME)
        output = run_step(fx_despeckle, image, tmp_path, facts={VersionData.CONTENT_FRAME: frame})
        assert output.data[VersionData.CONTENT_FRAME] == frame

    def test_a_step_with_no_image_is_an_error(self, fx_despeckle: Processor, tmp_path: Path) -> None:
        """Verify a step that reads an image fails with the key of the processor when it is given none.

        :param fx_despeckle: The processor under test.
        :type fx_despeckle: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        step_input = StepInput(image=None, params=fx_despeckle.validate_params({}), workdir=tmp_path)
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_despeckle.run(step_input)
