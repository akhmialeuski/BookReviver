"""The base of a port that serves one part of a book, which ``app`` registers an implementation of once.

A part of a book, such as its event channel or its job queue, is opened from the name of the directory of the book, and
whoever holds that name opens the part itself. A port of such a part inherits :class:`BookPart`, so it is opened by
``Port.open(dir_name)`` with no object that carries the implementation to the caller. ``app`` calls
``Port.register(opener)`` once when the application starts, and a test registers a fake the same way. A second
registration of a port replaces the first.
"""

from typing import TYPE_CHECKING, Any, Self, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.ids import BookDirectoryName

NOT_REGISTERED: str = '{port} is not registered, so a book cannot open it. Register an implementation first.'

# The opener each port was registered with, by the port that was asked to register it
_OPENERS: dict[type[BookPart], Callable[[BookDirectoryName], Any]] = {}


class BookPart:
    """A port of one part of a book, opened by the directory name of the book from the implementation registered."""

    @classmethod
    def register(cls, opener: Callable[[BookDirectoryName], Self]) -> None:
        """Register how the part of a book is opened, replacing the registration before it.

        :param opener: Function from the directory name of a book to its part.
        :type opener: Callable[[BookDirectoryName], Self]
        """
        _OPENERS[cls] = opener

    @classmethod
    def open(cls, dir_name: BookDirectoryName) -> Self:
        """Open the part of the book whose directory has the given name.

        Every call for one name reaches the same part of the book, so the objects of one book share it.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: The part of the book.
        :rtype: Self
        :raises RuntimeError: If no implementation is registered.
        """
        if (opener := _OPENERS.get(cls)) is None:
            err_msg = NOT_REGISTERED.format(port=cls.__name__)
            raise RuntimeError(err_msg)
        return cast('Self', opener(dir_name))
