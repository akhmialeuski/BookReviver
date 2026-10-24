"""Tests for the list of books over SQLite: every book of a window adds the same statements to the list.

The books live in files of their own, so the list reads the progress of each shown book from its file and the cost of a
window grows with the number of books in it. What the list must not do is cost more for one book than for another, such
as a query that grows with the size of the window.
"""

from functools import partial
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from bookreviver.adapters.storage import LocalAssetStore, LocalBookArchive, LocalSourceStore
from bookreviver.domain.values import Slice, SliceRequest
from bookreviver.services.books import BooksService, BookStores
from bookreviver.services.stage_summaries import StageSummaries
from tests.helpers.builders import EPOCH, make_job, make_page, make_page_stage, make_record
from tests.helpers.clock import FixedClock
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.fakes_jobs import JobFakes
from tests.helpers.seeding import store_book
from tests.helpers.statements import STATEMENT_EVENT, StatementCounter

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import AccountId, PageId
    from bookreviver.ports.persistence import BookDatabases, BookRepositories, UnitOfWork
    from bookreviver.ports.runtime import JobBoard
    from tests.helpers.builders import BookOpener

pytestmark = pytest.mark.anyio

# The windows of the list the statements are counted for, of one book, two books and every book of the owner
WINDOWS: tuple[int, int, int] = (1, 2, 5)
# How many books the owner has: as many as the largest window
BOOKS: int = WINDOWS[-1]


async def _store_books(
    uow: UnitOfWork, board: JobBoard, owner_id: AccountId, count: int, *, databases: BookDatabases
) -> None:
    """Store books that each have a page in a stage and a job on the board, so every progress query returns rows.

    :param uow: Unit of work over the catalogue and the books of the test.
    :type uow: UnitOfWork
    :param board: Board of the jobs, which holds them in memory and so sends no statement.
    :type board: JobBoard
    :param owner_id: Committed account owning the books.
    :type owner_id: AccountId
    :param count: Number of books to store.
    :type count: int
    :param databases: Opens the databases of the books of the test.
    :type databases: BookDatabases
    """
    for minutes in range(count):
        book_record = make_record(minutes=minutes)
        page = make_page()
        await store_book(uow, book_record, page, owner=owner_id, databases=databases)
        await databases.open(book_record.id).change(partial(_add_stage_record, page_id=page.id))
        board.put(make_job(book_id=book_record.id, minutes=minutes))


def _add_stage_record(repos: BookRepositories, _book_record: BookRecord, *, page_id: PageId) -> None:
    """Store a record of a stage of a page.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param _book_record: Record of the book, which the work does not need.
    :type _book_record: BookRecord
    :param page_id: The page.
    :type page_id: PageId
    """
    repos.page_stages.add(make_page_stage(page_id=page_id))


async def _statements_of_list(stores: BookOpener, board: JobBoard, owner_id: AccountId, window: int, root: Path) -> int:
    """List the owner's books as the list route does and count the statements every engine sends for their overviews.

    The window of the owner's books is read from the catalogue first, as the access dependency reads it, and the
    counter then listens on the ``Engine`` class, so the statements of the file of every book count.

    :param stores: Opens the units of work and the databases of the books of the test.
    :type stores: BookOpener
    :param board: Board of the jobs of the books.
    :type board: JobBoard
    :param owner_id: Account whose books are listed.
    :type owner_id: AccountId
    :param window: How many books the window holds.
    :type window: int
    :param root: Storage root of the stores the service needs and the list does not touch.
    :type root: Path
    :returns: Number of statements sent while listing.
    :rtype: int
    """
    uow = stores.uow()
    service = BooksService(
        uow=uow,
        databases=stores.databases,
        clock=FixedClock(EPOCH),
        files=BookStores(
            sources=LocalSourceStore(root=root), assets=LocalAssetStore(root=root), archive=LocalBookArchive(root=root)
        ),
        stages=StageSummaries(catalogue=FakeCatalogue([]), board=board),
    )
    request = SliceRequest(limit=window)
    entries = await uow.read(lambda repos: repos.library.list_for_owner(owner_id, request))
    opened = Slice(items=[service.book(entry) for entry in entries.items], total=entries.total)
    counter = StatementCounter()
    event.listen(Engine, STATEMENT_EVENT, counter)
    try:
        books = await service.overviews(opened)
    finally:
        event.remove(Engine, STATEMENT_EVENT, counter)
    assert len(books.items) == window
    assert all(book.progress is not None for book in books.items)
    return counter.count


class TestBookList:
    """Tests for the statements ``BooksService.overviews`` sends to SQLite for a window of books."""

    async def test_every_book_of_a_window_adds_the_same_number_of_statements(
        self, fx_open_book: BookOpener, fx_owner_id: AccountId, tmp_path: Path
    ) -> None:
        """Verify the statements grow by the same amount for each book, from one book to two and from one to five.

        A window is listed once before the count, so every connection and every book is open and the first read of a
        file does not add statements of its own. The growth from one book to two, four times over, is then the growth
        from one book to five.

        :param fx_open_book: Opens the units of work and the databases of the books of the test.
        :type fx_open_book: BookOpener
        :param fx_owner_id: Committed account owning the books.
        :type fx_owner_id: AccountId
        :param tmp_path: Directory of the stores the list does not touch.
        :type tmp_path: Path
        """
        board = JobFakes().board
        await _store_books(fx_open_book.uow(), board, fx_owner_id, BOOKS, databases=fx_open_book.databases)
        await _statements_of_list(fx_open_book, board, fx_owner_id, BOOKS, tmp_path)

        counts = {
            window: await _statements_of_list(fx_open_book, board, fx_owner_id, window, tmp_path) for window in WINDOWS
        }

        one, two, five = (counts[window] for window in WINDOWS)
        assert (two - one) * (BOOKS - 1) == five - one
