"""Who may work on what: the one place that opens a book or a recipe profile for the signed-in account.

The services, the domain and the jobs never check an owner. A route of a book or of a profile takes it from a
dependency of this module, which reads it from the catalogue, compares its owner with the signed-in account and answers
a book or a profile of another account exactly like a missing one, so the identifiers of other accounts cannot be
probed. A list of the account's books or profiles is read here too, so a service is handed the objects it works on and
never chooses whose they are. The books service opens each book with its database, and takes no part in the choice.

Every route of one book takes ``Annotated[Book, Depends(open_book)]``, written in place so that ruff sees the
``book_id`` of the path the dependency reads. FastAPI resolves a dependency once per request, so ``fresh_book`` and the
route get the same ``Book``, and the owner of the book is checked once.
"""

from functools import partial
from typing import TYPE_CHECKING, Annotated, Final

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import Depends, Path
from fastapi_pagination import Params

from bookreviver.api.auth import ActorDep
from bookreviver.api.pagination import slice_request
from bookreviver.api.schemas.profiles import ProfileLinkBody, ProfileQuery
from bookreviver.domain.entities import RecipeProfile
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import BookId, RecipeProfileId
from bookreviver.domain.values import Slice, SliceRequest
from bookreviver.ports.persistence import UnitOfWork
from bookreviver.services.book import Book
from bookreviver.services.books import BooksService

if TYPE_CHECKING:
    from bookreviver.domain.entities import LibraryEntry
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import CatalogueRepositories

BOOK_ID_DESCRIPTION: Final = 'Identifier of the book'
PROFILE_ID_DESCRIPTION: Final = 'Identifier of the recipe profile'
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


async def books_of(uow: UnitOfWork, books_service: BooksService, account_id: AccountId) -> list[Book]:
    """Read every book of an account, window by window, the latest added first.

    :param uow: Unit of work of the request, whose ``read`` of the catalogue lists the library.
    :type uow: UnitOfWork
    :param books_service: Books service of the request, which opens each book with its database.
    :type books_service: BooksService
    :param account_id: The account.
    :type account_id: AccountId
    :returns: The books of the account.
    :rtype: list[Book]
    """
    books: list[Book] = []
    while True:
        request = SliceRequest(offset=len(books), limit=ENTRY_WINDOW)
        window = await uow.read(partial(_owner_window, account_id=account_id, request=request))
        books.extend(books_service.book(entry) for entry in window.items)
        if len(books) >= window.total or not window.items:
            return books


