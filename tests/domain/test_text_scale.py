"""Tests for the rule that decides whether a page is scaled to the target line height, and by what factor."""

import pytest

from bookreviver.domain.text_scale import scale_factor

TARGET_PX: float = 20.0
LIMIT_PERCENT: float = 25.0


class TestScaleFactor:
    """Tests for scale_factor(), with a target of 20 pixels and a limit of 25 percent, so 15 and 25 are at the limit."""

    @pytest.mark.parametrize(
        ('measured', 'factor'),
        [(20.0, 1.0), (16.0, 1.25), (25.0, 0.8), (15.0, 20.0 / 15.0)],
        ids=['on-target', 'smaller-within', 'larger-at-the-limit', 'smaller-at-the-limit'],
    )
    def test_a_line_height_up_to_the_limit_is_scaled_to_the_target(self, measured: float, factor: float) -> None:
        """Verify a line height as far from the target as the limit allows, or less, gets the factor to the target.

        :param measured: Line height of the page in pixels.
        :type measured: float
        :param factor: The factor that brings it to the target.
        :type factor: float
        """
        assert scale_factor(measured, TARGET_PX, LIMIT_PERCENT) == pytest.approx(factor)

    @pytest.mark.parametrize('far', [25.5, 14.5], ids=['larger-past-the-limit', 'smaller-past-the-limit'])
    def test_a_line_height_past_the_limit_is_left_as_it_is(self, far: float) -> None:
        """Verify a line height farther from the target than the limit gets no factor, so its page is not scaled.

        :param far: Line height of the page in pixels.
        :type far: float
        """
        assert scale_factor(far, TARGET_PX, LIMIT_PERCENT) is None

    def test_a_limit_of_zero_still_scales_a_page_on_the_target(self) -> None:
        """Verify a page on the target gets the factor 1 when the limit is zero."""
        assert scale_factor(TARGET_PX, TARGET_PX, 0.0) == pytest.approx(1.0)

    def test_a_limit_of_zero_leaves_a_page_off_the_target_as_it_is(self) -> None:
        """Verify a page off the target gets no factor when the limit is zero, since no change of size is allowed."""
        assert scale_factor(TARGET_PX + 0.5, TARGET_PX, 0.0) is None
