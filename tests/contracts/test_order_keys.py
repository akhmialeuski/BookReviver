"""Contract of the OrderKeys port, run against every adapter registered in the conftest."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from delayed_assert import assert_expectations, expect

if TYPE_CHECKING:
    from bookreviver.ports.ordering import OrderKeys

# Enough appended pages to cross from one-character to two-character integer parts of the keys
APPENDED_PAGES: int = 300
# Enough insertions at one place to grow the fractional part of the keys by several digits
NESTED_INSERTIONS: int = 50
SPREAD_COUNT: int = 7


class Gap(NamedTuple):
    """Two neighbouring keys that a new key goes between.

    :ivar lower: Key before the gap, or None at the start of the book.
    :ivar upper: Key after the gap, or None at the end of the book.
    """

    lower: str | None
    upper: str | None


def _lies_in(key: str, gap: Gap) -> bool:
    """Return whether ``key`` sorts strictly inside the gap, byte by byte as the database compares keys.

    :param key: Key to place.
    :type key: str
    :param gap: Neighbouring keys, either of which may be open.
    :type gap: Gap
    :returns: True when the key sorts after the lower key and before the upper key.
    :rtype: bool
    """
    encoded = key.encode()
    after_lower = gap.lower is None or gap.lower.encode() < encoded
    before_upper = gap.upper is None or encoded < gap.upper.encode()
    return after_lower and before_upper


GAPS: list[Gap] = [
    Gap(None, None),
    Gap(None, 'a0'),
    Gap('a0', None),
    Gap('a0', 'a1'),
    Gap('a0', 'a0V'),
    Gap('Zz', 'a0'),
]
GAP_IDS: list[str] = ['empty-book', 'before-first', 'after-last', 'between-integers', 'between-fractions', 'negative']


class TestBetween:
    """Contract of OrderKeys.between()."""

    @pytest.mark.parametrize('gap', GAPS, ids=GAP_IDS)
    def test_key_sorts_inside_the_gap(self, fx_order_keys: OrderKeys, gap: Gap) -> None:
        """Verify the new key sorts strictly between its neighbours, byte by byte.

        :param fx_order_keys: Adapter of the port under test.
        :type fx_order_keys: OrderKeys
        :param gap: Neighbouring keys of the new key.
        :type gap: Gap
        """
        assert _lies_in(fx_order_keys.between(lower=gap.lower, upper=gap.upper), gap)

    def test_appending_keeps_book_order(self, fx_order_keys: OrderKeys) -> None:
        """Verify keys appended one after another at the end of a long book stay distinct and ascending.

        :param fx_order_keys: Adapter of the port under test.
        :type fx_order_keys: OrderKeys
        """
        keys = [fx_order_keys.between(lower=None, upper=None)]
        for _ in range(APPENDED_PAGES):
            keys.append(fx_order_keys.between(lower=keys[-1], upper=None))
        assert keys == sorted(set(keys), key=str.encode)

    def test_inserting_at_one_place_never_runs_out_of_keys(self, fx_order_keys: OrderKeys) -> None:
        """Verify a key always exists between a key and the one just inserted after it.

        :param fx_order_keys: Adapter of the port under test.
        :type fx_order_keys: OrderKeys
        """
        lower, upper = 'a0', 'a1'
        for _ in range(NESTED_INSERTIONS):
            upper = fx_order_keys.between(lower=lower, upper=upper)
        assert _lies_in(upper, Gap(lower, 'a1'))

    @pytest.mark.parametrize(
        'gap', [Gap('a1', 'a0'), Gap('a0', 'a0'), Gap('not a key', None)], ids=['reversed', 'equal', 'malformed']
    )
    def test_gap_without_room_is_rejected(self, fx_order_keys: OrderKeys, gap: Gap) -> None:
        """Reject neighbours in the wrong order, equal neighbours and a malformed key, naming the refused gap.

        :param fx_order_keys: Adapter of the port under test.
        :type fx_order_keys: OrderKeys
        :param gap: Neighbouring keys with no valid key between them.
        :type gap: Gap
        """
        with pytest.raises(ValueError, match=f'{gap.lower}.*{gap.upper}'):
            fx_order_keys.between(lower=gap.lower, upper=gap.upper)


class TestSpread:
    """Contract of OrderKeys.spread()."""

    @pytest.mark.parametrize('gap', GAPS, ids=GAP_IDS)
    def test_keys_ascend_inside_the_gap(self, fx_order_keys: OrderKeys, gap: Gap) -> None:
        """Verify the keys are as many as asked, distinct, ascending and all inside the gap.

        :param fx_order_keys: Adapter of the port under test.
        :type fx_order_keys: OrderKeys
        :param gap: Neighbouring keys of the new keys.
        :type gap: Gap
        """
        keys = list(fx_order_keys.spread(lower=gap.lower, upper=gap.upper, count=SPREAD_COUNT))
        expect(len(keys) == SPREAD_COUNT)
        expect(keys == sorted(set(keys), key=str.encode))
        expect(all(_lies_in(key, gap) for key in keys))
        assert_expectations()

    def test_zero_count_gives_no_keys(self, fx_order_keys: OrderKeys) -> None:
        """Verify asking for no keys gives none, as an import of a source without scans does.

        :param fx_order_keys: Adapter of the port under test.
        :type fx_order_keys: OrderKeys
        """
        assert list(fx_order_keys.spread(lower='a0', upper=None, count=0)) == []

    def test_negative_count_is_rejected(self, fx_order_keys: OrderKeys) -> None:
        """Reject a negative number of keys instead of answering with some keys.

        :param fx_order_keys: Adapter of the port under test.
        :type fx_order_keys: OrderKeys
        """
        with pytest.raises(ValueError, match='-1'):
            fx_order_keys.spread(lower=None, upper=None, count=-1)
