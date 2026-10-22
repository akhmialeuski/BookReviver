"""Tests of what the SQLAlchemy unit of work promises beyond the contract suite: its threads and its readers.

The contract suite runs the promises of the port, the order of the changes that wait for the writer included. These
tests show how this adapter keeps them: a read refuses every form of write the repositories use, and sees one committed
state while a change commits; the work of a change runs in a worker thread and different changes may run in different
threads over the one connection of the writer. They are ordered by events, never by a sleep.
"""

import threading
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from anyio import to_thread
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import BlockKind
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import SliceRequest
from bookreviver.ports.persistence import NoChangeOpenError
from tests.helpers.book_hold import HOLD_LIMIT_SECONDS
from tests.helpers.builders import make_page, make_page_version, make_project
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import PageVersion, Project
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

NEW_TITLE: str = 'Renamed'
# A version of no page, which no row holds
MISSING_VERSION: PageVersion = make_page_version(page_id=PageId(uuid4()))
WRITE_ARG: str = 'write'
# A write of each form the repositories use, which a read refuses, by the name of the test case; each takes the
# repositories and the stored project of the test
WRITES: dict[str, Callable[[BookRepositories, Project], object]] = {
    'insert-through-the-session': lambda repos, project: repos.pages.add(make_page(project_id=project.id)),
    'update-through-the-session': lambda repos, project: repos.projects.update(
        evolve(project, details=evolve(project.details, title=NEW_TITLE))
    ),
    'bulk-delete': lambda repos, _project: repos.page_versions.delete_many([MISSING_VERSION.id]),
}


def count_pages(repos: BookRepositories, project: Project) -> int:
    """Count the pages of the book.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param project: The project of the book.
    :type project: Project
    :returns: How many pages the transaction sees.
    :rtype: int
    """
    return repos.pages.list_for_project(project.id, SliceRequest()).total


class TestRead:
    """Tests for the transaction of ``read_book``, which runs on a connection of the readers of the book."""

    @pytest.mark.parametrize(WRITE_ARG, list(WRITES.values()), ids=list(WRITES))
    async def test_every_form_of_write_is_refused_and_leaves_the_project_as_it_was(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_owner_id: AccountId,
        write: Callable[[BookRepositories, Project], object],
    ) -> None:
        """Verify a read refuses a write through the session and a bulk statement alike, and stores nothing.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param write: A write the work of the read attempts.
        :type write: Callable[[BookRepositories, Project], object]
        """
        project = make_project()
        uow = fx_uow()
        await store_project(uow, project, owner=fx_owner_id)

        with pytest.raises(NoChangeOpenError):
            await uow.read_book(project.id, lambda repos: write(repos, project))

        stored, pages = await uow.read_book(
            project.id, lambda repos: (repos.projects.get(project.id), count_pages(repos, project))
        )
        expect(stored == project)
        expect(pages == 0)
        assert_expectations()

    async def test_read_sees_one_committed_state_while_a_change_commits_meanwhile(
        self, fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId
    ) -> None:
        """Verify two reads of one work see the same pages although a change commits another between them.

        The work reads, tells the test its snapshot is taken, and waits in its thread until the change has committed.
        Its second read is of the state its first one saw, which the write-ahead log keeps for a reader, and a read
        that begins after the commit sees the new page.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project()
        uow = fx_uow()
        await store_project(uow, project, make_page(project_id=project.id, order_key='a0'), owner=fx_owner_id)
        written = make_page(project_id=project.id, order_key='a1')
        snapshot_taken, change_committed = threading.Event(), threading.Event()
        seen: list[tuple[int, int]] = []

        def count_twice(repos: BookRepositories) -> tuple[int, int]:
            """Count the pages of the book, wait for the commit of the change, and count them again.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The number of pages before the commit and after it, as this transaction saw them.
            :rtype: tuple[int, int]
            """
            before = count_pages(repos, project)
            snapshot_taken.set()
            assert change_committed.wait(HOLD_LIMIT_SECONDS)
            return before, count_pages(repos, project)

        async def read_twice() -> None:
            """Run the work of the two reads in a read of the unit of work, and keep what it saw."""
            seen.append(await uow.read_book(project.id, count_twice))

        async with anyio.create_task_group() as group:
            group.start_soon(read_twice)
            assert await to_thread.run_sync(snapshot_taken.wait, HOLD_LIMIT_SECONDS)
            await uow.change_book(project.id, lambda repos, _project: repos.pages.add(written))
            change_committed.set()

        after = await uow.read_book(project.id, lambda repos: count_pages(repos, project))
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
            anyio.from_thread.run_sync(inside.set)
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
