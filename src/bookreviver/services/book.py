"""One open book, which makes everything it needs through ports and runs every use case that reads or changes it.

A book knows the name of its directory and opens its own parts by it: its directory, its database, its event channel
and its job queue. It knows no identifier and no owner, which live in the library entry of the catalogue: a caller that
needs them takes them where they already are, the routes from the path, the ``Access`` of the API from the library
entry, and a job from the queue it was taken from. ``BooksService`` opens a book of a library entry already read. For a
request, ``Access.book`` of the API opens a book of the signed-in account after it has checked the
owner, so a use case that takes a ``Book`` works on it and checks nothing itself. A job opens the book of its queue
from the name of the directory. A use case reads the book through ``read``, changes it through ``change``, and asks
``version`` and ``snapshot`` for its version and a copy of its store, and names no identifier of a book to any
transaction. It reaches its uploads and sources through ``sources`` and its derived files through ``assets``, the
stores of the directory of this book alone.

The use cases of one open book are methods of the book: its creation, its unpacking from an archive, the change of its
description and settings, its archive and its deletion. What a client draws from the book as a whole, its overview with
the progress and the sums and rows of its stages, is read by ``StageSummaries`` through ``read``.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from attrs import Factory, evolve, field, frozen

from bookreviver.domain.entities import BookRecord
from bookreviver.domain.values import ArchivedBook
from bookreviver.ports.persistence import BookDatabase
from bookreviver.ports.runtime import BookEvents, BookJobs
from bookreviver.ports.storage import BookDirectory

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import BinaryIO

    from bookreviver.domain.changes import BookChanges
    from bookreviver.domain.ids import BookDirectoryName
    from bookreviver.domain.values import BookDetails
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.storage import AssetStore, SourceStore


@frozen
class Book:
    """A book opened with its parts, for the acting account or for a job.

    The only argument of a book is the name of its directory. Its four parts are opened from that name when the book is
    built, through the ports ``app`` registered the openers of, and are no part of what makes two books equal.

    :ivar dir_name: The name of the directory of the book, by which the parts of the book are opened.
    :ivar _database: The database of the book, which runs the transactions of the book, reached through ``read``,
                     ``change``, ``version`` and ``snapshot``.
    :ivar _directory: The directory of the book, which holds its files and its database file, reached through
                      ``sources``, ``assets``, ``export``, ``create``, ``unpack`` and ``delete``.
    :ivar events: The event channel of the book, which every object of the book shares.
    :ivar jobs: The queue of the jobs of the book, which every object of the book shares.
    """

    dir_name: BookDirectoryName
    _database: BookDatabase = field(
        init=False,
        eq=False,
        repr=False,
        default=Factory(lambda self: BookDatabase.open(self.dir_name), takes_self=True),
    )
    _directory: BookDirectory = field(
        init=False,
        eq=False,
        repr=False,
        default=Factory(lambda self: BookDirectory.open(self.dir_name), takes_self=True),
    )
    events: BookEvents = field(
        init=False, eq=False, repr=False, default=Factory(lambda self: BookEvents.open(self.dir_name), takes_self=True)
    )
    jobs: BookJobs = field(
        init=False, eq=False, repr=False, default=Factory(lambda self: BookJobs.open(self.dir_name), takes_self=True)
    )

    @property
    def sources(self) -> SourceStore:
        """The store of the uploads and sources of the book."""
        return self._directory.sources

    @property
    def assets(self) -> AssetStore:
        """The store of the derived files of the book."""
        return self._directory.assets

    async def read[T](self, work: Callable[[BookRepositories], T]) -> T:
        """Run ``work`` on a transaction of the book that only reads, and return what it returns.

        :param work: Function over the repositories of the transaction.
        :type work: Callable[[BookRepositories], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises NoChangeOpenError: If the work writes.
        """
        return await self._database.read(work)

    async def change[T](self, work: Callable[[BookRepositories, BookRecord], T]) -> T:
        """Run ``work`` on a transaction of the book that writes, alone among its changes, with its record as it is.

        :param work: Function over the repositories of the transaction and the record of the book read at its start.
        :type work: Callable[[BookRepositories, BookRecord], T]
        :returns: What the work returns.
        :rtype: T
        :raises NotFoundError: If the book is not stored.
        :raises BookBusyError: If the write did not begin within the wait limit of the database.
        """
        return await self._database.change(work)

    async def version(self) -> int:
        """Return the version of the book, which every committed change that wrote a part of the book raised by one.

        :returns: The version of the book.
        :rtype: int
        :raises NotFoundError: If the book is not stored.
        """
        return await self._database.version()

    async def snapshot(self) -> bytes:
        """Return a copy of the store of the book as one committed state of it, for the archive of the book.

        :returns: The copy of the store.
        :rtype: bytes
        :raises NotFoundError: If the book is not stored.
        """
        return await self._database.snapshot()

    async def update(self, changes: BookChanges) -> None:
        """Change some fields of the book, such as its description or its cover, and mark it as updated.

        :param changes: New values of the fields to change.
        :type changes: BookChanges
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
            return repos.book_record.update(evolve(changes.apply_to(book_record), updated_at=datetime.now(UTC)))

        await self.change(change)

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
        return ArchivedBook(title=book_record.details.title, pieces=self._directory.export(database))

    async def create(self, details: BookDetails) -> None:
        """Make the empty book: its directory, then its database with the first description, created and changed now.

        :param details: Description of the book.
        :type details: BookDetails
        :raises ConflictError: If the book has a database already.
        """
        moment = datetime.now(UTC)
        await self._directory.create()
        await self._database.create(BookRecord(details=details, created_at=moment, updated_at=moment))

    async def unpack(self, archive: BinaryIO) -> None:
        """Make the book from the archive of an exported book: its directory first, then its database brought to date.

        A database that cannot be adopted removes the whole directory, so a refused archive leaves nothing behind.

        :param archive: The archive, a file that can be read from any position.
        :type archive: BinaryIO
        :raises InvalidArchiveError: If the file is not the archive of a book.
        :raises BookTooNewError: If the book was exported by a newer release.
        :raises ConflictError: If the directory of the book exists already.
        """
        await self._directory.unpack(archive)
        try:
            await self._database.adopt()
        except Exception:
            await self.delete()
            raise

    async def delete(self) -> None:
        """Close the database of the book once its transactions have ended, then delete its directory whole.

        A deletion broken off half way, such as by a failure of the disk, removes what is left when it runs again.
        """
        await self._database.close()
        await self._directory.delete()
