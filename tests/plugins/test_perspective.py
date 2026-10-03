"""Tests for the geometry.perspective processor, on sheets drawn for the tests and on two public-domain book scans.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    PerspectiveMethod,
    ProcessorScope,
    ReviewReason,
    SheetEdge,
    Stage,
    TransformKind,
    VersionData,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import Point, Quad
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import save
from tests.plugins.runner import run_on
from tests.plugins.synthetic import SHEET_PAPER, SHEET_SIZE_PX, draw_sheet

if TYPE_CHECKING:
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor
    from tests.plugins.synthetic import SyntheticSheet

SCAN_NAME: str = 'scan.png'
MIN_SHEET: str = 'min_sheet_fraction'
METHOD: str = 'method'
EDGE_STRENGTH: str = 'edge_strength'
# The width of the dark line along the edge of a drawn sheet that is the colour of its background, and the middle of the
# confidence, below which a sheet is not believed
EDGE_WIDTH_PX: int = 3
HALF: float = 0.5
KEY_PATTERN: str = r'geometry\.perspective'
# The names of the arguments of a test that gets a drawn sheet
SHEET_CASE: tuple[str, str] = ('rotation', 'slant')
# A table that makes a gray image black and white, for the point method of Pillow
BILEVEL_TABLE: list[int] = [0] * 128 + [255] * 128
SCAN_ON_BINDING: Path = Path(__file__).parent / 'data' / 'book_scan_on_binding.jpg'
CLEAN_PAGE: Path = Path(__file__).parent / 'data' / 'book_clean_page.jpg'
# How far a corner found may be from the true one, as a share of the diagonal of the scan
CORNER_TOLERANCE: float = 0.01
# The turns and the slants of the drawn sheets: the largest the task asks for, and a few in between
SHEETS: list[tuple[float, float]] = [(0.0, 0.0), (4.0, 0.03), (-5.0, 0.05), (5.0, 0.05), (-2.0, 0.0)]
# How much of the paper of a drawn sheet may be missing from the warped page, in the shape of the width and height
SIZE_TOLERANCE: float = 0.03
# The brightness of the strip of leather of the binding, which the warped page must not hold, and of the paper
BINDING_LIMIT: int = 100
EDIT_INSET_PX: int = 40
HALF_SCALE: float = 0.5
# Tones of a cover with grain and of a blank leaf with a little noise
COVER_TONE: int = 60
LEAF_TONE: int = 225
NOISE_SPREAD: float = 8.0
# How much of the left of a drawn sheet the scanner cuts off, in pixels
CUT_OFF_PX: int = 80


def corner_error(found: Quad, truth: tuple[tuple[float, float], ...], diagonal: float) -> float:
    """Measure how far the found corners are from the true ones.

    :param found: The quadrilateral the step reported.
    :type found: Quad
    :param truth: True corners, top left, top right, bottom right and bottom left.
    :type truth: tuple[tuple[float, float], ...]
    :param diagonal: Length of the diagonal of the scan in pixels.
    :type diagonal: float
    :returns: The largest distance of a corner from its true place, as a share of the diagonal.
    :rtype: float
    """
    distances = [
        float(np.hypot(corner.x - x, corner.y - y)) for corner, (x, y) in zip(found.points(), truth, strict=True)
    ]
    return max(distances) / diagonal


class TestPerspective:
    """Tests for Perspective."""

    @pytest.mark.parametrize(SHEET_CASE, SHEETS)
    def test_finds_the_corners_of_a_sheet_on_a_dark_background(
        self, fx_perspective: Processor, tmp_path: Path, rotation: float, slant: float
    ) -> None:
        """Verify the corners are within 1 percent of the diagonal of the true ones, and the page is not marked.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param rotation: Angle in degrees the drawn sheet is turned by.
        :type rotation: float
        :param slant: How much narrower the top of the drawn sheet is than its bottom.
        :type slant: float
        """
        sheet = draw_sheet(rotation_deg=rotation, perspective=slant)
        output = run_on(fx_perspective, save(sheet.image, tmp_path / SCAN_NAME), tmp_path)
        found = Quad.from_data(output.data[VersionData.QUAD])
        expect(corner_error(found, sheet.corners, sheet.diagonal) < CORNER_TOLERANCE)
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.review is None)
        expect(output.transform.kind is TransformKind.PERSPECTIVE)
        expect(output.transform.quad == Quad.from_data(output.data[VersionData.QUAD]))
        expect(output.data[VersionData.CUT_EDGES] == [])
        assert_expectations()

    @pytest.mark.parametrize(SHEET_CASE, SHEETS)
    def test_warps_the_sheet_into_an_upright_rectangle(
        self, fx_perspective: Processor, tmp_path: Path, rotation: float, slant: float
    ) -> None:
        """Verify the true corners land on the corners of the page, which has about the size of the flat sheet.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param rotation: Angle in degrees the drawn sheet is turned by.
        :type rotation: float
        :param slant: How much narrower the top of the drawn sheet is than its bottom.
        :type slant: float
        """
        sheet = draw_sheet(rotation_deg=rotation, perspective=slant)
        output = run_on(fx_perspective, save(sheet.image, tmp_path / SCAN_NAME), tmp_path)
        width, height = output.data[VersionData.WIDTH_PX], output.data[VersionData.HEIGHT_PX]
        mapped = [output.transform.to_output(Point(x=x, y=y)) for x, y in sheet.corners]
        target = [(0, 0), (width, 0), (width, height), (0, height)]
        tolerance = CORNER_TOLERANCE * np.hypot(width, height)
        expect(all(np.hypot(p.x - x, p.y - y) < tolerance for p, (x, y) in zip(mapped, target, strict=True)))
        expect(abs(width - SHEET_SIZE_PX[0]) < SIZE_TOLERANCE * SHEET_SIZE_PX[0])
        expect(abs(height - SHEET_SIZE_PX[1]) < SIZE_TOLERANCE * SHEET_SIZE_PX[1])
        assert output.image is not None
        with Image.open(output.image) as warped:
            expect(warped.size == (width, height))
        back = output.transform.to_input(output.transform.to_output(Point(x=500, y=600)))
        expect(abs(back.x - 500) < 1e-6)
        assert_expectations()

    def test_a_side_on_the_edge_of_the_scan_is_recorded_as_cut(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify a sheet whose left part the scanner cut off names its left side, and only that one.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sheet = draw_sheet()
        left = int(sheet.corners[0][0]) + CUT_OFF_PX
        scan = sheet.image.crop((left, 0, sheet.image.width, sheet.image.height))
        output = run_on(fx_perspective, save(scan, tmp_path / SCAN_NAME), tmp_path)
        expect(output.data[VersionData.CUT_EDGES] == [SheetEdge.LEFT.value])
        expect(output.review is None)
        assert_expectations()

    @pytest.mark.parametrize('tone', [COVER_TONE, LEAF_TONE], ids=['cover', 'blank-leaf'])
    def test_a_scan_with_no_paper_to_part_from_its_background_is_left_as_it_is(
        self, fx_perspective: Processor, tmp_path: Path, tone: int
    ) -> None:
        """Verify a cover and a blank leaf give their own image, an identity transform and the review mark.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param tone: Mean brightness of the grainy scan, dark for a cover and light for a blank leaf.
        :type tone: int
        """
        grain = np.random.default_rng(tone).normal(tone, NOISE_SPREAD, (900, 700, 3))
        image = save(Image.fromarray(grain.clip(0, 255).astype(np.uint8)), tmp_path / SCAN_NAME)
        output = run_on(fx_perspective, image, tmp_path)
        expect(output.image == image)
        expect(output.transform.kind is TransformKind.IDENTITY)
        expect(output.data[VersionData.SKIPPED] is True)
        expect(VersionData.QUAD not in output.data)
        expect(output.review is ReviewReason.NOT_APPLIED)
        expect(output.data[VersionData.REVIEW] == ReviewReason.NOT_APPLIED.value)
        assert_expectations()

    def test_a_sheet_smaller_than_the_minimum_is_left_as_it_is(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify the minimum share of the scan decides: the same sheet is found by default and refused by a high one.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(draw_sheet().image, tmp_path / SCAN_NAME)
        found = run_on(fx_perspective, image, tmp_path, params={MIN_SHEET: 0.5})
        refused = run_on(fx_perspective, image, tmp_path, params={MIN_SHEET: 0.95})
        expect(found.data[VersionData.SKIPPED] is False)
        expect(refused.data[VersionData.SKIPPED] is True)
        expect(refused.review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_quad_edit_replaces_the_search(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify the corners of the user are used as they are, with full confidence, even on a scan with no paper.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('RGB', (600, 800), (LEAF_TONE,) * 3), tmp_path / 'blank.png')
        quad = Quad.from_points(
            [
                Point(x=EDIT_INSET_PX, y=EDIT_INSET_PX),
                Point(x=560, y=EDIT_INSET_PX + 10),
                Point(x=550, y=780),
                Point(x=EDIT_INSET_PX, y=760),
            ]
        )
        output = run_on(fx_perspective, image, tmp_path, edit=quad)
        expect(output.data[VersionData.QUAD] == quad.to_data())
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(1.0))
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.review is None)
        expect(output.transform.quad == quad)
        expect(abs(output.data[VersionData.WIDTH_PX] - 520) < 12)
        assert_expectations()

    def test_a_preview_reports_the_corners_in_the_pixels_of_the_full_image(
        self, fx_perspective: Processor, tmp_path: Path
    ) -> None:
        """Verify a scan at half size gives the corners of the full one, which is what the editor of a page draws.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sheet = draw_sheet(rotation_deg=3.0, perspective=0.04)
        half = sheet.image.resize((sheet.image.width // 2, sheet.image.height // 2), Image.Resampling.LANCZOS)
        output = run_on(fx_perspective, save(half, tmp_path / 'half.png'), tmp_path, scale=HALF_SCALE)
        found = Quad.from_data(output.data[VersionData.QUAD])
        expect(corner_error(found, sheet.corners, sheet.diagonal) < CORNER_TOLERANCE)
        expect(output.data[VersionData.SOURCE_WIDTH_PX] == pytest.approx(sheet.image.width, abs=1))
        expect(output.data[VersionData.SOURCE_HEIGHT_PX] == pytest.approx(sheet.image.height, abs=1))
        assert_expectations()

    def test_a_scan_on_a_binding_is_cut_along_the_edge_of_the_paper(
        self, fx_perspective: Processor, tmp_path: Path
    ) -> None:
        """Verify a public-domain page on a dark binding is warped with no strip of the binding left on its left.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        output = run_on(fx_perspective, SCAN_ON_BINDING, tmp_path)
        assert output.image is not None
        with Image.open(output.image) as warped:
            gray = np.asarray(warped.convert('L'))
        found = Quad.from_data(output.data[VersionData.QUAD])
        left, top = min(found.top_left.x, found.bottom_left.x), found.top_left.y
        expect(left > 0)
        expect(top > 0)
        expect(float(gray[:, :4].mean()) > BINDING_LIMIT)
        expect(float(gray[:4, :].mean()) > BINDING_LIMIT)
        expect(SheetEdge.LEFT.value not in output.data[VersionData.CUT_EDGES])
        expect(SheetEdge.TOP.value not in output.data[VersionData.CUT_EDGES])
        expect(output.review is None)
        assert_expectations()

    def test_a_clean_page_is_its_own_sheet(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify a public-domain page with no background gives the corners of the scan, all four sides cut.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with Image.open(CLEAN_PAGE) as page:
            width, height = page.size
        output = run_on(fx_perspective, CLEAN_PAGE, tmp_path)
        found = Quad.from_data(output.data[VersionData.QUAD])
        truth = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height))
        expect(corner_error(found, truth, float(np.hypot(width, height))) < CORNER_TOLERANCE)
        expect(output.data[VersionData.CUT_EDGES] == [edge.value for edge in SheetEdge])
        assert_expectations()

    def test_a_bilevel_scan_stays_bilevel(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify warping a black-and-white scan makes no gray at the edges of the ink.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sheet: SyntheticSheet = draw_sheet(rotation_deg=2.0, perspective=0.03)
        bilevel = sheet.image.convert('L').point(BILEVEL_TABLE)
        output = run_on(fx_perspective, save(bilevel, tmp_path / SCAN_NAME), tmp_path)
        assert output.image is not None
        with Image.open(output.image) as warped:
            colours = {value for _count, value in warped.convert('L').getcolors() or []}
        expect(colours <= {0, 255})
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    def test_a_missing_image_is_refused(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify a step with no image says so.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_perspective.run(StepInput(image=None, params=fx_perspective.validate_params({}), workdir=tmp_path))

    def test_a_file_that_is_no_image_is_refused(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify a file OpenCV cannot read fails the step with a message, not with a crash.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        broken = tmp_path / 'broken.png'
        broken.write_bytes(b'not an image')
        with pytest.raises(ConflictError, match='cannot be read'):
            fx_perspective.run(StepInput(image=broken, params=fx_perspective.validate_params({}), workdir=tmp_path))

    @pytest.mark.parametrize(
        'raw',
        [{MIN_SHEET: 0}, {MIN_SHEET: 1.5}, {'fraction': 0.2}],
        ids=['no-sheet', 'larger-than-the-scan', 'unknown'],
    )
    def test_parameters_that_do_not_fit_the_schema_are_rejected(
        self, fx_perspective: Processor, raw: MetadataMap
    ) -> None:
        """Reject a share of nothing, a share beyond the scan, and a parameter it lacks.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_perspective.validate_params(raw)

    def test_defaults_are_filled_in_and_the_spec_names_the_stage_and_the_editor(
        self, fx_perspective: Processor
    ) -> None:
        """Verify what the interface reads: the default of the parameter, the stage, the scope and the editor.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        """
        spec = fx_perspective.spec
        expect(fx_perspective.validate_params({}) == {METHOD: PerspectiveMethod.PAPER_COLOUR, MIN_SHEET: 0.25})
        expect((spec.key, spec.stage, spec.scope) == ('geometry.perspective', Stage.GEOMETRY, ProcessorScope.PAGE))
        expect(spec.editor is EditorKind.QUAD)
        assert_expectations()

    def test_parameters_stored_before_the_methods_existed_keep_the_colour_of_the_paper(
        self, fx_perspective: Processor
    ) -> None:
        """Verify parameters that name no method are read as the colour of the paper, so an old recipe behaves as it did.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        """
        checked = fx_perspective.validate_params({MIN_SHEET: 0.4})
        assert checked == {METHOD: PerspectiveMethod.PAPER_COLOUR, MIN_SHEET: 0.4}

    def test_the_schema_offers_two_methods_as_a_one_of_with_the_fields_of_each(self, fx_perspective: Processor) -> None:
        """Verify the form can show only the fields of the chosen method.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        """
        schema = fx_perspective.spec.parameters
        offered = [schema['$defs'][option['$ref'].rsplit('/', 1)[-1]] for option in schema['oneOf']]
        expect(
            [option['properties'][METHOD]['const'] for option in offered]
            == [PerspectiveMethod.PAPER_COLOUR, PerspectiveMethod.EDGES]
        )
        expect('edge_strength' in offered[1]['properties'])
        expect('edge_strength' not in offered[0]['properties'])
        assert_expectations()

    def test_a_field_of_the_edges_is_refused_by_the_colour_of_the_paper(self, fx_perspective: Processor) -> None:
        """Verify the method paper-colour has no ``edge_strength`` and refuses it.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_perspective.validate_params({METHOD: PerspectiveMethod.PAPER_COLOUR, 'edge_strength': 40})


class TestPerspectiveEdges:
    """Tests for Perspective with the method of the edges of the sheet, for paper of the colour of its background."""

    @pytest.mark.parametrize(SHEET_CASE, SHEETS)
    def test_finds_the_corners_of_a_sheet_that_is_the_colour_of_its_background(
        self, fx_perspective: Processor, tmp_path: Path, rotation: float, slant: float
    ) -> None:
        """Verify the corners are within 1 percent of the diagonal of the true ones where the colour of the paper fails.

        The colour of the paper is shown to fail on the same scan, which is what the method is for.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param rotation: Angle in degrees the drawn sheet is turned by.
        :type rotation: float
        :param slant: How much narrower the top of the drawn sheet is than its bottom.
        :type slant: float
        """
        sheet = draw_sheet(rotation_deg=rotation, perspective=slant, background=SHEET_PAPER, edge_px=EDGE_WIDTH_PX)
        image = save(sheet.image, tmp_path / SCAN_NAME)
        output = run_on(fx_perspective, image, tmp_path, params={METHOD: PerspectiveMethod.EDGES})
        by_colour = run_on(fx_perspective, image, tmp_path)
        found = Quad.from_data(output.data[VersionData.QUAD])
        wrong = by_colour.data.get(VersionData.QUAD)
        expect(corner_error(found, sheet.corners, sheet.diagonal) < CORNER_TOLERANCE)
        expect(wrong is None or corner_error(Quad.from_data(wrong), sheet.corners, sheet.diagonal) > CORNER_TOLERANCE)
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.data[VersionData.CONFIDENCE] > HALF)
        expect(output.review is None)
        expect(output.transform.kind is TransformKind.PERSPECTIVE)
        assert_expectations()

    def test_a_scan_with_no_edges_has_no_sheet(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify a scan that is one flat tone, with nothing to part a sheet from its background, is left as it is.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('RGB', SHEET_SIZE_PX, SHEET_PAPER), tmp_path / SCAN_NAME)
        output = run_on(fx_perspective, image, tmp_path, params={METHOD: PerspectiveMethod.EDGES})
        expect(output.review is ReviewReason.NOT_APPLIED)
        expect(output.image == image)
        expect(output.data[VersionData.SKIPPED] is True)
        assert_expectations()

    def test_a_sheet_cut_by_the_scanner_has_its_side_on_the_edge_of_the_scan(
        self, fx_perspective: Processor, tmp_path: Path
    ) -> None:
        """Verify a side with no edge to be seen, where the scanner cut the paper, is the edge of the scan and is reported.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sheet = draw_sheet(background=SHEET_PAPER, edge_px=EDGE_WIDTH_PX)
        cut = sheet.image.crop((CUT_OFF_PX, 0, sheet.image.width, sheet.image.height))
        output = run_on(
            fx_perspective, save(cut, tmp_path / SCAN_NAME), tmp_path, params={METHOD: PerspectiveMethod.EDGES}
        )
        expect(SheetEdge.LEFT.value in output.data[VersionData.CUT_EDGES])
        expect(output.data[VersionData.SKIPPED] is False)
        assert_expectations()

    @pytest.mark.parametrize(
        'raw',
        [{METHOD: PerspectiveMethod.EDGES, EDGE_STRENGTH: 0}, {METHOD: PerspectiveMethod.EDGES, EDGE_STRENGTH: 300}],
        ids=['no-edges-seen', 'above-the-range-of-the-detector'],
    )
    def test_a_strength_outside_the_range_of_the_detector_is_rejected(
        self, fx_perspective: Processor, raw: MetadataMap
    ) -> None:
        """Reject an edge strength of nothing and one beyond what the detector of Canny can tell.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_perspective.validate_params(raw)

    def test_a_sheet_smaller_than_the_least_is_no_sheet(self, fx_perspective: Processor, tmp_path: Path) -> None:
        """Verify the parameter ``min_sheet_fraction`` is kept by this method too.

        :param fx_perspective: The processor under test.
        :type fx_perspective: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sheet = draw_sheet(background=SHEET_PAPER, edge_px=EDGE_WIDTH_PX)
        image = save(sheet.image, tmp_path / SCAN_NAME)
        output = run_on(fx_perspective, image, tmp_path, params={METHOD: PerspectiveMethod.EDGES, MIN_SHEET: 0.95})
        expect(output.data[VersionData.SKIPPED] is True)
        assert_expectations()
