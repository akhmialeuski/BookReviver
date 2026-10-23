"""Use cases of the sources of a book and their scans: listing, reading and deleting.

A source is an uploaded file kept as it was uploaded, and its scans are the images inside it. Both are read-only
records of where the book came from, and the book's pages hold copies of their own images, so a source can be deleted
without touching a page. Deleting one removes its files, its scans and the derived files of its scans, and the pages
made from those scans only lose the reference to them.

Every file of an upload is a source with a status. The imported ones are the sources of the book and what a list shows
unless it asks for more. The rejected and skipped ones are the report of an import, which the reader closes by deleting
them all. They hold no scan and no stored file.

A source is deleted files first and row last, like a book. Every lookup, a repeated deletion included, needs the
row, so a row deleted first would leave the files of a failed deletion out of every request's reach. Both stores treat
a missing source as deleted, so a deletion that fails part-way keeps the source, and repeating it finishes the job. A
source whose import keeps failing on one of its scans is left by this deletion too, and the user deletes the source by
hand. Nothing skips a scan automatically.

A source is not deleted while the book imports, because the import may be writing the renditions of its scans.
"""

from operator import attrgetter
from typing import TYPE_CHECKING

from bookreviver.domain.enums import SourceStatus
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import Slice
from bookreviver.services.imports import IMPORT_JOBS

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.entities import Actor, Scan, Source
    from bookreviver.domain.ids import BookId, SourceId
    from bookreviver.domain.values import SliceRequest
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from bookreviver.ports.runtime import JobBoard
    from bookreviver.ports.storage import AssetStore, SourceStore
    from bookreviver.services.library import Library

SOURCE_IMPORTING: str = 'This book is importing files. Delete the source when the import has finished.'


def _window[ItemT](items: Sequence[ItemT], request: SliceRequest) -> Slice[ItemT]:
    """Cut a window out of a whole collection that is small enough to be read at once.

    :param items: Every item of the collection, in collection order.
    :type items: Sequence[ItemT]
    :param request: Offset and limit of the window.
    :type request: SliceRequest
    :returns: The items of the window and the number of all the items.
    :rtype: Slice[ItemT]
    """
    return Slice(items=items[request.offset : request.offset + request.limit], total=len(items))


