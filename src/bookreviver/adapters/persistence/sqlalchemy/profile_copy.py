"""The one pass that copies the recipe profiles of the accounts from the catalogue into the books of their owners.

The recipe profiles used to belong to an account and live in the catalogue. They belong to a book now, and a recipe
that was made from a profile names it by its identifier. :class:`RecipeProfileCopy` reads the library of the catalogue
and the profiles of the owner of every entry, and writes each profile into the database of the book with the same
identifier, so the recipes of the book need no change. A book is brought to the head revision of its schema first, as
opening it does, since the table of the profiles is made by a revision.

A profile that the book holds already is skipped, so the pass can run again. The catalogue is only read.
"""

from functools import partial
from typing import TYPE_CHECKING

from attrs import frozen

from bookreviver.adapters.persistence.sqlalchemy.book_database import database_file
from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyBookRepositories
from bookreviver.domain.enums import BlockKind

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping, Sequence
    from pathlib import Path

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.entities import AccountProfile, LibraryEntry
    from bookreviver.domain.ids import AccountId, BookDirectoryName
    from bookreviver.ports.persistence import CatalogueDatabase, CatalogueRepositories


@frozen(kw_only=True)
class CopiedBook:
    """What the pass did to one book.

    :ivar directory: Name of the directory of the book.
    :ivar copied: How many profiles of its owner were written into the book.
    :ivar skipped: How many profiles of its owner the book held already.
    """

    directory: BookDirectoryName
    copied: int
    skipped: int


def _library_and_profiles(
    repos: CatalogueRepositories,
) -> tuple[Sequence[LibraryEntry], Mapping[AccountId, Sequence[AccountProfile]]]:
    """Read the entry of every book and the profiles of every account that owns one.

    :param repos: Repositories of the read of the catalogue.
    :type repos: CatalogueRepositories
    :returns: The entries, and the profiles of each owner in the order of the stages, then by creation.
    :rtype: tuple[Sequence[LibraryEntry], Mapping[AccountId, Sequence[AccountProfile]]]
    """
    entries = repos.library.list_all()
    owners = {entry.owner_id for entry in entries}
    return entries, {owner: repos.recipe_profiles.list_for_account(owner) for owner in owners}


class RecipeProfileCopy:
    """Copies the profiles of the accounts of the catalogue into the books they own."""

    def __init__(self, *, catalogue: CatalogueDatabase, books: BookRegistry, root: Path, wait_seconds: float) -> None:
        """Copy from ``catalogue`` into the books ``books`` opens.

        :param catalogue: The catalogue, at its head revision, which is only read.
        :type catalogue: CatalogueDatabase
        :param books: The registry of the databases of the books, which brings each file to the head revision.
        :type books: BookRegistry
        :param root: Storage root of the directories of the books, where the file of each book lies.
        :type root: Path
        :param wait_seconds: How long a write waits for the writer of a book before it gives up.
        :type wait_seconds: float
        """
        self._catalogue = catalogue
        self._books = books
        self._root = root
        self._wait_seconds = wait_seconds

    async def run(self) -> AsyncIterator[CopiedBook]:
        """Copy the profiles into the books one at a time, the book that joined the library first being first.

        :returns: Iterator yielding what was done to each book once it is done.
        :rtype: AsyncIterator[CopiedBook]
        :raises NotFoundError: If a book of the library has no database file.
        :raises BookTooNewError: If the file of a book is at a revision this code does not ship.
        :raises BookBusyError: If the writer of a book was not free within the wait limit.
        """
        entries, profiles = await self._catalogue.read(_library_and_profiles)
        for entry in sorted(entries, key=lambda entry: entry.added_at):
            path = database_file(self._root, entry.directory)
            async with self._books.use(entry.directory, path) as database:
                copied = await database.write(
                    partial(_copy_into_book, profiles[entry.owner_id]),
                    kind=BlockKind.BOOK,
                    book=entry.directory,
                    wait_seconds=self._wait_seconds,
                )
            yield CopiedBook(directory=entry.directory, copied=copied, skipped=len(profiles[entry.owner_id]) - copied)


def _copy_into_book(profiles: Sequence[AccountProfile], session: Session) -> int:
    """Write the profiles the book does not hold yet, in the transaction of the book.

    :param profiles: The profiles of the owner of the book.
    :type profiles: Sequence[AccountProfile]
    :param session: Session of the transaction that writes the book.
    :type session: Session
    :returns: How many profiles were written.
    :rtype: int
    """
    repository = SqlAlchemyBookRepositories(session).recipe_profiles
    held = {profile.id for profile in repository.list_all()}
    missing = [profile for profile in profiles if profile.id not in held]
    repository.add_many(missing)
    return len(missing)
