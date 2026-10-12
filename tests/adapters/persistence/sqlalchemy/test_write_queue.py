"""Tests for the write queue of SqlDatabase, which lets the blocks of one process ask SQLite for its lock in turn.

The order of the turns is part of the port and checked by its contract suite. What only this adapter has to show is
that a block waiting for its turn holds no connection of the pool, which a block waiting in the busy timeout of SQLite
did. The test is ordered by events and never by a sleep: the second block has joined the queue once its span started.
"""

from typing import TYPE_CHECKING

import anyio
import pytest
from sqlalchemy.pool import QueuePool

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Project
    from tests.helpers.tracers import AskingTracer

pytestmark = pytest.mark.anyio


class TestWriteQueue:
    """Tests for the blocks of one database that wait in its queue."""

    async def test_block_waiting_for_its_turn_holds_no_connection_of_the_pool(
        self, fx_traced_database: SqlDatabase, fx_project: Project, fx_tracer: AskingTracer
    ) -> None:
        """Verify that while one block holds the lock and another waits for its turn, one connection is checked out.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_project: Committed project both blocks change.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test, which tells when the second block has joined the queue.
        :type fx_tracer: AskingTracer
        """
        pool = fx_traced_database.engine.pool
        assert isinstance(pool, QueuePool)
        holding, release = anyio.Event(), anyio.Event()
        second_asking = fx_tracer.ask_after(2)
        checked_out: list[int] = []

        async def first() -> None:
            """Hold the book until the test lets it go."""
            async with (
                fx_traced_database.sessions() as session,
                SqlAlchemyUnitOfWork(session).change_book(fx_project.id),
            ):
                holding.set()
                await release.wait()

        async def second() -> None:
            """Ask for the book while the first block holds it."""
            async with (
                fx_traced_database.sessions() as session,
                SqlAlchemyUnitOfWork(session).change_book(fx_project.id),
            ):
                pass

        async with anyio.create_task_group() as group:
            group.start_soon(first)
            await holding.wait()
            group.start_soon(second)
            await second_asking.wait()
            await anyio.wait_all_tasks_blocked()
            checked_out.append(pool.checkedout())
            release.set()

        assert checked_out == [1]
