"""Tests for the cleanup.binarize processor, on pages drawn for the tests with the ground truth of their ink.

The tests need OpenCV and Doxa, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import (
    BinarizationMethod,
    ColorMode,
    EditorKind,
    OutputMode,
    ProcessorScope,
    Stage,
    TransformKind,
    VersionData,
    ZoneMode,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import Point, Regions, Zone
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import save
from tests.plugins.cleanup_pages import (
    INK,
    PAPER,
    PICTURE_COLUMNS,
    PICTURE_ROWS,
    lit_unevenly,
    read_gray,
    run_step,
    text_ink,
    with_picture,
)

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor

PAGE_NAME: str = 'page.png'
# What a method has to reach on the page of uneven light, which a single threshold does not
MIN_F_MEASURE: float = 0.9
FIXED_THRESHOLD: int = 128
# How far the page is turned, and what the scan of the test was made at
SCAN_DPI: int = 300
OUTPUT_DPI: int = 600
MIDDLE_TONES: int = 20
METHODS_OFFERED: set[str] = {method.value for method in BinarizationMethod} - {BinarizationMethod.NEURAL.value}
# The names of the parameters, and the methods the tests choose
METHOD: str = 'method'
MODE: str = 'mode'
WINDOW: str = 'window'
K: str = 'k'
OUTPUT_DPI_PARAM: str = 'output_dpi'
OTSU: BinarizationMethod = BinarizationMethod.OTSU
SAUVOLA: BinarizationMethod = BinarizationMethod.SAUVOLA
MIXED: OutputMode = OutputMode.MIXED
KEY_PATTERN: str = r'cleanup\.binarize'
# The words of a JSON Schema the form is checked through
DEFS: str = '$defs'
REF: str = '$ref'
ONE_OF: str = 'oneOf'
PROPERTIES: str = 'properties'
CONST: str = 'const'
# The fields each method of the form shows beside the ones every method has
WINDOW_FIELDS: dict[str, set[str]] = {
    'otsu': set(),
    'sauvola': {WINDOW, K},
    'wolf': {WINDOW, K},
    'isauvola': {WINDOW, K},
    'gatos': {WINDOW, K},
    'nick': {WINDOW, K},
    'su': {WINDOW},
    'bradley': {WINDOW},
}
COMMON_FIELDS: set[str] = {MODE, 'thickness', 'smooth', OUTPUT_DPI_PARAM, METHOD}
# The names of the work directories of the tests that run a step twice, and the frame the crop recorded
GLOBAL_DIR: str = 'global'
REMOVED_DIR: str = 'removed'
LEFT: str = 'left'
TOP: str = 'top'
WIDTH: str = 'width'
HEIGHT: str = 'height'
# A rectangle that covers the picture the search finds with room round it, which the user draws to remove it
REMOVED_ROWS: slice = slice(480, 740)
REMOVED_COLUMNS: slice = slice(100, 500)
DRAWN_ROWS: slice = slice(100, 200)
DRAWN_COLUMNS: slice = slice(100, 400)


def _f_measure(result: NDArray[np.uint8], truth: NDArray[np.bool_]) -> float:
    """Score the ink a step found against the ink that was drawn.

    :param result: The black and white page the step made.
    :type result: NDArray[np.uint8]
    :param truth: True where the page was drawn with ink.
    :type truth: NDArray[np.bool_]
    :returns: The F-measure of the ink pixels, the harmonic mean of precision and recall.
    :rtype: float
    """
    found = result == INK
    hits = float((found & truth).sum())
    precision = hits / max(float(found.sum()), 1.0)
    recall = hits / float(truth.sum())
    return 2 * precision * recall / (precision + recall) if hits else 0.0


def _rectangle(rows: slice, columns: slice, mode: ZoneMode) -> Regions:
    """Build the regions edit that holds one rectangle.

    :param rows: Rows the rectangle covers.
    :type rows: slice
    :param columns: Columns the rectangle covers.
    :type columns: slice
    :param mode: Whether it adds a picture or removes one.
    :type mode: ZoneMode
    :returns: The edit.
    :rtype: Regions
    """
    corners = (
        (columns.start, rows.start),
        (columns.stop, rows.start),
        (columns.stop, rows.stop),
        (columns.start, rows.stop),
    )
    return Regions(zones=(Zone(mode=mode, points=tuple(Point(x=x, y=y) for x, y in corners)),))


class TestBinarize:
    """Tests for Binarize."""

    def test_spec_is_the_first_step_of_the_cleanup_stage_with_the_regions_editor(self, fx_binarize: Processor) -> None:
        """Verify the spec: the key, the stage, the page scope and the editor of the picture zones.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        """
        spec = fx_binarize.spec
        expect(spec.key == KEY_PATTERN.replace('\\', ''))
        expect(spec.stage is Stage.CLEANUP)
        expect(spec.scope is ProcessorScope.PAGE)
        expect(spec.editor is EditorKind.REGIONS)
        assert_expectations()

    def test_form_offers_every_method_but_the_neural_one_and_each_shows_only_its_own_fields(
        self, fx_binarize: Processor
    ) -> None:
        """Verify the schema is a ``oneOf`` over the methods that lists the fields of each and leaves out ``neural``.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        """
        schema = fx_binarize.spec.parameters
        definitions = schema[DEFS]
        options = [definitions[option[REF].rsplit('/', 1)[1]][PROPERTIES] for option in schema[ONE_OF]]
        fields = {properties[METHOD][CONST]: set(properties) for properties in options}
        offered = set(fields)
        expect(offered == METHODS_OFFERED)
        for method, extra in WINDOW_FIELDS.items():
            expect(fields[method] == COMMON_FIELDS | extra)
        assert_expectations()

    def test_the_default_parameters_are_sauvola_with_a_window_of_41_and_k_of_0_2(self, fx_binarize: Processor) -> None:
        """Verify a step made from the defaults alone is the method the page is best served by.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        """
        params = fx_binarize.validate_params({})
        expect(params[METHOD] == SAUVOLA)
        expect((params[WINDOW], params[K]) == (41, 0.2))
        expect(params[MODE] == OutputMode.BW)
        assert_expectations()

    @pytest.mark.parametrize(
        'raw',
        [{METHOD: 'neural'}, {METHOD: OTSU, K: 0.3}, {METHOD: SAUVOLA, WINDOW: 2}, {OUTPUT_DPI_PARAM: 50}],
        ids=['neural-not-offered', 'otsu-has-no-k', 'window-too-small', 'resolution-too-low'],
    )
    def test_parameters_that_do_not_fit_the_method_are_refused(
        self, fx_binarize: Processor, raw: dict[str, object]
    ) -> None:
        """Verify a method that is not offered, a field the method lacks and a value out of range are all errors.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param raw: The parameters that do not fit.
        :type raw: dict[str, object]
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_binarize.validate_params(raw)

    def test_sauvola_keeps_the_ink_of_a_page_lit_unevenly_where_a_fixed_threshold_does_not(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify Sauvola reaches an F-measure of 0.9 against the ground truth, and a fixed threshold does not.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        truth = text_ink()
        scan = lit_unevenly(truth)
        image = save(Image.fromarray(scan), tmp_path / PAGE_NAME)
        output = run_step(fx_binarize, image, tmp_path, {METHOD: SAUVOLA})
        assert output.image is not None
        fixed = np.where(scan < FIXED_THRESHOLD, INK, PAPER).astype(np.uint8)
        expect(_f_measure(read_gray(output.image), truth) >= MIN_F_MEASURE)
        expect(_f_measure(fixed, truth) < MIN_F_MEASURE)
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    @pytest.mark.parametrize('method', sorted(METHODS_OFFERED - {OTSU}))
    def test_every_local_method_beats_the_one_threshold_of_otsu_on_the_page_lit_unevenly(
        self, fx_binarize: Processor, tmp_path: Path, method: str
    ) -> None:
        """Verify the neighbourhood methods that Doxa gives work on the page, and do better than Otsu does.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param method: The method under test.
        :type method: str
        """
        truth = text_ink()
        image = save(Image.fromarray(lit_unevenly(truth)), tmp_path / PAGE_NAME)
        (tmp_path / GLOBAL_DIR).mkdir()
        local = run_step(fx_binarize, image, tmp_path, {METHOD: method})
        global_ = run_step(fx_binarize, image, tmp_path / GLOBAL_DIR, {METHOD: OTSU})
        assert local.image is not None
        assert global_.image is not None
        expect(_f_measure(read_gray(local.image), truth) > _f_measure(read_gray(global_.image), truth))
        expect(local.data[VersionData.METHOD] == method)
        assert_expectations()

    def test_otsu_records_its_threshold_and_a_local_method_does_not(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify the one threshold of Otsu is in the data of the version, since it is the one a page has.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(lit_unevenly(text_ink())), tmp_path / PAGE_NAME)
        otsu = run_step(fx_binarize, image, tmp_path, {METHOD: OTSU})
        local = run_step(fx_binarize, image, tmp_path, {METHOD: SAUVOLA})
        expect(0 < otsu.data[VersionData.THRESHOLD] < PAPER)
        expect(VersionData.THRESHOLD not in local.data)
        assert_expectations()

    def test_thickness_makes_the_strokes_thinner_below_zero_and_thicker_above(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify the ink grows with the slider, which is the shift of the threshold ScanTailor has.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(lit_unevenly(text_ink())), tmp_path / PAGE_NAME)
        ink = []
        for thickness in (-30, 0, 30):
            output = run_step(fx_binarize, image, tmp_path, {'thickness': thickness})
            assert output.image is not None
            ink.append(int((read_gray(output.image) == INK).sum()))
        assert ink[0] < ink[1] < ink[2]

    def test_smoothing_rounds_the_edges_without_losing_the_letters(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify a smoothed page still scores as the ground truth does.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        truth = text_ink()
        image = save(Image.fromarray(lit_unevenly(truth)), tmp_path / PAGE_NAME)
        output = run_step(fx_binarize, image, tmp_path, {'smooth': True})
        assert output.image is not None
        assert _f_measure(read_gray(output.image), truth) >= MIN_F_MEASURE

    def test_output_resolution_enlarges_a_black_and_white_page_and_records_the_scale(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify a page of 300 dpi made at 600 is twice the size, with the scale in its transform and the new dpi.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = lit_unevenly(text_ink())
        image = save(Image.fromarray(scan), tmp_path / PAGE_NAME)
        height, width = scan.shape
        facts: MetadataMap = {VersionData.DPI: SCAN_DPI, VersionData.WIDTH_PX: width, VersionData.HEIGHT_PX: height}
        output = run_step(fx_binarize, image, tmp_path, {OUTPUT_DPI_PARAM: OUTPUT_DPI}, facts=facts)
        assert output.image is not None
        expect(read_gray(output.image).shape == (2 * height, 2 * width))
        expect(output.transform.kind is TransformKind.SCALE)
        expect(output.transform.to_output(Point(x=10, y=20)) == Point(x=20, y=40))
        expect(output.data[VersionData.DPI] == OUTPUT_DPI)
        expect((output.data[VersionData.SOURCE_WIDTH_PX], output.data[VersionData.SOURCE_HEIGHT_PX]) == (width, height))
        assert_expectations()

    def test_a_page_of_unknown_resolution_and_a_preview_are_not_enlarged(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify the factor is only worked out from a known resolution, and a preview is made at the size it reads.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = lit_unevenly(text_ink())
        image = save(Image.fromarray(scan), tmp_path / PAGE_NAME)
        height, width = scan.shape
        sized: MetadataMap = {VersionData.WIDTH_PX: width, VersionData.HEIGHT_PX: height}
        unknown = run_step(fx_binarize, image, tmp_path, {OUTPUT_DPI_PARAM: OUTPUT_DPI}, facts=sized)
        preview = run_step(
            fx_binarize,
            image,
            tmp_path,
            {OUTPUT_DPI_PARAM: OUTPUT_DPI},
            facts={**sized, VersionData.DPI: SCAN_DPI},
            scale=0.5,
        )
        assert unknown.image is not None
        assert preview.image is not None
        expect(read_gray(unknown.image).shape == scan.shape)
        expect(read_gray(preview.image).shape == scan.shape)
        expect(unknown.transform.kind is TransformKind.IDENTITY)
        assert_expectations()

    @pytest.mark.parametrize(MODE, [OutputMode.GRAY, OutputMode.COLOR])
    def test_gray_and_colour_modes_level_the_light_and_keep_the_tones(
        self, fx_binarize: Processor, tmp_path: Path, mode: OutputMode
    ) -> None:
        """Verify the paper at both corners comes out as bright, and the page is not made black and white.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param mode: The mode under test.
        :type mode: OutputMode
        """
        scan = lit_unevenly(text_ink())
        image = save(Image.fromarray(scan), tmp_path / PAGE_NAME)
        output = run_step(fx_binarize, image, tmp_path, {MODE: mode})
        assert output.image is not None
        leveled = read_gray(output.image)
        margin = np.s_[10:40, 10:40], np.s_[-40:-10, -40:-10]
        lit, dark = (float(np.median(leveled[corner])) for corner in margin)
        expect(float(np.median(scan[margin[0]])) - float(np.median(scan[margin[1]])) > 3 * MIDDLE_TONES)
        expect(abs(lit - dark) < MIDDLE_TONES)
        expect(output.color_mode is ColorMode.GRAY)
        assert_expectations()

    def test_mixed_mode_keeps_the_picture_in_tones_and_makes_the_text_black_and_white(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify the picture zone is found by itself, stays gray, and the text round it is ink or paper only.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(with_picture(lit_unevenly(text_ink()))), tmp_path / PAGE_NAME)
        output = run_step(fx_binarize, image, tmp_path, {MODE: MIXED})
        assert output.image is not None
        page = read_gray(output.image)
        picture = page[PICTURE_ROWS, PICTURE_COLUMNS]
        text = page[: PICTURE_ROWS.start - 20]
        expect(len(np.unique(picture)) > MIDDLE_TONES)
        expect(set(np.unique(text)) == {INK, PAPER})
        expect(output.color_mode is ColorMode.GRAY)
        expect(len(output.data[VersionData.ZONES]) == 1)
        assert_expectations()

    def test_mixed_mode_of_a_page_with_no_picture_is_black_and_white(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify no zone is found in text, and the page is then bilevel as a black and white one is.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(lit_unevenly(text_ink())), tmp_path / PAGE_NAME)
        output = run_step(fx_binarize, image, tmp_path, {MODE: MIXED})
        expect(output.color_mode is ColorMode.BILEVEL)
        expect(output.data[VersionData.ZONES] == [])
        assert_expectations()

    def test_a_zone_drawn_over_the_text_keeps_it_in_tones_and_a_zone_removed_makes_the_picture_text(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify the regions edit changes the result: the user adds a zone the search missed and removes one it found.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.fromarray(with_picture(lit_unevenly(text_ink()))), tmp_path / PAGE_NAME)
        added = run_step(
            fx_binarize, image, tmp_path, {MODE: MIXED}, edit=_rectangle(DRAWN_ROWS, DRAWN_COLUMNS, ZoneMode.ADD)
        )
        (tmp_path / REMOVED_DIR).mkdir()
        removed = run_step(
            fx_binarize,
            image,
            tmp_path / REMOVED_DIR,
            {MODE: MIXED},
            edit=_rectangle(REMOVED_ROWS, REMOVED_COLUMNS, ZoneMode.REMOVE),
        )
        assert added.image is not None
        assert removed.image is not None
        expect(len(np.unique(read_gray(added.image)[DRAWN_ROWS, DRAWN_COLUMNS])) > len({INK, PAPER}))
        expect(set(np.unique(read_gray(removed.image))) == {INK, PAPER})
        assert_expectations()

    def test_the_frame_of_the_crop_becomes_the_content_frame_of_the_cleaned_page(
        self, fx_binarize: Processor, tmp_path: Path
    ) -> None:
        """Verify the frame the crop found is moved by its margin into the pixels of the cropped page, and scaled.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        scan = lit_unevenly(text_ink())
        height, width = scan.shape
        frame = {LEFT: 500.0, TOP: 900.0, WIDTH: width - 80.0, HEIGHT: height - 120.0}
        facts: MetadataMap = {VersionData.FRAME: frame, VersionData.WIDTH_PX: width, VersionData.HEIGHT_PX: height}
        image = save(Image.fromarray(scan), tmp_path / PAGE_NAME)
        output = run_step(fx_binarize, image, tmp_path, facts=facts)
        expect(
            output.data[VersionData.CONTENT_FRAME]
            == {LEFT: 40.0, TOP: 60.0, WIDTH: width - 80.0, HEIGHT: height - 120.0}
        )
        assert_expectations()

    def test_a_step_with_no_image_is_an_error(self, fx_binarize: Processor, tmp_path: Path) -> None:
        """Verify a step that reads an image fails with the key of the processor when it is given none.

        :param fx_binarize: The processor under test.
        :type fx_binarize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        step_input = StepInput(image=None, params=fx_binarize.validate_params({}), workdir=tmp_path)
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_binarize.run(step_input)
