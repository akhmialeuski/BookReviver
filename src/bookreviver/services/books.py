"""Use cases of the set of books: opening, creating, importing and deleting them.

What reads or changes one open book, such as its overview, its description or its archive, is a method of ``Book``.
A use case works on the ``Book`` objects it is given and checks no owner: the access dependencies of the API open a
book of the signed-in account, and they choose the books of a list. The lookups ``book_page`` and ``book_pages`` are
synchronous functions over repositories: the work of ``Book.read`` or ``Book.change`` calls them with the repositories
it receives, and a caller outside a transaction wraps the call in ``Book.read``. ``open`` opens the book of an
identifier with its database, which the jobs use, and ``book`` does the same for a library entry that is already read,
which the access dependencies of the API use. Neither checks an owner.

A book is deleted files first, then its store, and the library entry last. Every use case of the book needs the entry,
a repeated deletion included, so an entry deleted first would leave the files of a failed deletion out of every
request's reach for good. A deletion that fails part-way keeps the entry, and repeating it removes what is left. Until
then the book may lack some of its files, which only a deletion the owner asked for can cause. The files go outside any
transaction. A start pass, ``sweep``, finishes a deletion or a creation that was broken off half way and is not
repeated by anyone: a store without an entry, or an entry without a store.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import frozen

from bookreviver.domain.entities import BookRecord, LibraryEntry
from bookreviver.domain.ids import BookId
from bookreviver.domain.values import SliceRequest
from bookreviver.services.book import Book

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import BinaryIO

    from bookreviver.domain.entities import Actor, BookOverview, Page
    from bookreviver.domain.values import BookDetails
    from bookreviver.ports.persistence import BookDatabases, PageRepository, UnitOfWork
    from bookreviver.ports.storage import AssetStore, SourceStore
    from bookreviver.services.book import BookTools

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


@frozen(kw_only=True)
class BookStores:
    """The stores of the files of a book, emptied when the book is deleted.

    :ivar sources: Store of the uploaded sources.
    :ivar assets: Store of the derived files.
    """

    sources: SourceStore
    assets: AssetStore


class BooksService:
    """Books of the acting account, with their description and their files."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        databases: BookDatabases,
        tools: BookTools,
        files: BookStores,
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, which runs the work of every use case on the catalogue in one
                    transaction.
        :type uow: UnitOfWork
        :param databases: Opens the database of a book, which runs the transactions of that book.
        :type databases: BookDatabases
        :param tools: What every book the service opens works with, whose clock also sets the creation times and whose
                  archive also unpacks the imported books.
        :type tools: BookTools
        :param files: Stores of the source files and the derived files, emptied when a book is deleted.
        :type files: BookStores
        """
        self._uow = uow
        self._databases = databases
        self._tools = tools
        self._files = files

    def book(self, entry: LibraryEntry) -> Book:
        """Open the book a library entry names with its database, without reading anything.

        :param entry: The entry of the library.
        :type entry: LibraryEntry
        :returns: The book, whose owner is the one the entry names.
        :rtype: Book
        """
        return Book(entry.book_id, entry.owner_id, self._databases.open(entry.book_id), self._tools)

    async def open(self, book_id: BookId) -> Book:
        """Open the book of an identifier with its database, whoever owns it.

        Who may work on the book is decided by the caller: the access dependencies of the API for a request, and
        nobody for a job, which acts for no account.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :returns: The book.
        :rtype: Book
        :raises NotFoundError: If the library has no such book.
        """
        return self.book(await self._uow.read(lambda repos: repos.library.get(book_id)))

    async def listed(self) -> list[Book]:
        """Open every book the library lists, whoever owns it, for the passes of the start.

        :returns: The open books, one for each entry of the library.
        :rtype: list[Book]
        """
        entries = await self._uow.read(lambda repos: repos.library.list_all())
        return [self.book(entry) for entry in entries]

    async def create(self, owner: Actor, details: BookDetails) -> BookOverview:
        """Create an empty book of an account: its store first, then its entry in the library.

        :param owner: Account the entry of the library names as the owner.
        :type owner: Actor
        :param details: Description of the book.
        :type details: BookDetails
        :returns: The stored book, which has no pages, sources or scans yet.
        :rtype: BookOverview
        """
        moment = self._tools.clock.now()
        book_record = await self._uow.create_book(
            BookRecord(id=BookId(uuid4()), details=details, created_at=moment, updated_at=moment)
        )
        entry = LibraryEntry(book_id=book_record.id, owner_id=owner.account_id, added_at=moment)
        await self._uow.change(lambda repos: repos.library.add(entry))
        return await self.book(entry).overview()

    async def import_archive(self, owner: Actor, archive: BinaryIO) -> BookOverview:
        """Take the archive of a book in as a new book of an account, under a new identifier.

        The archive is unpacked database first, then the database is brought to this release and the record of its book
        renamed, and the entry of the library comes last, as for a book created here. A failure removes what was
        unpacked, and an import broken off is a store without an entry, which the start pass removes.

        :param owner: Account the entry of the library names as the owner.
        :type owner: Actor
        :param archive: The archive, a file that can be read from any position.
        :type archive: BinaryIO
        :returns: The new book with its counts and progress.
        :rtype: BookOverview
        :raises InvalidArchiveError: If the file is not the archive of a book.
        :raises BookTooNewError: If the book was exported by a newer release.
        """
        book_id = BookId(uuid4())
        try:
            await self._tools.archive.unpack(book_id, archive)
            await self._uow.adopt_book(book_id)
        except Exception:
            await self._erase(book_id)
            raise
        entry = LibraryEntry(book_id=book_id, owner_id=owner.account_id, added_at=self._tools.clock.now())
        await self._uow.change(lambda repos: repos.library.add(entry))
        return await self.book(entry).overview()

    async def _erase(self, book_id: BookId) -> None:
        """Delete the source files and derived files of a book, then its store, which a repeated call finishes.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        """
        await self._files.sources.delete_book(book_id)
        await self._files.assets.delete_book(book_id)
        await self._uow.delete_book(book_id)

    async def _forget(self, book_id: BookId) -> None:
        """Delete the entry of a book from the library.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        """
        await self._uow.change(lambda repos: repos.library.delete(book_id))

    async def delete(self, book: Book) -> None:
        """Delete the book's source files and derived files, then its store, then its entry in the library.

        The files go outside any transaction, so a slow store holds no lock of the book. A failure at any step leaves
        the entry in place, and calling this again finishes the deletion.

        :param book: The open book.
        :type book: Book
        """
        await self._erase(book.id)
        # The entry goes last: only an existing entry lets a repeated call reach what a failed call left behind
        await self._forget(book.id)

    async def delete_all(self, books: Iterable[Book]) -> None:
        """Delete the books with all their files, as the deletion of the account that owns them needs.

        Each book is deleted in transactions of its own, files first, so a failure keeps the books not yet deleted,
        and calling this again with the books that are left deletes them.

        :param books: The books to delete.
        :type books: Iterable[Book]
        """
        for book in books:
            await self.delete(book)

    async def sweep(self) -> None:
        """Finish the creations and deletions of books that were broken off half way, which is a pass of the start.

        A creation stores the book before its entry, and a deletion removes the entry after the book, so a store
        without an entry is a creation that stopped and an entry without a store is a deletion that stopped. No one can
        open the book of either, so its files and what is left of it go.
        """
        stored = set(await self._uow.stored_books())
        entries = {book.id for book in await self.listed()}
        for book_id in sorted(stored - entries):
            await self._erase(book_id)
        for book_id in sorted(entries - stored):
            await self._erase(book_id)
            await self._forget(book_id)
