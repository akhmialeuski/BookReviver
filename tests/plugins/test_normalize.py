"""Tests for the geometry.normalize processor, on blocks of text and on sheets with a block, drawn at different scales.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import TYPE_CHECKING, ClassVar, Unpack

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    HorizontalAlign,
    MarginsBy,
    NormalizeParam,
    PageSide,
    PaperFill,
    ProcessorScope,
    ReviewReason,
    Stage,
    TransformKind,
    VersionData,
    VerticalAlign,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.geometry import ContentBox, Point, Rect
from bookreviver.plugins.cv_image import line_pitch
from bookreviver.ports.processing import StepInput
from tests.helpers.samples import INK, LINE_PITCH_PX, PAPER, save, text_page
from tests.plugins.runner import run_on

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.processing import Processor, StepOutput
    from tests.plugins.runner import StepExtras

KEY_PATTERN: str = r'geometry\.normalize'
BLOCK_NAME: str = 'block.png'
SCALES: tuple[float, ...] = (0.8, 1.0, 1.2)
# The resolution of the input of the tests, which makes ten pixels of a millimetre, so a margin in millimetres is that
# many tens of pixels
TEST_DPI: float = 254.0
PIXELS_PER_MM: int = 10
# The page the tests normalize to, and its margins, which leave a work area that the block of any scale fits in
PAGE_WIDTH_PX: int = 1000
PAGE_HEIGHT_PX: int = 1400
MARGIN_TOP_MM: float = 10.0
MARGIN_BOTTOM_MM: float = 15.0
MARGIN_INNER_MM: float = 12.0
MARGIN_OUTER_MM: float = 6.0
MARGIN_TOP_PX: int = round(MARGIN_TOP_MM * PIXELS_PER_MM)
MARGIN_BOTTOM_PX: int = round(MARGIN_BOTTOM_MM * PIXELS_PER_MM)
MARGIN_INNER_PX: int = round(MARGIN_INNER_MM * PIXELS_PER_MM)
MARGIN_OUTER_PX: int = round(MARGIN_OUTER_MM * PIXELS_PER_MM)
PAGE: MetadataMap = {
    NormalizeParam.PAGE_WIDTH: PAGE_WIDTH_PX,
    NormalizeParam.PAGE_HEIGHT: PAGE_HEIGHT_PX,
    NormalizeParam.MARGIN_TOP: MARGIN_TOP_MM,
    NormalizeParam.MARGIN_BOTTOM: MARGIN_BOTTOM_MM,
    NormalizeParam.MARGIN_INNER: MARGIN_INNER_MM,
    NormalizeParam.MARGIN_OUTER: MARGIN_OUTER_MM,
}
# The input that carries the resolution, as the version of a scan does
SCAN: MetadataMap = {VersionData.DPI: TEST_DPI}
ALIGN_X: str = 'align_horizontal'
ALIGN_Y: str = 'align_vertical'
MARGINS_BY: str = 'margins_by'
LINE_TOLERANCE: float = 0.02
EDGE_TOLERANCE_PX: float = 2.0
# The block of the alignment tests: shorter and narrower than the work area, so there is room to stand at an edge
SHORT_BLOCK_PX: tuple[int, int] = (300, 200)
# The ink of a block is black, and every sample below this is ink
INK_LIMIT: int = 128
# The sheet a block of text lies on in the tests that find the box themselves, and where the block lies on it
SHEET_SIZE_PX: tuple[int, int] = (1_200, 1_600)
BLOCK_AT_PX: tuple[int, int] = (300, 400)
# How far the box a search finds may be from the ink of the block, which it keeps a few pixels of room round
SEARCH_TOLERANCE_PX: float = 10.0
HALF_SCALE: float = 0.5
LINE_HEIGHT: str = NormalizeParam.LINE_HEIGHT
TARGET_LINE_PX: float = float(LINE_PITCH_PX)
PAPER_COLOUR: tuple[int, int, int] = (238, 226, 190)
SIDES: tuple[PageSide, ...] = (PageSide.LEFT, PageSide.RIGHT)
SIDE_ARG: str = 'side'


def text_block(scale: float = 1.0) -> Image.Image:
    """Draw the block of text of a page, as tight as the crop cuts it, and scale it as a photograph would.

    :param scale: Size of the block over its size at the target line height.
    :type scale: float
    :returns: The gray block, which has ink at each of its four edges.
    :rtype: Image.Image
    """
    page = text_page(500, 700)
    ink = Image.eval(page, lambda value: PAPER - value)
    block = page.crop(ink.getbbox())
    return block.resize((round(block.width * scale), round(block.height * scale)), Image.Resampling.LANCZOS)


def solid_block(width: int, height: int) -> Image.Image:
    """Draw a block that is all ink, whose box is the box of the ink on the page it is put on.

    :param width: Width of the block in pixels.
    :type width: int
    :param height: Height of the block in pixels.
    :type height: int
    :returns: The gray block.
    :rtype: Image.Image
    """
    return Image.new('L', (width, height), INK)


def ink_box(path: Path) -> tuple[int, int, int, int]:
    """Find the box of the ink of a page.

    :param path: Image of the page.
    :type path: Path
    :returns: Left, top, right and bottom of the pixels that are ink, the right and the bottom being outside the box.
    :rtype: tuple[int, int, int, int]
    """
    with Image.open(path) as image:
        samples = np.asarray(image.convert('L'))
    rows, columns = np.nonzero(samples < INK_LIMIT)
    return int(columns.min()), int(rows.min()), int(columns.max()) + 1, int(rows.max()) + 1


def crop_facts(block: Image.Image, scale: float = 1.0) -> MetadataMap:
    """Give the data of a version a crop made of a block as tight as the crop cuts it, which is the input of the step.

    :param block: The block of text, which is the whole image the crop cut.
    :type block: Image.Image
    :param scale: Size of the image over the size of the full image, 1 for a full run.
    :type scale: float
    :returns: The size of the image and the frame of the crop, which is the whole of it, in the pixels of the full image.
    :rtype: MetadataMap
    """
    frame = Rect(left=0, top=0, width=block.width / scale, height=block.height / scale)
    return {VersionData.WIDTH_PX: block.width, VersionData.HEIGHT_PX: block.height, VersionData.FRAME: frame.to_data()}


def sheet_with(block: Image.Image) -> Image.Image:
    """Lay a block of text on a sheet of white paper, as the page of a book without Select content is.

    :param block: The block of text.
    :type block: Image.Image
    :returns: The sheet, which is larger than the block and has paper on every side of it.
    :rtype: Image.Image
    """
    sheet = Image.new('L', SHEET_SIZE_PX, PAPER)
    sheet.paste(block, BLOCK_AT_PX)
    return sheet


def normalized(
    processor: Processor,
    block: Image.Image,
    workdir: Path,
    params: MetadataMap | None = None,
    **extras: Unpack[StepExtras],
) -> StepOutput:
    """Run the step on a block the crop cut, with the page of the tests and the parameters a test changes.

    The input carries the frame of the crop, which is the whole block, unless a test gives other facts.

    :param processor: The processor under test.
    :type processor: Processor
    :param block: The block of text.
    :type block: Image.Image
    :param workdir: Directory the step writes into.
    :type workdir: Path
    :param params: Parameters that differ from the page of the tests.
    :type params: MetadataMap | None
    :param extras: The side, the edit, the facts or the scale, each when the test gives it.
    :type extras: Unpack[StepExtras]
    :returns: The output.
    :rtype: StepOutput
    """
    extras['facts'] = {**SCAN, **crop_facts(block, extras.get('scale', 1.0)), **extras.get('facts', {})}
    return run_on(processor, save(block, workdir / BLOCK_NAME), workdir, params={**PAGE, **(params or {})}, **extras)


class TestNormalize:
    """Tests for Normalize."""

    @pytest.mark.parametrize('scale', SCALES)
    def test_pages_at_different_scales_have_one_size_and_one_line_height(
        self, fx_normalize: Processor, tmp_path: Path, scale: float
    ) -> None:
        """Verify blocks at 0.8, 1 and 1.2 of the size land on one page size with the target line height.

        The lines stand at the target distance within 2 percent.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param scale: Size of the block over its size at the target line height.
        :type scale: float
        """
        # A block drawn larger is a scan of the same paper at a higher resolution
        output = normalized(
            fx_normalize,
            text_block(scale),
            tmp_path,
            params={LINE_HEIGHT: TARGET_LINE_PX},
            facts={VersionData.DPI: TEST_DPI * scale},
        )
        assert output.image is not None
        frame = Rect.from_data(output.data[VersionData.FRAME])
        with Image.open(output.image) as page:
            placed = page.convert('L').crop(
                (round(frame.left), round(frame.top), round(frame.left + frame.width), round(frame.top + frame.height))
            )
            expect(page.size == (PAGE_WIDTH_PX, PAGE_HEIGHT_PX))
        pitch = line_pitch(np.asarray(placed))
        expect(pitch is not None and abs(pitch - TARGET_LINE_PX) <= LINE_TOLERANCE * TARGET_LINE_PX)
        expect(
            (output.data[VersionData.WIDTH_PX], output.data[VersionData.HEIGHT_PX]) == (PAGE_WIDTH_PX, PAGE_HEIGHT_PX)
        )
        expect(output.data[VersionData.LINE_HEIGHT_PX] == pytest.approx(TARGET_LINE_PX, rel=LINE_TOLERANCE))
        expect(output.review is None)
        expect(output.transform.kind is TransformKind.PLACE)
        assert_expectations()

    def test_the_line_height_the_crop_recorded_is_used_without_measuring_again(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the distance the crop wrote into the data decides the scale, so a step after it does not search again.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        output = normalized(
            fx_normalize,
            block,
            tmp_path,
            params={LINE_HEIGHT: TARGET_LINE_PX},
            facts={VersionData.LINE_HEIGHT_PX: TARGET_LINE_PX * 1.1},
        )
        frame = Rect.from_data(output.data[VersionData.FRAME])
        expect(frame.width == pytest.approx(block.width / 1.1, rel=0.001))
        assert_expectations()

    @pytest.mark.parametrize(
        ('given', 'review'),
        [(1.2, None), (0.8, None), (1.5, ReviewReason.TEXT_SIZE), (0.6, ReviewReason.TEXT_SIZE)],
        ids=['larger-within', 'smaller-within', 'larger-beyond', 'smaller-beyond'],
    )
    def test_a_change_of_size_beyond_the_limit_is_not_made_and_marks_the_page(
        self, fx_normalize: Processor, tmp_path: Path, given: float, review: ReviewReason | None
    ) -> None:
        """Verify text within the limit of the target is scaled to it and text beyond the limit is marked.

        Text beyond the limit is placed as it is and marked for review.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param given: Line height of the page over the target.
        :type given: float
        :param review: Why the page is marked, or None.
        :type review: ReviewReason | None
        """
        block = text_block(given)
        output = normalized(fx_normalize, block, tmp_path, params={LINE_HEIGHT: TARGET_LINE_PX})
        frame = Rect.from_data(output.data[VersionData.FRAME])
        expect(output.review is review)
        if review is None:
            expect(frame.width == pytest.approx(block.width / given, rel=LINE_TOLERANCE))
        else:
            expect(frame.width == pytest.approx(block.width))
        assert_expectations()

    def test_a_line_height_of_zero_keeps_the_size_of_the_text(self, fx_normalize: Processor, tmp_path: Path) -> None:
        """Verify the default, which asks for no scaling, places the block as it is and marks nothing.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block(1.3)
        output = normalized(fx_normalize, block, tmp_path)
        expect(Rect.from_data(output.data[VersionData.FRAME]).width == pytest.approx(block.width))
        expect(output.review is None)
        assert_expectations()

    def test_a_block_with_no_lines_to_measure_is_marked_when_a_line_height_is_asked_for(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify a block the lines of which cannot be found is placed as it is and marked as not sure.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        output = normalized(fx_normalize, solid_block(*SHORT_BLOCK_PX), tmp_path, params={LINE_HEIGHT: TARGET_LINE_PX})
        expect(output.review is ReviewReason.LOW_CONFIDENCE)
        expect(Rect.from_data(output.data[VersionData.FRAME]).width == pytest.approx(SHORT_BLOCK_PX[0]))
        assert_expectations()

    def test_the_inner_and_outer_margins_of_the_two_pages_of_a_spread_mirror(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the inner and outer margins of a right page and a left page mirror each other.

        A block by the gutter is at the inner margin from the left on a right page and from the right on a left page,
        and the one by the outer edge is at the outer margin on the other side.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = solid_block(*SHORT_BLOCK_PX)
        boxes = {}
        for side in SIDES:
            for align in (HorizontalAlign.INNER, HorizontalAlign.OUTER):
                output = normalized(fx_normalize, block, tmp_path, params={ALIGN_X: align}, side=side)
                assert output.image is not None
                boxes[side, align] = ink_box(output.image)
        right_page, left_page = PageSide.RIGHT, PageSide.LEFT
        inner, outer = HorizontalAlign.INNER, HorizontalAlign.OUTER
        expect(boxes[right_page, inner][0] == MARGIN_INNER_PX)
        expect(PAGE_WIDTH_PX - boxes[left_page, inner][2] == MARGIN_INNER_PX)
        expect(PAGE_WIDTH_PX - boxes[right_page, outer][2] == MARGIN_OUTER_PX)
        expect(boxes[left_page, outer][0] == MARGIN_OUTER_PX)
        # The two pages are mirror images of each other
        expect(boxes[right_page, inner][0] == PAGE_WIDTH_PX - boxes[left_page, inner][2])
        assert_expectations()

    def test_left_and_right_margins_are_absolute_whatever_the_side_of_the_page(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify margins by left and right are absolute on a left page as on a right page.

        The inner margin is the left one and the outer margin the right one.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = solid_block(*SHORT_BLOCK_PX)
        for side in SIDES:
            boxes = {}
            for align in (HorizontalAlign.LEFT, HorizontalAlign.RIGHT):
                output = normalized(
                    fx_normalize,
                    block,
                    tmp_path,
                    params={MARGINS_BY: MarginsBy.LEFT_RIGHT, ALIGN_X: align},
                    side=side,
                )
                assert output.image is not None
                # The step writes the page to the same file every time, so it is read before the next run
                boxes[align] = ink_box(output.image)
            expect(boxes[HorizontalAlign.LEFT][0] == MARGIN_INNER_PX)
            expect(PAGE_WIDTH_PX - boxes[HorizontalAlign.RIGHT][2] == MARGIN_OUTER_PX)
        assert_expectations()

    @pytest.mark.parametrize(
        ('vertical', 'expected'),
        [
            (VerticalAlign.TOP, MARGIN_TOP_PX),
            (
                VerticalAlign.CENTER,
                MARGIN_TOP_PX + (PAGE_HEIGHT_PX - MARGIN_TOP_PX - MARGIN_BOTTOM_PX - SHORT_BLOCK_PX[1]) / 2,
            ),
            (VerticalAlign.BOTTOM, PAGE_HEIGHT_PX - MARGIN_BOTTOM_PX - SHORT_BLOCK_PX[1]),
        ],
        ids=[member.value for member in VerticalAlign],
    )
    def test_every_vertical_alignment_puts_a_short_block_at_its_edge(
        self, fx_normalize: Processor, tmp_path: Path, vertical: VerticalAlign, expected: float
    ) -> None:
        """Verify a block lower than the work area stands at the top, in the middle or at the bottom of it.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param vertical: The vertical alignment.
        :type vertical: VerticalAlign
        :param expected: Distance of the top of the block from the top of the page in pixels.
        :type expected: float
        """
        output = normalized(fx_normalize, solid_block(*SHORT_BLOCK_PX), tmp_path, params={ALIGN_Y: vertical})
        assert output.image is not None
        _, top, _, bottom = ink_box(output.image)
        expect(abs(top - expected) <= EDGE_TOLERANCE_PX)
        expect(bottom - top == SHORT_BLOCK_PX[1])
        assert_expectations()

    @pytest.mark.parametrize(SIDE_ARG, SIDES, ids=[side.value for side in SIDES])
    @pytest.mark.parametrize('horizontal', list(HorizontalAlign), ids=[member.value for member in HorizontalAlign])
    def test_every_horizontal_alignment_puts_a_short_block_at_its_edge(
        self, fx_normalize: Processor, tmp_path: Path, horizontal: HorizontalAlign, side: PageSide
    ) -> None:
        """Verify a block narrower than the work area stands at the edge the alignment names on a page of either side.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param horizontal: The horizontal alignment.
        :type horizontal: HorizontalAlign
        :param side: Side of the book the page lies on.
        :type side: PageSide
        """
        output = normalized(
            fx_normalize, solid_block(*SHORT_BLOCK_PX), tmp_path, params={ALIGN_X: horizontal}, side=side
        )
        assert output.image is not None
        left, _, right, _ = ink_box(output.image)
        gutter_on_left = side is PageSide.RIGHT
        left_margin = MARGIN_INNER_PX if gutter_on_left else MARGIN_OUTER_PX
        right_margin = MARGIN_OUTER_PX if gutter_on_left else MARGIN_INNER_PX
        # Where the left edge of the block stands when the block is by the left margin and when it is by the right one
        by_left = left_margin
        by_right = PAGE_WIDTH_PX - right_margin - SHORT_BLOCK_PX[0]
        by_edge = {
            HorizontalAlign.INNER: by_left if gutter_on_left else by_right,
            HorizontalAlign.OUTER: by_right if gutter_on_left else by_left,
            HorizontalAlign.LEFT: by_left,
            HorizontalAlign.RIGHT: by_right,
            HorizontalAlign.CENTER: (by_left + by_right) / 2,
        }
        expect(abs(left - by_edge[horizontal]) <= EDGE_TOLERANCE_PX)
        expect(right - left == SHORT_BLOCK_PX[0])
        assert_expectations()

    def test_the_rest_of_the_page_is_the_median_colour_of_the_paper_or_white(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the page round the block is filled with the paper of the block, or with white when asked.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        grey = np.asarray(text_block().convert('L'))
        block = Image.fromarray(np.where(grey[..., None] < INK_LIMIT, 0, PAPER_COLOUR).astype(np.uint8))
        for fill, colour in ((PaperFill.PAPER, PAPER_COLOUR), (PaperFill.WHITE, (PAPER,) * 3)):
            output = normalized(fx_normalize, block, tmp_path, params={'fill': fill})
            assert output.image is not None
            with Image.open(output.image) as page:
                expect(page.getpixel((2, 2)) == colour)
                expect(page.getpixel((PAGE_WIDTH_PX - 3, PAGE_HEIGHT_PX - 3)) == colour)
            expect(output.color_mode is ColorMode.COLOR)
        assert_expectations()

    def test_a_bilevel_page_stays_bilevel_when_it_is_scaled(self, fx_normalize: Processor, tmp_path: Path) -> None:
        """Verify scaling a black-and-white block makes no gray.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block().convert('1').convert('L')
        output = normalized(fx_normalize, block, tmp_path, params={LINE_HEIGHT: TARGET_LINE_PX * 1.1})
        assert output.image is not None
        with Image.open(output.image) as page:
            colours = {value for _count, value in page.convert('L').getcolors() or []}
        expect(colours <= {INK, PAPER})
        expect(output.color_mode is ColorMode.BILEVEL)
        assert_expectations()

    def test_a_preview_gives_a_page_of_the_size_of_the_preview_and_the_frame_of_the_full_page(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify a block at half size is put on a page of half the size, and the frame is told in full pixels.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        full = normalized(fx_normalize, block, tmp_path, params={LINE_HEIGHT: TARGET_LINE_PX})
        half_block = block.resize((block.width // 2, block.height // 2), Image.Resampling.LANCZOS)
        half = normalized(fx_normalize, half_block, tmp_path, params={LINE_HEIGHT: TARGET_LINE_PX}, scale=HALF_SCALE)
        found, truth = Rect.from_data(half.data[VersionData.FRAME]), Rect.from_data(full.data[VersionData.FRAME])
        expect(
            (half.data[VersionData.WIDTH_PX], half.data[VersionData.HEIGHT_PX])
            == (PAGE_WIDTH_PX // 2, PAGE_HEIGHT_PX // 2)
        )
        expect(found.left == pytest.approx(truth.left, abs=2))
        expect(found.top == pytest.approx(truth.top, abs=2))
        expect(found.width == pytest.approx(truth.width, rel=0.02))
        # The size of the image the step read is told in the pixels of the full image, which the editor is drawn on
        expect(half.data[VersionData.SOURCE_WIDTH_PX] == round(half_block.width / HALF_SCALE))
        expect(half.data[VersionData.SOURCE_HEIGHT_PX] == round(half_block.height / HALF_SCALE))
        assert_expectations()

    def test_the_transform_carries_a_point_of_the_block_to_its_place_on_the_page(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the corners of the box go to the corners of its place on the page, and back, whatever lies round it.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        edit = ContentBox(left=100, top=200, width=SHORT_BLOCK_PX[0], height=SHORT_BLOCK_PX[1])
        sheet = Image.new('L', SHEET_SIZE_PX, PAPER)
        sheet.paste(solid_block(*SHORT_BLOCK_PX), (100, 200))
        output = run_on(fx_normalize, save(sheet, tmp_path / BLOCK_NAME), tmp_path, params=PAGE, facts=SCAN, edit=edit)
        place = Rect.from_data(output.data[VersionData.FRAME])
        corner = output.transform.to_output(Point(x=edit.left + edit.width, y=edit.top + edit.height))
        back = output.transform.to_input(Point(x=place.left, y=place.top))
        expect(corner.x == pytest.approx(place.left + place.width, abs=1))
        expect(corner.y == pytest.approx(place.top + place.height, abs=1))
        expect((back.x, back.y) == pytest.approx((edit.left, edit.top), abs=1))
        assert_expectations()

    def test_the_side_of_the_page_is_what_the_spec_asks_for(self, fx_normalize: Processor) -> None:
        """Verify what the service reads: the key, the stage, the editor and that the step depends on the page side.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        """
        spec = fx_normalize.spec
        expect((spec.key, spec.stage, spec.scope) == ('geometry.normalize', Stage.GEOMETRY, ProcessorScope.PAGE))
        expect(spec.editor is EditorKind.CONTENT_BOX)
        expect(spec.by_page_side)
        assert_expectations()

    def test_the_parameters_have_a_name_for_every_one_measuring_the_book_writes(self, fx_normalize: Processor) -> None:
        """Verify the names measuring the book writes are parameters of the step, so a write is never refused.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        """
        names = set(fx_normalize.spec.parameters['properties'])
        expect({member.value for member in NormalizeParam} <= names)
        expect(fx_normalize.validate_params({}).keys() == names)
        assert_expectations()

    @pytest.mark.parametrize(
        'raw',
        [
            {NormalizeParam.PAGE_WIDTH: 10},
            {NormalizeParam.MARGIN_TOP: -1},
            {NormalizeParam.MARGIN_TOP: 51},
            {'max_scale_change': 150},
            {ALIGN_Y: 'middle'},
            {MARGINS_BY: 'both'},
            {'margin': 5},
        ],
        ids=[
            'page-too-small',
            'negative-margin',
            'margin-beyond-the-bound',
            'change-over-a-hundred-percent',
            'unknown-vertical-alignment',
            'unknown-margins',
            'unknown-parameter',
        ],
    )
    def test_parameters_that_do_not_fit_the_schema_are_rejected(
        self, fx_normalize: Processor, raw: MetadataMap
    ) -> None:
        """Reject a page that is too small, a margin out of its range, and a name it lacks.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param raw: Parameters under test.
        :type raw: MetadataMap
        """
        with pytest.raises(InvalidParametersError, match=KEY_PATTERN):
            fx_normalize.validate_params({**PAGE, **raw})

    def test_a_missing_image_is_refused(self, fx_normalize: Processor, tmp_path: Path) -> None:
        """Verify a step with no image says so.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match=KEY_PATTERN):
            fx_normalize.run(StepInput(image=None, params=fx_normalize.validate_params({}), workdir=tmp_path))


class TestNormalizeMillimetres:
    """Tests for the margins as lengths of the paper: millimetres turned into pixels by the resolution of the page."""

    BLOCK_PX: ClassVar[tuple[int, int]] = (300, 200)
    EVEN_MARGINS: ClassVar[MetadataMap] = {
        NormalizeParam.PAGE_WIDTH: 0,
        NormalizeParam.PAGE_HEIGHT: 0,
        NormalizeParam.MARGIN_TOP: 10.0,
        NormalizeParam.MARGIN_BOTTOM: 10.0,
        NormalizeParam.MARGIN_INNER: 10.0,
        NormalizeParam.MARGIN_OUTER: 10.0,
    }

    @pytest.mark.parametrize(('dpi', 'pixels'), [(300.0, 118), (600.0, 236)], ids=['300-dpi', '600-dpi'])
    def test_ten_millimetres_are_the_pixels_of_the_resolution(
        self, fx_normalize: Processor, tmp_path: Path, dpi: float, pixels: int
    ) -> None:
        """Verify a margin of 10 mm is 118 pixels at 300 dpi and 236 pixels at 600 dpi on every side of the box.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param dpi: Resolution of the input.
        :type dpi: float
        :param pixels: The margin in pixels the resolution gives.
        :type pixels: int
        """
        width, height = self.BLOCK_PX
        output = normalized(
            fx_normalize,
            solid_block(width, height),
            tmp_path,
            params=self.EVEN_MARGINS,
            facts={VersionData.DPI: dpi},
            side=PageSide.RIGHT,
        )
        assert output.image is not None
        left, top, right, bottom = ink_box(output.image)
        # The box is centred in the room the margins leave, which is a pixel wider than the box at most
        expect((left, top) == pytest.approx((pixels, pixels), abs=1))
        expect(output.data[VersionData.WIDTH_PX] == pytest.approx(right + pixels, abs=1))
        expect(output.data[VersionData.HEIGHT_PX] == pytest.approx(bottom + pixels, abs=1))
        expect(output.data[VersionData.MARGIN_PIXELS_PER_MM] == pytest.approx(dpi / 25.4))
        assert_expectations()

    def test_two_pages_of_one_book_at_different_resolutions_have_equal_margins_in_millimetres(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify a scan at 300 dpi and the same paper scanned at 360 dpi get the same margins once the text is scaled.

        The text of both is brought to one line height, so the pages are made of the same pixels, and the margin of 10 mm
        is the same number of them. The larger scan is the largest the change of size the step allows.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        margins = []
        for scale, dpi in ((1.0, 300.0), (1.2, 360.0)):
            output = normalized(
                fx_normalize,
                text_block(scale),
                tmp_path,
                params={**self.EVEN_MARGINS, LINE_HEIGHT: TARGET_LINE_PX},
                facts={VersionData.DPI: dpi},
                side=PageSide.RIGHT,
            )
            assert output.image is not None
            margins.append(ink_box(output.image)[:2])
        expect(margins[0] == pytest.approx(margins[1], abs=3))
        expect(margins[0] == pytest.approx((118, 118), abs=3))
        assert_expectations()

    def test_a_page_with_no_resolution_takes_a_share_of_the_width_of_the_box(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify with no resolution the box is taken for a block of 100 mm, so 10 mm is a tenth of its width.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        width, height = self.BLOCK_PX
        block = solid_block(width, height)
        output = run_on(
            fx_normalize,
            save(block, tmp_path / BLOCK_NAME),
            tmp_path,
            params=self.EVEN_MARGINS,
            facts=crop_facts(block),
            side=PageSide.RIGHT,
        )
        assert output.image is not None
        share = width / 10
        expect(ink_box(output.image)[:2] == pytest.approx((share, share), abs=1))
        expect(output.data[VersionData.WIDTH_PX] == width + 2 * share)
        expect(output.data[VersionData.MARGIN_PIXELS_PER_MM] == pytest.approx(width / 100))
        assert_expectations()

    def test_a_page_of_the_book_with_no_resolution_takes_the_scale_from_its_width(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the pages of a book of the size by the book get one scale whatever the box of each is.

        The page is 1200 pixels wide and holds 100 mm of box and 20 mm of margins, so a millimetre is 10 pixels, for a
        narrow box and for a wide one.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        lefts = []
        for width in (200, 500):
            block = solid_block(width, 300)
            output = run_on(
                fx_normalize,
                save(block, tmp_path / BLOCK_NAME),
                tmp_path,
                params={
                    **self.EVEN_MARGINS,
                    NormalizeParam.PAGE_WIDTH: 1_200,
                    NormalizeParam.PAGE_HEIGHT: 1_000,
                    ALIGN_X: HorizontalAlign.LEFT,
                },
                facts=crop_facts(block),
                side=PageSide.RIGHT,
            )
            assert output.image is not None
            lefts.append(ink_box(output.image)[0])
        expect(lefts == [100, 100])
        assert_expectations()

    def test_margins_that_take_the_whole_page_are_refused(self, fx_normalize: Processor, tmp_path: Path) -> None:
        """Verify the margins of a page of the size given that leave no room, at the resolution of the page, are refused.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        with pytest.raises(ConflictError, match='no room'):
            normalized(
                fx_normalize,
                solid_block(*self.BLOCK_PX),
                tmp_path,
                params={**self.EVEN_MARGINS, NormalizeParam.PAGE_WIDTH: 200, NormalizeParam.PAGE_HEIGHT: 400},
                facts={VersionData.DPI: 300.0},
            )


class TestNormalizeContentBox:
    """Tests for the box of the content the step places: found on the sheet, drawn by the user, or missing."""

    def test_a_sheet_with_no_crop_before_it_places_the_block_of_text_and_not_the_sheet(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the step finds the block on a sheet when no Select content cut it, and puts that block on the page.

        The sheet is much larger than the block, and the block is at the target line height, so it is placed as it is
        at the top margin and its width is the width it has on the sheet.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        sheet = sheet_with(block)
        output = run_on(
            fx_normalize,
            save(sheet, tmp_path / BLOCK_NAME),
            tmp_path,
            params={**PAGE, LINE_HEIGHT: TARGET_LINE_PX, ALIGN_Y: VerticalAlign.TOP},
            facts=SCAN,
        )
        assert output.image is not None
        left, top, right, bottom = ink_box(output.image)
        found = Rect.from_data(output.data[VersionData.CONTENT_BOX])
        expect(abs((right - left) - block.width) <= SEARCH_TOLERANCE_PX)
        expect(abs((bottom - top) - block.height) <= SEARCH_TOLERANCE_PX)
        expect(abs(top - MARGIN_TOP_PX) <= SEARCH_TOLERANCE_PX)
        expect(left >= MARGIN_OUTER_PX and right <= PAGE_WIDTH_PX - MARGIN_OUTER_PX)
        expect(abs(found.left - BLOCK_AT_PX[0]) <= SEARCH_TOLERANCE_PX)
        expect(abs(found.top - BLOCK_AT_PX[1]) <= SEARCH_TOLERANCE_PX)
        expect(abs(found.width - block.width) <= SEARCH_TOLERANCE_PX)
        expect(output.data[VersionData.BLOCK_SCALE] == pytest.approx(1.0, rel=LINE_TOLERANCE))
        expect(output.review is None)
        expect(output.data[VersionData.SOURCE_WIDTH_PX] == SHEET_SIZE_PX[0])
        assert_expectations()

    def test_a_sheet_with_no_ink_is_placed_whole_and_unscaled_and_marked(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify a page with no content to find a box by is not scaled, and says it was not sure.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        blank = Image.new('L', SHEET_SIZE_PX, PAPER)
        output = run_on(
            fx_normalize,
            save(blank, tmp_path / BLOCK_NAME),
            tmp_path,
            params={**PAGE, LINE_HEIGHT: TARGET_LINE_PX},
            facts=SCAN,
        )
        expect(output.review is ReviewReason.NOT_APPLIED)
        expect(Rect.from_data(output.data[VersionData.FRAME]).width == pytest.approx(SHEET_SIZE_PX[0]))
        expect(VersionData.CONTENT_BOX not in output.data)
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(0.0))
        assert_expectations()

    def test_the_box_of_the_user_replaces_the_one_that_is_found_and_the_rest_of_the_sheet_is_dropped(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify a content box edit is the box that is placed, with full confidence and no review.

        The edit takes the left half of the block of text, so the page holds that half and no more.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        sheet = sheet_with(block)
        edit = ContentBox(left=BLOCK_AT_PX[0], top=BLOCK_AT_PX[1], width=block.width // 2, height=block.height)
        output = run_on(
            fx_normalize,
            save(sheet, tmp_path / BLOCK_NAME),
            tmp_path,
            params={**PAGE, LINE_HEIGHT: TARGET_LINE_PX},
            facts=SCAN,
            edit=edit,
        )
        assert output.image is not None
        left, _top, right, _bottom = ink_box(output.image)
        expect(Rect.from_data(output.data[VersionData.CONTENT_BOX]) == Rect(**edit.to_data()))
        expect(right - left <= edit.width + EDGE_TOLERANCE_PX)
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(1.0))
        expect(output.review is None)
        assert_expectations()

    def test_a_frame_of_the_old_kind_is_not_a_box_of_the_content(self, fx_normalize: Processor, tmp_path: Path) -> None:
        """Verify a rectangle that is no content box, such as a place of a block on the page saved earlier, is ignored.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        output = run_on(
            fx_normalize,
            save(sheet_with(block), tmp_path / BLOCK_NAME),
            tmp_path,
            params={**PAGE, LINE_HEIGHT: TARGET_LINE_PX},
            facts=SCAN,
            edit=Rect(left=0, top=0, width=50, height=50),
        )
        expect(abs(Rect.from_data(output.data[VersionData.CONTENT_BOX]).width - block.width) <= SEARCH_TOLERANCE_PX)
        assert_expectations()

    def test_the_box_grown_by_the_margins_is_recorded_in_the_pixels_of_the_input(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the box grown by the margins, which an editor draws on the input, is the box and the margins over the scale.

        The text is a fifth larger than the target, so the box is scaled down by that much and each margin, which is in
        pixels of the page, is that much larger in pixels of the input.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        given = 1.2
        block = text_block(given)
        output = normalized(
            fx_normalize,
            block,
            tmp_path,
            params={LINE_HEIGHT: TARGET_LINE_PX},
            facts={VersionData.DPI: TEST_DPI * given},
            side=PageSide.RIGHT,
        )
        grown = Rect.from_data(output.data[VersionData.MARGIN_BOX])
        factor = output.data[VersionData.BLOCK_SCALE]
        expect(factor == pytest.approx(1 / given, rel=LINE_TOLERANCE))
        # The right page has its gutter on the left, where the inner margin is
        expect(grown.left == pytest.approx(-MARGIN_INNER_PX / factor, abs=1))
        expect(grown.top == pytest.approx(-MARGIN_TOP_PX / factor, abs=1))
        expect(grown.width == pytest.approx(block.width + (MARGIN_INNER_PX + MARGIN_OUTER_PX) / factor, abs=1))
        expect(grown.height == pytest.approx(block.height + (MARGIN_TOP_PX + MARGIN_BOTTOM_PX) / factor, abs=1))
        assert_expectations()


class TestNormalizeUncutInput:
    """Tests for the input of Select content, which records the frame of the content and cuts nothing."""

    def test_the_block_is_cut_out_of_the_whole_page_by_the_frame_select_content_recorded(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the page Select content handed on whole is cut by its frame here, and the frame is not searched again.

        The frame is the left half of the block, which no search would find, so a page holding that half and no more
        shows the frame was read from the data of the input.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        sheet = sheet_with(block)
        frame = Rect(left=BLOCK_AT_PX[0], top=BLOCK_AT_PX[1], width=block.width // 2, height=block.height)
        facts: MetadataMap = {
            **SCAN,
            VersionData.WIDTH_PX: sheet.width,
            VersionData.HEIGHT_PX: sheet.height,
            VersionData.FRAME: frame.to_data(),
            VersionData.CONTENT_FRAME: frame.to_data(),
            VersionData.CONFIDENCE: 0.9,
        }
        output = run_on(
            fx_normalize,
            save(sheet, tmp_path / BLOCK_NAME),
            tmp_path,
            params={**PAGE, LINE_HEIGHT: TARGET_LINE_PX},
            facts=facts,
        )
        assert output.image is not None
        left, _top, right, _bottom = ink_box(output.image)
        expect(Rect.from_data(output.data[VersionData.CONTENT_BOX]) == frame)
        expect(right - left <= frame.width + EDGE_TOLERANCE_PX)
        expect(output.data[VersionData.CONFIDENCE] == pytest.approx(0.9))
        expect(output.data[VersionData.SOURCE_WIDTH_PX] == sheet.width)
        assert_expectations()

    def test_a_preview_of_the_whole_page_takes_the_frame_in_the_pixels_of_the_preview(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the frame in the pixels of the full image is brought to a preview of the page that was not cut.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        sheet = sheet_with(block)
        half = sheet.resize((sheet.width // 2, sheet.height // 2), Image.Resampling.LANCZOS)
        frame = Rect(left=BLOCK_AT_PX[0], top=BLOCK_AT_PX[1], width=block.width, height=block.height)
        facts: MetadataMap = {
            **SCAN,
            VersionData.WIDTH_PX: half.width,
            VersionData.HEIGHT_PX: half.height,
            VersionData.CONTENT_FRAME: frame.to_data(),
        }
        output = run_on(
            fx_normalize,
            save(half, tmp_path / BLOCK_NAME),
            tmp_path,
            params={**PAGE, LINE_HEIGHT: TARGET_LINE_PX},
            facts=facts,
            scale=HALF_SCALE,
        )
        found = Rect.from_data(output.data[VersionData.CONTENT_BOX])
        expect(found.left == pytest.approx(frame.left, abs=1))
        expect(found.width == pytest.approx(frame.width, abs=1))
        assert_expectations()

    def test_the_block_stands_on_the_page_as_the_content_frame_of_the_steps_after_it(
        self, fx_normalize: Processor, tmp_path: Path
    ) -> None:
        """Verify the frame the step records for the cleanup is the place of the block on the page, not the one of its input.

        The margins at the two sides differ, so the block is not in the middle of the page, and a frame worked out from
        the size of the page would be wrong.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        block = text_block()
        sheet = sheet_with(block)
        frame = Rect(left=BLOCK_AT_PX[0], top=BLOCK_AT_PX[1], width=block.width, height=block.height)
        facts: MetadataMap = {
            **SCAN,
            VersionData.WIDTH_PX: sheet.width,
            VersionData.HEIGHT_PX: sheet.height,
            VersionData.CONTENT_FRAME: frame.to_data(),
        }
        output = run_on(
            fx_normalize,
            save(sheet, tmp_path / BLOCK_NAME),
            tmp_path,
            params={**PAGE, LINE_HEIGHT: TARGET_LINE_PX, ALIGN_X: HorizontalAlign.OUTER},
            facts=facts,
            side=PageSide.RIGHT,
        )
        assert output.image is not None
        placed = Rect.from_data(output.data[VersionData.CONTENT_FRAME])
        left, top, right, bottom = ink_box(output.image)
        expect(output.data[VersionData.CONTENT_FRAME] == output.data[VersionData.FRAME])
        expect(abs(placed.left - left) <= SEARCH_TOLERANCE_PX)
        expect(abs(placed.top - top) <= SEARCH_TOLERANCE_PX)
        expect(abs(placed.left + placed.width - right) <= SEARCH_TOLERANCE_PX)
        expect(abs(placed.top + placed.height - bottom) <= SEARCH_TOLERANCE_PX)
        assert_expectations()


class TestNormalizePageOfTheBlock:
    """Tests for the page Normalize makes before the book gives it a size, which is the block and its margins."""

    @pytest.mark.parametrize(SIDE_ARG, SIDES, ids=[side.value for side in SIDES])
    def test_a_page_of_size_zero_is_the_block_and_its_margins(
        self, fx_normalize: Processor, tmp_path: Path, side: PageSide
    ) -> None:
        """Verify the page fits the block with each margin round it, the inner one at the gutter of the side.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param side: Side of the book the page lies on.
        :type side: PageSide
        """
        width, height = SHORT_BLOCK_PX
        output = normalized(
            fx_normalize,
            solid_block(width, height),
            tmp_path,
            params={NormalizeParam.PAGE_WIDTH: 0, NormalizeParam.PAGE_HEIGHT: 0},
            side=side,
        )
        left = MARGIN_OUTER_PX if side is PageSide.LEFT else MARGIN_INNER_PX
        assert output.image is not None
        expect(
            (output.data[VersionData.WIDTH_PX], output.data[VersionData.HEIGHT_PX])
            == (width + MARGIN_INNER_PX + MARGIN_OUTER_PX, height + MARGIN_TOP_PX + MARGIN_BOTTOM_PX)
        )
        expect(ink_box(output.image) == (left, MARGIN_TOP_PX, left + width, MARGIN_TOP_PX + height))
        assert_expectations()

    def test_the_default_page_is_by_the_book_until_the_book_gives_it_a_size(self, fx_normalize: Processor) -> None:
        """Verify the size of the page is 0 by default, which the parameters accept whatever the margins.

        :param fx_normalize: The processor under test.
        :type fx_normalize: Processor
        """
        params = fx_normalize.validate_params({})
        expect((params[NormalizeParam.PAGE_WIDTH], params[NormalizeParam.PAGE_HEIGHT]) == (0, 0))
        assert_expectations()
