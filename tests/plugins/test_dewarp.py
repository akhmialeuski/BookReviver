"""Tests for the geometry.dewarp processor, on pages bent by a known field.

A flat page of lines of words is bent by moving its columns by a known shift, as a book bends a page at its gutter. A
dewarped page is straight when the middle of each of its lines stays at one height over the width of the page, so the
tests measure how far the middle of the ink of a line wanders from band to band of the page. The tests need OpenCV, and
are skipped with the reason where the optional group ``cv`` is not installed.
"""

import json
from typing import TYPE_CHECKING

import httpx
import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import (
    ColorMode,
    DewarpMethod,
    EditorKind,
    ProcessorScope,
    ReviewReason,
    Stage,
    TransformKind,
    VersionData,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import Mesh, Point
from bookreviver.ports.processing import ProcessorSettings, StepInput
from tests.helpers.samples import LINE_PITCH_PX, MARGIN_PX, WORD_HEIGHT_PX, save, text_page
from tests.plugins.runner import run_on
from tests.plugins.synthetic import (
    BACKGROUND_TONE,
    GUTTER_BEND_PX,
    PICTURE_BOX,
    PICTURE_PAGE_SIZE_PX,
    SHEET_BOX,
    SLIGHT_BEND_PX,
    bend_columns,
    draw_picture_page,
)

if TYPE_CHECKING:
    from pathlib import Path

    from numpy.typing import NDArray

    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor, StepOutput

PAGE_NAME: str = 'page.png'
KEY_PATTERN: str = r'geometry\.dewarp'
# The names of the parameters of the step, the key of the fields of a schema, and the label of a case that is unknown
METHOD: str = 'method'
MIN_BEND: str = 'min_bend'
MAX_RESIDUAL: str = 'max_residual'
MIN_LINES: str = 'min_lines'
PROPERTIES: str = 'properties'
UNKNOWN_CASE: str = 'unknown'
# The share of the shift of the right edge that a bend measures at the least, as its inverse
BEND_SHARE_OF_SHIFT: float = 5.0
# The confidence a page that is straightened well has at least, the number of lines of a page of three, the fewest
# curves a mesh has, and the lines of a page that has two edges
CONFIDENT: float = 0.9
THREE_LINES: int = 3
TWO_CURVES: int = 2
# How far the edges of the sheet found may be from the places they were drawn at, in pixels
EDGE_TOLERANCE_PX: float = 3.0
# The lines a page with a block as tall as ten lines loses, at the most
LINES_UNDER_BLOCK: int = 5
PAGE_SIZE_PX: tuple[int, int] = (1_000, 1_200)
# How far the middle of a line may wander over the page, in pixels for each thousand of its width, which is the limit
# the task sets for a page that is dewarped
STRAIGHT_PX_PER_1000: float = 1.0
# The width of the bands the middle of a line is taken in, the rows around a line that are looked at, and the share of
# a band that a word has to fill for the band to count
BAND_PX: int = 10
ROWS_ABOVE_PX: int = 6
ROWS_IN_WINDOW_PX: int = 26
FULL_BAND_SHARE: float = 0.95
# The fewest bands of a line that hold a word, below which the line is not measured, and the fewest lines measured
MIN_BANDS_PER_LINE: int = 8
MIN_LINES_MEASURED: int = 30
# How far the lines of the bent page wander, which the tests have to see before they trust what they measure
MIN_BENT_WANDER_PX: float = 15.0
# The tone above which a pixel of the sheet is paper, and what an edge of the picture is looked for by
PAPER_LIMIT: int = 200
# The rows a scan for the edge of the picture starts from, in the space of paper above it and below it
PICTURE_SEARCH_TOP: int = 200
PICTURE_SEARCH_BOTTOM: int = 840
# The share of the width the edge of the picture is measured over, taken in from each side of the picture
PICTURE_INSET_PX: int = 20
# How many nodes a curve of the summary has, and how many rows the summary has for a page of many lines
SUMMARY_NODES: int = 5
SUMMARY_ROWS: int = 5
# The grid of the mesh file
GRID_ROWS: int = 45
GRID_COLUMNS: int = 31
# Colour tones of a page drawn in colour
COLOUR_TINT: tuple[int, int, int] = (230, 215, 170)


def line_tops() -> list[int]:
    """Give the rows the lines of a page drawn by ``text_page`` start at.

    :returns: The top row of each line of a page of the size the tests use.
    :rtype: list[int]
    """
    return list(range(MARGIN_PX, PAGE_SIZE_PX[1] - MARGIN_PX, LINE_PITCH_PX))


def wanders(image: NDArray[np.uint8], top: float) -> float | None:
    """Measure how far the middle of one line of words wanders over the width of the page.

    The line is cut into bands, and in each band that a word fills the middle of the ink is worked out to a fraction of a
    pixel. The measure is how far apart the highest and the lowest of those are.

    :param image: The samples of the page, in gray, with black ink.
    :type image: NDArray[np.uint8]
    :param top: The row the line starts at in the flat page, moved by the shift the page has at its middle.
    :type top: float
    :returns: The distance in pixels, or None when too few bands hold a word to say.
    :rtype: float | None
    """
    start = round(top) - ROWS_ABOVE_PX
    window = 255 - image[start : start + ROWS_IN_WINDOW_PX].astype(np.float64)
    rows = np.arange(window.shape[0], dtype=np.float64)
    full = FULL_BAND_SHARE * 255 * BAND_PX * (WORD_HEIGHT_PX + 1)
    middles = [
        float(band.sum(axis=1) @ rows) / float(band.sum())
        for left in range(0, window.shape[1] - BAND_PX + 1, BAND_PX)
        if (band := window[:, left : left + BAND_PX]).sum() >= full
    ]
    return float(max(middles) - min(middles)) if len(middles) >= MIN_BANDS_PER_LINE else None


def worst_wander(image: NDArray[np.uint8], shifts: NDArray[np.float64]) -> tuple[float, int]:
    """Measure the line of a page that wanders the most.

    A line that the bend moved off the bottom of the page is not measured, since part of it is gone.

    :param image: The samples of the page, in gray, with black ink.
    :type image: NDArray[np.uint8]
    :param shifts: How far each column of the page was moved by the bend, of which the shift at the middle is where a
                   straightened line lies.
    :type shifts: NDArray[np.float64]
    :returns: How far the worst line wanders in pixels, and how many lines were measured.
    :rtype: tuple[float, int]
    """
    middle = float(shifts[len(shifts) // 2])
    last_row = image.shape[0] - float(shifts.max()) - WORD_HEIGHT_PX - 1
    found = [value for top in line_tops() if top < last_row and (value := wanders(image, top + middle)) is not None]
    return max(found), len(found)


def edge_rows(image: NDArray[np.uint8], start: int, step: int, columns: tuple[int, int]) -> NDArray[np.float64]:
    """Find the row at which the picture begins, in each band of columns, scanning from a row of paper.

    :param image: The samples of the page, in gray.
    :type image: NDArray[np.uint8]
    :param start: The row to start from, which lies on paper.
    :type start: int
    :param step: 1 to scan downwards, for the top of the picture, and -1 to scan upwards, for the bottom.
    :type step: int
    :param columns: The first and the last column the bands are taken between.
    :type columns: tuple[int, int]
    :returns: For each band the row, to a fraction of a pixel, at which its mean tone falls through the limit of paper.
    :rtype: NDArray[np.float64]
    """
    edges = []
    for left in range(columns[0], columns[1] - BAND_PX + 1, BAND_PX):
        tones = image[:, left : left + BAND_PX].astype(np.float64).mean(axis=1)
        row = start
        while tones[row] >= PAPER_LIMIT:
            row += step
        # The mean crosses the limit between two rows, which is placed by the share of the step it covers
        edges.append(row - step * (PAPER_LIMIT - tones[row]) / (tones[row - step] - tones[row]))
    return np.array(edges)


def bent_page(tmp_path: Path, depth: float = GUTTER_BEND_PX) -> tuple[Path, NDArray[np.uint8], NDArray[np.float64]]:
    """Draw a page of lines of words, bend it, and save it.

    :param tmp_path: Directory to save the page in.
    :type tmp_path: Path
    :param depth: How far the right edge is shifted, in pixels for each thousand of the width.
    :type depth: float
    :returns: The path of the saved page, the samples of the bent page, and the shift of each column.
    :rtype: tuple[Path, NDArray[np.uint8], NDArray[np.float64]]
    """
    flat = np.asarray(text_page(*PAGE_SIZE_PX), dtype=np.uint8)
    bent, shifts = bend_columns(flat, depth)
    return save(Image.fromarray(bent), tmp_path / PAGE_NAME), bent, shifts


def read_gray(output: StepOutput) -> NDArray[np.uint8]:
    """Read the image of the output of a step as gray samples.

    :param output: What the step made.
    :type output: StepOutput
    :returns: The samples.
    :rtype: NDArray[np.uint8]
    """
    assert output.image is not None
    with Image.open(output.image) as image:
        return np.asarray(image.convert('L'), dtype=np.uint8)


class TestDewarpTextLines:
    """Tests for Dewarp with the method of the lines of text."""

    def test_a_page_bent_by_a_known_field_comes_back_straight(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify every line of the page is straight within 1 pixel for each 1000 of its width, after being bent by 30.

        The bend itself is checked to be deep, so that the test is known to be about a page that is bent.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, _, shifts = bent_page(tmp_path)
        output = run_on(fx_dewarp, path, tmp_path)
        worst_after, measured = worst_wander(read_gray(output), shifts)
        expect(float(shifts.max() - shifts.min()) > MIN_BENT_WANDER_PX)
        expect(measured >= MIN_LINES_MEASURED)
        expect(worst_after <= STRAIGHT_PX_PER_1000 * PAGE_SIZE_PX[0] / 1_000)
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.review is None)
        assert_expectations()

    @pytest.mark.parametrize('depth', [10.0, 60.0], ids=['slight', 'deep'])
    def test_a_page_comes_back_straight_for_other_depths_of_the_bend(
        self, fx_dewarp: Processor, tmp_path: Path, depth: float
    ) -> None:
        """Verify the page is straight within the same limit for a bend a third and twice as deep.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param depth: How far the right edge is shifted, in pixels for each thousand of the width.
        :type depth: float
        """
        path, _, shifts = bent_page(tmp_path, depth)
        output = run_on(fx_dewarp, path, tmp_path)
        worst, _ = worst_wander(read_gray(output), shifts)
        assert worst <= STRAIGHT_PX_PER_1000 * PAGE_SIZE_PX[0] / 1_000

    def test_the_data_tell_the_bend_the_lines_and_the_curves_the_editor_starts_from(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify the data hold the bend, the number of lines, a small residual, and a summary of five curves of five nodes.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, _, _ = bent_page(tmp_path)
        output = run_on(fx_dewarp, path, tmp_path)
        summary = Mesh.from_data(output.data[VersionData.MESH])
        # The bend is how far a line departs from the straight line that fits it best, which is a quarter of the shift of
        # the right edge for the cubic and the quadratic this page is bent by
        expect(output.data[VersionData.BEND] > GUTTER_BEND_PX / BEND_SHARE_OF_SHIFT)
        expect(output.data[VersionData.LINES] >= MIN_LINES_MEASURED)
        expect(output.data[VersionData.RESIDUAL] < STRAIGHT_PX_PER_1000)
        expect(output.data[VersionData.CONFIDENCE] > CONFIDENT)
        expect(len(summary.rows) == SUMMARY_ROWS)
        expect({len(row) for row in summary.rows} == {SUMMARY_NODES})
        expect(output.data[VersionData.SOURCE_WIDTH_PX] == PAGE_SIZE_PX[0])
        assert_expectations()

    def test_the_step_hands_the_mesh_to_the_runner_which_makes_the_transform(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify the output holds a mesh file of 45 rows and 31 columns of places and leaves the transform to the runner.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, _, _ = bent_page(tmp_path)
        output = run_on(fx_dewarp, path, tmp_path)
        assert output.mesh is not None
        document = json.loads(output.mesh.read_text(encoding='utf-8'))
        grid = np.array(document['grid'])
        expect(grid.shape == (GRID_ROWS, GRID_COLUMNS, 2))
        expect((document['width'], document['height']) == PAGE_SIZE_PX)
        # The first node of the flat page is taken from near the top left corner of the bent page
        expect(abs(grid[0, 0, 0]) < 1)
        expect(output.transform.kind is TransformKind.IDENTITY)
        assert_expectations()

    def test_a_flat_page_is_left_as_it_is_with_the_review_reason_not_applied(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify a page with no noticeable bend keeps its own image, an identity transform and no mesh file.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = save(text_page(*PAGE_SIZE_PX), tmp_path / PAGE_NAME)
        output = run_on(fx_dewarp, path, tmp_path)
        expect(output.review is ReviewReason.NOT_APPLIED)
        expect(output.image == path)
        expect(output.mesh is None)
        expect(output.transform.kind is TransformKind.IDENTITY)
        expect(output.data[VersionData.SKIPPED] is True)
        expect(output.data[VersionData.BEND] < SLIGHT_BEND_PX * 2)
        assert_expectations()

    def test_a_page_bent_less_than_the_least_bend_is_left_as_it_is(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a bend under the parameter ``min_bend`` is not corrected, and one over it is.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, _, _ = bent_page(tmp_path, depth=10.0)
        left = run_on(fx_dewarp, path, tmp_path, {MIN_BEND: 50})
        done = run_on(fx_dewarp, path, tmp_path, {MIN_BEND: 1})
        expect(left.review is ReviewReason.NOT_APPLIED)
        expect(left.image == path)
        expect(done.image != path)
        expect(done.review is None)
        assert_expectations()

    def test_a_page_with_too_few_lines_is_left_as_it_is_and_marked(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a page of three lines, under the least of five, is left as it is with the reason few-lines.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        flat = np.asarray(text_page(*PAGE_SIZE_PX), dtype=np.uint8).copy()
        flat[MARGIN_PX + THREE_LINES * LINE_PITCH_PX :] = 255
        bent, _ = bend_columns(flat, GUTTER_BEND_PX)
        path = save(Image.fromarray(bent), tmp_path / PAGE_NAME)
        output = run_on(fx_dewarp, path, tmp_path)
        expect(output.review is ReviewReason.FEW_LINES)
        expect(output.image == path)
        expect(output.data[VersionData.LINES] == THREE_LINES)
        expect(output.data[VersionData.SKIPPED] is True)
        # The curves it did find are there for the editor to start from
        expect(len(Mesh.from_data(output.data[VersionData.MESH]).rows) >= TWO_CURVES)
        assert_expectations()

    def test_a_page_whose_lines_are_still_bent_after_the_fit_is_marked(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify a residual above the limit marks the page though the page is dewarped.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, _, _ = bent_page(tmp_path)
        output = run_on(fx_dewarp, path, tmp_path, {MAX_RESIDUAL: 0.001})
        expect(output.review is ReviewReason.HIGH_RESIDUAL)
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.mesh is not None)
        assert_expectations()

    def test_a_picture_among_the_lines_does_not_spoil_the_fit(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a dark block as tall as ten lines is left out of the lines, and the lines are still made straight.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        flat = np.asarray(text_page(*PAGE_SIZE_PX), dtype=np.uint8).copy()
        top = MARGIN_PX + 10 * LINE_PITCH_PX
        flat[top : top + 10 * LINE_PITCH_PX, MARGIN_PX : PAGE_SIZE_PX[0] - MARGIN_PX] = 90
        bent, shifts = bend_columns(flat, GUTTER_BEND_PX)
        path = save(Image.fromarray(bent), tmp_path / PAGE_NAME)
        output = run_on(fx_dewarp, path, tmp_path)
        middle = float(shifts[PAGE_SIZE_PX[0] // 2])
        above = [wanders(read_gray(output), top_row + middle) for top_row in line_tops()[:9]]
        expect(output.review is None)
        expect(output.data[VersionData.LINES] < len(line_tops()) - LINES_UNDER_BLOCK)
        expect(all(value is not None and value <= STRAIGHT_PX_PER_1000 for value in above))
        assert_expectations()

    def test_a_bilevel_page_stays_bilevel_and_keeps_its_size(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify remapping a black-and-white page makes no gray at the edges of the ink, and the size is not changed.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        flat = text_page(*PAGE_SIZE_PX)
        bent, _ = bend_columns(np.asarray(flat, dtype=np.uint8), GUTTER_BEND_PX)
        path = save(Image.fromarray((bent >= 128).astype(np.uint8) * 255), tmp_path / PAGE_NAME)
        output = run_on(fx_dewarp, path, tmp_path)
        assert output.image is not None
        with Image.open(output.image) as result:
            colours = {value for _count, value in result.convert('L').getcolors() or []}
            expect(colours <= {0, 255})
            expect(result.size == PAGE_SIZE_PX)
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    def test_a_colour_page_stays_colour(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify the three planes of a colour page are kept.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        _, bent, _ = bent_page(tmp_path)
        tinted = np.stack([bent * (tone / 255) for tone in COLOUR_TINT], axis=-1).astype(np.uint8)
        path = save(Image.fromarray(tinted), tmp_path / 'colour.png')
        output = run_on(fx_dewarp, path, tmp_path)
        assert output.image is not None
        with Image.open(output.image) as result:
            expect(result.mode == 'RGB')
        expect(output.color_mode is ColorMode.COLOR)
        assert_expectations()

    def test_a_blank_page_is_left_as_it_is(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a page with no ink has no lines, and is left as it is with the reason few-lines.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = save(Image.new('L', PAGE_SIZE_PX, 255), tmp_path / 'blank.png')
        output = run_on(fx_dewarp, path, tmp_path)
        expect(output.review is ReviewReason.FEW_LINES)
        expect(output.image == path)
        expect(output.data[VersionData.LINES] == 0)
        assert_expectations()


class TestDewarpMeshEdit:
    """Tests for Dewarp with a mesh edit of the user."""

    @staticmethod
    def true_curves(first: float, last: float, shift_at: NDArray[np.float64]) -> Mesh:
        """Make the top curve and the bottom curve the user would lay on the first and the last line of a bent page.

        :param first: The row the first line starts at in the flat page, moved to the middle of the line.
        :type first: float
        :param last: The row the last line starts at in the flat page, moved to the middle of the line.
        :type last: float
        :param shift_at: The shift of the five columns the nodes stand at.
        :type shift_at: NDArray[np.float64]
        :returns: The two curves of five nodes.
        :rtype: Mesh
        """
        xs = np.linspace(0, PAGE_SIZE_PX[0] - 1, SUMMARY_NODES)
        return Mesh(
            rows=tuple(
                tuple(Point(x=float(x), y=float(row + shift)) for x, shift in zip(xs, shift_at, strict=True))
                for row in (first, last)
            )
        )

    def test_two_curves_the_user_laid_on_the_lines_straighten_the_page(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify the page is straight within the limit by the two curves of the user, with the confidence 1.

        The two curves bend the page between them as the bend of the first and the last line, which is the bend of every
        line of this page, so the lines between are straight too.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, _, shifts = bent_page(tmp_path)
        nodes = shifts[np.round(np.linspace(0, PAGE_SIZE_PX[0] - 1, SUMMARY_NODES)).astype(int)]
        half_word = WORD_HEIGHT_PX / 2
        tops = line_tops()
        curves = self.true_curves(tops[0] + half_word, tops[-1] + half_word, nodes)
        output = run_on(fx_dewarp, path, tmp_path, edit=curves)
        worst, _ = worst_wander(read_gray(output), shifts)
        expect(worst <= STRAIGHT_PX_PER_1000 * PAGE_SIZE_PX[0] / 1_000)
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(1.0))
        expect(output.review is None)
        expect(Mesh.from_data(output.data[VersionData.MESH]) == curves)
        assert_expectations()

    def test_the_curves_of_the_user_change_the_result_and_apply_to_a_flat_page(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify the edit replaces the search, and is applied though the page has no bend of its own.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = save(text_page(*PAGE_SIZE_PX), tmp_path / PAGE_NAME)
        automatic = run_on(fx_dewarp, path, tmp_path)
        bowed = self.true_curves(100.0, 1_000.0, np.array([0.0, 8.0, 12.0, 8.0, 0.0]))
        manual = run_on(fx_dewarp, path, tmp_path, edit=bowed)
        expect(automatic.image == path)
        expect(manual.image != path)
        expect(manual.review is None)
        expect(manual.data[VersionData.SKIPPED] is False)
        assert_expectations()

    def test_a_full_grid_of_curves_is_followed(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a mesh of five rows is taken as it is, with the number of its rows in the data.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = save(text_page(*PAGE_SIZE_PX), tmp_path / PAGE_NAME)
        xs = np.linspace(0, PAGE_SIZE_PX[0] - 1, SUMMARY_NODES)
        grid = Mesh(
            rows=tuple(
                tuple(Point(x=float(x), y=float(row + 10 * np.sin(x / 300))) for x in xs)
                for row in np.linspace(100, 1_000, SUMMARY_ROWS)
            )
        )
        output = run_on(fx_dewarp, path, tmp_path, edit=grid)
        expect(output.data[VersionData.LINES] == SUMMARY_ROWS)
        expect(output.image != path)
        assert_expectations()

    def test_the_curves_of_a_preview_are_taken_in_the_pixels_of_its_image(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify a preview at half size scales the edit down, and reports the summary in the pixels of the full image.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        half = save(text_page(PAGE_SIZE_PX[0] // 2, PAGE_SIZE_PX[1] // 2), tmp_path / PAGE_NAME)
        bowed = self.true_curves(100.0, 1_000.0, np.array([0.0, 8.0, 12.0, 8.0, 0.0]))
        output = run_on(fx_dewarp, half, tmp_path, edit=bowed, scale=0.5)
        expect(Mesh.from_data(output.data[VersionData.MESH]) == bowed)
        expect(output.data[VersionData.SOURCE_WIDTH_PX] == PAGE_SIZE_PX[0])
        assert_expectations()


class TestDewarpPageEdges:
    """Tests for Dewarp with the method of the edges of the sheet."""

    def test_a_page_with_a_picture_and_three_lines_is_flattened_without_bending_the_picture(
        self, fx_dewarp: Processor, tmp_path: Path
    ) -> None:
        """Verify the sheet and the picture come back straight within the limit, and the picture keeps its height.

        The page has too few lines for the method of the lines, which the test shows by asking it for the same page.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        bent, _ = bend_columns(draw_picture_page(), GUTTER_BEND_PX, BACKGROUND_TONE)
        path = save(Image.fromarray(bent), tmp_path / PAGE_NAME)
        by_lines = run_on(fx_dewarp, path, tmp_path)
        output = run_on(fx_dewarp, path, tmp_path, {METHOD: DewarpMethod.PAGE_EDGES})
        flat = read_gray(output)
        columns = (PICTURE_BOX[0] + PICTURE_INSET_PX, PICTURE_BOX[2] - PICTURE_INSET_PX)
        tops = edge_rows(flat, PICTURE_SEARCH_TOP, 1, columns)
        bottoms = edge_rows(flat, PICTURE_SEARCH_BOTTOM, -1, columns)
        limit = STRAIGHT_PX_PER_1000 * PICTURE_PAGE_SIZE_PX[0] / 1_000
        expect(by_lines.review is ReviewReason.FEW_LINES)
        expect(output.review is None)
        expect(output.data[VersionData.LINES] == TWO_CURVES)
        expect(tops.max() - tops.min() <= limit)
        expect(bottoms.max() - bottoms.min() <= limit)
        expect(float((bottoms - tops).max() - (bottoms - tops).min()) <= limit)
        # The picture keeps the height it had before the page was bent
        expect(abs(float((bottoms - tops).mean()) - (PICTURE_BOX[3] - PICTURE_BOX[1])) <= limit)
        assert_expectations()

    def test_the_curves_it_found_are_the_edges_of_the_sheet(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify the summary for the editor holds the two edges, which stand about where the sheet begins and ends.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        bent, _ = bend_columns(draw_picture_page(), GUTTER_BEND_PX, BACKGROUND_TONE)
        path = save(Image.fromarray(bent), tmp_path / PAGE_NAME)
        output = run_on(fx_dewarp, path, tmp_path, {METHOD: DewarpMethod.PAGE_EDGES})
        top, bottom = Mesh.from_data(output.data[VersionData.MESH]).rows
        expect(abs(top[0].y - SHEET_BOX[1]) < EDGE_TOLERANCE_PX)
        expect(abs(bottom[0].y - SHEET_BOX[3]) < EDGE_TOLERANCE_PX)
        expect(top[-1].y > top[0].y + GUTTER_BEND_PX / 2)
        assert_expectations()

    def test_a_page_with_no_background_has_no_edges_to_follow(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a page that is paper to its edges is left as it is, with the reason not-applied.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path = save(text_page(*PAGE_SIZE_PX), tmp_path / PAGE_NAME)
        output = run_on(fx_dewarp, path, tmp_path, {METHOD: DewarpMethod.PAGE_EDGES})
        expect(output.review is ReviewReason.NOT_APPLIED)
        expect(output.image == path)
        assert_expectations()


class TestDewarpUvDoc:
    """Tests for Dewarp with the UVDoc network, whose model is downloaded and is not in the repository."""

    def test_the_method_needs_a_models_directory(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a processor that was not told where the models are refuses the method with a message.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        path, _, _ = bent_page(tmp_path)
        with pytest.raises(ConflictError, match='models directory'):
            run_on(fx_dewarp, path, tmp_path, {METHOD: DewarpMethod.UVDOC})

    def test_a_model_that_cannot_be_downloaded_fails_the_step_with_a_message(
        self, fx_dewarp: Processor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify a failed download is a ConflictError that names the file, and leaves no partial file behind.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param monkeypatch: Fixture that replaces the download.
        :type monkeypatch: pytest.MonkeyPatch
        """

        def refuse(*_args: object, **_kwargs: object) -> None:
            """Fail as a server that cannot be reached does.

            :param _args: Arguments of the call.
            :type _args: object
            :param _kwargs: Keyword arguments of the call.
            :type _kwargs: object
            :raises httpx.ConnectError: Always.
            """
            err_msg = 'no route'
            raise httpx.ConnectError(err_msg)

        monkeypatch.setattr(httpx, 'stream', refuse)
        models = tmp_path / 'models'
        fx_dewarp.configure(ProcessorSettings(models_dir=models))
        path, _, _ = bent_page(tmp_path)
        with pytest.raises(ConflictError, match=r'UVDoc_grid\.onnx'):
            run_on(fx_dewarp, path, tmp_path, {METHOD: DewarpMethod.UVDOC})
        assert not list(models.glob('*.part'))


class TestDewarpParameters:
    """Tests for the parameters and the spec of Dewarp."""

    def test_defaults_are_filled_in_and_the_spec_names_the_stage_and_the_editor(self, fx_dewarp: Processor) -> None:
        """Verify what the interface reads: the lines of text by default, the stage, the scope and the editor of the mesh.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        """
        spec = fx_dewarp.spec
        expect(
            fx_dewarp.validate_params({})
            == {METHOD: DewarpMethod.TEXT_LINES, MIN_BEND: 2.0, MAX_RESIDUAL: 3.0, MIN_LINES: 5}
        )
        expect((spec.key, spec.stage, spec.scope) == ('geometry.dewarp', Stage.GEOMETRY, ProcessorScope.PAGE))
        expect(spec.editor is EditorKind.MESH)
        assert_expectations()

    def test_the_schema_offers_the_three_methods_as_a_one_of_with_the_fields_of_each(
        self, fx_dewarp: Processor
    ) -> None:
        """Verify the form can show only the fields of the chosen method, and the method that is not offered is absent.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        """
        schema = fx_dewarp.spec.parameters
        definitions = schema['$defs']
        offered = [definitions[option['$ref'].rsplit('/', 1)[-1]] for option in schema['oneOf']]
        expect(
            [option[PROPERTIES][METHOD]['const'] for option in offered]
            == [DewarpMethod.TEXT_LINES, DewarpMethod.PAGE_EDGES, DewarpMethod.UVDOC]
        )
        expect(MIN_LINES in offered[0][PROPERTIES])
        expect(MIN_LINES not in offered[1][PROPERTIES])
        expect(MAX_RESIDUAL not in offered[2][PROPERTIES])
        expect(all(option.get('description') is None for option in offered))
        assert_expectations()

    @pytest.mark.parametrize(
        'method', [DewarpMethod.DOCRES, 'layout', 'sharp'], ids=['gpu-not-installed', 'other-step', UNKNOWN_CASE]
    )
    def test_a_method_that_is_not_offered_is_rejected(self, fx_dewarp: Processor, method: str) -> None:
        """Reject DocRes, which the plugin of the gpu group brings later, a method of another step, and one that is unknown.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param method: The method named.
        :type method: str
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_dewarp.validate_params({METHOD: method})

    @pytest.mark.parametrize(
        'raw',
        [{MIN_LINES: 1}, {MIN_BEND: -1}, {MAX_RESIDUAL: 0}, {MIN_LINES: 5.5}, {'angle': 1}],
        ids=['one-line', 'negative-bend', 'no-residual', 'fraction-of-a-line', UNKNOWN_CASE],
    )
    def test_parameters_that_do_not_fit_the_schema_are_rejected(self, fx_dewarp: Processor, raw: MetadataMap) -> None:
        """Reject a fit by one line, a negative bend, a limit of zero, a fraction of a line, and a parameter it lacks.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_dewarp.validate_params(raw)

    def test_a_field_of_one_method_is_refused_by_another(self, fx_dewarp: Processor) -> None:
        """Verify the page-edges method has no ``min_lines`` and refuses it.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_dewarp.validate_params({METHOD: DewarpMethod.PAGE_EDGES, MIN_LINES: 5})

    def test_a_missing_image_is_refused(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a step with no image says so.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_dewarp.run(StepInput(image=None, params=fx_dewarp.validate_params({}), workdir=tmp_path))

    def test_a_file_that_is_no_image_is_refused(self, fx_dewarp: Processor, tmp_path: Path) -> None:
        """Verify a file OpenCV cannot read fails the step with a message, not with a crash.

        :param fx_dewarp: The processor under test.
        :type fx_dewarp: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        broken = tmp_path / 'broken.png'
        broken.write_bytes(b'not an image')
        with pytest.raises(ConflictError, match='cannot be read'):
            fx_dewarp.run(StepInput(image=broken, params=fx_dewarp.validate_params({}), workdir=tmp_path))
