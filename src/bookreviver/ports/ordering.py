"""Ordering port: the keys that place the pages of a book in order.

A page stores its position as an order key, a string that sorts byte by byte between the keys of its neighbours. A new
key between two keys always exists, so inserting or moving a page writes one row and renumbers nothing. The domain may
import only the standard library and attrs, so the keys come from a port whose adapter wraps a library.

The methods are synchronous, like ``Clock.now``: they compute a string in memory and never wait on I/O.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


class OrderKeys(ABC):
    """Builds order keys between the keys of two neighbouring pages."""

    @abstractmethod
    def between(self, *, lower: str | None, upper: str | None) -> str:
        """Return a key that sorts after ``lower`` and before ``upper``.

        :param lower: Key of the page before the new position, or None at the start of the book.
        :type lower: str | None
        :param upper: Key of the page after the new position, or None at the end of the book.
        :type upper: str | None
        :returns: A new key strictly between the two, the first key of a book when both are None.
        :rtype: str
        :raises ValueError: If a key is not a valid order key or ``lower`` does not sort before ``upper``.
        """

    @abstractmethod
    def spread(self, *, lower: str | None, upper: str | None, count: int) -> Sequence[str]:
        """Return ``count`` keys in ascending order, all after ``lower`` and before ``upper``.

        :param lower: Key of the page before the new positions, or None at the start of the book.
        :type lower: str | None
        :param upper: Key of the page after the new positions, or None at the end of the book.
        :type upper: str | None
        :param count: Number of keys, such as one per new scan appended to the book.
        :type count: int
        :returns: The keys in ascending order, none for a count of zero.
        :rtype: Sequence[str]
        :raises ValueError: If a key is not a valid order key, ``lower`` does not sort before ``upper``, or ``count``
                            is negative.
        """
