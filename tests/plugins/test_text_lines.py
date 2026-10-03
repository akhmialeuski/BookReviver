"""Tests for the search of the lines of text of a page, which the dewarping and the baselines of deskewing share.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from typing import cast

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from tests.helpers.samples import CV_MISSING, LINE_PITCH_PX, MARGIN_PX, text_page
from tests.plugins.synthetic import bend_columns

PAGE_SIZE_PX: tuple[int, int] = (900, 1_200)
# The lines a page of this size holds, from the first row of words to the last that fits above the margin
LINES_ON_PAGE: int = len(range(MARGIN_PX, PAGE_SIZE_PX[1] - MARGIN_PX, LINE_PITCH_PX))
# How far the places of the points may be from where the lines were drawn, in pixels
PLACE_TOLERANCE_PX: float = 1.0
# The width of a page that is larger than the one the lines are searched on, and the grain of a cover
LARGE_WIDTH_PX: int = 2_700
GRAIN_SEED: int = 4
GRAIN_TONE: int = 90
GRAIN_SPREAD: float = 9.0
# The darkness of a block that stands for a picture, and the rows it covers as lines of the page
PICTURE_TONE: int = 90
PICTURE_LINES: int = 10
# The slant in pixels over the width of a line given to test the straight fit
SLANT_PX: float = 12.0
# A point put far from its line, and how far
OUTLIER_INDEX: int = 7
OUTLIER_PX: float = 40.0


@pytest.fixture
def fx_search() -> type:
    """Give the class of the search of lines, or skip the test where OpenCV is not installed.

    :returns: The class ``TextLineSearch``.
    :rtype: type
    """
    return cast('type', pytest.importorskip('bookreviver.plugins.text_lines', reason=CV_MISSING).TextLineSearch)


def samples(image: Image.Image) -> np.ndarray:
    """Read a drawn page as the 8-bit samples the search reads.

    :param image: The page.
    :type image: Image.Image
    :returns: The samples.
    :rtype: np.ndarray
    """
    return np.asarray(image, dtype=np.uint8)


class TestTextLineSearch:
    """Tests for TextLineSearch."""

    def test_finds_every_line_of_a_page_once_from_the_top_to_the_bottom(self, fx_search: type) -> None:
        """Verify one line is found for each line drawn, in order, with the middle of its words as its height.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        lines = fx_search(samples(text_page(*PAGE_SIZE_PX))).find()
        middles = [float(np.median(line.y)) for line in lines]
        drawn = [top + 6 for top in range(MARGIN_PX, PAGE_SIZE_PX[1] - MARGIN_PX, LINE_PITCH_PX)]
        expect(len(lines) == LINES_ON_PAGE)
        expect(all(abs(found - truth) < PLACE_TOLERANCE_PX for found, truth in zip(middles, drawn, strict=True)))
        assert_expectations()

    def test_the_points_of_a_line_run_from_left_to_right_inside_the_page(self, fx_search: type) -> None:
        """Verify the places of the points increase and lie on the page, the first near the left margin.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        [line, *_] = fx_search(samples(text_page(*PAGE_SIZE_PX))).find()
        expect(bool(np.all(np.diff(line.x) > 0)))
        expect(line.left > 0)
        expect(line.right < PAGE_SIZE_PX[0])
        expect(abs(line.left - MARGIN_PX) < MARGIN_PX)
        assert_expectations()

    def test_the_points_are_in_the_pixels_of_the_page_whatever_its_size(self, fx_search: type) -> None:
        """Verify a page three times as wide as the search works on gives the places of its own pixels.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        scale = LARGE_WIDTH_PX // PAGE_SIZE_PX[0]
        large = text_page(*PAGE_SIZE_PX).resize((LARGE_WIDTH_PX, PAGE_SIZE_PX[1] * scale), Image.Resampling.NEAREST)
        lines = fx_search(samples(large)).find()
        drawn = [(top + 6) * scale for top in range(MARGIN_PX, PAGE_SIZE_PX[1] - MARGIN_PX, LINE_PITCH_PX)]
        expect(len(lines) == LINES_ON_PAGE)
        expect(
            all(
                abs(float(np.median(line.y)) - truth) < PLACE_TOLERANCE_PX * scale
                for line, truth in zip(lines, drawn, strict=True)
            )
        )
        assert_expectations()

    def test_a_block_as_tall_as_ten_lines_is_a_picture_and_not_a_line(self, fx_search: type) -> None:
        """Verify a dark block across the page is left out, and the lines above and below it are found.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        page = samples(text_page(*PAGE_SIZE_PX)).copy()
        top = MARGIN_PX + PICTURE_LINES * LINE_PITCH_PX
        page[top : top + PICTURE_LINES * LINE_PITCH_PX, MARGIN_PX : PAGE_SIZE_PX[0] - MARGIN_PX] = PICTURE_TONE
        lines = fx_search(page).find()
        expect(len(lines) < LINES_ON_PAGE - PICTURE_LINES + 2)
        expect(len(lines) >= LINES_ON_PAGE - 2 * PICTURE_LINES)
        expect(all(not top < float(np.median(line.y)) < top + PICTURE_LINES * LINE_PITCH_PX for line in lines))
        assert_expectations()

    def test_a_line_shorter_than_half_the_width_is_left_out(self, fx_search: type) -> None:
        """Verify a heading that is a third of the width is no line, and the full lines are still found.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        page = samples(text_page(*PAGE_SIZE_PX)).copy()
        page[MARGIN_PX : MARGIN_PX + LINE_PITCH_PX, PAGE_SIZE_PX[0] // 3 :] = 255
        lines = fx_search(page).find()
        expect(len(lines) == LINES_ON_PAGE - 1)
        assert_expectations()

    def test_a_page_with_no_ink_has_no_lines(self, fx_search: type) -> None:
        """Verify blank paper and the grain of a cover, whose tones do not part into ink and paper, have none.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        grain = np.random.default_rng(GRAIN_SEED).normal(GRAIN_TONE, GRAIN_SPREAD, PAGE_SIZE_PX[::-1])
        blank = np.full(PAGE_SIZE_PX[::-1], 255, dtype=np.uint8)
        expect(fx_search(blank).find() == [])
        expect(fx_search(grain.clip(0, 255).astype(np.uint8)).find() == [])
        assert_expectations()

    def test_a_colour_page_gives_the_same_lines_as_its_gray_copy(self, fx_search: type) -> None:
        """Verify the three planes of a colour page are read as one, and give the lines of the gray page.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        gray = text_page(*PAGE_SIZE_PX)
        lines = fx_search(samples(gray)).find()
        colour = fx_search(samples(gray.convert('RGB'))).find()
        expect(len(colour) == len(lines))
        expect(abs(float(np.median(colour[0].y)) - float(np.median(lines[0].y))) < PLACE_TOLERANCE_PX)
        assert_expectations()

    def test_the_curve_fitted_to_a_bent_line_follows_the_bend(self, fx_search: type) -> None:
        """Verify the cubic of the first line of a bent page gives the shift of its columns to a fraction of a pixel.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        flat = samples(text_page(*PAGE_SIZE_PX))
        bent, shifts = bend_columns(flat, SLANT_PX)
        [line, *_] = fx_search(bent).find()
        coefficients, deviation = line.fit(PAGE_SIZE_PX[0], 3)
        fitted = np.polyval(coefficients, np.arange(PAGE_SIZE_PX[0]) / PAGE_SIZE_PX[0])
        truth = MARGIN_PX + 6 + shifts
        within = slice(int(line.left), int(line.right))
        expect(float(np.abs(fitted - truth)[within].max()) < PLACE_TOLERANCE_PX)
        expect(deviation < PLACE_TOLERANCE_PX)
        assert_expectations()

    def test_a_point_far_from_its_line_does_not_move_the_fit(self, fx_search: type) -> None:
        """Verify a stray point is dropped from the fit again and again, and the curve stays on the line.

        :param fx_search: The class under test.
        :type fx_search: type
        """
        [line, *_] = fx_search(samples(text_page(*PAGE_SIZE_PX))).find()
        clean, _ = line.fit(PAGE_SIZE_PX[0], 1)
        stray = line.y.copy()
        stray[OUTLIER_INDEX] += OUTLIER_PX
        spoiled = type(line)(x=line.x, y=stray)
        coefficients, deviation = spoiled.fit(PAGE_SIZE_PX[0], 1)
        expect(bool(np.allclose(coefficients, clean, atol=PLACE_TOLERANCE_PX)))
        expect(deviation < PLACE_TOLERANCE_PX)
        assert_expectations()
