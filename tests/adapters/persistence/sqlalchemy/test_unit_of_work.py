"""Tests of what the SQLAlchemy unit of work promises beyond the contract suite: its threads, its readers and its queue.

The contract suite runs the promises every adapter of the port makes. These tests show how this adapter keeps them: a
read refuses every form of write the repositories use, and sees one committed state while a change commits; the work of
a change runs in a worker thread and different changes may run in different threads over the one connection of the
writer; and changes that wait for the writer have it in the order they asked. They are ordered by events and by a held
book, never by a sleep.
"""

import asyncio
import threading
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from anyio import to_thread
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import BlockKind
from bookreviver.domain.errors import BookBusyError
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import SliceRequest
from bookreviver.ports.persistence import NoChangeOpenError
from tests.helpers.book_gate import HOLD_LIMIT_SECONDS, hold_book_in_thread
from tests.helpers.builders import make_page_version, make_project
from tests.helpers.seeding import store_project
from tests.helpers.wait_limits import IMPATIENT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import PageVersion, Project
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import Repositories
    from tests.helpers.tracers import AskingTracer

pytestmark = pytest.mark.anyio

NEW_TITLE: str = 'Renamed'
# A version of no page, which no row holds
MISSING_VERSION: PageVersion = make_page_version(page_id=PageId(uuid4()))
# How many changes queue behind a held book in the test of the order they run in
WAITERS: int = 4
WRITE_ARG: str = 'write'
# A write of each form the repositories use, which a read refuses, by the name of the test case; each takes the
# repositories and the stored project of the test
WRITES: dict[str, Callable[[Repositories, Project], object]] = {
    'insert-through-the-session': lambda repos, project: repos.projects.add(make_project(owner_id=project.owner_id)),
    'update-through-the-session': lambda repos, project: repos.projects.update(
        evolve(project, details=evolve(project.details, title=NEW_TITLE))
    ),
    'bulk-delete': lambda repos, _project: repos.page_versions.delete_many([MISSING_VERSION.id]),
}