class AccountBooks:
    """The books of one account at a time, which the deletion of the account removes with their files first.

    :ivar _uow: Unit of work of the request, whose catalogue lists the books of an account.
    :ivar _books: Books service of the request, which deletes the books.
    """

    def __init__(self, *, uow: UnitOfWork, books: BooksService) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request.
        :type uow: UnitOfWork
        :param books: Books service of the request.
        :type books: BooksService
        """
        self._uow = uow
        self._books = books

    async def delete_all(self, account_id: AccountId) -> None:
        """Delete every book of an account with all its files, which must happen before the account is deleted.

        :param account_id: The account.
        :type account_id: AccountId
        """
        await self._books.delete_all(await books_of(self._uow, self._books, account_id))


@inject
async def open_book(
    book_id: Annotated[BookId, Path(description=BOOK_ID_DESCRIPTION)],
    actor: ActorDep,
    books: FromDishka[BooksService],
) -> Book:
    """Open the book the path names, when the signed-in account owns it.

    :param book_id: Identifier of the book, from the path.
    :type book_id: BookId
    :param actor: The signed-in account.
    :type actor: Actor
    :param books: Books service of the request, which opens the book with its database.
    :type books: BooksService
    :returns: The open book.
    :rtype: Book
    :raises NotFoundError: If the library has no such book, or the book belongs to another account.
    """
    book = await books.open(book_id)
    if book.owner_id != actor.account_id:
        raise NotFoundError(book_id)
    return book


@inject
async def own_books(
    params: Annotated[Params, Depends()],
    actor: ActorDep,
    uow: FromDishka[UnitOfWork],
    books: FromDishka[BooksService],
) -> Slice[Book]:
    """Read the window of the signed-in account's books the page parameters ask for, the latest added first.

    :param params: Page number and size from the query.
    :type params: Params
    :param actor: The signed-in account.
    :type actor: Actor
    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :param books: Books service of the request, which opens each book with its database.
    :type books: BooksService
    :returns: The books of the window, and the number of all the account's books.
    :rtype: Slice[Book]
    """
    request = slice_request(params)
    window = await uow.read(lambda repos: repos.library.list_for_owner(actor.account_id, request))
    return Slice(items=[books.book(entry) for entry in window.items], total=window.total)


@inject
async def all_own_books(actor: ActorDep, uow: FromDishka[UnitOfWork], books: FromDishka[BooksService]) -> list[Book]:
    """Read every book of the signed-in account, which a change of one of its profiles reaches.

    :param actor: The signed-in account.
    :type actor: Actor
    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :param books: Books service of the request, which opens each book with its database.
    :type books: BooksService
    :returns: The books of the account.
    :rtype: list[Book]
    """
    return await books_of(uow, books, actor.account_id)


def _owned_profile(repos: CatalogueRepositories, account_id: AccountId, profile_id: RecipeProfileId) -> RecipeProfile:
    """Read a profile in a read of the catalogue, when the account owns it.

    :param repos: Repositories of the read.
    :type repos: CatalogueRepositories
    :param account_id: The signed-in account.
    :type account_id: AccountId
    :param profile_id: Identifier of the profile.
    :type profile_id: RecipeProfileId
    :returns: The profile.
    :rtype: RecipeProfile
    :raises NotFoundError: If there is no such profile, or it belongs to another account.
    """
    profile = repos.recipe_profiles.get(profile_id)
    if profile.account_id != account_id:
        raise NotFoundError(profile_id)
    return profile


@inject
async def open_profile(
    profile_id: Annotated[RecipeProfileId, Path(description=PROFILE_ID_DESCRIPTION)],
    actor: ActorDep,
    uow: FromDishka[UnitOfWork],
) -> RecipeProfile:
    """Open the profile the path names, when the signed-in account owns it.

    :param profile_id: Identifier of the profile, from the path.
    :type profile_id: RecipeProfileId
    :param actor: The signed-in account.
    :type actor: Actor
    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :returns: The profile.
    :rtype: RecipeProfile
    :raises NotFoundError: If there is no such profile, or it belongs to another account.
    """
    return await uow.read(lambda repos: _owned_profile(repos, actor.account_id, profile_id))


@inject
async def linked_profile(body: ProfileLinkBody, actor: ActorDep, uow: FromDishka[UnitOfWork]) -> RecipeProfile | None:
    """Open the profile the body of a link names, when the signed-in account owns it, or none for an unlink.

    :param body: The profile, or null to unlink the recipe.
    :type body: ProfileLinkBody
    :param actor: The signed-in account.
    :type actor: Actor
    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :returns: The profile, or None when the body names none.
    :rtype: RecipeProfile | None
    :raises NotFoundError: If there is no such profile, or it belongs to another account.
    """
    profile_id = body.profile_id
    if profile_id is None:
        return None
    return await uow.read(lambda repos: _owned_profile(repos, actor.account_id, profile_id))


@inject
async def own_profiles(
    query: Annotated[ProfileQuery, Depends()], actor: ActorDep, uow: FromDishka[UnitOfWork]
) -> Slice[RecipeProfile]:
    """Read the window of the signed-in account's profiles, in the order of the stages, then by creation.

    :param query: Page number and size, and the stage to list, from the query.
    :type query: ProfileQuery
    :param actor: The signed-in account.
    :type actor: Actor
    :param uow: Unit of work of the request.
    :type uow: UnitOfWork
    :returns: The profiles of the window, and the number of all that match.
    :rtype: Slice[RecipeProfile]
    """
    request = slice_request(query)
    profiles = await uow.read(lambda repos: repos.recipe_profiles.list_for_account(actor.account_id, query.stage))
    return Slice(items=profiles[request.offset : request.offset + request.limit], total=len(profiles))
