"""Tests for the geometry.crop processor, on pages drawn for the tests and on two public-domain book scans.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from pathlib import Path
from typing import TYPE_CHECKING, cast

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image, ImageDraw

from bookreviver.domain.enums import (
    Binarization,
    ColorMode,
    CropMethod,
    EditorKind,
    ProcessorScope,
    ReviewReason,
    SheetEdge,
    Stage,
    TransformKind,
    VersionData,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import Point, Rect
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import CV_MISSING, LINE_PITCH_PX, PAPER, save, text_page
from tests.plugins.runner import run_on
from tests.plugins.synthetic import SHEET_PAPER, SHEET_SIZE_PX, draw_sheet

if TYPE_CHECKING:
    from collections.abc import Callable

    from numpy.typing import NDArray

    from bookreviver.domain.values import MetadataMap
    from bookreviver.plugins.crop import FrameSearch
    from bookreviver.ports.processing import Processor

SCAN_ON_BINDING: Path = Path(__file__).parent / 'data' / 'book_scan_on_binding.jpg'
CLEAN_PAGE: Path = Path(__file__).parent / 'data' / 'book_clean_page.jpg'
PAGE_NAME: str = 'page.png'
HALF_NAME: str = 'half.png'
KEY_PATTERN: str = r'geometry\.crop'
METHOD: str = 'method'
MARGIN: str = 'margin_percent'
BINARIZATION: str = 'binarization'
SPECK: str = 'noise_min_area'
# Samples at or below this are ink, on every page of the tests
INK_LIMIT: int = 100
# The band along each side of a warped sheet that is left out of the count, since the background bleeds into it
EDGE_INSET_PX: int = 10
# The share of the ink of a page that the frame has to hold: all of it, and most of it for a scan with marks beside the text
ALL_INK: float = 0.999
MOST_INK: float = 0.97
# The pages drawn with a sheet on a dark background: the turns and the slants the task asks for
SHEETS: list[tuple[float, float]] = [(0.0, 0.0), (4.0, 0.03), (-5.0, 0.05), (5.0, 0.05)]
SHEET_CASE: tuple[str, str] = ('rotation', 'slant')
FRAME_TOLERANCE_PX: float = 2.0
# The sizes the lines are drawn at, as the pages of a book scanned and photographed are, and how far the pitch may be off
LINE_SCALES: tuple[float, ...] = (0.8, 1.0, 1.2)
LINE_TOLERANCE: float = 0.02
EDIT_FRAME: Rect = Rect(left=100, top=150, width=300, height=420)
HALF_SCALE: float = 0.5
# The page of the paper-coloured test, its words, and how far the words stand from the left edge
COLOUR_PAGE_PX: tuple[int, int] = (600, 800)
WORDS_LEFT_PX: int = 12
TONE_TOLERANCE: int = 3
NOISE_SPREAD: float = 3.0
# The page drawn with the borders of a scan: its size, the tones of the paper, of the text and of the borders, and where
# the text, the header and the borders lie
EDGE_PAGE_PX: tuple[int, int] = (600, 1000)
EDGE_PAPER: int = 235
EDGE_TEXT: int = 25
EDGE_BORDER: int = 70
TEXT_LEFT_PX: int = 80
TEXT_RIGHT_PX: int = 520
TEXT_TOP_PX: int = 140
TEXT_PITCH_PX: int = 36
TEXT_LINES: int = 22
GLYPH_HEIGHT_PX: int = 14
HEADER_TOP_PX: int = 50
STRIP_WIDTH_PX: int = 25
BAND_WIDTH_PX: int = 10
SPECKS_LEFT_PX: int = 575
SPECKS_WIDTH_PX: int = 6
SPECK_HEIGHT_PX: int = 10
SPECK_PITCH_PX: int = 16
WORD_WIDTHS_PX: tuple[int, ...] = (30, 52, 41, 75, 36, 63)
WORD_GAP_PX: int = 10
FRAME_SLACK_PX: float = 5.0
ALL_SIDES: frozenset[SheetEdge] = frozenset(SheetEdge)
NO_SIDES: frozenset[SheetEdge] = frozenset[SheetEdge]()


def ink_count(path: Path, inset: int = 0) -> int:
    """Count the pixels of ink in an image.

    :param path: Image to read.
    :type path: Path
    :param inset: Width in pixels of the band along each side that is left out, where a warped sheet has the dark fringe
                  of the background that it was laid on.
    :type inset: int
    :returns: The number of pixels at or below the limit of the ink.
    :rtype: int
    """
    with Image.open(path) as image:
        samples = np.asarray(image.convert('L'))
    inside = samples[inset : samples.shape[0] - inset, inset : samples.shape[1] - inset]
    return int((inside <= INK_LIMIT).sum())


def upright_sheet(perspective: Processor, workdir: Path, rotation: float, slant: float) -> Path:
    """Draw a sheet on a dark background and warp it upright with the perspective step.

    :param perspective: The perspective processor.
    :type perspective: Processor
    :param workdir: Directory the files are written into.
    :type workdir: Path
    :param rotation: Angle in degrees the drawn sheet is turned by.
    :type rotation: float
    :param slant: How much narrower the top of the drawn sheet is than its bottom.
    :type slant: float
    :returns: The path of the warped page.
    :rtype: Path
    """
    scan = save(draw_sheet(rotation_deg=rotation, perspective=slant).image, workdir / 'scan.png')
    warped = workdir / 'warped'
    warped.mkdir()
    output = run_on(perspective, scan, warped)
    assert output.image is not None
    return output.image


def edge_page(*, text_right: int = TEXT_RIGHT_PX, borders: bool = True) -> NDArray[np.uint8]:
    """Draw a page of lines of words under a header, with the dark strip, the shadow and the specks of a scan.

    The words are blocks one glyph high. The header is two dashes round a digit. The strip is a solid column along the
    whole left side, the shadow a solid band along the right side with a column of specks short of it.

    :param text_right: Column where the lines of text end, where a line that is longer is cut by the edge of the page.
    :type text_right: int
    :param borders: Whether the strip, the shadow and the specks are drawn.
    :type borders: bool
    :returns: The gray samples of the page.
    :rtype: NDArray[np.uint8]
    """
    width, height = EDGE_PAGE_PX
    page = np.full((height, width), EDGE_PAPER, dtype=np.uint8)
    for line in range(TEXT_LINES):
        top, left, word = TEXT_TOP_PX + line * TEXT_PITCH_PX, TEXT_LEFT_PX, line
        while left < text_right:
            right = min(left + WORD_WIDTHS_PX[word % len(WORD_WIDTHS_PX)], text_right)
            page[top : top + GLYPH_HEIGHT_PX, left:right] = EDGE_TEXT
            left, word = right + WORD_GAP_PX, word + 1
    header = HEADER_TOP_PX
    page[header + 5 : header + 9, 220:260] = EDGE_TEXT
    page[header : header + GLYPH_HEIGHT_PX, 290:302] = EDGE_TEXT
    page[header + 5 : header + 9, 340:380] = EDGE_TEXT
    if borders:
        page[:, :STRIP_WIDTH_PX] = EDGE_BORDER
        page[:, width - BAND_WIDTH_PX :] = EDGE_BORDER
        for top in range(0, height, SPECK_PITCH_PX):
            page[top : top + SPECK_HEIGHT_PX, SPECKS_LEFT_PX : SPECKS_LEFT_PX + SPECKS_WIDTH_PX] = EDGE_BORDER
    return page


def text_box() -> tuple[int, int, int, int]:
    """Give the box of the text and the header of the page that ``edge_page`` draws.

    :returns: Left, top, right and bottom in pixels.
    :rtype: tuple[int, int, int, int]
    """
    bottom = TEXT_TOP_PX + (TEXT_LINES - 1) * TEXT_PITCH_PX + GLYPH_HEIGHT_PX
    return TEXT_LEFT_PX, HEADER_TOP_PX, TEXT_RIGHT_PX, bottom


@pytest.fixture
def fx_frame_search() -> Callable[..., FrameSearch]:
    """Offer a builder of the search of the content frame, or skip the test where OpenCV is not installed.

    :returns: A function that builds a ``FrameSearch`` with the default parameters of the step, from the samples of a
              page and the sides of the sheet that lie on the edge of the scan.
    :rtype: Callable[..., FrameSearch]
    """
    crop = pytest.importorskip('bookreviver.plugins.crop', reason=CV_MISSING)

    def build(image: NDArray[np.uint8], cut_edges: frozenset[SheetEdge]) -> FrameSearch:
        """Build a search of the frame.

        :param image: The samples of the page.
        :type image: NDArray[np.uint8]
        :param cut_edges: Sides of the sheet that lie on the edge of the scan.
        :type cut_edges: frozenset[SheetEdge]
        :returns: The search.
        :rtype: FrameSearch
        """
        return cast('FrameSearch', crop.FrameSearch(image, crop.CropParams(), cut_edges))

    return build


def box_of(frame: Rect) -> tuple[float, float, float, float]:
    """Give the left, top, right and bottom of a frame.

    :param frame: The frame.
    :type frame: Rect
    :returns: The four sides.
    :rtype: tuple[float, float, float, float]
    """
    return frame.left, frame.top, frame.left + frame.width, frame.top + frame.height


class TestFrameSearchOnTheEdgesOfAScan:
    """Tests for the frame that FrameSearch finds on a page with the borders and the shadows of a scan."""

    @pytest.mark.parametrize('cut_edges', [ALL_SIDES, NO_SIDES], ids=['every-side-cut', 'no-side-cut'])
    def test_the_frame_holds_the_text_and_the_header_and_not_the_borders(
        self, fx_frame_search: Callable[..., FrameSearch], cut_edges: frozenset[SheetEdge]
    ) -> None:
        """Verify the strip, the shadow and the specks along the sides give no ink to the frame, cut sides or not.

        :param fx_frame_search: The builder of the search under test.
        :type fx_frame_search: Callable[..., FrameSearch]
        :param cut_edges: Sides of the sheet that lie on the edge of the scan.
        :type cut_edges: frozenset[SheetEdge]
        """
        found = fx_frame_search(edge_page(), cut_edges).find()
        assert found is not None
        for side, truth in zip(box_of(found.rect), text_box(), strict=True):
            expect(side == pytest.approx(truth, abs=FRAME_SLACK_PX))
        assert_expectations()

    def test_the_borders_are_cleaned_where_every_side_is_cut_as_where_none_is(
        self, fx_frame_search: Callable[..., FrameSearch]
    ) -> None:
        """Verify a page of a sheet that fills the scan gets the frame of the page that has no cut side.

        :param fx_frame_search: The builder of the search under test.
        :type fx_frame_search: Callable[..., FrameSearch]
        """
        cut = fx_frame_search(edge_page(), ALL_SIDES).find()
        uncut = fx_frame_search(edge_page(), NO_SIDES).find()
        assert cut is not None
        assert uncut is not None
        assert cut.rect == uncut.rect

    def test_text_that_the_scanner_cut_at_a_side_keeps_the_frame_at_the_edge(
        self, fx_frame_search: Callable[..., FrameSearch]
    ) -> None:
        """Verify lines that run on to the right edge, a side that was cut, are kept, and the frame comes to the edge.

        :param fx_frame_search: The builder of the search under test.
        :type fx_frame_search: Callable[..., FrameSearch]
        """
        found = fx_frame_search(
            edge_page(text_right=EDGE_PAGE_PX[0], borders=False), frozenset({SheetEdge.RIGHT})
        ).find()
        assert found is not None
        expect(found.rect.left + found.rect.width == pytest.approx(EDGE_PAGE_PX[0], abs=FRAME_SLACK_PX))
        expect(found.rect.left == pytest.approx(TEXT_LEFT_PX, abs=FRAME_SLACK_PX))
        assert_expectations()

    def test_a_page_with_no_ink_has_no_frame(self, fx_frame_search: Callable[..., FrameSearch]) -> None:
        """Verify a blank page and one with a few specks of dust give no frame, with every side cut or none.

        :param fx_frame_search: The builder of the search under test.
        :type fx_frame_search: Callable[..., FrameSearch]
        """
        blank = np.full((EDGE_PAGE_PX[1], EDGE_PAGE_PX[0]), EDGE_PAPER, dtype=np.uint8)
        dusty = blank.copy()
        dusty[200:202, 100:102] = EDGE_TEXT
        dusty[700:702, 400:402] = EDGE_TEXT
        for page in (blank, dusty):
            for cut_edges in (ALL_SIDES, NO_SIDES):
                expect(fx_frame_search(page, cut_edges).find() is None)
        assert_expectations()


class TestCropOnTheEdgesOfAScan:
    """Tests for the Crop step on a page with the borders and the shadows of a scan."""

    @pytest.mark.parametrize('cut_edges', [ALL_SIDES, NO_SIDES], ids=['every-side-cut', 'no-side-cut'])
    def test_the_page_is_cut_to_the_text_and_the_header_and_is_not_marked(
        self, fx_crop: Processor, tmp_path: Path, cut_edges: frozenset[SheetEdge]
    ) -> None:
        """Verify the frame holds the text and the header, leaves the borders out, and raises no review on cut sides.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param cut_edges: Sides of the sheet that lie on the edge of the scan.
        :type cut_edges: frozenset[SheetEdge]
        """
        image = save(Image.fromarray(edge_page()), tmp_path / PAGE_NAME)
        edges = sorted(edge.value for edge in cut_edges)
        output = run_on(fx_crop, image, tmp_path, facts={VersionData.CUT_EDGES: edges})
        frame = Rect.from_data(output.data[VersionData.FRAME])
        for side, truth in zip(box_of(frame), text_box(), strict=True):
            expect(side == pytest.approx(truth, abs=FRAME_SLACK_PX))
        expect(output.review is None)
        assert_expectations()

    def test_text_cut_at_a_cut_side_raises_the_review_and_keeps_the_edge(
        self, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify text that runs on to a cut right side keeps the frame at the edge and marks the page for review.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = edge_page(text_right=EDGE_PAGE_PX[0], borders=False)
        image = save(Image.fromarray(page), tmp_path / PAGE_NAME)
        output = run_on(fx_crop, image, tmp_path, facts={VersionData.CUT_EDGES: [SheetEdge.RIGHT.value]})
        frame = Rect.from_data(output.data[VersionData.FRAME])
        expect(frame.left + frame.width == pytest.approx(EDGE_PAGE_PX[0], abs=FRAME_SLACK_PX))
        expect(output.review is ReviewReason.CUT_BY_EDGE)
        assert_expectations()

    def test_a_page_with_no_ink_but_the_borders_of_the_scan_is_left_as_it_is(
        self, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a page of dust is left as it is and marked, with every side cut.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = np.full((EDGE_PAGE_PX[1], EDGE_PAGE_PX[0]), EDGE_PAPER, dtype=np.uint8)
        page[300:302, 200:202] = EDGE_TEXT
        image = save(Image.fromarray(page), tmp_path / PAGE_NAME)
        output = run_on(fx_crop, image, tmp_path, facts={VersionData.CUT_EDGES: [edge.value for edge in SheetEdge]})
        expect(output.data[VersionData.SKIPPED] is True)
        expect(VersionData.FRAME not in output.data)
        expect(output.review is ReviewReason.NOT_APPLIED)
        assert_expectations()


class TestCrop:
    """Tests for Crop."""

    @pytest.mark.parametrize(SHEET_CASE, SHEETS)
    def test_the_frame_holds_all_the_ink_of_the_page(
        self, fx_perspective: Processor, fx_crop: Processor, tmp_path: Path, rotation: float, slant: float
    ) -> None:
        """Verify no ink is cut off: the page cut to the bare frame holds the ink of the whole page.

        :param fx_perspective: The perspective processor that straightens the drawn sheet.
        :type fx_perspective: Processor
        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param rotation: Angle in degrees the drawn sheet is turned by.
        :type rotation: float
        :param slant: How much narrower the top of the drawn sheet is than its bottom.
        :type slant: float
        """
        page = upright_sheet(fx_perspective, tmp_path, rotation, slant)
        output = run_on(fx_crop, page, tmp_path, params={MARGIN: 0})
        assert output.image is not None
        expect(ink_count(output.image) >= ALL_INK * ink_count(page, EDGE_INSET_PX))
        expect(output.review is None)
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.transform.kind is TransformKind.CROP)
        assert_expectations()

    def test_the_frame_hugs_the_text_of_a_page(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify the frame of a page of words is the box of the words, within a few pixels.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = text_page(*SHEET_SIZE_PX)
        output = run_on(fx_crop, save(page, tmp_path / PAGE_NAME), tmp_path)
        left, top, right, bottom = Image.eval(page, lambda value: PAPER - value).getbbox() or (0, 0, 0, 0)
        frame = Rect.from_data(output.data[VersionData.FRAME])
        expect(0 <= left - frame.left <= FRAME_TOLERANCE_PX * 4)
        expect(0 <= top - frame.top <= FRAME_TOLERANCE_PX * 4)
        expect(0 <= frame.left + frame.width - right <= FRAME_TOLERANCE_PX * 4)
        expect(0 <= frame.top + frame.height - bottom <= FRAME_TOLERANCE_PX * 4)
        assert_expectations()

    def test_the_margin_is_a_share_of_the_width_of_the_frame(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify each side gets the percent of the width of the frame the parameter says, and none by default.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(text_page(*SHEET_SIZE_PX), tmp_path / PAGE_NAME)
        default = run_on(fx_crop, image, tmp_path)
        bare = run_on(fx_crop, image, tmp_path, params={MARGIN: 0})
        narrow = run_on(fx_crop, image, tmp_path, params={MARGIN: 8})
        wide = run_on(fx_crop, image, tmp_path, params={MARGIN: 20})
        frame_width = Rect.from_data(default.data[VersionData.FRAME]).width
        expect(default.data[VersionData.WIDTH_PX] == bare.data[VersionData.WIDTH_PX])
        expect(default.data[VersionData.WIDTH_PX] == pytest.approx(frame_width, abs=FRAME_TOLERANCE_PX))
        expect(narrow.data[VersionData.WIDTH_PX] == pytest.approx(1.16 * frame_width, abs=FRAME_TOLERANCE_PX))
        expect(wide.data[VersionData.WIDTH_PX] == pytest.approx(1.4 * frame_width, abs=FRAME_TOLERANCE_PX))
        expect(
            narrow.data[VersionData.HEIGHT_PX]
            == pytest.approx(bare.data[VersionData.HEIGHT_PX] + 0.16 * frame_width, abs=FRAME_TOLERANCE_PX)
        )
        assert_expectations()

    def test_the_margin_beyond_the_page_is_the_colour_of_the_paper(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify what the margin reaches beyond the edge of the page is filled with the median colour of the paper.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = Image.new('RGB', COLOUR_PAGE_PX, SHEET_PAPER)
        draw = ImageDraw.Draw(page)
        for top in range(100, COLOUR_PAGE_PX[1] - 100, 28):
            draw.rectangle((WORDS_LEFT_PX, top, COLOUR_PAGE_PX[0] - WORDS_LEFT_PX, top + 12), fill=(0, 0, 0))
        noise = np.random.default_rng(3).normal(0, NOISE_SPREAD, (COLOUR_PAGE_PX[1], COLOUR_PAGE_PX[0], 1))
        grainy = Image.fromarray((np.asarray(page) + noise).clip(0, 255).astype(np.uint8))
        output = run_on(fx_crop, save(grainy, tmp_path / PAGE_NAME), tmp_path, params={MARGIN: 20})
        assert output.image is not None
        with Image.open(output.image) as cropped:
            samples = np.asarray(cropped)
        expect(samples.shape[1] > COLOUR_PAGE_PX[0])
        expect(all(abs(int(samples[0, 0, plane]) - SHEET_PAPER[plane]) <= TONE_TOLERANCE for plane in range(3)))
        assert_expectations()

    def test_a_blank_page_is_left_as_it_is(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify a page with nothing on it, or with a few specks of dust, gives its own image and the review mark.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        blank = Image.new('L', (700, 900), PAPER)
        dusty = blank.copy()
        ImageDraw.Draw(dusty).point([(100, 200), (101, 200), (400, 700)], fill=0)
        for name, page in (('blank', blank), ('dusty', dusty)):
            image = save(page, tmp_path / f'{name}.png')
            output = run_on(fx_crop, image, tmp_path)
            expect(output.image == image)
            expect(output.transform.kind is TransformKind.IDENTITY)
            expect(output.data[VersionData.SKIPPED] is True)
            expect(VersionData.FRAME not in output.data)
            expect(output.review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_text_on_a_side_the_scanner_cut_is_kept_and_marked(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify lines that touch a cut side count as text and mark the page, while on an uncut side they are an edge.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = Image.new('L', (700, 900), PAPER)
        draw = ImageDraw.Draw(page)
        for top in range(100, 800, 28):
            draw.rectangle((0, top, 500, top + 12), fill=0)
        image = save(page, tmp_path / PAGE_NAME)
        cut = run_on(fx_crop, image, tmp_path, facts={VersionData.CUT_EDGES: [SheetEdge.LEFT.value]})
        uncut = run_on(fx_crop, image, tmp_path, facts={VersionData.CUT_EDGES: []})
        expect(Rect.from_data(cut.data[VersionData.FRAME]).left <= FRAME_TOLERANCE_PX * 4)
        expect(cut.review is ReviewReason.CUT_BY_EDGE)
        expect(cut.data[VersionData.CUT_EDGES] == [SheetEdge.LEFT.value])
        expect(uncut.review is ReviewReason.NOT_APPLIED)
        assert_expectations()

    def test_rect_edit_replaces_the_search(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify the frame of the user is used as it is, with full confidence, even on a page with nothing on it.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('L', (600, 800), PAPER), tmp_path / PAGE_NAME)
        output = run_on(fx_crop, image, tmp_path, edit=EDIT_FRAME)
        expect(Rect.from_data(output.data[VersionData.FRAME]) == EDIT_FRAME)
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(1.0))
        expect(output.data[VersionData.SKIPPED] is False)
        expect(output.review is None)
        expect(output.data[VersionData.WIDTH_PX] == pytest.approx(EDIT_FRAME.width, abs=FRAME_TOLERANCE_PX))
        assert_expectations()

    def test_transform_puts_the_frame_inside_the_margin(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify a point of the page lands where the cut puts it, and goes back to where it was.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        image = save(Image.new('L', (600, 800), PAPER), tmp_path / PAGE_NAME)
        output = run_on(fx_crop, image, tmp_path, params={MARGIN: 8}, edit=EDIT_FRAME)
        margin = 0.08 * EDIT_FRAME.width
        corner = output.transform.to_output(Point(x=EDIT_FRAME.left, y=EDIT_FRAME.top))
        back = output.transform.to_input(corner)
        expect(corner.x == pytest.approx(margin, abs=1))
        expect(corner.y == pytest.approx(margin, abs=1))
        expect(back.x == pytest.approx(EDIT_FRAME.left))
        expect(output.transform.quad is not None)
        assert_expectations()

    def test_the_adaptive_threshold_finds_the_same_frame_on_a_page_lit_unevenly(
        self, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify both ways of making the page black and white find the frame of text, though the light falls off.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = np.asarray(text_page(*SHEET_SIZE_PX), dtype=np.float64)
        fade = np.linspace(1.0, 0.55, page.shape[1])
        image = save(Image.fromarray((page * fade).astype(np.uint8)), tmp_path / PAGE_NAME)
        otsu = run_on(fx_crop, image, tmp_path, params={BINARIZATION: Binarization.OTSU})
        adaptive = run_on(fx_crop, image, tmp_path, params={BINARIZATION: Binarization.ADAPTIVE})
        found = Rect.from_data(adaptive.data[VersionData.FRAME])
        reference = Rect.from_data(otsu.data[VersionData.FRAME])
        expect(abs(found.left - reference.left) < 10)
        expect(abs(found.width - reference.width) < 10)
        expect(abs(found.height - reference.height) < 10)
        assert_expectations()

    def test_a_preview_reports_the_frame_in_the_pixels_of_the_full_image(
        self, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a page at half size gives the frame of the full one, which is what the editor of a page draws.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = text_page(*SHEET_SIZE_PX)
        full = run_on(fx_crop, save(page, tmp_path / PAGE_NAME), tmp_path)
        half_page = page.resize((page.width // 2, page.height // 2), Image.Resampling.LANCZOS)
        half = run_on(fx_crop, save(half_page, tmp_path / HALF_NAME), tmp_path, scale=HALF_SCALE)
        found, truth = Rect.from_data(half.data[VersionData.FRAME]), Rect.from_data(full.data[VersionData.FRAME])
        expect(found.left == pytest.approx(truth.left, abs=6))
        expect(found.width == pytest.approx(truth.width, abs=8))
        expect(found.height == pytest.approx(truth.height, abs=8))
        expect(half.data[VersionData.SOURCE_WIDTH_PX] == page.width)
        expect(half.data[VersionData.SOURCE_HEIGHT_PX] == page.height)
        assert_expectations()

    def test_a_bilevel_page_stays_bilevel(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify cutting a black-and-white page makes no gray, and the margin beyond the page is white.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = text_page(*SHEET_SIZE_PX).convert('1').convert('L')
        output = run_on(fx_crop, save(page, tmp_path / PAGE_NAME), tmp_path, params={MARGIN: 30})
        assert output.image is not None
        with Image.open(output.image) as cropped:
            colours = {value for _count, value in cropped.convert('L').getcolors() or []}
        expect(colours <= {0, PAPER})
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    def test_a_scan_on_a_binding_is_cut_inside_the_sheet_with_all_its_text(
        self, fx_perspective: Processor, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a public-domain page, once its sheet is straightened, is framed inside the sheet with the text whole.

        :param fx_perspective: The perspective processor that straightens the scan.
        :type fx_perspective: Processor
        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sheet = run_on(fx_perspective, SCAN_ON_BINDING, tmp_path)
        assert sheet.image is not None
        output = run_on(fx_crop, sheet.image, tmp_path, params={MARGIN: 0}, facts=sheet.data)
        assert output.image is not None
        frame = Rect.from_data(output.data[VersionData.FRAME])
        width, height = sheet.data[VersionData.WIDTH_PX], sheet.data[VersionData.HEIGHT_PX]
        expect(frame.left >= 0)
        expect(frame.top >= 0)
        expect(frame.left + frame.width <= width)
        expect(frame.top + frame.height <= height)
        expect(ink_count(output.image) >= MOST_INK * ink_count(sheet.image, EDGE_INSET_PX))
        assert_expectations()

    def test_a_clean_page_keeps_all_its_text(
        self, fx_perspective: Processor, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a public-domain page with no background is framed with its text whole.

        :param fx_perspective: The perspective processor that finds the page to be its own sheet.
        :type fx_perspective: Processor
        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        sheet = run_on(fx_perspective, CLEAN_PAGE, tmp_path)
        assert sheet.image is not None
        output = run_on(fx_crop, sheet.image, tmp_path, params={MARGIN: 0}, facts=sheet.data)
        assert output.image is not None
        expect(ink_count(output.image) >= MOST_INK * ink_count(sheet.image, EDGE_INSET_PX))
        expect(output.data[VersionData.SKIPPED] is False)
        assert_expectations()

    def test_a_missing_image_is_refused(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify a step with no image says so.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_crop.run(StepInput(image=None, params=fx_crop.validate_params({}), workdir=tmp_path))

    def test_a_file_that_is_no_image_is_refused(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify a file OpenCV cannot read fails the step with a message, not with a crash.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        broken = tmp_path / 'broken.png'
        broken.write_bytes(b'not an image')
        with pytest.raises(ConflictError, match='cannot be read'):
            fx_crop.run(StepInput(image=broken, params=fx_crop.validate_params({}), workdir=tmp_path))

    @pytest.mark.parametrize(
        'raw',
        [{MARGIN: -1}, {MARGIN: 80}, {BINARIZATION: 'sauvola'}, {SPECK: -3}, {'margin': 5}],
        ids=['negative-margin', 'margin-too-wide', 'unknown-method', 'negative-speck', 'unknown'],
    )
    def test_parameters_that_do_not_fit_the_schema_are_rejected(self, fx_crop: Processor, raw: MetadataMap) -> None:
        """Reject a margin outside its range, a method it lacks, a negative speck, and a parameter it lacks.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_crop.validate_params(raw)

    def test_defaults_are_filled_in_and_the_spec_names_the_stage_and_the_editor(self, fx_crop: Processor) -> None:
        """Verify what the interface reads: the defaults of the parameters, the stage, the scope and the editor.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        """
        spec = fx_crop.spec
        expect(
            fx_crop.validate_params({})
            == {METHOD: CropMethod.INK_BLOCKS, MARGIN: 0.0, BINARIZATION: Binarization.OTSU.value, SPECK: 4}
        )
        expect((spec.key, spec.stage, spec.scope) == ('geometry.crop', Stage.GEOMETRY, ProcessorScope.PAGE))
        expect(spec.editor is EditorKind.RECT)
        assert_expectations()

    def test_parameters_stored_before_the_methods_existed_keep_the_blocks_of_ink(self, fx_crop: Processor) -> None:
        """Verify parameters that name no method are read as the blocks of ink, so an old recipe behaves as it did.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        """
        checked = fx_crop.validate_params({MARGIN: 3.0})
        assert checked[METHOD] == CropMethod.INK_BLOCKS

    def test_the_method_layout_is_declared_and_not_offered(self, fx_crop: Processor) -> None:
        """Verify the method that reads the regions of the Layout stage is refused while no such stage exists.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        """
        assert CropMethod.LAYOUT in set(CropMethod)
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_crop.validate_params({METHOD: CropMethod.LAYOUT})


