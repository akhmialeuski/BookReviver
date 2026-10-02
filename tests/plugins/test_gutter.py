"""Tests for the gutter search, on spreads drawn for the tests and on one real spread of a book of the 1880s.

The tests need OpenCV, and are skipped with the reason where the optional group ``cv`` is not installed.
"""

from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from tests.plugins.synthetic import as_samples, draw_blank, draw_single_page, draw_spread

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.plugins.cv_image import Samples
    from bookreviver.plugins.gutter import GutterSearch

SHADOW_ARGUMENT: str = 'shadow'
SLANT_ARGUMENT: str = 'slant_deg'
SHADOW_CASES = pytest.mark.parametrize(SHADOW_ARGUMENT, [True, False], ids=[SHADOW_ARGUMENT, 'light'])
COLOUR_PLANES: int = 3
MAX_SLANT_DEG: float = 5.0
MID_SLANT_DEG: float = -2.5
SLANT_CASES = pytest.mark.parametrize(SLANT_ARGUMENT, [-MAX_SLANT_DEG, MID_SLANT_DEG, 0.0, 2.5, MAX_SLANT_DEG])
# The cut may leave the true gutter by this share of the width of the scan on any row
MAX_ERROR_SHARE: float = 0.01
# The least confidence of a found gutter in a clean drawn spread
MIN_FOUND_CONFIDENCE: float = 0.1
SLANT_TOLERANCE_DEG: float = 0.5
# The real spread, downscaled to 1600 pixels of width, and its gutter marked by hand at the top and at the bottom row
REAL_SPREAD: Path = Path(__file__).parent / 'data' / 'spread_1880s.jpg'
REAL_GUTTER_TOP_X: float = 832.0
REAL_GUTTER_BOTTOM_X: float = 781.0


def worst_error_share(search: GutterSearch, scan: Samples, top_x: float, bottom_x: float) -> float:
    """Measure how far the found cut strays from a known gutter.

    :param search: The search under test.
    :type search: GutterSearch
    :param scan: The samples of the scan.
    :type scan: Samples
    :param top_x: Distance of the true gutter from the left edge at the top row.
    :type top_x: float
    :param bottom_x: Distance of the true gutter from the left edge at the bottom row.
    :type bottom_x: float
    :returns: The largest distance on any row, as a share of the width of the scan.
    :rtype: float
    """
    height, width = (int(size) for size in scan.shape[:2])
    cut = search.search(scan)
    worst = float(np.abs(cut.at_rows(height) - np.linspace(top_x, bottom_x, height)).max())
    return worst / width