class TestRead:
    """Tests for the transaction of ``read``, which runs on a connection of the readers."""

    @pytest.mark.parametrize(WRITE_ARG, list(WRITES.values()), ids=list(WRITES))
    async def test_every_form_of_write_is_refused_and_leaves_the_project_as_it_was(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, write: Callable[[Repositories, Project], object]
    ) -> None:
        """Verify a read refuses a write through the session and a bulk statement alike, and stores nothing.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param write: A write the work of the read attempts.
        :type write: Callable[[Repositories, Project], object]
        """
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project)

        with pytest.raises(NoChangeOpenError):
            await uow.read(lambda repos: write(repos, project))

        owned = await uow.read(lambda repos: repos.projects.list_for_owner(fx_owner_id, SliceRequest()))
        expect([overview.project for overview in owned.items] == [project])
        assert_expectations()

    async def test_read_sees_one_committed_state_while_a_change_commits_meanwhile(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify two reads of one work see the same projects although a change commits another between them.

        The work reads, tells the test its snapshot is taken, and waits in its thread until the change has committed.
        Its second read is of the state its first one saw, which the write-ahead log keeps for a reader, and a read
        that begins after the commit sees the new project.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the projects.
        :type fx_owner_id: AccountId
        """
        stored, written = make_project(owner_id=fx_owner_id), make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, stored)
        snapshot_taken, change_committed = threading.Event(), threading.Event()
        seen: list[tuple[int, int]] = []

        def count_twice(repos: Repositories) -> tuple[int, int]:
            """Count the owner's projects, wait for the commit of the change, and count them again.

            :param repos: Repositories of the transaction.
            :type repos: Repositories
            :returns: The number of projects before the commit and after it, as this transaction saw them.
            :rtype: tuple[int, int]
            """
            before = repos.projects.list_for_owner(fx_owner_id, SliceRequest()).total
            snapshot_taken.set()
            assert change_committed.wait(HOLD_LIMIT_SECONDS)
            return before, repos.projects.list_for_owner(fx_owner_id, SliceRequest()).total

        async def read_twice() -> None:
            """Run the work of the two reads in a read of the unit of work, and keep what it saw."""
            seen.append(await uow.read(count_twice))

        async with anyio.create_task_group() as group:
            group.start_soon(read_twice)
            assert await to_thread.run_sync(snapshot_taken.wait, HOLD_LIMIT_SECONDS)
            await uow.change(lambda repos: repos.projects.add(written))
            change_committed.set()

        after = await uow.read(lambda repos: repos.projects.list_for_owner(fx_owner_id, SliceRequest()).total)
        expect(seen == [(1, 1)], f'{seen} seen by the read')
        expect(after == 2, f'{after} seen by a later read')
        assert_expectations()


class TestChangeThreads:
    """Tests for the threads the work of the changes runs in."""

    async def test_works_of_successive_changes_run_on_different_threads_over_the_one_writer_connection(
        self, fx_database: SqlDatabase
    ) -> None:
        """Verify a change whose thread is another than the previous one uses the same connection of the writer.

        anyio may run the works of two changes on different worker threads, since the capacity limiter of the writer
        only lets one run at a time. The second change is given another thread by occupying the idle thread the first
        one left, and the connection, which was opened for the first thread, must still do its work, which the driver
        refuses unless the engine opened it for every thread.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        loop = asyncio.get_running_loop()
        inside, release = anyio.Event(), threading.Event()
        occupied: list[int] = []

        def identify(session: Session) -> tuple[int, int]:
            """Name the thread the work runs in and the driver connection of its transaction.

            :param session: Session of the transaction of the change.
            :type session: Session
            :returns: The identifier of the thread and of the driver connection.
            :rtype: tuple[int, int]
            """
            return threading.get_ident(), id(session.connection().connection.driver_connection)

        def occupy_the_idle_thread() -> int:
            """Tell the test the thread is taken, hold it until the test lets it go, and name it.

            :returns: The identifier of the thread.
            :rtype: int
            """
            loop.call_soon_threadsafe(inside.set)
            release.wait(HOLD_LIMIT_SECONDS)
            return threading.get_ident()

        async def occupy() -> None:
            """Run the holder of the idle thread, and keep the identifier of the thread it held."""
            occupied.append(await to_thread.run_sync(occupy_the_idle_thread))

        change = partial(fx_database.write, identify, kind=BlockKind.OTHER, book=None, wait_seconds=HOLD_LIMIT_SECONDS)
        first_thread, first_connection = await change()
        # Both are set inside the task group, which the type checker cannot know runs its body to the end
        second_thread, second_connection = first_thread, first_connection
        async with anyio.create_task_group() as group:
            group.start_soon(occupy)
            await inside.wait()
            second_thread, second_connection = await change()
            release.set()

        expect(occupied == [first_thread], 'the idle thread of the first change was the one occupied')
        expect(second_thread != first_thread, 'the second change ran on another thread')
        expect(second_connection == first_connection, 'both changes used the one connection of the writer')
        assert_expectations()


class TestWriterQueue:
    """Tests for the changes that wait for the writer, which gives it out in the order they asked."""

    async def test_waiting_changes_have_the_writer_in_the_order_they_asked(
        self, fx_traced_database: SqlDatabase, fx_project: Project, fx_tracer: AskingTracer
    ) -> None:
        """Verify changes that queue behind a held book run in the order their spans started, one by one.

        Each change is started only once the span of the one before it has started, which happens before it waits, so
        the order they asked in is known without a sleep. The book is held by a change whose work blocks its thread.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        :param fx_tracer: Tracer of the test, which tells when a change has asked for the writer.
        :type fx_tracer: AskingTracer
        """
        uow = SqlAlchemyUnitOfWork(fx_traced_database)
        order: list[int] = []
        # The hold ends first, when the block ends, and the group then waits for the changes that queued behind it
        async with anyio.create_task_group() as group, hold_book_in_thread(uow, fx_project.id):
            for number in range(WAITERS):
                asked = fx_tracer.ask_after(1)
                group.start_soon(uow.change, lambda _repos, taken=number: order.append(taken))
                await asked.wait()
        assert order == list(range(WAITERS))

    async def test_change_that_does_not_have_the_writer_within_its_limit_is_refused_and_the_holder_commits(
        self, fx_traced_database: SqlDatabase, fx_project: Project
    ) -> None:
        """Verify a change with a limit of a few milliseconds is refused behind a held book, and the book is kept.

        :param fx_traced_database: Database whose blocks are timed.
        :type fx_traced_database: SqlDatabase
        :param fx_project: Committed project of the database.
        :type fx_project: Project
        """
        holder = SqlAlchemyUnitOfWork(fx_traced_database)
        asker = SqlAlchemyUnitOfWork(fx_traced_database, wait_seconds=IMPATIENT_SECONDS)
        async with hold_book_in_thread(holder, fx_project.id):
            with pytest.raises(BookBusyError):
                await asker.change(lambda _repos: None)
        assert await holder.read(lambda repos: repos.projects.get(fx_project.id)) == fx_project
