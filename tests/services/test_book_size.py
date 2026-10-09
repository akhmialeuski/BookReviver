"""Tests for the size of the page of the book, which holds every box as the normalize step will place it.

The step scales a box to the target line height, except where the line height of its page is farther from the target
than ``max_scale_change``: that page is left at its own size. The size of the page of the book is worked out from the
boxes as they will stand, so a box the step leaves alone is counted as it is.
"""

from typing import TYPE_CHECKING, NamedTuple

import pytest

from bookreviver.domain.enums import NormalizeParam
from bookreviver.domain.margins import MM_PER_INCH, NOMINAL_BLOCK_MM
from bookreviver.services.book_measure import BlockMeasure, BookSize

if TYPE_CHECKING:
    from bookreviver.domain.values import MetadataMap

BLOCK_PX: tuple[float, float] = (300.0, 600.0)
# The line height of the book, which the median of the pages is
TARGET_LINE_PX: float = 30.0
# A page whose line height is within the limit of the target, and so is scaled to it: 27 is a tenth below 30
WITHIN_LINE_PX: float = 27.0
WITHIN_BLOCK_PX: tuple[float, float] = (330.0, 660.0)
# The share of the box that the margins the measure writes add, which are 8, 10, 10 and 8 percent of the box
MEASURED_SHARE: float = 1.18
# The margins of a step that was not measured, which are 25 mm across the page and 25 mm down it
DEFAULT_SIDE_MM: float = 25.0
SIZE_TOLERANCE_PX: float = 3.0
CASE_ARG: str = 'case'


class CapCase(NamedTuple):
    """A page whose line height is far from the target, the limit the step has, and the largest box that results.

    :ivar current: The parameters of the step.
    :ivar far_line_height: The line height of the page whose box is the one to watch, in pixels.
    :ivar width: Width of the largest box of the book as the step places the boxes, in pixels.
    :ivar height: Height of the largest box of the book, in pixels.
    """

    current: MetadataMap
    far_line_height: float
    width: float
    height: float


CAP_CASES: tuple[CapCase, ...] = (
    # 20 is a third below the target, past the default limit of 25 percent, so the box stays 300 by 600 and the box of
    # the page that is within the limit, 330 by 660 brought down by a tenth to 367 by 733, is the largest
    CapCase(current={}, far_line_height=20.0, width=366.67, height=733.33),
    # With the limit at 50 percent the same page is scaled by half as much again, to 450 by 900
    CapCase(current={NormalizeParam.MAX_SCALE_CHANGE: 50.0}, far_line_height=20.0, width=450.0, height=900.0),
    # 22.5 is exactly a quarter below the target, which is still within the limit, so the box becomes 400 by 800
    CapCase(current={}, far_line_height=22.5, width=400.0, height=800.0),
    # 22.4 is just past the limit, so the box stays as it is
    CapCase(current={}, far_line_height=22.4, width=366.67, height=733.33),
    # A limit of 0 scales no page that is not on the target, so the largest box is the one of 330 by 660 as it is
    CapCase(current={NormalizeParam.MAX_SCALE_CHANGE: 0.0}, far_line_height=20.0, width=330.0, height=660.0),
)
CAP_IDS: tuple[str, ...] = (
    'beyond-the-default-limit',
    'a-wider-limit-of-the-step',
    'exactly-at-the-limit',
    'just-past-the-limit',
    'no-change-allowed',
)


def book_with(far_line_height: float) -> list[BlockMeasure]:
    """Make the boxes of a book: three pages on the target, one within the limit of it, and one far from it.

    :param far_line_height: The line height of the last page, in pixels.
    :type far_line_height: float
    :returns: The measures, whose median line height is the target.
    :rtype: list[BlockMeasure]
    """
    width, height = BLOCK_PX
    on_target = [BlockMeasure(width=width, height=height, line_height=TARGET_LINE_PX) for _ in range(3)]
    within = BlockMeasure(width=WITHIN_BLOCK_PX[0], height=WITHIN_BLOCK_PX[1], line_height=WITHIN_LINE_PX)
    far = BlockMeasure(width=width, height=height, line_height=far_line_height)
    return [*on_target, within, far]


class TestBookSizeCapsTheScale:
    """Tests for the boxes the page of the book holds: each is brought to the target as far as the step brings it."""

    @pytest.mark.parametrize(CASE_ARG, CAP_CASES, ids=CAP_IDS)
    def test_the_measured_page_holds_the_boxes_as_the_step_places_them(self, case: CapCase) -> None:
        """Verify the page is the largest box the step gives a page, with the margins the measure writes.

        :param case: The limit of the step, the far page and the largest box that results.
        :type case: CapCase
        """
        measured = BookSize(book_with(case.far_line_height)).measured(case.current)
        expected = (case.width * MEASURED_SHARE, case.height * MEASURED_SHARE)
        assert (measured[NormalizeParam.PAGE_WIDTH], measured[NormalizeParam.PAGE_HEIGHT]) == pytest.approx(
            expected, abs=SIZE_TOLERANCE_PX
        )

    @pytest.mark.parametrize(CASE_ARG, CAP_CASES, ids=CAP_IDS)
    def test_the_page_by_the_book_holds_the_boxes_as_the_step_places_them(self, case: CapCase) -> None:
        """Verify a run that takes the page by the book gives the same page, with the margins the step has.

        :param case: The limit of the step, the far page and the largest box that results.
        :type case: CapCase
        """
        page = BookSize(book_with(case.far_line_height)).by_the_book(case.current)
        # The margins of a step that was not measured are 25 mm across and 25 mm down, in millimetres of a box that is
        # taken for 100 mm wide, so each is a quarter of the width of the box
        margin_px = case.width * DEFAULT_SIDE_MM / NOMINAL_BLOCK_MM
        expected = (case.width + margin_px, case.height + margin_px)
        assert (page[NormalizeParam.PAGE_WIDTH], page[NormalizeParam.PAGE_HEIGHT]) == pytest.approx(
            expected, abs=SIZE_TOLERANCE_PX
        )

    def test_the_resolution_of_the_margins_follows_the_scale_the_step_applies(self) -> None:
        """Verify a page the step leaves at its size does not make the pixels of a millimetre larger than they are.

        The page is 20 pixels between lines against 30, which is past the limit, so it stays at 300 dpi; counted as
        scaled it would be at 450 dpi and the margins in millimetres would come out a third shorter.
        """
        width, height = BLOCK_PX
        on_target = [BlockMeasure(width=width, height=height, line_height=TARGET_LINE_PX, dpi=300.0) for _ in range(3)]
        far = BlockMeasure(width=100.0, height=200.0, line_height=20.0, dpi=300.0)
        measured = BookSize([*on_target, far]).measured({})
        # A tenth of a block of 300 pixels at 300 dpi is 2.54 mm
        assert measured[NormalizeParam.MARGIN_INNER] == pytest.approx(width / 10 / (300.0 / MM_PER_INCH), abs=0.05)
