"""Use cases of books: listing, creating, describing, exporting, importing and deleting them.

A use case works on the ``Book`` objects it is given and checks no owner: the access dependencies of the API open a
book of the signed-in account, and they choose the books of a list. The lookups ``book_page`` and ``book_pages`` are
synchronous functions over repositories: the work of ``UnitOfWork.read_book`` or ``change_book`` calls them with the
repositories it receives, and a caller outside a transaction wraps the call in ``UnitOfWork.read_book``.

A book is deleted files first, then its store, and the library entry last. Every use case of the book needs the entry,
a repeated deletion included, so an entry deleted first would leave the files of a failed deletion out of every
request's reach for good. A deletion that fails part-way keeps the entry, and repeating it removes what is left. Until
then the book may lack some of its files, which only a deletion the owner asked for can cause. The files go outside any
transaction. A start pass, ``sweep``, finishes a deletion or a creation that was broken off half way and is not
repeated by anyone: a store without an entry, or an entry without a store.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import BookRecord, LibraryEntry
from bookreviver.domain.ids import BookId
from bookreviver.domain.values import ArchivedBook, Slice, SliceRequest

if TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import BinaryIO

    from bookreviver.domain.changes import BookChanges
    from bookreviver.domain.entities import Actor, BookOverview, Page
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import StepId
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.values import BookDetails
    from bookreviver.ports.persistence import BookRepositories, PageRepository, UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, BookArchive, SourceStore
    from bookreviver.services.book import Book
    from bookreviver.services.stage_summaries import StageSummaries

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
    """The stores of the files of a book, emptied when the book is deleted, and the archive that carries them all.

    :ivar sources: Store of the uploaded sources.
    :ivar assets: Store of the derived files.
    :ivar archive: Archive of a whole book, which packs a book and unpacks one as a new book.
    """

    sources: SourceStore
    assets: AssetStore
    archive: BookArchive


class BooksService:
    """Books of the acting account, with their description and their files."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        clock: Clock,
        files: BookStores,
        stages: StageSummaries,
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, which runs the work of every use case in one transaction.
        :type uow: UnitOfWork
        :param clock: Clock setting the creation and update times.
        :type clock: Clock
        :param files: Stores of the source files and the derived files, emptied when a book is deleted.
        :type files: BookStores
        :param stages: Sums of the stages of books, which give each book its progress.
        :type stages: StageSummaries
        """
        self._uow = uow
        self._clock = clock
        self._files = files
        self._stages = stages

    async def overviews(self, books: Slice[Book]) -> Slice[BookOverview]:
        """Return the overview of each book of a window, in the order given.

        :param books: The open books of the window, and the number of all the books the window was cut from.
        :type books: Slice[Book]
        :returns: The books of the window with their counts and progress, and the same total.
        :rtype: Slice[BookOverview]
        """
        return Slice(items=[await self._overview(book.id) for book in books.items], total=books.total)

    async def create(self, owner: Actor, details: BookDetails) -> BookOverview:
        """Create an empty book of an account: its store first, then its entry in the library.

        :param owner: Account the entry of the library names as the owner.
        :type owner: Actor
        :param details: Description of the book.
        :type details: BookDetails
        :returns: The stored book, which has no pages, sources or scans yet.
        :rtype: BookOverview
        """
        moment = self._clock.now()
        book_record = await self._uow.create_book(
            BookRecord(id=BookId(uuid4()), details=details, created_at=moment, updated_at=moment)
        )
        entry = LibraryEntry(book_id=book_record.id, owner_id=owner.account_id, added_at=moment)
        await self._uow.change(lambda repos: repos.library.add(entry))
        return await self._overview(book_record.id)

    async def get(self, book: Book) -> BookOverview:
        """Return the book.

        :param book: The open book.
        :type book: Book
        :returns: The book with its counts and progress.
        :rtype: BookOverview
        """
        return await self._overview(book.id)

    async def stages(self, book: Book, request: SliceRequest) -> Slice[StageSummary]:
        """Return the stages of the book, each summed over its pages.

        :param book: The open book.
        :type book: Book
        :param request: Offset and limit of the window of stages.
        :type request: SliceRequest
        :returns: The window of the stages in the order of the pipeline, and the number of all the stages.
        :rtype: Slice[StageSummary]
        """

        def work(repos: BookRepositories) -> Slice[StageSummary]:
            """Sum every stage of the book and cut the window the request asks for.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The window of the stages in the order of the pipeline, and the number of all the stages.
            :rtype: Slice[StageSummary]
            """
            summaries = self._stages.of_book(repos, repos.book_record.overview(repos.book_record.get(book.id)))
            return Slice(items=summaries[request.offset : request.offset + request.limit], total=len(summaries))

        return await self._uow.read_book(book.id, work)

    async def stage_pages(
        self, book: Book, stage: Stage, request: SliceRequest, step_id: StepId | None = None
    ) -> Slice[StageRow]:
        """Return a window of the pages of the book, each with where it stands in a stage.

        :param book: The open book.
        :type book: Book
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window of pages.
        :type request: SliceRequest
        :param step_id: A step of a recipe of the stage to place each page at, or None for the rows of the stage alone.
        :type step_id: StepId | None
        :returns: The rows of the window in book order, and the number of pages of the book.
        :rtype: Slice[StageRow]
        :raises NotFoundError: If no recipe of the stage has the step.
        """
        return await self._uow.read_book(
            book.id,
            lambda repos: self._stages.rows(repos, stage, request, step_id),
        )

    async def update(self, book: Book, changes: BookChanges) -> BookOverview:
        """Change some fields of the book, such as its description or its cover, and mark it as updated.

        :param book: The open book.
        :type book: Book
        :param changes: New values of the fields to change.
        :type changes: BookChanges
        :returns: The changed book with its counts and progress.
        :rtype: BookOverview
        :raises NotFoundError: If the new cover is not a page of the book.
        :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
        """

        def change(repos: BookRepositories, book_record: BookRecord) -> BookRecord:
            """Store the record of the book with the changed fields.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it.
            :type book_record: BookRecord
            :returns: The record of the book as stored.
            :rtype: BookRecord
            :raises NotFoundError: If the new cover is not a page of the book.
            :raises ValueError: If the changed description breaks one of its rules, such as an empty title.
            """
            return repos.book_record.update(evolve(changes.apply_to(book_record), updated_at=self._clock.now()))

        await self._uow.change_book(book.id, change)
        return await self._overview(book.id)

    async def export(self, book: Book) -> ArchivedBook:
        """Return the archive of the book, which another instance imports as a book of its own.

        The database is copied first, as one committed state, and the files after it. A file is written once and never
        replaced, so every file the copy names is on the disk when the archive reads it, unless the book is deleted or
        a source of it removed while the archive is read.

        :param book: The open book.
        :type book: Book
        :returns: The title of the book and the stream of its archive.
        :rtype: ArchivedBook
        """
        book_record = await self._uow.read_book(book.id, lambda repos: repos.book_record.get(book.id))
        database = await self._uow.snapshot_book(book.id)
        return ArchivedBook(title=book_record.details.title, pieces=self._files.archive.export(book.id, database))

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
            await self._files.archive.unpack(book_id, archive)
            await self._uow.adopt_book(book_id)
        except Exception:
            await self._erase(book_id)
            raise
        entry = LibraryEntry(book_id=book_id, owner_id=owner.account_id, added_at=self._clock.now())
        await self._uow.change(lambda repos: repos.library.add(entry))
        return await self._overview(book_id)

    async def _overview(self, book_id: BookId) -> BookOverview:
        """Read one book with its counts and progress.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :returns: The book with its counts and progress.
        :rtype: BookOverview
        """
        return await self._uow.read_book(
            book_id,
            lambda repos: self._stages.with_progress(repos, repos.book_record.overview(repos.book_record.get(book_id))),
        )

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
        entries = {entry.book_id for entry in await self._uow.read(lambda repos: repos.library.list_all())}
        for book_id in sorted(stored - entries):
            await self._erase(book_id)
        for book_id in sorted(entries - stored):
            await self._erase(book_id)
            await self._forget(book_id)
