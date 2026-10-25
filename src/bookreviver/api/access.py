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

from typing import TYPE_CHECKING, Annotated, Final
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, Path
from fastapi_pagination import Params
from fastapi_users.models import UserProtocol

from bookreviver.api.auth import ActorDep, signed_in_user
from bookreviver.api.pagination import slice_request
from bookreviver.domain.entities import Actor, LibraryEntry
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import AccountId, BookId
from bookreviver.domain.values import Slice
from bookreviver.ports.persistence import CatalogueDatabase
from bookreviver.services.book import Book

if TYPE_CHECKING:
    from bookreviver.domain.values import SliceRequest

BOOK_ID_DESCRIPTION: Final = 'Identifier of the book'


class Access:
    """The one place the API decides who may work on what, for the signed-in account of one request.

    A book is opened here and nowhere else: the library entry is read from the catalogue, its owner is compared with
    the account of the request, and a book of another account answers like a missing one, so the identifiers of other
    accounts cannot be probed. What was opened is kept, so a request that asks for the same book twice reads the entry
    once.
    """

    def __init__(self, actor: Actor, catalogue: CatalogueDatabase) -> None:
        """Keep the account of the request and the catalogue its library is read from.

        :param actor: The signed-in account.
        :type actor: Actor
        :param catalogue: Catalogue database of the request.
        :type catalogue: CatalogueDatabase
        """
        self._actor = actor
        self._catalogue = catalogue
        self._opened: dict[BookId, Book] = {}

    @property
    def actor(self) -> Actor:
        """The signed-in account, for a use case that creates a record it then owns.

        :returns: The signed-in account.
        :rtype: Actor
        """
        return self._actor

    async def book(self, book_id: BookId) -> Book:
        """Open a book of the signed-in account.

        :param book_id: Identifier of the book, from the path.
        :type book_id: BookId
        :returns: The open book.
        :rtype: Book
        :raises NotFoundError: If the library has no such book, or the book belongs to another account.
        """
        if (opened := self._opened.get(book_id)) is not None:
            return opened
        entry = await self._catalogue.read(lambda repos: repos.library.get(book_id))
        if entry.owner_id != self._actor.account_id:
            raise NotFoundError(book_id)
        return self._opened.setdefault(book_id, Book(entry.directory))

    async def books(self, request: SliceRequest) -> Slice[LibraryEntry]:
        """Read the window of the library entries of the signed-in account's books, the latest added first.

        A route opens the book of an entry with ``Book(entry.directory)``, and takes the identifier of the book from
        the entry.

        :param request: The window to read.
        :type request: SliceRequest
        :returns: The entries of the window, and the number of all the account's books.
        :rtype: Slice[LibraryEntry]
        """
        return await self._catalogue.read(lambda repos: repos.library.list_for_owner(self._actor.account_id, request))


@inject
def access(
    user: Annotated[UserProtocol[UUID], Depends(signed_in_user)], catalogue: FromDishka[CatalogueDatabase]
) -> Access:
    """Build the access of the signed-in account for the request.

    :param user: The signed-in, active, verified user that ``signed_in_user`` resolves to.
    :type user: UserProtocol[UUID]
    :param catalogue: Catalogue database of the request.
    :type catalogue: CatalogueDatabase
    :returns: The access whose library is the catalogue of the request.
    :rtype: Access
    """
    return Access(Actor(account_id=AccountId(user.id)), catalogue)


AccessDep = Annotated[Access, Depends(access)]


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
