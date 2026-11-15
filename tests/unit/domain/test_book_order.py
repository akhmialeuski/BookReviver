"""Tests for ``BookOrder``, the order of the pages of a book that a batch of new pages reads once."""

from uuid import UUID

import pytest

from bookreviver.domain.ids import PageId
from bookreviver.domain.pages import BookOrder, PageAnchor
from bookreviver.domain.sides import Side

FIRST: PageId = PageId(UUID(int=1))
MIDDLE: PageId = PageId(UUID(int=2))
LAST: PageId = PageId(UUID(int=3))
LOST: PageId = PageId(UUID(int=9))
# The order of a book of three pages
ORDER: BookOrder = BookOrder(keys={FIRST: 'a0', MIDDLE: 'a1', LAST: 'a2'})
# Each place, with the keys of the pages before it and after it
GAPS: dict[str, tuple[PageAnchor | None, tuple[str | None, str | None]]] = {
    'before-the-first-page': (PageAnchor(page_id=FIRST, side=Side.BEFORE), (None, 'a0')),
    'after-the-first-page': (PageAnchor(page_id=FIRST, side=Side.AFTER), ('a0', 'a1')),
    'before-the-middle-page': (PageAnchor(page_id=MIDDLE, side=Side.BEFORE), ('a0', 'a1')),
    'after-the-last-page': (PageAnchor(page_id=LAST, side=Side.AFTER), ('a2', None)),
    'end-of-the-book': (None, ('a2', None)),
}


class TestBookOrder:
    """Tests for the positions and the gaps of ``BookOrder``."""

    def test_positions_count_the_pages_before_each_page(self) -> None:
        """Verify every page stands at its index in the order, from zero."""
        assert ORDER.positions() == {FIRST: 0, MIDDLE: 1, LAST: 2}

    @pytest.mark.parametrize(('anchor', 'gap'), GAPS.values(), ids=GAPS.keys())
    def test_gap_lies_between_the_anchor_and_its_neighbour_on_that_side(
        self, anchor: PageAnchor | None, gap: tuple[str | None, str | None]
    ) -> None:
        """Verify a place is bounded by its page and the neighbour on its side, or by nothing past either end.

        :param anchor: The place, or None for the end of the book.
        :type anchor: PageAnchor | None
        :param gap: The key before the place and the key after it.
        :type gap: tuple[str | None, str | None]
        """
        assert ORDER.gaps([anchor]) == {anchor: gap}

    def test_anchor_that_is_not_a_page_of_the_book_has_no_gap(self) -> None:
        """Verify a place next to a page the book does not have is left out, and the other places are kept."""
        lost = PageAnchor(page_id=LOST, side=Side.AFTER)

        assert ORDER.gaps([lost, None]) == {None: ('a2', None)}

    def test_end_of_an_empty_book_is_unbounded(self) -> None:
        """Verify the end of a book without pages lies between no keys."""
        assert BookOrder(keys={}).gaps([None]) == {None: (None, None)}
