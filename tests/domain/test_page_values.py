"""Tests for the values of pages without a scan: their size, their kind of origin and the page a user adds."""

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import NewPageOrigin, PageKind, PageOrigin, VersionData
from bookreviver.domain.values import NewPage, PageSize


class TestPageSize:
    """Tests for PageSize."""

    def test_round_trips_through_the_data_of_a_version(self) -> None:
        """Verify the data a size is written as reads back as the same size, with and without a resolution."""
        for size in (PageSize(width_px=2200, height_px=3000, dpi=300.0), PageSize(width_px=10, height_px=20)):
            expect(PageSize.from_data(size.as_data()) == size)
        assert_expectations()

    def test_leaves_an_unknown_resolution_out_of_the_data(self) -> None:
        """Verify a size without a resolution writes no key for it, which reads as unknown."""
        assert VersionData.DPI not in PageSize(width_px=10, height_px=20).as_data()

    @pytest.mark.parametrize(
        'data',
        [
            {},
            {'width_px': 10},
            {'width_px': 0, 'height_px': 10},
            {'width_px': -5, 'height_px': 10},
            {'width_px': '10', 'height_px': 10},
            {'width_px': 10.5, 'height_px': 10},
        ],
        ids=['empty', 'no-height', 'zero-width', 'negative-width', 'text-width', 'fractional-width'],
    )
    def test_data_without_a_usable_width_and_height_is_no_size(self, data: dict[str, object]) -> None:
        """Verify data that holds no positive whole width and height reads as no size at all.

        :param data: Data of a version.
        :type data: dict[str, object]
        """
        assert PageSize.from_data(data) is None

    @pytest.mark.parametrize('dpi', [0, -1, 'sharp', None])
    def test_a_resolution_that_is_not_positive_is_unknown(self, dpi: object) -> None:
        """Verify a recorded resolution that is zero, negative or not a number is read as unknown.

        :param dpi: The recorded resolution.
        :type dpi: object
        """
        size = PageSize.from_data({'width_px': 10, 'height_px': 20, 'dpi': dpi})

        assert size == PageSize(width_px=10, height_px=20, dpi=None)

    @pytest.mark.parametrize('sides', [(0, 10), (10, 0), (-1, 10)])
    def test_sides_must_be_positive(self, sides: tuple[int, int]) -> None:
        """Verify a size cannot be built with a side of zero or less.

        :param sides: Width and height.
        :type sides: tuple[int, int]
        """
        with pytest.raises(ValueError, match=r'width_px|height_px'):
            PageSize(width_px=sides[0], height_px=sides[1])


class TestNewPageOrigin:
    """Tests for NewPageOrigin."""

    def test_makes_the_page_origin_of_the_same_value(self) -> None:
        """Verify both origins a user may add are origins of a page, and the third origin is not one of them."""
        expect([origin.page_origin for origin in NewPageOrigin] == [PageOrigin.BLANK, PageOrigin.PLACEHOLDER])
        expect(PageOrigin.SCAN.value not in {origin.value for origin in NewPageOrigin})
        assert_expectations()


class TestNewPage:
    """Tests for NewPage."""

    def test_a_blank_leaf_may_have_a_size(self) -> None:
        """Verify a blank leaf takes a size, and a page without one is the median size."""
        size = PageSize(width_px=10, height_px=20)

        expect(NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK, size=size).size == size)
        expect(NewPage(origin=NewPageOrigin.BLANK, kind=PageKind.BLANK).size is None)
        assert_expectations()

    def test_a_placeholder_has_no_size(self) -> None:
        """Verify a size cannot be given to a page that has no image."""
        with pytest.raises(ValueError, match='no size'):
            NewPage(origin=NewPageOrigin.PLACEHOLDER, kind=PageKind.TITLE, size=PageSize(width_px=10, height_px=20))
