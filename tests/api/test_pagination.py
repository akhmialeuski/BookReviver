"""Tests for the conversion between page parameters and domain slices."""

from fastapi_pagination import Params

from bookreviver.api.pagination import Pager
from bookreviver.api.schemas.base import ResponseModel
from bookreviver.domain.values import Slice, SliceRequest

PAGE_SIZE: int = 10
TOTAL: int = 25


class NumberSchema(ResponseModel):
    """A trivial response schema.

    :ivar value: The number the domain item carried.
    """

    value: int


class TestPager:
    """Tests for Pager."""

    def test_request_turns_page_number_into_offset(self) -> None:
        """Verify the third page of ten starts at item twenty."""
        pager = Pager[int, NumberSchema](Params(page=3, size=PAGE_SIZE), lambda value: NumberSchema(value=value))
        assert pager.request == SliceRequest(offset=20, limit=PAGE_SIZE)

    def test_page_maps_items_and_counts_pages(self) -> None:
        """Verify a slice becomes a typed page with the total and the number of pages."""
        pager = Pager[int, NumberSchema](Params(page=3, size=PAGE_SIZE), lambda value: NumberSchema(value=value))
        page = pager.page(Slice(items=[20, 21], total=TOTAL))
        assert (page.items, page.total, page.page, page.pages) == (
            [NumberSchema(value=20), NumberSchema(value=21)],
            TOTAL,
            3,
            3,
        )
