"""Order keys from the fractional-indexing package, a port of the algorithm Figma and Replicache use.

A key is a base-62 string whose integer part grows with every key appended at the end, so a book gains pages without
ever running out of keys, and a key between two neighbours gets one more fractional digit at most. The package reports
every refused input as its own ``FIError``, which the adapter reports as the ``ValueError`` of the port.
"""

from typing import TYPE_CHECKING, override

from fractional_indexing import FIError, generate_key_between, generate_n_keys_between

from bookreviver.ports.ordering import OrderKeys

if TYPE_CHECKING:
    from collections.abc import Sequence

# Message of a refused gap, naming both neighbours and the package's reason
NO_ROOM: str = 'No order key lies between {lower} and {upper}: {reason}'


class FractionalOrderKeys(OrderKeys):
    """Order keys of the fractional-indexing package in its default base-62 alphabet."""

    @override
    def between(self, *, lower: str | None, upper: str | None) -> str:
        """Return a key that sorts after ``lower`` and before ``upper``.

        :param lower: Key of the page before the new position, or None at the start of the book.
        :type lower: str | None
        :param upper: Key of the page after the new position, or None at the end of the book.
        :type upper: str | None
        :returns: A new key strictly between the two, ``a0`` when both are None.
        :rtype: str
        :raises ValueError: If a key is not a valid order key or ``lower`` does not sort before ``upper``.
        """
        try:
            return generate_key_between(lower, upper)
        except FIError as error:
            raise ValueError(NO_ROOM.format(lower=lower, upper=upper, reason=error)) from error

    @override
    def spread(self, *, lower: str | None, upper: str | None, count: int) -> Sequence[str]:
        """Return ``count`` keys in ascending order, all after ``lower`` and before ``upper``.

        :param lower: Key of the page before the new positions, or None at the start of the book.
        :type lower: str | None
        :param upper: Key of the page after the new positions, or None at the end of the book.
        :type upper: str | None
        :param count: Number of keys.
        :type count: int
        :returns: The keys in ascending order, none for a count of zero.
        :rtype: Sequence[str]
        :raises ValueError: If a key is not a valid order key, ``lower`` does not sort before ``upper``, or ``count``
                            is negative.
        """
        # The package answers a negative count with one key instead of refusing it
        if count < 0:
            err_msg = f'Cannot make {count} order keys.'
            raise ValueError(err_msg)
        try:
            return generate_n_keys_between(lower, upper, count)
        except FIError as error:
            raise ValueError(NO_ROOM.format(lower=lower, upper=upper, reason=error)) from error
