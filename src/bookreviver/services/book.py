"""One open book, opened once when a request or a job reaches it.

For a request, the access dependency ``open_book`` of the API opens a book of the signed-in account after it has
checked the owner, so a use case that takes a ``Book`` works on it and checks nothing itself.
"""

from typing import TYPE_CHECKING

from attrs import frozen

if TYPE_CHECKING:
    from bookreviver.domain.ids import AccountId, BookId


@frozen
class Book:
    """A book the acting account opened.

    :ivar id: Identifier of the book in the library.
    :ivar owner_id: The account that owns the book, which opened it.
    """

    id: BookId
    owner_id: AccountId
