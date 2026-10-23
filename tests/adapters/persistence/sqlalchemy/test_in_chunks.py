"""Tests of the reads of a list of identifiers on SQLAlchemy, which send the list a chunk at a time.

A statement binds a limited number of values, 32766 on SQLite, and a book of many thousands of pages names more
identifiers than that in one read. The tests make the chunk small, so seven identifiers need three statements.
"""

from math import ceil
from typing import TYPE_CHECKING, Any, Self

import pytest
from delayed_assert import assert_expectations, expect
from sqlalchemy import event

from bookreviver.adapters.persistence.sqlalchemy.repositories import RowRepository
from tests.helpers.builders import make_page, make_page_stage, make_page_version, make_record
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.entities import BookRecord, Page, PageVersion
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

CHUNK: int = 3
COUNT: int = 7
BEFORE_EXECUTE: str = 'before_cursor_execute'
# The statement that begins a transaction, which the counts leave out
TRANSACTION_BEGIN: str = 'BEGIN'


class Statements:
    """The number of values each statement of a block of reads bound.

    :ivar bound: The number of parameters of every statement, in the order they ran.
    """

    def __init__(self, engine: Engine) -> None:
        """Listen to the statements of an engine.

        :param engine: The engine whose statements are counted.
        :type engine: Engine
        """
        self._engine = engine
        self.bound: list[int] = []

    def __enter__(self) -> Self:
        """Start counting.

        :returns: The counter.
        :rtype: Self
        """
        event.listen(self._engine, BEFORE_EXECUTE, self._count, named=True)
        return self

    def __exit__(self, *exception: object) -> None:
        """Stop counting.

        :param exception: The exception the block raised, if any.
        :type exception: object
        """
        event.remove(self._engine, BEFORE_EXECUTE, self._count)

    def _count(self, **arguments: Any) -> None:
        """Record how many values a statement binds, leaving out the BEGIN a read sends with its first statement.

        :param arguments: What SQLAlchemy tells of the statement, by name: its text, its values and its cursor.
        :type arguments: Any
        """
        if not str(arguments['statement']).startswith(TRANSACTION_BEGIN):
            self.bound.append(len(arguments['parameters']))


async def _seed_book(uow: UnitOfWork, owner_id: AccountId) -> tuple[BookRecord, list[Page], list[PageVersion]]:
    """Commit a book of seven pages in book order, each with one version created a minute after the one before.

    :param uow: Unit of work to commit through.
    :type uow: UnitOfWork
    :param owner_id: Account owning the book.
    :type owner_id: AccountId
    :returns: The book, the pages in book order and the versions in the order they were created.
    :rtype: tuple[BookRecord, list[Page], list[PageVersion]]
    """
    book_record = make_record()
    pages = [make_page(order_key=f'a{number}') for number in range(COUNT)]
    versions = [make_page_version(page_id=page.id, minutes=number) for number, page in enumerate(pages)]
    await store_book(uow, book_record, *pages, owner=owner_id, versions=versions)

    def save_stages(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Save a record of a stage for every page of the book.

        :param repos: BookRepositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Record of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        for page in pages:
            repos.page_stages.save(make_page_stage(page_id=page.id))

    await uow.change_book(book_record.id, save_stages)
    return book_record, pages, versions


@pytest.fixture
def fx_small_chunks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the chunk of a list of identifiers three, so the book of seven pages needs three of them.

    :param monkeypatch: Restores the chunk after the test.
    :type monkeypatch: pytest.MonkeyPatch
    """
    monkeypatch.setattr(RowRepository, 'IN_CHUNK_SIZE', CHUNK)


@pytest.mark.usefixtures('fx_small_chunks')
class TestListsLongerThanAChunk:
    """Tests for the reads that take more identifiers than one chunk holds."""

    async def test_every_row_comes_back_in_the_order_one_statement_would_give_it(
        self, fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId
    ) -> None:
        """Verify the pages come in book order and the versions earliest first, whichever chunk each is read in.

        The identifiers are given in reverse, so no chunk holds a run of the order.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Account that may own books.
        :type fx_owner_id: AccountId
        """
        uow = fx_uow()
        book_record, pages, versions = await _seed_book(uow, fx_owner_id)
        listed_pages, listed_versions, listed_stages = await uow.read_book(
            book_record.id,
            lambda repos: (
                repos.pages.list_by_ids([page.id for page in reversed(pages)]),
                repos.page_versions.list_by_ids([version.id for version in reversed(versions)]),
                repos.page_stages.list_for_pages([page.id for page in reversed(pages)]),
            ),
        )
        expect([page.id for page in listed_pages] == [page.id for page in pages])
        expect([version.id for version in listed_versions] == [version.id for version in versions])
        expect({record.page_id for record in listed_stages} == {page.id for page in pages})
        assert_expectations()

    async def test_no_statement_binds_more_values_than_a_chunk(
        self, fx_uow: UnitOfWorkFactory, fx_books: BookRegistry, fx_owner_id: AccountId
    ) -> None:
        """Verify a read of seven identifiers is three statements of at most three values, and so is a deletion.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_books: Registry of the books of the test, whose engines of the book are listened to.
        :type fx_books: BookRegistry
        :param fx_owner_id: Account that may own books.
        :type fx_owner_id: AccountId
        """
        uow = fx_uow()
        book_record, pages, versions = await _seed_book(uow, fx_owner_id)
        version_ids = [version.id for version in versions]

        def list_versions(repos: BookRepositories) -> Statements:
            """List the versions by their identifiers, and count the statements that took.

            Only the statements of the read are counted, not the ones that begin and end its transaction.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :returns: The counter of the statements the listing sent.
            :rtype: Statements
            """
            with Statements(book.read_engine) as counted:
                repos.page_versions.list_by_ids(version_ids)
            return counted

        def delete_versions(repos: BookRepositories, _book_record: BookRecord) -> Statements:
            """Delete the versions by their identifiers, and count the statements that took.

            Only the deletion is counted, not the statements that begin the transaction or read the book.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            :returns: The counter of the statements the deletion sent.
            :rtype: Statements
            """
            with Statements(book.engine) as counted:
                repos.page_versions.delete_many(version_ids)
            return counted

        async with fx_books.use(book_record.id) as book:
            reading = await uow.read_book(book_record.id, list_versions)
            deleting = await uow.change_book(book_record.id, delete_versions)
        remaining = await uow.read_book(book_record.id, lambda repos: repos.page_versions.list_for_page(pages[0].id))
        expect(reading.bound == [CHUNK] * (COUNT // CHUNK) + [COUNT % CHUNK], f'read bound {reading.bound}')
        expect(
            len(deleting.bound) == ceil(COUNT / CHUNK) and max(deleting.bound) <= CHUNK,
            f'deletion bound {deleting.bound}',
        )
        expect(remaining == [])
        assert_expectations()
