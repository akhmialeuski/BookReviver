"""Tests for the SQL statements a keyed save and the update of several pages send to SQLite inside a write.

A save, a save of several entities and an update of several pages are each one statement of the database, not one
statement for every entity and not a read before each write, which is what keeps the writer held for a short time.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.stages import Stage
from tests.harness.adapters.statements import StatementLog
from tests.harness.builders.domain import (
    directory_of,
    make_page,
    make_page_stage,
    make_page_step_state,
    make_record,
    make_step_values,
)
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import AccountId, BookId
    from bookreviver.domain.pages import Page
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory, LocatedBookDatabases

pytestmark = pytest.mark.anyio

# How many pages, and so how many records or updates, a test writes at once
BATCH: int = 5
# The label the updated pages get
UPDATED_LABEL: str = '[4]'
# The keyed saves, by the table they write: each builds an entity of the page and calls the save of its repository
SAVES: dict[str, Callable[[BookRepositories, Page], object]] = {
    'page stage': lambda repos, page: repos.page_stages.save(make_page_stage(page_id=page.id)),
    'step state': lambda repos, page: repos.page_step_states.save(make_page_step_state(page_id=page.id)),
    'step values': lambda repos, page: repos.step_values.save(make_step_values()),
}


@pytest.fixture
async def fx_stored_book(
    fx_catalogue: CatalogueDatabaseFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
) -> tuple[BookId, list[Page]]:
    """Commit a book of five pages, which the writes of a test change.

    :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_owner_id: Committed account owning the book.
    :type fx_owner_id: AccountId
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The identifier of the book and its pages.
    :rtype: tuple[BookId, list[Page]]
    """
    pages = [make_page(order_key=f'a{index}') for index in range(BATCH)]
    return await store_book(fx_catalogue(), make_record(), *pages, owner=fx_owner_id, databases=fx_databases()), pages


async def _statements_of(
    books: BookRegistry,
    book_id: BookId,
    calls: Sequence[Callable[[BookRepositories], object]],
    *,
    databases: LocatedBookDatabases,
) -> list[int]:
    """Run the calls one after another in one change of the book and count the statements each of them sends.

    :param books: Registry of the books, whose engine of the writer of the book is listened to.
    :type books: BookRegistry
    :param book_id: Book whose book the change holds.
    :type book_id: BookId
    :param calls: Functions over the repositories of the change.
    :type calls: Sequence[Callable[[BookRepositories], object]]
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The number of statements of each call, in the order given.
    :rtype: list[int]
    """
    async with books.use(directory_of(book_id)) as book:
        with StatementLog(book.engine) as log:

            def work(repos: BookRepositories, _book_record: BookRecord) -> list[int]:
                """Call the functions and note how many statements the log gained during each.

                :param repos: BookRepositories of the transaction.
                :type repos: BookRepositories
                :param _book_record: Record of the book, which the work does not need.
                :type _book_record: BookRecord
                :returns: The number of statements of each call.
                :rtype: list[int]
                """
                counts = []
                for call in calls:
                    sent = len(log.statements)
                    call(repos)
                    counts.append(len(log.statements) - sent)
                return counts

            return await databases.open(book_id).change(work)


class TestKeyedSave:
    """Tests for the statements ``save`` and ``save_many`` of the keyed repositories send."""

    @pytest.mark.parametrize('save', SAVES.values(), ids=SAVES.keys())
    async def test_save_sends_one_statement_whether_the_key_is_new_or_stored(
        self,
        fx_books: BookRegistry,
        fx_stored_book: tuple[BookId, list[Page]],
        save: Callable[[BookRepositories, Page], object],
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the first save of a key and the second, which replaces it, are one statement each.

        A save that read the key first and then inserted or updated it would send two statements for a new key and
        three for a stored one.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_stored_book: The committed book and its pages.
        :type fx_stored_book: tuple[BookId, list[Page]]
        :param save: Function that saves an entity of the page in the repository under test.
        :type save: Callable[[BookRepositories, Page], object]
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, pages = fx_stored_book

        counts = await _statements_of(
            fx_books,
            book_id,
            [lambda repos: save(repos, pages[0]), lambda repos: save(repos, pages[0])],
            databases=fx_databases(),
        )

        assert counts == [1, 1]

    async def test_save_many_sends_one_statement_for_five_records(
        self, fx_books: BookRegistry, fx_stored_book: tuple[BookId, list[Page]], fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify five records of five pages are stored by one statement, not one for each.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_stored_book: The committed book and its pages.
        :type fx_stored_book: tuple[BookId, list[Page]]
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, pages = fx_stored_book
        records = [make_page_stage(page_id=page.id, stage=Stage.CLEANUP) for page in pages]

        counts = await _statements_of(
            fx_books, book_id, [lambda repos: repos.page_stages.save_many(records)], databases=fx_databases()
        )

        assert counts == [1]


class TestUpdateMany:
    """Tests for the statements ``update_many`` of the pages sends."""

    async def test_update_many_sends_one_read_and_one_update_for_five_pages(
        self, fx_books: BookRegistry, fx_stored_book: tuple[BookId, list[Page]], fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify five pages are read by one statement and updated by one, not read and updated one at a time.

        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_stored_book: The committed book and its pages.
        :type fx_stored_book: tuple[BookId, list[Page]]
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, pages = fx_stored_book
        updated = [evolve(page, manual_label=UPDATED_LABEL, included=False) for page in pages]

        counts = await _statements_of(
            fx_books, book_id, [lambda repos: repos.pages.update_many(updated)], databases=fx_databases()
        )

        assert counts == [2]
