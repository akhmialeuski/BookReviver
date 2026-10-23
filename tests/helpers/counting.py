"""A unit of work that counts its reads, for the tests that prove how many snapshots of a book a request takes."""

from typing import TYPE_CHECKING, override

from bookreviver.ports.persistence import UnitOfWork

if TYPE_CHECKING:
    from collections.abc import Callable, Collection

    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories


class CountingUnitOfWork(UnitOfWork):
    """Hands every call to another unit of work, and counts the reads of the catalogue and of the books.

    :ivar reads: How many times ``read_book`` was called, each of which is a thread, a connection and a snapshot of a
                 book in an adapter that has them.
    :ivar catalogue_reads: How many times ``read`` was called, each of which is a snapshot of the catalogue.
    """

    def __init__(self, inner: UnitOfWork) -> None:
        """Wrap a unit of work.

        :param inner: The unit of work that runs the work.
        :type inner: UnitOfWork
        """
        self._inner = inner
        self.reads = 0
        self.catalogue_reads = 0

    @override
    async def read[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Count the read of the catalogue and run it on the wrapped unit of work.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        self.catalogue_reads += 1
        return await self._inner.read(work)

    @override
    async def change[T](self, work: Callable[[CatalogueRepositories], T]) -> T:
        """Run the change of the catalogue on the wrapped unit of work.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[CatalogueRepositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._inner.change(work)

    @override
    async def read_book[T](self, book_id: BookId, work: Callable[[BookRepositories], T]) -> T:
        """Count the read of the book and run it on the wrapped unit of work.

        :param book_id: Book that is read.
        :type book_id: BookId
        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        self.reads += 1
        return await self._inner.read_book(book_id, work)

    @override
    async def change_book[T](self, book_id: BookId, work: Callable[[BookRepositories, BookRecord], T]) -> T:
        """Run the change of the book on the wrapped unit of work.

        :param book_id: Book that is changed.
        :type book_id: BookId
        :param work: Function over the repositories of the transaction and the record of the book read at its start.
        :type work: Callable[[BookRepositories, BookRecord], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._inner.change_book(book_id, work)

    @override
    async def create_book(self, book_record: BookRecord) -> BookRecord:
        """Make the store of a new book on the wrapped unit of work.

        :param book_record: The record of the new book.
        :type book_record: BookRecord
        :returns: The record of the book as stored.
        :rtype: BookRecord
        """
        return await self._inner.create_book(book_record)

    @override
    async def delete_book(self, book_id: BookId) -> None:
        """Remove the store of a book on the wrapped unit of work.

        :param book_id: Book that is removed.
        :type book_id: BookId
        """
        await self._inner.delete_book(book_id)

    @override
    async def book_version(self, book_id: BookId) -> int:
        """Read the version of a book on the wrapped unit of work.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The version of the book.
        :rtype: int
        """
        return await self._inner.book_version(book_id)

    @override
    async def snapshot_book(self, book_id: BookId) -> bytes:
        """Copy the store of a book on the wrapped unit of work.

        :param book_id: Book that is copied.
        :type book_id: BookId
        :returns: The copy of the store.
        :rtype: bytes
        """
        return await self._inner.snapshot_book(book_id)

    @override
    async def adopt_book(self, book_id: BookId) -> BookRecord:
        """Take in the store an archive placed for a new book, on the wrapped unit of work.

        :param book_id: Identifier of the new book.
        :type book_id: BookId
        :returns: The record of the new book.
        :rtype: BookRecord
        """
        return await self._inner.adopt_book(book_id)

    @override
    async def stored_books(self) -> Collection[BookId]:
        """Return the books the wrapped unit of work stores.

        :returns: The identifiers of the stored books.
        :rtype: Collection[BookId]
        """
        return await self._inner.stored_books()