class TestCropLineHeight:
    """Tests for the line height Crop records, which the normalization of the book reads."""

    @pytest.mark.parametrize('scale', LINE_SCALES)
    def test_the_distance_between_the_lines_is_recorded_for_the_page(
        self, fx_crop: Processor, tmp_path: Path, scale: float
    ) -> None:
        """Verify the line height in the data is the pitch of the lines drawn, within 2 percent, at any scale of the page.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param scale: Size of the page over the size it was drawn at.
        :type scale: float
        """
        page = text_page(*SHEET_SIZE_PX)
        scaled = page.resize((round(page.width * scale), round(page.height * scale)), Image.Resampling.LANCZOS)
        output = run_on(fx_crop, save(scaled, tmp_path / PAGE_NAME), tmp_path)
        expect(output.data[VersionData.LINE_HEIGHT_PX] == pytest.approx(LINE_PITCH_PX * scale, rel=LINE_TOLERANCE))
        assert_expectations()

    def test_a_preview_reports_the_line_height_in_the_pixels_of_the_full_image(
        self, fx_crop: Processor, tmp_path: Path
    ) -> None:
        """Verify a page at half size gives the line height of the full page, as it gives the frame.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = text_page(*SHEET_SIZE_PX)
        half_page = page.resize((page.width // 2, page.height // 2), Image.Resampling.LANCZOS)
        half = run_on(fx_crop, save(half_page, tmp_path / HALF_NAME), tmp_path, scale=HALF_SCALE)
        expect(half.data[VersionData.LINE_HEIGHT_PX] == pytest.approx(LINE_PITCH_PX, rel=LINE_TOLERANCE))
        assert_expectations()

    def test_a_page_with_fewer_than_three_lines_has_no_line_height(self, fx_crop: Processor, tmp_path: Path) -> None:
        """Verify a frame too short to hold the ripple of lines of text leaves the line height out.

        :param fx_crop: The processor under test.
        :type fx_crop: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        page = Image.new('L', (600, 800), PAPER)
        ImageDraw.Draw(page).rectangle((100, 300, 500, 340), fill=0)
        output = run_on(fx_crop, save(page, tmp_path / PAGE_NAME), tmp_path)
        expect(VersionData.LINE_HEIGHT_PX not in output.data)
        expect(output.data[VersionData.SKIPPED] is False)
        assert_expectations()
