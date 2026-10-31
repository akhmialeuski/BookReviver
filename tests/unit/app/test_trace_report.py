"""Tests for the arithmetic of the trace report: the union of intervals and the nearest-rank percentile."""

import pytest

from bookreviver.app import trace_report


class TestMergedLength:
    """Tests for the union of intervals, which counts what overlaps once."""

    def test_overlapping_and_separate_intervals_are_added_up_once(self) -> None:
        """Verify the union of 0 to 2, 1 to 3 and 5 to 6 is 3 + 1 seconds, and an interval inside another adds none."""
        intervals = [(5.0, 6.0), (1.0, 3.0), (0.0, 2.0), (0.5, 1.5)]
        assert trace_report.merged_length(intervals) == pytest.approx(4.0)

    def test_no_interval_has_no_length(self) -> None:
        """Verify the union of nothing is nothing."""
        assert trace_report.merged_length([]) == pytest.approx(0.0)


class TestPercentile:
    """Tests for the nearest-rank percentile."""

    @pytest.mark.parametrize(('percent', 'expected'), [(50, 50.0), (90, 1000.0), (99, 1000.0), (100, 1000.0)], ids=str)
    def test_percentile_is_the_smallest_value_that_covers_the_share(self, percent: int, expected: float) -> None:
        """Verify the rank is the ceiling of the share of the count, so the value is one that was observed.

        :param percent: The percentile.
        :type percent: int
        :param expected: The value of the nearest rank among 0, 50, 500 and 1000.
        :type expected: float
        """
        assert trace_report.percentile([1000.0, 0.0, 500.0, 50.0], percent) == expected
