"""The book a request works on, opened once from the ``book_id`` of its path.

Every route of one book takes ``Annotated[Book, Depends(open_book)]``, written in place so that ruff sees the
``book_id`` of the path the dependency reads. FastAPI resolves a dependency once per request, so ``fresh_book`` and the
route get the same ``Book``, and the owner of the book is checked once.
"""

from typing import Annotated, Final

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Path

from bookreviver.api.auth import ActorDep
from bookreviver.domain.ids import BookId
from bookreviver.services.book import Book
from bookreviver.services.books import BooksService

BOOK_ID_DESCRIPTION: Final = 'Identifier of the book'


@inject
async def open_book(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)], actor: ActorDep, books: FromDishka[BooksService]
) -> Book:
    """Open the book the path names, for the signed-in account.

    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param books: Books service of the request.
    :type books: BooksService
    :returns: The open book.
    :rtype: Book
    :raises NotFoundError: If the account has no such book.
    """
    return await books.open(actor, book_id)
