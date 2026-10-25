"""Use cases of the set of books: opening, creating, importing and deleting them.

What reads or changes one open book, such as its description or its archive, is a method of ``Book``.
A use case works on the ``Book`` objects it is given and checks no owner: the access dependencies of the API open a
book of the signed-in account, and they choose the books of a list. The lookups ``book_page`` and ``book_pages`` are
synchronous functions over repositories: the work of ``Book.read`` or ``Book.change`` calls them with the repositories
it receives, and a caller outside a transaction wraps the call in ``Book.read``. ``open`` opens the book of an
identifier, which the jobs use, and ``book`` does the same for a library entry that is already read, which the access
dependencies of the API use. Neither checks an owner.

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

from bookreviver.domain.entities import LibraryEntry
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import BookDirectoryName, BookId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import SliceRequest
from bookreviver.services.book import Book

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable
    from pathlib import Path
    from typing import BinaryIO

    from bookreviver.domain.entities import Actor, Page
    from bookreviver.domain.ids import StorageKey
    from bookreviver.domain.values import BookDetails
    from bookreviver.ports.persistence import CatalogueDatabase, PageRepository

# How many pages are read from the book at a time, when a use case needs all of them
PAGE_WINDOW: int = 1_000


def book_pages(pages: PageRepository) -> list[Page]:
    """Read every page of the book in book order, window by window, which a transaction of the book calls.

    :param pages: Repository of the transaction to read the pages from.
    :type pages: PageRepository
    :returns: The pages of the book, the placeholders and the pages kept out of the book included.
    :rtype: list[Page]
    """
    book: list[Page] = []
    while True:
        window = pages.list_window(SliceRequest(offset=len(book), limit=PAGE_WINDOW))
        book.extend(window.items)
        if len(book) >= window.total or not window.items:
            return book


class BooksService:
    """Books of the acting account, with their description and their files."""

    def __init__(self, *, catalogue: CatalogueDatabase) -> None:
        """Work over the catalogue database of one request, which keeps the library entries of the books.

        :param catalogue: Catalogue database of the request, which runs the work of every use case on the catalogue in
                          one transaction.
        :type catalogue: CatalogueDatabase
        """
        self._catalogue = catalogue

    def book(self, entry: LibraryEntry) -> Book:
        """Open the book a library entry names, without reading anything.

        :param entry: The entry of the library.
        :type entry: LibraryEntry
        :returns: The book, whose owner is the one the entry names.
        :rtype: Book
        """
        return Book(entry.book_id, entry.owner_id, entry.directory)

    async def open(self, book_id: BookId) -> Book:
        """Open the book of an identifier, whoever owns it.

        Who may work on the book is decided by the caller: the access dependencies of the API for a request, and
        nobody for a job, which acts for no account.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :returns: The book.
        :rtype: Book
        :raises NotFoundError: If the library has no such book.
        """
        return self.book(await self._catalogue.read(lambda repos: repos.library.get(book_id)))

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
        async with self.book(entry).assets.readable(key) as path:
            yield path

    async def listed(self) -> list[Book]:
        """Open every book the library lists, whoever owns it, for the passes of the start.

        :returns: The open books, one for each entry of the library.
        :rtype: list[Book]
        """
        entries = await self._catalogue.read(lambda repos: repos.library.list_all())
        return [self.book(entry) for entry in entries]

    async def create(self, owner: Actor, details: BookDetails) -> Book:
        """Create an empty book of an account: its directory, its database with its description, then its entry.

        :param owner: Account the entry of the library names as the owner.
        :type owner: Actor
        :param details: Description of the book.
        :type details: BookDetails
        :returns: The new book, open for its owner, which has no pages, sources or scans yet.
        :rtype: Book
        """
        entry = self._new_entry(owner)
        book = self.book(entry)
        await book.create(details)
        await self._catalogue.change(lambda repos: repos.library.add(entry))
        return book

    async def import_archive(self, owner: Actor, archive: BinaryIO) -> Book:
        """Take the archive of a book in as a new book of an account, under a new identifier.

        The book unpacks the archive into its new directory and brings its database to this release, and the entry of
        the library comes last, as for a book created here. The record of the book names no book, so nothing in it
        changes. An archive that is refused removes the whole directory.

        :param owner: Account the entry of the library names as the owner.
        :type owner: Actor
        :param archive: The archive, a file that can be read from any position.
        :type archive: BinaryIO
        :returns: The new book, open for its owner.
        :rtype: Book
        :raises InvalidArchiveError: If the file is not the archive of a book.
        :raises BookTooNewError: If the book was exported by a newer release.
        """
        entry = self._new_entry(owner)
        book = self.book(entry)
        await book.unpack(archive)
        await self._catalogue.change(lambda repos: repos.library.add(entry))
        return book

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

    async def delete(self, book: Book) -> None:
        """Delete the book's entry in the library, then the book with its directory.

        The entry goes first, so a request that comes later finds no book. A failure after the entry leaves a directory
        nobody lists, and ``Book.delete`` of the same book removes it.

        :param book: The open book.
        :type book: Book
        """
        await self._catalogue.change(lambda repos: repos.library.delete(book.id))
        await book.delete()

    async def delete_all(self, books: Iterable[Book]) -> None:
        """Delete the books with all their files, as the deletion of the account that owns them needs.

        Each book is deleted in transactions of its own, entry first, so a failure keeps the books not yet deleted,
        and calling this again with the books that are left deletes them.

        :param books: The books to delete.
        :type books: Iterable[Book]
        """
        for book in books:
            await self.delete(book)
