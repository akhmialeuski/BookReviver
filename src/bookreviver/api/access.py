"""Who may work on what: the one place that opens a book for the signed-in account.

The services, the domain and the jobs never check an owner. Every route of the API is mounted under a router whose
dependency is :func:`access`, so a request without a session is refused before any route runs. A route that works on a
book, lists the account's books or creates a record the account then owns declares ``access: AccessDep`` and asks the
:class:`Access` it gets. The recipe profiles live in the book, so whoever may open the book may work on its profiles.

A route of one book declares ``book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)]`` in place, so that
ruff sees the path parameter, and opens the book with ``await access.book(book_id)``. FastAPI resolves a dependency once
per request, so the guard, ``fresh_book`` and the route share one :class:`Access`, which reads the library entry of a
book once and checks its owner once.
"""

from typing import TYPE_CHECKING, Annotated, Final
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends
from fastapi_users.models import UserProtocol

from bookreviver.api.auth import signed_in_user
from bookreviver.domain.entities import Actor
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import AccountId
from bookreviver.ports.persistence import CatalogueDatabase
from bookreviver.services.book import Book

if TYPE_CHECKING:
    from bookreviver.domain.entities import LibraryEntry
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.values import Slice, SliceRequest

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
