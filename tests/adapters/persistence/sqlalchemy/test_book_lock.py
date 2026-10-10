"""Test of a move of pages and a write of one of them by a job that race on one SQLite file with two sessions.

The move is inside its ``change_book`` block when the job starts its own, so the job's block has to wait for the move to
commit, and then reads the order the move wrote. Neither write is lost, which no revision of a row had to tell.
"""

from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.adapters.storage import LocalAssetStore
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import ContentType, Side
from bookreviver.domain.values import PageAnchor, SliceRequest
from tests.helpers.book_gate import hold_book
from tests.helpers.builders import EPOCH, make_page, make_project
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.page_services import make_page_service
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId, PageId
    from tests.helpers.book_gate import BookGate

pytestmark = pytest.mark.anyio

ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2', 'a3', 'a4')
MOVED: int = 2
ANCHOR: int = 3


class TestMoveAgainstJobWrite:
    """Tests for a use case that writes pages while a job writes one of them, on SQLite."""

    async def test_a_job_write_started_inside_a_group_move_waits_and_both_writes_are_kept(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the job's block waits for the move, reads the new order, and writes the content type over it.

        The move holds the book by the gate after its block is entered. The job's session asks for its own block then,
        and is not allowed to read before the move has committed, which is checked after ``wait_all_tasks_blocked``.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        :param monkeypatch: Fixture that restores the patched method after the test.
        :type monkeypatch: pytest.MonkeyPatch
        """
        project = make_project(owner_id=fx_owner_id)
        pages = [make_page(project_id=project.id, order_key=key) for key in ORDER_KEYS]
        async with fx_database.sessions() as session:
            await store_project(SqlAlchemyUnitOfWork(session), project, *pages)
        moved = [pages[0].id, pages[1].id]
        anchor = PageAnchor(page_id=pages[ANCHOR].id, side=Side.AFTER)
        seen_by_job: list[PageId] = []

        async def job_writes_content() -> None:
            """Write the content type of the second moved page in a block of a session of its own."""
            async with fx_database.sessions() as session:
                job = SqlAlchemyUnitOfWork(session)
                async with job.change_book(project.id):
                    window = await job.pages.list_for_project(project.id, SliceRequest())
                    seen_by_job.extend(page.id for page in window.items)
                    page = await job.pages.get(moved[1])
                    await job.pages.update(evolve(page, content_type=ContentType.COLOR_PICTURE))

        async def move_the_group() -> None:
            """Move the first two pages after the fourth in a block of a session of its own, held by the gate."""
            async with fx_database.sessions() as session:
                uow = SqlAlchemyUnitOfWork(session)
                gate_holder.append(hold_book(uow, monkeypatch))
                runtime = (RecordingEventBus(), FixedClock(EPOCH), RecordingJobQueue())
                service = make_page_service(uow, LocalAssetStore(root=tmp_path / 'storage'), runtime)
                await service.move_group(Actor(account_id=fx_owner_id), project.id, moved, anchor)

        gate_holder: list[BookGate] = []
        async with anyio.create_task_group() as group:
            group.start_soon(move_the_group)
            while not gate_holder:
                await anyio.wait_all_tasks_blocked()
            gate = gate_holder[0]
            await gate.entered.wait()
            group.start_soon(job_writes_content)
            await anyio.wait_all_tasks_blocked()
            expect(not seen_by_job)
            gate.proceed.set()

        expected = [pages[MOVED].id, pages[ANCHOR].id, *moved, pages[4].id]
        async with fx_database.sessions() as session:
            stored = (await SqlAlchemyUnitOfWork(session).pages.list_for_project(project.id, SliceRequest())).items
        expect([page.id for page in stored] == expected)
        expect(next(page for page in stored if page.id == moved[1]).content_type == ContentType.COLOR_PICTURE)
        expect(seen_by_job == expected)
        assert_expectations()
