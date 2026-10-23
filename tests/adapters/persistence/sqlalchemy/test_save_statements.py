"""Tests for the SQL statements a keyed save and the update of several pages send to SQLite inside a write.

A save, a save of several entities and an update of several pages are each one statement of the database, not one
statement for every entity and not a read before each write, which is what keeps the writer held for a short time.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import Stage
from tests.helpers.builders import (
    make_page,
    make_page_stage,
    make_page_step_state,
    make_record,
    make_step_values,
)
from tests.helpers.seeding import store_book
from tests.helpers.statements import StatementLog

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.entities import BookRecord, Page
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from tests.helpers.database import UnitOfWorkFactory

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
async def fx_stored_book(fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId) -> tuple[BookRecord, list[Page]]:
    """Commit a book of five pages, which the writes of a test change.

    :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_owner_id: Committed account owning the book.
    :type fx_owner_id: AccountId
    :returns: The book and its pages.
    :rtype: tuple[BookRecord, list[Page]]
    """
    book_record = make_record()
    pages = [make_page(order_key=f'a{index}') for index in range(BATCH)]
    await store_book(fx_uow(), book_record, *pages, owner=fx_owner_id)
    return book_record, pages


async def _statements_of(
    uow: UnitOfWork,
    books: BookRegistry,
    book_record: BookRecord,
    calls: Sequence[Callable[[BookRepositories], object]],
) -> list[int]:
    """Run the calls one after another in one change of the book and count the statements each of them sends.

    :param uow: Unit of work the change runs on.
    :type uow: UnitOfWork
    :param books: Registry of the books, whose engine of the writer of the book is listened to.
    :type books: BookRegistry
    :param book_record: Book whose book the change holds.
    :type book_record: BookRecord
    :param calls: Functions over the repositories of the change.
    :type calls: Sequence[Callable[[BookRepositories], object]]
    :returns: The number of statements of each call, in the order given.
    :rtype: list[int]
    """
    async with books.use(book_record.id) as book:
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

            return await uow.change_book(book_record.id, work)


class TestKeyedSave:
    """Tests for the statements ``save`` and ``save_many`` of the keyed repositories send."""

    @pytest.mark.parametrize('save', SAVES.values(), ids=SAVES.keys())
    async def test_save_sends_one_statement_whether_the_key_is_new_or_stored(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_books: BookRegistry,
        fx_stored_book: tuple[BookRecord, list[Page]],
        save: Callable[[BookRepositories, Page], object],
    ) -> None:
        """Verify the first save of a key and the second, which replaces it, are one statement each.

        A save that read the key first and then inserted or updated it would send two statements for a new key and
        three for a stored one.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_stored_book: The committed book and its pages.
        :type fx_stored_book: tuple[BookRecord, list[Page]]
        :param save: Function that saves an entity of the page in the repository under test.
        :type save: Callable[[BookRepositories, Page], object]
        """
        book_record, pages = fx_stored_book

        counts = await _statements_of(
            fx_uow(), fx_books, book_record, [lambda repos: save(repos, pages[0]), lambda repos: save(repos, pages[0])]
        )

        assert counts == [1, 1]

    async def test_save_many_sends_one_statement_for_five_records(
        self, fx_uow: UnitOfWorkFactory, fx_books: BookRegistry, fx_stored_book: tuple[BookRecord, list[Page]]
    ) -> None:
        """Verify five records of five pages are stored by one statement, not one for each.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_stored_book: The committed book and its pages.
        :type fx_stored_book: tuple[BookRecord, list[Page]]
        """
        book_record, pages = fx_stored_book
        records = [make_page_stage(page_id=page.id, stage=Stage.CLEANUP) for page in pages]

        counts = await _statements_of(
            fx_uow(), fx_books, book_record, [lambda repos: repos.page_stages.save_many(records)]
        )

        assert counts == [1]


class TestUpdateMany:
    """Tests for the statements ``update_many`` of the pages sends."""

    async def test_update_many_sends_one_read_and_one_update_for_five_pages(
        self, fx_uow: UnitOfWorkFactory, fx_books: BookRegistry, fx_stored_book: tuple[BookRecord, list[Page]]
    ) -> None:
        """Verify five pages are read by one statement and updated by one, not read and updated one at a time.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_stored_book: The committed book and its pages.
        :type fx_stored_book: tuple[BookRecord, list[Page]]
        """
        book_record, pages = fx_stored_book
        updated = [evolve(page, manual_label=UPDATED_LABEL, included=False) for page in pages]

        counts = await _statements_of(fx_uow(), fx_books, book_record, [lambda repos: repos.pages.update_many(updated)])

        assert counts == [2]
