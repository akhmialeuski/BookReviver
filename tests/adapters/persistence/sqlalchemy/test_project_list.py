"""Tests for the list of books over SQLite: its progress costs the same statements for one book as for a full window."""

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import event

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState
from bookreviver.domain.values import SliceRequest
from bookreviver.services.projects import ProjectService
from bookreviver.services.stage_summaries import StageSummaries
from tests.helpers.builders import EPOCH, make_job, make_page, make_page_stage, make_project
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.statements import STATEMENT_EVENT, StatementCounter

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

# A full window of the list, against the one book the statements are compared with
WINDOW: int = 5


async def _commit_books(database: SqlDatabase, owner_id: AccountId, count: int) -> None:
    """Commit books that each have a page in a stage and an active job, so every progress query returns rows.

    :param database: Fresh SQLite database with every table created.
    :type database: SqlDatabase
    :param owner_id: Committed account owning the books.
    :type owner_id: AccountId
    :param count: Number of books to commit.
    :type count: int
    """
    async with database.sessions() as session:
        uow = SqlAlchemyUnitOfWork(session)
        projects = []
        async with uow.change():
            for minutes in range(count):
                project = await uow.projects.add(make_project(owner_id=owner_id, minutes=minutes))
                await uow.jobs.add(make_job(project_id=project.id, state=JobState.RUNNING, minutes=minutes))
                projects.append(project)
        for project in projects:
            async with uow.change_book(project.id):
                page = await uow.pages.add(make_page(project_id=project.id))
                await uow.page_stages.add(make_page_stage(page_id=page.id))


async def _statements_of_list(database: SqlDatabase, owner_id: AccountId, root: Path) -> int:
    """List the owner's books through the project service and count the statements the request sends.

    :param database: SQLite database holding the owner's books.
    :type database: SqlDatabase
    :param owner_id: Account whose books are listed.
    :type owner_id: AccountId
    :param root: Storage root of the stores the service needs and the list does not touch.
    :type root: Path
    :returns: Number of statements sent while listing.
    :rtype: int
    """
    counter = StatementCounter()
    event.listen(database.engine.sync_engine, STATEMENT_EVENT, counter)
    try:
        async with database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            service = ProjectService(
                uow=uow,
                clock=FixedClock(EPOCH),
                sources=LocalSourceStore(root=root),
                assets=LocalAssetStore(root=root),
                stages=StageSummaries(uow=uow, catalogue=FakeCatalogue([])),
            )
            books = await service.list(Actor(account_id=owner_id), SliceRequest(limit=WINDOW))
    finally:
        event.remove(database.engine.sync_engine, STATEMENT_EVENT, counter)
    assert all(book.progress is not None for book in books.items)
    return counter.count


class TestProjectList:
    """Tests for the statements ``ProjectService.list`` sends to SQLite."""

    async def test_progress_of_a_window_costs_the_statements_of_one_book(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, tmp_path: Path
    ) -> None:
        """Verify a window of five books takes as many statements as one book, so no query runs per book.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the books.
        :type fx_owner_id: AccountId
        :param tmp_path: Directory of the stores the list does not touch.
        :type tmp_path: Path
        """
        await _commit_books(fx_database, fx_owner_id, 1)
        one_book = await _statements_of_list(fx_database, fx_owner_id, tmp_path)
        await _commit_books(fx_database, fx_owner_id, WINDOW - 1)
        full_window = await _statements_of_list(fx_database, fx_owner_id, tmp_path)
        assert full_window == one_book
