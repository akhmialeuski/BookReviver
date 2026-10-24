"""How fresh a read of a book is: a mark that changes whenever anything a read of the book shows changes.

A read of a book shows what its file holds and, for some reads, the jobs of the book the process runs. The file has a
version that every committed change of a part a reader asks for raises, and the board of the jobs has a mark that every
change of a job of the book changes, so the two together say whether a read made earlier still holds. Only an open
book has a mark, so the mark of a book is never told to an account that does not own it.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookreviver.ports.runtime import JobBoard
    from bookreviver.services.book import Book

# What stands between the version of a book and the mark of its jobs
MARK_SEPARATOR: str = '.'


class BookFreshness:
    """The freshness of the reads of the books of the acting account.

    :ivar _board: Board of the jobs, which marks the changes of the jobs of a book.
    """

    def __init__(self, *, board: JobBoard) -> None:
        """Work over the ports of one request.

        :param board: Board of the jobs of the books.
        :type board: JobBoard
        """
        self._board = board

    async def version(self, book: Book) -> int:
        """Return the version of the book.

        :param book: The open book.
        :type book: Book
        :returns: The version of the book.
        :rtype: int
        """
        return await book.version()

    async def mark(self, book: Book) -> str:
        """Return the mark of what the reads of the book show now.

        :param book: The open book.
        :type book: Book
        :returns: The version of the book and the mark of its jobs.
        :rtype: str
        """
        version = await self.version(book)
        return f'{version}{MARK_SEPARATOR}{self._board.revision(book.id)}'
