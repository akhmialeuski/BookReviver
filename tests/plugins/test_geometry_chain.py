"""Tests for the three steps of the default Geometry recipe run one after another, as a stage runs them.

Each step reads the image and the data of the one before. The tests check what has to survive the hand-over: the sides the
scanner cut, and the reason an early step marked the page, since only the version of the last step is the one the stage
stands on. They need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import ReviewReason, TransformKind, VersionData
from bookreviver.domain.geometry import Rect
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import save
from tests.plugins.synthetic import draw_sheet

if TYPE_CHECKING:
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor, StepOutput

SCAN_ON_BINDING: Path = Path(__file__).parent / 'data' / 'book_scan_on_binding.jpg'
SCAN_NAME: str = 'scan.png'
# The steps of the recipe, in the order they run
STEP_COUNT: int = 3
# Tones of a cover with grain
COVER_TONE: int = 60
NOISE_SPREAD: float = 8.0
# The turn and the slant of the drawn sheet, and the largest angle the deskew step may find on a sheet straightened already
TURN_DEG: float = 3.0
SLANT: float = 0.04
RESIDUAL_TURN_DEG: float = 1.0


def run_recipe(steps: list[Processor], image: Path, workdir: Path, params: list[dict[str, float]]) -> list[StepOutput]:
    """Run the steps one after another, each on the image and the data of the one before.

    :param steps: The processors in the order they run.
    :type steps: list[Processor]
    :param image: Image the first step reads.
    :type image: Path
    :param workdir: Directory the files of the steps are written into.
    :type workdir: Path
    :param params: Parameters of each step.
    :type params: list[dict[str, float]]
    :returns: The output of each step.
    :rtype: list[StepOutput]
    """
    outputs: list[StepOutput] = []
    current: Path = image
    data: MetadataMap = {}
    for index, (processor, raw) in enumerate(zip(steps, params, strict=True)):
        directory = workdir / str(index)
        directory.mkdir()
        step_input = StepInput(image=current, params=processor.validate_params(raw), input_data=data, workdir=directory)
        [output] = processor.run(step_input).outputs
        assert output.image is not None
        outputs.append(output)
        current, data = output.image, output.data
    return outputs


class TestGeometryChain:
    """Tests for the perspective, deskew and crop steps run in a row."""

    def test_a_sheet_on_a_dark_background_comes_out_as_a_flat_page(
        self, fx_perspective: Processor, fx_deskew: Processor, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify the three steps find a frame smaller than the page, with no mark, and the sides cut pass through.

        The last step cuts nothing, so its image is the one the step before it made, and the frame it found is inside it.

        :param fx_perspective: The perspective processor.
        :type fx_perspective: Processor
        :param fx_deskew: The deskew processor.
        :type fx_deskew: Processor
        :param fx_crop: The crop processor.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = draw_sheet(rotation_deg=TURN_DEG, perspective=SLANT)
        outputs = run_recipe(
            [fx_perspective, fx_deskew, fx_crop], save(scan.image, tmp_path / SCAN_NAME), tmp_path, [{}, {}, {}]
        )
        perspective, deskew, crop = outputs
        expect(len(outputs) == STEP_COUNT)
        expect(abs(deskew.data[VersionData.ANGLE]) < RESIDUAL_TURN_DEG)
        expect(deskew.data[VersionData.CUT_EDGES] == perspective.data[VersionData.CUT_EDGES])
        expect(crop.data[VersionData.CUT_EDGES] == perspective.data[VersionData.CUT_EDGES])
        expect(crop.review is None)
        expect(crop.transform.kind is TransformKind.IDENTITY)
        expect(crop.image == deskew.image)
        expect(Rect.from_data(crop.data[VersionData.FRAME]).width < crop.data[VersionData.WIDTH_PX])
        assert_expectations()

    def test_a_cover_stays_as_it_is_through_all_three_steps(
        self, fx_perspective: Processor, fx_deskew: Processor, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a scan with no paper is left alone by every step, and the version of the last one is marked.

        :param fx_perspective: The perspective processor.
        :type fx_perspective: Processor
        :param fx_deskew: The deskew processor.
        :type fx_deskew: Processor
        :param fx_crop: The crop processor.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        grain = np.random.default_rng(COVER_TONE).normal(COVER_TONE, NOISE_SPREAD, (900, 700, 3))
        cover = save(Image.fromarray(grain.clip(0, 255).astype(np.uint8)), tmp_path / SCAN_NAME)
        outputs = run_recipe([fx_perspective, fx_deskew, fx_crop], cover, tmp_path, [{}, {}, {}])
        expect(all(output.image == cover for output in outputs))
        expect(all(output.transform.kind is TransformKind.IDENTITY for output in outputs))
        expect(outputs[-1].review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_the_reason_of_an_early_step_reaches_the_last_one(
        self, fx_perspective: Processor, fx_deskew: Processor, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a sheet the first step refused marks the last version though the later steps found their results.

        :param fx_perspective: The perspective processor.
        :type fx_perspective: Processor
        :param fx_deskew: The deskew processor.
        :type fx_deskew: Processor
        :param fx_crop: The crop processor.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = save(draw_sheet().image, tmp_path / SCAN_NAME)
        outputs = run_recipe(
            [fx_perspective, fx_deskew, fx_crop], scan, tmp_path, [{'min_sheet_fraction': 0.95}, {}, {}]
        )
        expect(outputs[0].review is ReviewReason.NOT_APPLIED)
        expect(outputs[-1].data[VersionData.SKIPPED] is False)
        expect(outputs[-1].review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_a_scan_on_a_binding_is_found_and_straightened_without_a_mark(
        self, fx_perspective: Processor, fx_deskew: Processor, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a public-domain page on a dark binding is found and levelled by the first two steps with no reason.

        The third step may mark the page, since the watermark on the page touches the bottom side, which the scanner cut.

        :param fx_perspective: The perspective processor.
        :type fx_perspective: Processor
        :param fx_deskew: The deskew processor.
        :type fx_deskew: Processor
        :param fx_crop: The crop processor.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        perspective, deskew, crop = run_recipe(
            [fx_perspective, fx_deskew, fx_crop], SCAN_ON_BINDING, tmp_path, [{}, {}, {}]
        )
        expect(perspective.review is None)
        expect(deskew.review is None)
        expect(crop.data[VersionData.SKIPPED] is False)
        assert_expectations()
