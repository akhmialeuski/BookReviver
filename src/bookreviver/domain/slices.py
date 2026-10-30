"""A slice of a collection: which part of it to read, and that part with the size of the whole."""

from typing import TYPE_CHECKING

from attrs import field, frozen, validators

if TYPE_CHECKING:
    from collections.abc import Sequence


@frozen(kw_only=True)
class SliceRequest:
    """Which part of a collection to read.

    :ivar offset: Number of items to skip from the start.
    :ivar limit: Largest number of items to return.
    """

    offset: int = field(default=0, validator=validators.ge(0))
    limit: int = field(default=50, validator=validators.gt(0))


@frozen(kw_only=True)
class Slice[ItemT]:
    """A part of a collection together with the size of the whole collection.

    :ivar items: Items of the requested part, in collection order.
    :ivar total: Number of items in the whole collection.
    """

    items: Sequence[ItemT]
    total: int = field(validator=validators.ge(0))
