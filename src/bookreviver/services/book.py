"""One open book: a book of the acting account, opened once when a request or a job reaches it.

Only ``BooksService.open`` opens a book for an account, after it has checked the owner, so a use case that takes a
``Book`` works on a book its actor may work on and checks nothing itself.
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
