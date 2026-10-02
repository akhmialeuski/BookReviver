"""Tests for the geometry.deskew processor, on pages generated for the tests.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

import time
from typing import TYPE_CHECKING
from uuid import uuid4

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    ProcessorScope,
    ReviewReason,
    Stage,
    TransformKind,
    VersionData,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import Point
from bookreviver.domain.ids import PageId
from bookreviver.ports.processing import StepInput
from tests.helpers.builders import make_page_edit
from tests.helpers.samples import save, spread, text_page, turned

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor

# How far the angle that is found may be from the angle the page was turned by, in degrees
ANGLE_TOLERANCE: float = 0.2
# The angle of the rotation edit of a test, and the longer side of the preview the budget is measured on, in pixels
EDIT_DEGREES: float = 2.5
PREVIEW_WIDTH_PX: int = 1_400
PREVIEW_HEIGHT_PX: int = 2_048
# The grain of a cover or a blank leaf: its seed, mean tone and spread
GRAIN_SEED: int = 4
GRAIN_TONE: int = 90
GRAIN_SPREAD: float = 9.0
# What a preview of the step may take, which is the budget of the visible page
PREVIEW_BUDGET_SECONDS: float = 1.0


def run_on(processor: Processor, image: Path, workdir: Path, **params: object) -> MetadataMap:
    """Run the step on an image and return the data of its one output.

    :param processor: The processor under test.
    :type processor: Processor
    :param image: Image to read.
    :type image: Path
    :param workdir: Directory the step writes into.
    :type workdir: Path
    :param params: Parameters of the step.
    :type params: object
    :returns: The data of the output.
    :rtype: MetadataMap
    """
    checked = processor.validate_params(params)
    [output] = processor.run(StepInput(image=image, params=checked, workdir=workdir)).outputs
    return output.data


class TestDeskew:
    """Tests for Deskew."""

    @pytest.mark.parametrize('skew', [1.7, -3.2, 4.5], ids=['small', 'clockwise', 'large'])
    def test_finds_the_angle_a_page_was_turned_by_and_turns_it_back(
        self, fx_deskew: Processor, tmp_path: Path, skew: float
    ) -> None:
        """Verify the angle found is the opposite of the skew, with the confidence of a page of clear lines.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param skew: Angle in degrees, counter-clockwise, the generated page is turned by.
        :type skew: float
        """
        image = save(turned(text_page(900, 1200), skew), tmp_path / 'page.png')
        params = fx_deskew.validate_params({})
        [output] = fx_deskew.run(StepInput(image=image, params=params, workdir=tmp_path)).outputs
        expect(abs(output.data[VersionData.ANGLE] + skew) < ANGLE_TOLERANCE)
        expect(output.data[VersionData.CONFIDENCE] > params['min_confidence'])
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.review is None)
        expect(output.transform.kind is TransformKind.ROTATE)
        expect(output.transform.angle == output.data[VersionData.ANGLE])
        assert_expectations()

    def test_the_page_that_is_turned_back_is_level_when_it_is_deskewed_again(
        self, fx_deskew: Processor, tmp_path: Path
    ) -> None:
        """Verify the output is straight: a second pass finds no angle worth turning by.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        source = save(turned(text_page(900, 1200), 3.0), tmp_path / 'page.png')
        params = fx_deskew.validate_params({})
        [first] = fx_deskew.run(StepInput(image=source, params=params, workdir=tmp_path)).outputs
        assert first.image is not None
        second = tmp_path / 'second'
        second.mkdir()
        again = run_on(fx_deskew, first.image, second, max_angle=5)
        assert abs(again[VersionData.ANGLE]) < ANGLE_TOLERANCE

    def test_a_blank_page_is_left_as_it_is_and_says_it_was_skipped(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify a page with no lines to follow gives its own image, an identity transform, no angle and a review mark.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('L', (400, 500), 255), tmp_path / 'blank.png')
        params = fx_deskew.validate_params({})
        [output] = fx_deskew.run(StepInput(image=image, params=params, workdir=tmp_path)).outputs
        expect(output.image == image)
        expect(output.transform.kind is TransformKind.IDENTITY)
        expect((output.data[VersionData.ANGLE], output.data[VersionData.SKIPPED]) == (0.0, True))
        expect(output.review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_grain_with_no_ink_in_it_is_left_as_it_is(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify the grain of a cover is not taken for lines of text: the step gives the confidence 0 and skips the page.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        grain = np.random.default_rng(GRAIN_SEED).normal(GRAIN_TONE, GRAIN_SPREAD, (900, 700))
        image = save(Image.fromarray(grain.clip(0, 255).astype(np.uint8)), tmp_path / 'grain.png')
        params = fx_deskew.validate_params({})
        [output] = fx_deskew.run(StepInput(image=image, params=params, workdir=tmp_path)).outputs
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(0.0))
        expect(output.data[VersionData.SKIPPED] is True)
        expect(output.image == image)
        expect(output.review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_confidence_below_the_minimum_skips_the_page(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify a minimum of 1 skips even a page of clear lines, which is what the parameter is for.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(turned(text_page(900, 1200), 2.0), tmp_path / 'page.png')
        params = fx_deskew.validate_params({'min_confidence': 1})
        [output] = fx_deskew.run(StepInput(image=image, params=params, workdir=tmp_path)).outputs
        expect(output.data[VersionData.SKIPPED] is True)
        expect(output.review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_rotation_edit_replaces_the_search(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify the angle of the user is used as it is, with full confidence, even on a page with no lines.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('L', (400, 500), 255), tmp_path / 'blank.png')
        edit = make_page_edit(page_id=PageId(uuid4()), degrees=EDIT_DEGREES)
        params = fx_deskew.validate_params({})
        [output] = fx_deskew.run(StepInput(image=image, params=params, edit=edit, workdir=tmp_path)).outputs
        centre = Point(x=200, y=250)
        expect(output.data[VersionData.ANGLE] == EDIT_DEGREES)
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(1.0))
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.review is None)
        expect(abs(output.transform.to_output(centre).x - centre.x) < 1)
        assert_expectations()

    def test_transform_maps_a_point_of_the_output_back_to_the_input(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify the transform is the turn of the image: a point goes out and comes back to where it was.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(turned(text_page(900, 1200), 3.0), tmp_path / 'page.png')
        params = fx_deskew.validate_params({})
        [output] = fx_deskew.run(StepInput(image=image, params=params, workdir=tmp_path)).outputs
        point = Point(x=120, y=840)
        back = output.transform.to_input(output.transform.to_output(point))
        expect(abs(back.x - point.x) < 1e-6)
        expect(abs(back.y - point.y) < 1e-6)
        assert_expectations()

    def test_a_bilevel_page_stays_bilevel_and_keeps_its_size(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify turning a black-and-white page makes no gray at the edges of the ink, and the size is not changed.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(turned(text_page(900, 1200).convert('1'), 2.0).convert('L'), tmp_path / 'page.png')
        params = fx_deskew.validate_params({'min_confidence': 0})
        [output] = fx_deskew.run(StepInput(image=image, params=params, workdir=tmp_path)).outputs
        assert output.image is not None
        with Image.open(output.image) as result:
            colours = {value for _count, value in result.convert('L').getcolors() or []}
            expect(colours <= {0, 255})
            expect(result.size == (900, 1200))
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    def test_a_colour_page_stays_colour(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify the three planes of a colour page are kept.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(spread(450, 600, tilt=2.0), tmp_path / 'scan.png')
        params = fx_deskew.validate_params({'min_confidence': 0})
        [output] = fx_deskew.run(StepInput(image=image, params=params, workdir=tmp_path)).outputs
        assert output.image is not None
        with Image.open(output.image) as result:
            expect(result.mode == 'RGB')
        expect(output.color_mode is ColorMode.COLOR)
        assert_expectations()

    def test_a_missing_image_is_refused(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify a step with no image says so.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=r'geometry\.deskew'):
            fx_deskew.run(StepInput(image=None, params=fx_deskew.validate_params({}), workdir=tmp_path))

    def test_a_file_that_is_no_image_is_refused(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify a file OpenCV cannot read fails the step with a message, not with a crash.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        broken = tmp_path / 'broken.png'
        broken.write_bytes(b'not an image')
        with pytest.raises(ConflictError, match='cannot be read'):
            fx_deskew.run(StepInput(image=broken, params=fx_deskew.validate_params({}), workdir=tmp_path))

    def test_preview_of_the_visible_page_fits_the_budget(self, fx_deskew: Processor, tmp_path: Path) -> None:
        """Verify the preview step on the image of a preview takes under a second, the budget of the visible page.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(turned(text_page(PREVIEW_WIDTH_PX, PREVIEW_HEIGHT_PX), 2.0), tmp_path / 'preview.png')
        step_input = StepInput(image=image, scale=0.5, params=fx_deskew.validate_params({}), workdir=tmp_path)
        started = time.perf_counter()
        [output] = fx_deskew.preview(step_input).outputs
        elapsed = time.perf_counter() - started
        expect(elapsed < PREVIEW_BUDGET_SECONDS)
        expect(abs(output.data[VersionData.ANGLE] + 2.0) < ANGLE_TOLERANCE)
        assert_expectations()

    @pytest.mark.parametrize(
        'raw',
        [{'max_angle': 0}, {'max_angle': 60}, {'min_confidence': 2}, {'min_confidence': -1}, {'angle': 1}],
        ids=['no-range', 'range-too-wide', 'confidence-over-one', 'negative-confidence', 'unknown'],
    )
    def test_parameters_that_do_not_fit_the_schema_are_rejected(self, fx_deskew: Processor, raw: MetadataMap) -> None:
        """Reject a range that is empty or beyond reason, a confidence outside 0 to 1, and a parameter it lacks.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=r'geometry\.deskew'):
            fx_deskew.validate_params(raw)

    def test_defaults_are_filled_in_and_the_spec_names_the_stage_and_the_editor(self, fx_deskew: Processor) -> None:
        """Verify what the interface reads: the defaults of the parameters, the stage, the scope and the editor.

        :param fx_deskew: The processor under test.
        :type fx_deskew: Processor
        """
        spec = fx_deskew.spec
        expect(fx_deskew.validate_params({}) == {'max_angle': 5.0, 'min_confidence': 0.3})
        expect((spec.key, spec.stage, spec.scope) == ('geometry.deskew', Stage.GEOMETRY, ProcessorScope.PAGE))
        expect(spec.editor is EditorKind.ROTATION)
        assert_expectations()
