"""How fresh a read of a book is: a mark that changes whenever anything a read of the book shows changes.

A read of a book shows what its file holds and, for some reads, the jobs of the book the process runs. The file has a
version that every committed change of a part a reader asks for raises, and the board of the jobs has a mark that every
change of a job of the book changes, so the two together say whether a read made earlier still holds. The owner is
checked first, so the mark of a book is never told to an account that does not own it.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import JobBoard
    from bookreviver.services.library import Library

# What stands between the version of a book and the mark of its jobs
MARK_SEPARATOR: str = '.'


class BookFreshness:
    """The freshness of the reads of the books of the acting account.

    :ivar _uow: Unit of work of the request, which reads the version of a book.
    :ivar _library: Library that opens the books of the acting account.
    :ivar _board: Board of the jobs, which marks the changes of the jobs of a book.
    """

    def __init__(self, *, uow: UnitOfWork, library: Library, board: JobBoard) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request.
        :type uow: UnitOfWork
        :param library: Library that opens the books of the acting account.
        :type library: Library
        :param board: Board of the jobs of the books.
        :type board: JobBoard
        """
        self._uow = uow
        self._library = library
        self._board = board

    async def version(self, actor: Actor, book_id: BookId) -> int:
        """Return the version of one of the actor's books.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: The book.
        :type book_id: BookId
        :returns: The version of the book.
        :rtype: int
        :raises NotFoundError: If the actor has no such project.
        """
        await self._library.open(actor, book_id)
        return await self._uow.book_version(book_id)

    async def mark(self, actor: Actor, book_id: BookId) -> str:
        """Return the mark of what the reads of one of the actor's books show now.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: The book.
        :type book_id: BookId
        :returns: The version of the book and the mark of its jobs.
        :rtype: str
        :raises NotFoundError: If the actor has no such project.
        """
        version = await self.version(actor, book_id)
        return f'{version}{MARK_SEPARATOR}{self._board.revision(book_id)}'