class SourceService:
    """Sources and scans of the acting account's books, and the deletion of a source with its files."""

    def __init__(
        self, *, uow: UnitOfWork, library: Library, board: JobBoard, sources: SourceStore, assets: AssetStore
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose ``change_book`` ends the deletion of a source.
        :type uow: UnitOfWork
        :param library: Library that opens the books of the acting account.
        :type library: Library
        :param board: Board holding the jobs of every book, which tells whether the book imports.
        :type board: JobBoard
        :param sources: Store of the uploaded sources, emptied of a source when it is deleted.
        :type sources: SourceStore
        :param assets: Store of the derived files, emptied of the renditions of a source's scans when it is deleted.
        :type assets: AssetStore
        """
        self._uow = uow
        self._library = library
        self._board = board
        self._sources = sources
        self._assets = assets

    async def list(
        self,
        actor: Actor,
        book_id: BookId,
        request: SliceRequest,
        *,
        statuses: Collection[SourceStatus] = (SourceStatus.IMPORTED,),
    ) -> Slice[Source]:
        """Return a window of the book's files in the given statuses, in the order they were recorded.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param statuses: Which files to list: the imported sources, which are the book's sources, and the rejected
                         and skipped files of the report of an import.
        :type statuses: Collection[SourceStatus]
        :returns: The sources of the window and the number of all the files listed.
        :rtype: Slice[Source]
        :raises NotFoundError: If the actor has no such book.
        """
        await self._library.open(actor, book_id)

        def work(repos: BookRepositories) -> Slice[Source]:
            """Read the files of the book in the statuses and cut the window.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The sources of the window and the number of all the files listed.
            :rtype: Slice[Source]
            """
            found = [*repos.sources.list_all()] if SourceStatus.IMPORTED in statuses else []
            found += [source for source in repos.sources.list_unimported() if source.status in statuses]
            found.sort(key=attrgetter('imported_at', 'id'))
            return _window(found, request)

        return await self._uow.read_book(book_id, work)

    async def delete_unimported(self, actor: Actor, book_id: BookId) -> None:
        """Delete the rejected and skipped files of the book, which is closing the report of an import.

        The imported sources stay. The files of a rejected or skipped source were never stored, so only rows go.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :raises NotFoundError: If the actor has no such book.
        :raises ConflictError: If the book has an import queued or running, which is writing the report.
        """
        await self._library.open(actor, book_id)
        self._refuse_active_import(book_id)
        await self._uow.change_book(book_id, lambda repos, _book_record: repos.sources.delete_unimported())

    def _refuse_active_import(self, book_id: BookId) -> None:
        """Refuse a book that has an import queued or running, which writes sources and their scans.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :raises ConflictError: If the book has an import queued or running.
        """
        if any(job.kind in IMPORT_JOBS for job in self._board.jobs(book_id, active=True)):
            raise ConflictError(SOURCE_IMPORTING)

    async def get(self, actor: Actor, book_id: BookId, source_id: SourceId) -> Source:
        """Return one source of the book.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param source_id: Identifier of the source.
        :type source_id: SourceId
        :returns: The source.
        :rtype: Source
        :raises NotFoundError: If the actor has no such book, or the book has no such source.
        """
        await self._library.open(actor, book_id)
        return await self._uow.read_book(book_id, lambda repos: repos.sources.get(source_id))

    async def scans(
        self, actor: Actor, book_id: BookId, request: SliceRequest, *, source_id: SourceId | None = None
    ) -> Slice[Scan]:
        """Return a window of the scans of the book, or of one of its sources, in the order of the book.

        The scans of a book come source by source in import order, and the scans of a source by their numbers.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :param source_id: Source whose scans are listed, or None for the scans of every source of the book.
        :type source_id: SourceId | None
        :returns: The scans of the window and the number of all the scans listed.
        :rtype: Slice[Scan]
        :raises NotFoundError: If the actor has no such book, or the book has no such source.
        """
        await self._library.open(actor, book_id)

        def work(repos: BookRepositories) -> Slice[Scan]:
            """Read the window of the scans of the book, or of one of its sources.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The scans of the window and the number of all the scans listed.
            :rtype: Slice[Scan]
            :raises NotFoundError: If the book has no such source.
            """
            if source_id is None:
                return repos.scans.list_window(request)
            repos.sources.get(source_id)
            return _window(repos.scans.list_for_source(source_id), request)

        return await self._uow.read_book(book_id, work)

    async def delete(self, actor: Actor, book_id: BookId, source_id: SourceId) -> None:
        """Delete a source's files and the renditions of its scans, then the source with its scans.

        The pages of the book made from the scans keep their images, versions, numbers and order, and lose only the
        reference to their scan. The owner and the source are checked before any file goes, and the files go outside
        any transaction, so a slow store holds no lock of the book. Only the rows go in a ``change_book``, which this
        use case opens. A failure at any step leaves the source in place, and calling this again finishes the
        deletion.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param book_id: Identifier of the book.
        :type book_id: BookId
        :param source_id: Identifier of the source.
        :type source_id: SourceId
        :raises NotFoundError: If the actor has no such book, or the book has no such source.
        :raises ConflictError: If the book has an import queued or running.
        """
        await self._library.open(actor, book_id)
        await self._uow.read_book(book_id, lambda repos: repos.sources.get(source_id))
        self._refuse_active_import(book_id)
        # The row goes last: only an existing row lets a repeated call reach files a failed call left behind
        await self._sources.delete_source(book_id, source_id)
        await self._assets.delete_prefix(BookKeys(book_id).source_scans(source_id))
        await self._uow.change_book(book_id, lambda repos, _book_record: repos.sources.delete(source_id))
