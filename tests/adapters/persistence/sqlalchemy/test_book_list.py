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

from bookreviver.domain.slices import SliceRequest
from bookreviver.services.book import Book
from bookreviver.services.stage_summaries import StageSummaries
from tests.harness.adapters.statements import STATEMENT_EVENT, StatementCounter
from tests.harness.builders.domain import make_job, make_page, make_page_stage, make_record
from tests.harness.builders.seeding import store_book
from tests.harness.fakes.jobs import JobFakes
from tests.harness.fakes.processing import FakeCatalogue

if TYPE_CHECKING:
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import AccountId, PageId
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase
    from tests.harness.adapters.database import LocatedBookDatabases
    from tests.harness.builders.domain import BookOpener
    from tests.harness.fakes.board import FakeJobBoard

pytestmark = pytest.mark.anyio

# The windows of the list the statements are counted for, of one book, two books and every book of the owner
WINDOWS: tuple[int, int, int] = (1, 2, 5)
# How many books the owner has: as many as the largest window
BOOKS: int = WINDOWS[-1]


async def _store_books(
    catalogue: CatalogueDatabase,
    board: FakeJobBoard,
    owner_id: AccountId,
    count: int,
    *,
    databases: LocatedBookDatabases,
) -> None:
    """Store books that each have a page in a stage and a job on the board, so every progress query returns rows.

    :param catalogue: Catalogue database over the catalogue and the books of the test.
    :type catalogue: CatalogueDatabase
    :param board: Board of the jobs, which holds them in memory and so sends no statement.
    :type board: FakeJobBoard
    :param owner_id: Committed account owning the books.
    :type owner_id: AccountId
    :param count: Number of books to store.
    :type count: int
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    """
    for minutes in range(count):
        page = make_page()
        book_id = await store_book(catalogue, make_record(minutes=minutes), page, owner=owner_id, databases=databases)
        await databases.open(book_id).change(partial(_add_stage_record, page_id=page.id))
        board.put(book_id, make_job(minutes=minutes))


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


async def _statements_of_list(stores: BookOpener, owner_id: AccountId, window: int) -> int:
    """List the owner's books as the list route does and count the statements every engine sends for their overviews.

    The window of the owner's books is read from the catalogue first, as ``Access.books`` reads it, and the
    counter then listens on the ``Engine`` class, so the statements of the file of every book count.

    :param stores: Opens the catalogue databases and the databases of the books of the test.
    :type stores: BookOpener
    :param owner_id: Account whose books are listed.
    :type owner_id: AccountId
    :param window: How many books the window holds.
    :type window: int
    :returns: Number of statements sent while listing.
    :rtype: int
    """
    catalogue = stores.catalogue()
    stages = StageSummaries(processors=FakeCatalogue([]))
    request = SliceRequest(limit=window)
    entries = await catalogue.read(lambda repos: repos.library.list_for_owner(owner_id, request))
    opened = [Book(entry.directory) for entry in entries.items]
    counter = StatementCounter()
    event.listen(Engine, STATEMENT_EVENT, counter)
    try:
        books = [await stages.overview(book) for book in opened]
    finally:
        event.remove(Engine, STATEMENT_EVENT, counter)
    assert len(books) == window
    assert all(book.progress is not None for book in books)
    return counter.count


class TestBookList:
    """Tests for the statements ``StageSummaries.overview`` sends to SQLite for a window of books."""

    async def test_every_book_of_a_window_adds_the_same_number_of_statements(
        self, fx_open_book: BookOpener, fx_owner_id: AccountId
    ) -> None:
        """Verify the statements grow by the same amount for each book, from one book to two and from one to five.

        A window is listed once before the count, so every connection and every book is open and the first read of a
        file does not add statements of its own. The growth from one book to two, four times over, is then the growth
        from one book to five.

        :param fx_open_book: Opens the catalogue databases and the databases of the books of the test.
        :type fx_open_book: BookOpener
        :param fx_owner_id: Committed account owning the books.
        :type fx_owner_id: AccountId
        """
        board = JobFakes().board
        await _store_books(fx_open_book.catalogue(), board, fx_owner_id, BOOKS, databases=fx_open_book.databases)
        await _statements_of_list(fx_open_book, fx_owner_id, BOOKS)

        counts = {window: await _statements_of_list(fx_open_book, fx_owner_id, window) for window in WINDOWS}

        one, two, five = (counts[window] for window in WINDOWS)
        assert (two - one) * (BOOKS - 1) == five - one
