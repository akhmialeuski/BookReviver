"""One open book, which runs every transaction of its own database.

``BooksService.open`` opens a book with the database of its file, and so does ``BooksService.book`` for a library
entry already read. For a request, the access dependency ``open_book`` of the API opens a book of the signed-in account
after it has checked the owner, so a use case that takes a ``Book`` works on it and checks nothing itself. A job opens
the book of its job in the same way. A use case reads the book through ``read``, changes it through ``change``, and
asks ``version`` and ``snapshot`` for its version and a copy of its store, and names no identifier of a book to any
transaction.
"""

from typing import TYPE_CHECKING

from attrs import field, frozen

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import AccountId, BookId
    from bookreviver.ports.persistence import BookDatabase, BookRepositories


@frozen
class Book:
    """A book opened with its database, for the acting account or for a job.

    :ivar id: Identifier of the book in the library.
    :ivar owner_id: The account that owns the book.
    :ivar database: The database of the book, which runs the transactions of the book. It is no part of what makes two
                    books equal.
    """

    id: BookId
    owner_id: AccountId
    database: BookDatabase = field(eq=False, repr=False)

    async def read[T](self, work: Callable[[BookRepositories], T]) -> T:
        """Run ``work`` on a transaction of the book that only reads, and return what it returns.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises NoChangeOpenError: If the work writes.
        """
        return await self.database.read(work)

    async def change[T](self, work: Callable[[BookRepositories, BookRecord], T]) -> T:
        """Run ``work`` on a transaction of the book that writes, alone among its changes, with its record as it is.

        :param work: Function over the repositories of the transaction and the record of the book read at its start.
        :type work: Callable[[BookRepositories, BookRecord], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookBusyError: If the write did not begin within the wait limit of the database.
        """
        return await self.database.change(work)

    async def version(self) -> int:
        """Return the version of the book, which every committed change that wrote a part of the book raised by one.

        :returns: The version of the book.
        :rtype: int
        :raises NotFoundError: If the book is not stored.
        """
        return await self.database.version()

    async def snapshot(self) -> bytes:
        """Return a copy of the store of the book as one committed state of it, for the archive of the book.

        :returns: The copy of the store.
        :rtype: bytes
        :raises NotFoundError: If the book is not stored.
        """
        return await self.database.snapshot()
