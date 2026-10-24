"""A book database that counts its reads, for the tests that prove how many snapshots of a book a request takes."""

from typing import TYPE_CHECKING, override

from bookreviver.ports.persistence import BookDatabase

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.entities import BookRecord
    from bookreviver.ports.persistence import BookRepositories


class CountingBookDatabase(BookDatabase):
    """Hands every call to another book database, and counts the reads of the book.

    :ivar reads: How many times ``read`` was called, each of which is a thread, a connection and a snapshot of the book
                 in an adapter that has them.
    """

    def __init__(self, inner: BookDatabase) -> None:
        """Wrap a book database.

        :param inner: The book database that runs the work.
        :type inner: BookDatabase
        """
        self._inner = inner
        self.reads = 0

    @override
    async def read[T](self, work: Callable[[BookRepositories], T]) -> T:
        """Count the read of the book and run it on the wrapped book database.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        """
        self.reads += 1
        return await self._inner.read(work)

    @override
    async def change[T](self, work: Callable[[BookRepositories, BookRecord], T]) -> T:
        """Run the change of the book on the wrapped book database.

        :param work: Function over the repositories of the transaction and the record of the book read at its start.
        :type work: Callable[[BookRepositories, BookRecord], T]
        :returns: What the work returns.
        :rtype: T
        """
        return await self._inner.change(work)

    @override
    async def version(self) -> int:
        """Read the version of the book on the wrapped book database.

        :returns: The version of the book.
        :rtype: int
        """
        return await self._inner.version()

    @override
    async def snapshot(self) -> bytes:
        """Copy the store of the book on the wrapped book database.

        :returns: The copy of the store.
        :rtype: bytes
        """
        return await self._inner.snapshot()

    @override
    async def create(self, book_record: BookRecord) -> None:
        """Store the record of a new book on the wrapped book database.

        :param book_record: The record of the book.
        :type book_record: BookRecord
        """
        await self._inner.create(book_record)

    @override
    async def close(self) -> None:
        """Close the wrapped book database for good."""
        await self._inner.close()
