"""Tests for the scale that turns the millimetres of a margin into pixels."""

import pytest

from bookreviver.domain.margins import NOMINAL_BLOCK_MM, MarginScale

TEN_MM: float = 10.0


class TestMarginScale:
    """Tests for MarginScale."""

    @pytest.mark.parametrize(('dpi', 'pixels'), [(300.0, 118.11), (600.0, 236.22)], ids=['300-dpi', '600-dpi'])
    def test_ten_millimetres_are_the_pixels_of_the_resolution(self, dpi: float, pixels: float) -> None:
        """Verify 10 mm are about 118 pixels at 300 dpi and about 236 at 600 dpi, and the pixels are as long back.

        :param dpi: Resolution of the page.
        :type dpi: float
        :param pixels: The pixels of 10 mm.
        :type pixels: float
        """
        scale = MarginScale.from_dpi(dpi)
        assert scale.pixels(TEN_MM) == pytest.approx(pixels, abs=0.01)
        assert scale.millimetres(scale.pixels(TEN_MM)) == pytest.approx(TEN_MM)

    def test_a_page_with_no_resolution_takes_the_width_of_its_box_for_a_nominal_block(self) -> None:
        """Verify a box of 500 pixels is the nominal block, so a tenth of its width is 10 mm."""
        scale = MarginScale.from_block(500)
        assert scale.pixels(NOMINAL_BLOCK_MM / 10) == pytest.approx(50)

    def test_a_page_of_the_book_has_the_nominal_block_and_the_side_margins_across(self) -> None:
        """Verify a page of 1200 pixels that holds the nominal block and 20 mm of margins has 10 pixels in a mm."""
        assert MarginScale.from_page(1_200, 20).pixels_per_mm == pytest.approx(10)
