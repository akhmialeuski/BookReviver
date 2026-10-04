"""Tests for the comparison of what a step found on a page with the rest of the book."""

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import StepMeasure, VersionData
from bookreviver.domain.step_measures import (
    ANGLE_TOLERANCE_DEGREES,
    FRAME_TOLERANCE_SHARE,
    MIN_READINGS,
    departs,
    median_of,
    read_measure,
)

FRAME_WIDTH_PX: float = 1000.0
FRAME_HEIGHT_PX: float = 1600.0
BOOK_FRAME: tuple[float, float] = (FRAME_WIDTH_PX, FRAME_HEIGHT_PX)


def frame_data(width: float, height: float) -> dict[str, object]:
    """Build the data of a version whose step found a frame of the content.

    :param width: Width of the frame in pixels.
    :type width: float
    :param height: Height of the frame in pixels.
    :type height: float
    :returns: The data with the frame.
    :rtype: dict[str, object]
    """
    return {VersionData.FRAME: {'left': 10.0, 'top': 20.0, 'width': width, 'height': height}}


class TestReadMeasure:
    """Tests for read_measure."""

    def test_the_angle_and_the_size_of_the_frame_are_read_from_the_data_of_the_version(self) -> None:
        """Verify each measure reads the number or numbers its step records."""
        expect(read_measure(StepMeasure.ANGLE, {VersionData.ANGLE: 1.5}) == (1.5,))
        expect(read_measure(StepMeasure.FRAME_SIZE, frame_data(300.0, 400.0)) == (300.0, 400.0))
        assert_expectations()

    def test_a_step_that_left_the_page_as_it_was_has_nothing_to_compare(self) -> None:
        """Verify the angle of a skipped deskew, which is zero by default, is not a reading."""
        assert read_measure(StepMeasure.ANGLE, {VersionData.ANGLE: 0.0, VersionData.SKIPPED: True}) is None

    @pytest.mark.parametrize('measure', list(StepMeasure))
    def test_a_version_that_records_nothing_has_no_reading(self, measure: StepMeasure) -> None:
        """Verify a version without the number, or with one that is no number, has no reading.

        :param measure: What the step finds.
        :type measure: StepMeasure
        """
        expect(read_measure(measure, {}) is None)
        expect(read_measure(measure, {VersionData.ANGLE: 'x', VersionData.FRAME: 'x'}) is None)
        assert_expectations()


class TestMedianOf:
    """Tests for median_of."""

    def test_each_number_has_the_median_of_its_own_column(self) -> None:
        """Verify the width and the height of the frames are taken apart."""
        readings = [(1.0, 10.0), (5.0, 20.0), (3.0, 30.0)]
        assert median_of(readings) == (3.0, 20.0)

    def test_fewer_pages_than_a_book_have_no_median(self) -> None:
        """Verify one or two pages do not make a book to compare with."""
        assert median_of([(1.0,)] * (MIN_READINGS - 1)) is None


class TestDeparts:
    """Tests for departs."""

    def test_an_angle_departs_by_more_than_the_tolerance_either_way(self) -> None:
        """Verify the tolerance is in degrees and counts both directions."""
        median = (0.3,)
        expect(departs(StepMeasure.ANGLE, (0.3 + ANGLE_TOLERANCE_DEGREES + 0.1,), median))
        expect(departs(StepMeasure.ANGLE, (0.3 - ANGLE_TOLERANCE_DEGREES - 0.1,), median))
        expect(not departs(StepMeasure.ANGLE, (0.3 + ANGLE_TOLERANCE_DEGREES - 0.1,), median))
        assert_expectations()

    @pytest.mark.parametrize(
        ('reading', 'expected'),
        [
            ((FRAME_WIDTH_PX * (1 + FRAME_TOLERANCE_SHARE + 0.05), FRAME_HEIGHT_PX), True),
            ((FRAME_WIDTH_PX, FRAME_HEIGHT_PX * (1 - FRAME_TOLERANCE_SHARE - 0.05)), True),
            ((FRAME_WIDTH_PX * 1.05, FRAME_HEIGHT_PX * 0.95), False),
        ],
        ids=['wider', 'shorter', 'alike'],
    )
    def test_a_frame_departs_when_a_side_is_far_from_the_median_by_a_share_of_it(
        self, reading: tuple[float, float], *, expected: bool
    ) -> None:
        """Verify the tolerance of the frame is a share of the median, and either side can carry it.

        :param reading: The width and the height of the frame of a page.
        :type reading: tuple[float, float]
        :param expected: Whether the page departs.
        :type expected: bool
        """
        assert departs(StepMeasure.FRAME_SIZE, reading, BOOK_FRAME) is expected
