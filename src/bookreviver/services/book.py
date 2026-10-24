"""One open book, which runs every transaction of its own database and every use case that reads or changes it.

``BooksService.open`` opens a book with the database of its file, and so does ``BooksService.book`` for a library
entry already read. For a request, the access dependency ``open_book`` of the API opens a book of the signed-in account
after it has checked the owner, so a use case that takes a ``Book`` works on it and checks nothing itself. A job opens
the book of its job in the same way. A use case reads the book through ``read``, changes it through ``change``, and
asks ``version`` and ``snapshot`` for its version and a copy of its store, and names no identifier of a book to any
transaction. It reaches its uploads and sources through ``sources`` and its derived files through ``assets``, the stores
of the directory of this book alone.

The use cases of one open book are methods of the book: its first description, its overview, the change of its
description and settings, the sums and rows of its stages, its archive and its deletion. ``BookTools`` holds what every
book of a request works with besides its own database, so the books service hands the same tools to every book it opens.
"""

from typing import TYPE_CHECKING

from attrs import evolve, field, frozen

from bookreviver.domain.entities import BookRecord
from bookreviver.domain.values import ArchivedBook, Slice

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.domain.changes import BookChanges
    from bookreviver.domain.entities import BookOverview
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import AccountId, BookId, StepId
    from bookreviver.domain.stage_summaries import StageRow, StageSummary
    from bookreviver.domain.values import BookDetails, SliceRequest
    from bookreviver.ports.persistence import BookDatabase, BookRepositories
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore, BookDirectory, SourceStore
    from bookreviver.services.stage_summaries import StageSummaries


@frozen(kw_only=True)
class BookTools:
    """What every open book of a request works with besides its own database.

    :ivar stages: Sums of the stages of a book, which give the book its progress and its stage rows.
    :ivar clock: Clock setting the time a book was created and last changed.
    """

    stages: StageSummaries
    clock: Clock


@frozen
class Book:
    """A book opened with its database, for the acting account or for a job.

    :ivar id: Identifier of the book in the library.
    :ivar owner_id: The account that owns the book.
    :ivar database: The database of the book, which runs the transactions of the book. It is no part of what makes two
                    books equal.
    :ivar directory: The directory of the book, which holds its files and its database file, no part of what makes two
                     books equal.
    :ivar tools: What the use cases of the book work with besides its database, no part of what makes two books equal.
    """

    id: BookId
    owner_id: AccountId
    database: BookDatabase = field(eq=False, repr=False)
    directory: BookDirectory = field(eq=False, repr=False)
    tools: BookTools = field(eq=False, repr=False)

    @property
    def sources(self) -> SourceStore:
        """The store of the uploads and sources of the book."""
        return self.directory.sources

    @property
    def assets(self) -> AssetStore:
        """The store of the derived files of the book."""
        return self.directory.assets

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

    async def overview(self) -> BookOverview:
        """Return the book with its counts and progress.

        :returns: The book with its counts and progress.
        :rtype: BookOverview
        :raises NotFoundError: If the book is not stored.
        """
        return await self.read(
            lambda repos: self.tools.stages.with_progress(repos, repos.book_record.overview(self.id))
        )

    async def update(self, changes: BookChanges) -> BookOverview:
        """Change some fields of the book, such as its description or its cover, and mark it as updated.

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
            return repos.book_record.update(evolve(changes.apply_to(book_record), updated_at=self.tools.clock.now()))

        await self.change(change)
        return await self.overview()

    async def stages(self, request: SliceRequest) -> Slice[StageSummary]:
        """Return the stages of the book, each summed over its pages.

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
            summaries = self.tools.stages.of_book(repos, repos.book_record.overview(self.id))
            return Slice(items=summaries[request.offset : request.offset + request.limit], total=len(summaries))

        return await self.read(work)

    async def stage_pages(self, stage: Stage, request: SliceRequest, step_id: StepId | None = None) -> Slice[StageRow]:
        """Return a window of the pages of the book, each with where it stands in a stage.

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
        return await self.read(lambda repos: self.tools.stages.rows(repos, stage, request, step_id))

    async def export(self) -> ArchivedBook:
        """Return the archive of the book, which another instance imports as a book of its own.

        The database is copied first, as one committed state, and the files after it. A file is written once and never
        replaced, so every file the copy names is on the disk when the archive reads it, unless the book is deleted or
        a source of it removed while the archive is read.

        :returns: The title of the book and the stream of its archive.
        :rtype: ArchivedBook
        :raises NotFoundError: If the book is not stored.
        """
        book_record = await self.read(lambda repos: repos.book_record.get())
        database = await self.snapshot()
        return ArchivedBook(title=book_record.details.title, pieces=self.directory.export(database))

    async def describe(self, details: BookDetails) -> None:
        """Write the first description of a new book, whose database holds no record yet, created and changed now.

        :param details: Description of the book.
        :type details: BookDetails
        :raises ConflictError: If the book is described already.
        """
        moment = self.tools.clock.now()
        await self.database.create(BookRecord(details=details, created_at=moment, updated_at=moment))

    async def delete(self) -> None:
        """Close the database of the book once its transactions have ended, then delete its directory whole.

        A deletion broken off half way, such as by a failure of the disk, removes what is left when it runs again.
        """
        await self.database.close()
        await self.directory.delete()
