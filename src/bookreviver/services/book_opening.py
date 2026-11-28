"""Opening the books for the accounts: what a book needs before its first use in the process, and one open book each.

The application goes over no book when it starts. A book is prepared the first time a request opens it in the process,
whether the user opens the book or sees it in the library, and before the request reads it:

- the upload an import was receiving or running on when the process before ended is removed, since no job can use it
  any more, and its sources that were stored stay. A book is prepared before any request of the process can upload to
  it, so no upload of this process is removed;
- when the processors installed are not those the record of the book says it was last checked against, the stages
  whose result a replaced version of a processor made are marked stale, the blank leaves among them are given a pending
  version, which the next ``prepare-pages`` job of the book writes, and the record is given the processors installed,
  so the next process with the same plugins skips both.

Each account works on one book at a time. Opening a book makes it the book of the account and lets go of the one it
worked on before, and going back to the library lets go of it too, so the database of a book the user left holds no
connection. A book the library only shows is let go of as soon as it is read, unless an account works on it. A job
of a book the user left keeps the book open until it ends, and nothing of it is cancelled.
"""

from typing import TYPE_CHECKING

import anyio
from attrs import evolve

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.core.book import Book
    from bookreviver.core.plugins import ProcessorCatalog
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import AccountId, BookDirectoryName
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.outdated_results import OutdatedResults
    from bookreviver.services.pages import PageService


class BookOpening:
    """The books opened in the process: each prepared once, and the one book each account works on.

    Built once by the container.
    """

    def __init__(self, *, processors: ProcessorCatalog, outdated: OutdatedResults, pages: PageService) -> None:
        """Prepare the books against the processors installed, with the services that bring a book up to them.

        :param processors: The processors the application can run, with the versions that are installed.
        :type processors: ProcessorCatalog
        :param outdated: What marks the stages a replaced version of a processor made stale.
        :type outdated: OutdatedResults
        :param pages: The page service, which makes again the leaves a replaced version made.
        :type pages: PageService
        """
        self.installed = frozenset(spec.ref for spec in processors.specs())
        self.outdated = outdated
        self.pages = pages
        # Never emptied, so an upload of this process is never taken for one the process before left
        self._prepared: set[BookDirectoryName] = set()
        self._working: dict[AccountId, Book] = {}
        # Taken to prepare a book, so two requests that open it first at once prepare it once
        self._lock = anyio.Lock()

    async def open(self, actor: Actor, book: Book) -> None:
        """Make the book the one the account works on, prepared, and let go of the book it worked on before.

        :param actor: The account that opens the book.
        :type actor: Actor
        :param book: The book, of that account.
        :type book: Book
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the book is of a newer release.
        """
        await self._prepare(book)
        left = self._working.get(actor.account_id)
        self._working[actor.account_id] = book
        if left is not None and left != book:
            await left.release()

    async def leave(self, actor: Actor) -> None:
        """Let go of the book the account worked on, as it goes back to its library.

        :param actor: The account.
        :type actor: Actor
        """
        if (left := self._working.pop(actor.account_id, None)) is not None:
            await left.release()

    async def glance[T](self, book: Book, read: Callable[[Book], Awaitable[T]]) -> T:
        """Read a book the library shows, prepared, and let go of it after, unless an account works on it.

        :param book: The book.
        :type book: Book
        :param read: What is read of the book.
        :type read: Callable[[Book], Awaitable[T]]
        :returns: What the read returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the book is of a newer release.
        """
        await self._prepare(book)
        try:
            return await read(book)
        finally:
            if book not in self._working.values():
                await book.release()

    async def _prepare(self, book: Book) -> None:
        """Prepare the book, unless it was prepared in the process already.

        A preparation that fails leaves the book unprepared, so the next opening tries again.

        :param book: The book.
        :type book: Book
        """
        if book.dir_name in self._prepared:
            return
        async with self._lock:
            if book.dir_name not in self._prepared:
                await BookPreparation(self, book).run()
                self._prepared.add(book.dir_name)


class BookPreparation:
    """The preparation of one book for its first use in the process."""

    def __init__(self, opening: BookOpening, book: Book) -> None:
        """Prepare a book for the opening it belongs to.

        :param opening: The opening of the books, which holds the processors installed and the services.
        :type opening: BookOpening
        :param book: The book.
        :type book: Book
        """
        self._opening = opening
        self._book = book

    async def run(self) -> None:
        """Remove the uploads the process before left, and bring the results up to the processors installed.

        The record is given the processors last, so a preparation broken off half way runs again whole, and each of its
        passes leaves alone what an earlier one did.

        :raises NotFoundError: If the book is not stored.
        :raises BookTooNewError: If the book is of a newer release.
        """
        await self._book.sources.discard_all_staged()
        checked = await self._book.read(lambda repos: repos.book_record.get().checked_processors)
        if checked == self._opening.installed:
            return
        await self._opening.outdated.mark_stale(self._book)
        await self._opening.pages.remake_outdated_leaves(self._book)
        await self._book.change(self._record_checked)

    def _record_checked(self, repos: BookRepositories, book_record: BookRecord) -> None:
        """Record the processors installed as those the results of the book were checked against.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param book_record: The record of the book as the change finds it.
        :type book_record: BookRecord
        """
        repos.book_record.update(evolve(book_record, checked_processors=self._opening.installed))
