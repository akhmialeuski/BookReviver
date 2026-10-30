"""Use cases of the set of books: creating, importing, listing and deleting them, and serving their derived files.

What reads or changes one open book, such as its description or its archive, is a method of ``Book``.
A use case works on the ``Book`` objects it is given and checks no owner: the ``Access`` of the API opens a book of the
signed-in account, and chooses the books of a list. A book knows no identifier, so ``create`` and ``import_archive``
return the identifier they gave the new book, and ``delete`` takes the identifier and the book the caller has opened.

A new book makes itself, with ``Book.create``, and an imported book unpacks itself from its archive, with
``Book.unpack``. Either way its library entry comes last, so a creation broken off half way leaves a directory nobody
lists. A book is deleted entry first, then ``Book.delete`` closes its database and removes its directory whole. Once the
entry is gone no request can open the book, so a deletion broken off half way leaves a directory nobody lists, which
only takes room on the disk, and the use case that holds the book can repeat ``Book.delete`` to remove what is left.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import BookDirectoryName, BookId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.library import LibraryEntry
from bookreviver.services.book import Book

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
    from typing import BinaryIO

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookDetails
    from bookreviver.domain.ids import StorageKey
    from bookreviver.ports.persistence import CatalogueDatabase


class BooksService:
    """Keeps the library entries of the books in the catalogue, and creates, imports, lists and deletes the books."""

    def __init__(self, *, catalogue: CatalogueDatabase) -> None:
        """Work over the catalogue database of one request, which keeps the library entries of the books.

        :param catalogue: Catalogue database of the request, which runs the work of every use case on the catalogue in
                          one transaction.
        :type catalogue: CatalogueDatabase
        """
        self._catalogue = catalogue

    @asynccontextmanager
    async def open_asset(self, book_id: BookId, key: StorageKey) -> AsyncIterator[Path]:
        """Give a local path of the derived file at ``key`` for as long as the context is open.

        The derived files are read by anyone who has the identifier of their book and their key, with no owner checked,
        from the directory the library entry of the book names. A key under the book's ``incoming/`` or ``sources/``
        belongs to no derived file and is reported as not found without a look at the library or the store.

        :param book_id: The book of the file, such as one taken from the address of an image.
        :type book_id: BookId
        :param key: Storage key of a derived file relative to the directory of its book, from the same address.
        :type key: StorageKey
        :returns: Context manager yielding the path of the file, valid while the context is open.
        :rtype: AsyncIterator[Path]
        :raises NotFoundError: If the key is not under the ``assets/`` of a book, the library has no such book, or
                               nothing is stored at the key.
        """
        if not BookKeys.is_asset(key):
            raise NotFoundError(key)
        entry = await self._catalogue.read(lambda repos: repos.library.get(book_id))
        async with Book(entry.directory).assets.readable(key) as path:
            yield path

    async def listed(self) -> list[Book]:
        """Open every book the library lists, whoever owns it, for the passes of the start.

        :returns: The open books, one for each entry of the library.
        :rtype: list[Book]
        """
        entries = await self._catalogue.read(lambda repos: repos.library.list_all())
        return [Book(entry.directory) for entry in entries]

    async def create(self, owner: Actor, details: BookDetails) -> BookId:
        """Create an empty book of an account: its directory, its database with its description, then its entry.

        :param owner: Account the entry of the library names as the owner.
        :type owner: Actor
        :param details: Description of the book.
        :type details: BookDetails
        :returns: The identifier of the new book, which has no pages, sources or scans yet.
        :rtype: BookId
        """
        entry = self._new_entry(owner)
        await Book(entry.directory).create(details)
        await self._catalogue.change(lambda repos: repos.library.add(entry))
        return entry.book_id

    async def import_archive(self, owner: Actor, archive: BinaryIO) -> BookId:
        """Take the archive of a book in as a new book of an account, under a new identifier.

        The book unpacks the archive into its new directory and brings its database to this release, and the entry of
        the library comes last, as for a book created here. The record of the book names no book, so nothing in it
        changes. An archive that is refused removes the whole directory.

        :param owner: Account the entry of the library names as the owner.
        :type owner: Actor
        :param archive: The archive, a file that can be read from any position.
        :type archive: BinaryIO
        :returns: The identifier of the new book.
        :rtype: BookId
        :raises InvalidArchiveError: If the file is not the archive of a book.
        :raises BookTooNewError: If the book was exported by a newer release.
        """
        entry = self._new_entry(owner)
        await Book(entry.directory).unpack(archive)
        await self._catalogue.change(lambda repos: repos.library.add(entry))
        return entry.book_id

    def _new_entry(self, owner: Actor) -> LibraryEntry:
        """Build the library entry of a new book of an account, whose directory is named by its new identifier.

        :param owner: Account the entry names as the owner.
        :type owner: Actor
        :returns: The entry, not stored yet, added now.
        :rtype: LibraryEntry
        """
        book_id = BookId(uuid4())
        return LibraryEntry(
            book_id=book_id,
            owner_id=owner.account_id,
            directory=BookDirectoryName(str(book_id)),
            added_at=datetime.now(UTC),
        )

    async def delete(self, *, book_id: BookId, book: Book) -> None:
        """Delete the book's entry in the library, then the book with its directory.

        The entry goes first, so a request that comes later finds no book. A failure after the entry leaves a directory
        nobody lists, and ``Book.delete`` of the same directory removes it.

        :param book_id: Identifier of the book, which the library entry is kept under.
        :type book_id: BookId
        :param book: The book to delete, opened for the account that owns it.
        :type book: Book
        :raises NotFoundError: If the library holds the entry no more, as when another deletion took it first.
        """
        await self._catalogue.change(lambda repos: repos.library.delete(book_id))
        await book.delete()
