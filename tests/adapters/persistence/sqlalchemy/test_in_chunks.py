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
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.helpers.builders import make_page, make_page_stage, make_page_version, make_project
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Page, PageVersion, Project
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

CHUNK: int = 3
COUNT: int = 7
BEFORE_EXECUTE: str = 'before_cursor_execute'


class Statements:
    """The number of values each statement of a block of reads bound.

    :ivar bound: The number of parameters of every statement, in the order they ran.
    """

    def __init__(self, database: SqlDatabase) -> None:
        """Listen to the statements of the database.

        :param database: The database whose statements are counted.
        :type database: SqlDatabase
        """
        self._engine = database.engine.sync_engine
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
        """Record how many values a statement binds.

        :param arguments: What SQLAlchemy tells of the statement, by name: its text, its values and its cursor.
        :type arguments: Any
        """
        self.bound.append(len(arguments['parameters']))


async def _seed_book(database: SqlDatabase, owner_id: AccountId) -> tuple[Project, list[Page], list[PageVersion]]:
    """Commit a book of seven pages in book order, each with one version created a minute after the one before.

    :param database: Database to commit into.
    :type database: SqlDatabase
    :param owner_id: Account owning the book.
    :type owner_id: AccountId
    :returns: The project, the pages in book order and the versions in the order they were created.
    :rtype: tuple[Project, list[Page], list[PageVersion]]
    """
    project = make_project(owner_id=owner_id)
    pages = [make_page(project_id=project.id, order_key=f'a{number}') for number in range(COUNT)]
    versions = [make_page_version(page_id=page.id, minutes=number) for number, page in enumerate(pages)]
    async with database.sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await store_project(uow, project, *pages, versions=versions)
        async with uow.change_book(project.id):
            for page in pages:
                await uow.page_stages.save(make_page_stage(page_id=page.id))
    return project, pages, versions


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
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify the pages come in book order and the versions earliest first, whichever chunk each is read in.

        The identifiers are given in reverse, so no chunk holds a run of the order.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Account that may own projects.
        :type fx_owner_id: AccountId
        """
        project, pages, versions = await _seed_book(fx_database, fx_owner_id)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            listed_pages = await uow.pages.list_by_ids(project.id, [page.id for page in reversed(pages)])
            listed_versions = await uow.page_versions.list_by_ids([version.id for version in reversed(versions)])
            listed_stages = await uow.page_stages.list_for_pages([page.id for page in reversed(pages)])
        expect([page.id for page in listed_pages] == [page.id for page in pages])
        expect([version.id for version in listed_versions] == [version.id for version in versions])
        expect({record.page_id for record in listed_stages} == {page.id for page in pages})
        assert_expectations()

    async def test_no_statement_binds_more_values_than_a_chunk(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a read of seven identifiers is three statements of at most three values, and so is a deletion.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Account that may own projects.
        :type fx_owner_id: AccountId
        """
        project, pages, versions = await _seed_book(fx_database, fx_owner_id)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            with Statements(fx_database) as reading:
                await uow.page_versions.list_by_ids([version.id for version in versions])
            async with uow.change_book(project.id):
                # Only the deletion is counted, not the statement that locks the book
                with Statements(fx_database) as deleting:
                    await uow.page_versions.delete_many([version.id for version in versions])
            remaining = await uow.page_versions.list_for_page(pages[0].id)
        expect(reading.bound == [CHUNK] * (COUNT // CHUNK) + [COUNT % CHUNK])
        expect(len(deleting.bound) == ceil(COUNT / CHUNK) and max(deleting.bound) <= CHUNK)
        expect(remaining == [])
        assert_expectations()
