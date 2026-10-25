"""Who may work on what: the one place that opens a book for the signed-in account.

The services, the domain and the jobs never check an owner. A route of a book takes it from a dependency of this
module, which reads it from the catalogue, compares its owner with the signed-in account and answers a book of another
account exactly like a missing one, so the identifiers of other accounts cannot be probed. A list of the account's books
is read here too, so a service is handed the objects it works on and never chooses whose they are. The recipe profiles
live in the book, so whoever may open the book may work on its profiles.

Every route of one book takes ``Annotated[Book, Depends(open_book)]``, written in place so that ruff sees the
``book_id`` of the path the dependency reads. ``open_book`` opens the book of the entry ``own_entry`` read, and the
deletion of a book takes that entry itself. FastAPI resolves a dependency once per request, so ``fresh_book`` and the
route get the same ``Book``, and the entry is read and its owner checked once.
"""

from functools import partial
from typing import TYPE_CHECKING, Annotated, Final

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, Path
from fastapi_pagination import Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import slice_request
from bookreviver.domain.entities import LibraryEntry
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import BookId
from bookreviver.domain.values import Slice, SliceRequest
from bookreviver.ports.persistence import CatalogueDatabase
from bookreviver.services.book import Book

if TYPE_CHECKING:
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import CatalogueRepositories
    from bookreviver.services.books import BooksService

BOOK_ID_DESCRIPTION: Final = 'Identifier of the book'
# Entries of the library read at a time when every book of an account is wanted
ENTRY_WINDOW: Final = 1_000


def _owner_window(repos: CatalogueRepositories, *, account_id: AccountId, request: SliceRequest) -> Slice[LibraryEntry]:
    """Read one window of the entries of an account's books, which the work of a read of the catalogue calls.

    :param repos: Repositories of the read.
    :type repos: CatalogueRepositories
    :param account_id: The account.
    :type account_id: AccountId
    :param request: Offset and limit of the window.
    :type request: SliceRequest
    :returns: The entries of the window, and the number of all the account's entries.
    :rtype: Slice[LibraryEntry]
    """
    return repos.library.list_for_owner(account_id, request)


async def entries_of(catalogue: CatalogueDatabase, account_id: AccountId) -> list[LibraryEntry]:
    """Read the library entry of every book of an account, window by window, the latest added first.

    :param catalogue: Catalogue database of the request, whose ``read`` of the catalogue lists the library.
    :type catalogue: CatalogueDatabase
    :param account_id: The account.
    :type account_id: AccountId
    :returns: The entries of the books of the account.
    :rtype: list[LibraryEntry]
    """
    entries: list[LibraryEntry] = []
    while True:
        request = SliceRequest(offset=len(entries), limit=ENTRY_WINDOW)
        window = await catalogue.read(partial(_owner_window, account_id=account_id, request=request))
        entries.extend(window.items)
        if len(entries) >= window.total or not window.items:
            return entries


class AccountBooks:
    """The books of one account at a time, which the deletion of the account removes with their files first.

    :ivar _catalogue: Catalogue database of the request, whose catalogue lists the books of an account.
    :ivar _books: Books service of the request, which deletes the books.
    """

    def __init__(self, *, catalogue: CatalogueDatabase, books: BooksService) -> None:
        """Work over the ports of one request.

        :param catalogue: Catalogue database of the request.
        :type catalogue: CatalogueDatabase
        :param books: Books service of the request.
        :type books: BooksService
        """
        self._catalogue = catalogue
        self._books = books

    async def delete_all(self, account_id: AccountId) -> None:
        """Delete every book of an account with all its files, which must happen before the account is deleted.

        Each book is deleted entry first, so a failure keeps the books not deleted yet, and calling this again deletes
        them.

        :param account_id: The account.
        :type account_id: AccountId
        """
        for entry in await entries_of(self._catalogue, account_id):
            await self._books.delete(entry)


@inject
async def own_entry(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    catalogue: FromDishka[CatalogueDatabase],
) -> LibraryEntry:
    """Read the library entry of the book the path names, when the signed-in account owns it.

    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param catalogue: Catalogue database of the request, whose library entry of the book names its owner.
    :type catalogue: CatalogueDatabase
    :returns: The library entry of the book.
    :rtype: LibraryEntry
    :raises NotFoundError: If the library has no such book, or the book belongs to another account.
    """
    entry = await catalogue.read(lambda repos: repos.library.get(book_id))
    if entry.owner_id != actor.account_id:
        raise NotFoundError(book_id)
    return entry


def open_book(entry: Annotated[LibraryEntry, Depends(own_entry)]) -> Book:
    """Open the book the path names, when the signed-in account owns it.

    :param entry: The library entry of the book, read once for the request.
    :type entry: LibraryEntry
    :returns: The open book.
    :rtype: Book
    """
    return Book(entry.directory)


@inject
async def own_books(
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    catalogue: FromDishka[CatalogueDatabase],
) -> Slice[LibraryEntry]:
    """Read the window of the library entries of the signed-in account's books the page parameters ask for.

    The latest added comes first. A route opens the book of an entry with ``Book(entry.directory)``, and takes the
    identifier of the book from the entry.

    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param catalogue: Catalogue database of the request.
    :type catalogue: CatalogueDatabase
    :returns: The entries of the window, and the number of all the account's books.
    :rtype: Slice[LibraryEntry]
    """
    request = slice_request(params)
    return await catalogue.read(lambda repos: repos.library.list_for_owner(actor.account_id, request))