class TestGutterSearch:
    """Tests for GutterSearch."""

    @SLANT_CASES
    @SHADOW_CASES
    def test_the_cut_follows_the_gutter_of_a_slanting_spread(
        self, fx_gutter_search: Callable[..., GutterSearch], slant_deg: float, *, shadow: bool
    ) -> None:
        """Verify the cut stays within one percent of the width of the true gutter on every row, with and without shadow.

        :param fx_gutter_search: Builder of the search under test.
        :type fx_gutter_search: Callable[..., GutterSearch]
        :param slant_deg: Angle the spread is laid at.
        :type slant_deg: float
        :param shadow: Whether the binding casts a shadow.
        :type shadow: bool
        """
        drawn = draw_spread(slant_deg=slant_deg, shadow=shadow)
        scan = as_samples(drawn.image)
        search = fx_gutter_search()
        cut = search.search(scan)
        expect(worst_error_share(search, scan, drawn.top_x, drawn.bottom_x) <= MAX_ERROR_SHARE)
        expect(cut.confidence >= MIN_FOUND_CONFIDENCE)
        expect(abs(cut.slant_deg(scan.shape[0]) - slant_deg) <= SLANT_TOLERANCE_DEG)
        assert_expectations()

    @SLANT_CASES
    @SHADOW_CASES
    def test_a_picture_across_the_gutter_does_not_pull_the_cut_away(
        self, fx_gutter_search: Callable[..., GutterSearch], slant_deg: float, *, shadow: bool
    ) -> None:
        """Verify the strips a picture covers are left out or outvoted, so the cut stays on the gutter.

        :param fx_gutter_search: Builder of the search under test.
        :type fx_gutter_search: Callable[..., GutterSearch]
        :param slant_deg: Angle the spread is laid at.
        :type slant_deg: float
        :param shadow: Whether the binding casts a shadow.
        :type shadow: bool
        """
        drawn = draw_spread(slant_deg=slant_deg, shadow=shadow, picture=True)
        scan = as_samples(drawn.image)
        search = fx_gutter_search()
        expect(worst_error_share(search, scan, drawn.top_x, drawn.bottom_x) <= MAX_ERROR_SHARE)
        expect(search.search(scan).confidence >= MIN_FOUND_CONFIDENCE)
        assert_expectations()

    def test_an_empty_spread_has_no_gutter_and_is_cut_in_the_middle(
        self, fx_gutter_search: Callable[..., GutterSearch]
    ) -> None:
        """Verify blank paper gives the vertical cut through the middle of the band with the confidence 0.

        :param fx_gutter_search: Builder of the search under test.
        :type fx_gutter_search: Callable[..., GutterSearch]
        """
        scan = as_samples(draw_blank())
        cut = fx_gutter_search().search(scan)
        expect(cut.confidence == 0)
        expect(cut.top_x == cut.bottom_x == pytest.approx(scan.shape[1] / 2))
        assert_expectations()

    def test_a_single_page_shows_no_gutter(self, fx_gutter_search: Callable[..., GutterSearch]) -> None:
        """Verify a page of text with no binding in the middle gives a confidence below the usual minimum.

        :param fx_gutter_search: Builder of the search under test.
        :type fx_gutter_search: Callable[..., GutterSearch]
        """
        cut = fx_gutter_search().search(as_samples(draw_single_page()))
        assert cut.confidence < MIN_FOUND_CONFIDENCE

    def test_the_slant_is_limited(self, fx_gutter_search: Callable[..., GutterSearch]) -> None:
        """Verify a gutter that leans more than the limit allows is cut at the limit.

        :param fx_gutter_search: Builder of the search under test.
        :type fx_gutter_search: Callable[..., GutterSearch]
        """
        limit_deg = 2.0
        scan = as_samples(draw_spread(slant_deg=MAX_SLANT_DEG).image)
        cut = fx_gutter_search(max_slant_deg=limit_deg).search(scan)
        assert cut.slant_deg(scan.shape[0]) == pytest.approx(limit_deg, abs=0.01)

    def test_a_colour_scan_is_searched_like_a_gray_one(self, fx_gutter_search: Callable[..., GutterSearch]) -> None:
        """Verify blue, green and red planes give the cut the gray plane gives.

        :param fx_gutter_search: Builder of the search under test.
        :type fx_gutter_search: Callable[..., GutterSearch]
        """
        gray = as_samples(draw_spread(slant_deg=MID_SLANT_DEG).image)
        colour = np.ascontiguousarray(np.repeat(gray[:, :, np.newaxis], COLOUR_PLANES, axis=2))
        search = fx_gutter_search()
        assert search.search(colour).top_x == pytest.approx(search.search(gray).top_x, abs=1)

    def test_the_cut_of_a_real_spread_is_within_one_percent_of_the_gutter_marked_by_hand(
        self, fx_gutter_search: Callable[..., GutterSearch]
    ) -> None:
        """Verify the cut of a real spread of a book of the 1880s, whose shadow is uneven and whose scan is crooked.

        :param fx_gutter_search: Builder of the search under test.
        :type fx_gutter_search: Callable[..., GutterSearch]
        """
        with Image.open(REAL_SPREAD) as spread:
            scan = as_samples(spread.convert('L'))
        search = fx_gutter_search()
        expect(worst_error_share(search, scan, REAL_GUTTER_TOP_X, REAL_GUTTER_BOTTOM_X) <= MAX_ERROR_SHARE)
        expect(search.search(scan).confidence >= MIN_FOUND_CONFIDENCE)
        assert_expectations()
