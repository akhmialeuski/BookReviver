"""Tests of what the SQLAlchemy catalogue database promises beyond the contract suite: its threads and its readers.

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

from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyBookRepositories
from bookreviver.domain.enums import BlockKind, BookResource
from bookreviver.domain.events import BookChanged
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import SliceRequest
from bookreviver.ports.persistence import NoChangeOpenError
from tests.helpers.book_hold import HOLD_LIMIT_SECONDS
from tests.helpers.builders import make_book_place, make_page, make_page_version, make_record
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import BookRecord, PageVersion
    from bookreviver.domain.ids import AccountId
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.helpers.fakes_jobs import RecordingEventBus

pytestmark = pytest.mark.anyio

NEW_TITLE: str = 'Renamed'
# The group a page is put in and taken out of
GROUP: str = 'Engravings'
# A version of no page, which no row holds
MISSING_VERSION: PageVersion = make_page_version(page_id=PageId(uuid4()))
WRITE_ARG: str = 'write'
# A write of each form the repositories use, which a read refuses, by the name of the test case; each takes the
# repositories and the stored book of the test
WRITES: dict[str, Callable[[BookRepositories, BookRecord], object]] = {
    'insert-through-the-session': lambda repos, book_record: repos.pages.add(make_page()),
    'update-through-the-session': lambda repos, book_record: repos.book_record.update(
        evolve(book_record, details=evolve(book_record.details, title=NEW_TITLE))
    ),
    'bulk-delete': lambda repos, _book_record: repos.page_versions.delete_many([MISSING_VERSION.id]),
}


def count_pages(repos: BookRepositories, _book_record: BookRecord) -> int:
    """Count the pages of the book.

    :param repos: Repositories of the transaction.
    :type repos: BookRepositories
    :param _book_record: The record of the book.
    :type _book_record: BookRecord
    :returns: How many pages the transaction sees.
    :rtype: int
    """
    return repos.pages.list_window(SliceRequest()).total


class TestRead:
    """Tests for the transaction of ``Book.read``, which runs on a connection of the readers of the book."""

    @pytest.mark.parametrize(WRITE_ARG, list(WRITES.values()), ids=list(WRITES))
    async def test_every_form_of_write_is_refused_and_leaves_the_book_as_it_was(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_owner_id: AccountId,
        write: Callable[[BookRepositories, BookRecord], object],
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a read refuses a write through the session and a bulk statement alike, and stores nothing.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param write: A write the work of the read attempts.
        :type write: Callable[[BookRepositories, BookRecord], object]
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, owner=fx_owner_id, databases=fx_databases())

        with pytest.raises(NoChangeOpenError):
            await fx_databases().open(book_id).read(lambda repos: write(repos, book_record))

        stored, pages = (
            await fx_databases()
            .open(book_id)
            .read(lambda repos: (repos.book_record.get(), count_pages(repos, book_record)))
        )
        expect(stored == book_record)
        expect(pages == 0)
        assert_expectations()

    async def test_read_sees_one_committed_state_while_a_change_commits_meanwhile(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify two reads of one work see the same pages although a change commits another between them.

        The work reads, tells the test its snapshot is taken, and waits in its thread until the change has committed.
        Its second read is of the state its first one saw, which the write-ahead log keeps for a reader, and a read
        that begins after the commit sees the new page.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        catalogue = fx_catalogue()
        book_id = await store_book(
            catalogue, book_record, make_page(order_key='a0'), owner=fx_owner_id, databases=fx_databases()
        )
        written = make_page(order_key='a1')
        snapshot_taken, change_committed = threading.Event(), threading.Event()
        seen: list[tuple[int, int]] = []

        def count_twice(repos: BookRepositories) -> tuple[int, int]:
            """Count the pages of the book, wait for the commit of the change, and count them again.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The number of pages before the commit and after it, as this transaction saw them.
            :rtype: tuple[int, int]
            """
            before = count_pages(repos, book_record)
            snapshot_taken.set()
            assert change_committed.wait(HOLD_LIMIT_SECONDS)
            return before, count_pages(repos, book_record)

        async def read_twice() -> None:
            """Run the work of the two reads in a read of the catalogue database, and keep what it saw."""
            seen.append(await fx_databases().open(book_id).read(count_twice))

        async with anyio.create_task_group() as group:
            group.start_soon(read_twice)
            assert await to_thread.run_sync(snapshot_taken.wait, HOLD_LIMIT_SECONDS)
            await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(written))
            change_committed.set()

        after = await fx_databases().open(book_id).read(lambda repos: count_pages(repos, book_record))
        expect(seen == [(1, 1)], f'{seen} seen by the read')
        expect(after == 2, f'{after} seen by a later read')
        assert_expectations()


def group_key(repos: BookRepositories) -> int | None:
    """Return the key of the row of the group of the test, read from the table.

    :param repos: Repositories of the transaction, which are those of this adapter.
    :type repos: BookRepositories
    :returns: The key, or None when the book has no such group.
    :rtype: int | None
    """
    assert isinstance(repos, SqlAlchemyBookRepositories)
    return repos.groups.find(GROUP)


class TestBookVersion:
    """Tests for the version of a book, which a change that writes a part of the book raises and announces."""

    async def test_a_change_of_the_pages_raises_the_version_and_names_the_pages(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_databases: BookDatabasesFactory,
        fx_owner_id: AccountId,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify adding a page raises the version by one and announces it with the pages, after the change.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_databases: Function opening an opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_book_changes: Where the test collects the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=fx_owner_id, databases=fx_databases())
        database = fx_databases().open(book_id)
        before = await database.version()
        fx_book_changes.published.clear()

        await database.change(lambda repos, _book_record: repos.pages.add(make_page()))

        after = await database.version()
        expect(after == before + 1)
        expect(fx_book_changes.published == [BookChanged(version=after, resources=frozenset({BookResource.PAGES}))])
        assert_expectations()

    async def test_a_change_that_writes_nothing_or_only_the_place_leaves_the_version(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_databases: BookDatabasesFactory,
        fx_owner_id: AccountId,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a change that reads, and one that writes only the place of the reader, announce nothing.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_databases: Function opening an opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_book_changes: Where the test collects the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, make_record(), owner=fx_owner_id, databases=fx_databases())
        database = fx_databases().open(book_id)
        before = await database.version()
        fx_book_changes.published.clear()

        await database.change(count_pages)
        await database.change(lambda repos, _book_record: repos.book_places.save(make_book_place()))

        expect(await database.version() == before)
        expect(fx_book_changes.published == [])
        assert_expectations()

    async def test_a_bulk_delete_names_its_part_only_when_it_deleted_a_row(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_databases: BookDatabasesFactory,
        fx_owner_id: AccountId,
        fx_book_changes: RecordingEventBus,
    ) -> None:
        """Verify a delete of versions that finds none is no change, and one that deletes a version names the versions.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_databases: Function opening an opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_book_changes: Where the test collects the changes of the books.
        :type fx_book_changes: RecordingEventBus
        """
        book_record, page = make_record(), make_page()
        version = make_page_version(page_id=page.id)
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, page, owner=fx_owner_id, databases=fx_databases())
        database = fx_databases().open(book_id)
        await database.change(lambda repos, _book_record: repos.page_versions.add(version))
        fx_book_changes.published.clear()

        await database.change(lambda repos, _book_record: repos.page_versions.delete_many([MISSING_VERSION.id]))
        await database.change(lambda repos, _book_record: repos.page_versions.delete_many([version.id]))

        assert [event.resources for event in fx_book_changes.published if isinstance(event, BookChanged)] == [
            frozenset({BookResource.VERSIONS})
        ]


class TestGroups:
    """Tests for the rows of the groups of the pages, which a change makes and prunes."""

    async def test_a_group_is_made_by_its_first_page_and_removed_with_its_last(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify putting a page in a group makes the row of the group, and taking it out removes the row.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record, page = make_record(), make_page()
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, owner=fx_owner_id, databases=fx_databases())
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.add(page))

        grouped = (
            await fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.pages.update(evolve(page, group_label=GROUP)))
        )
        made = await fx_databases().open(book_id).read(group_key)
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.pages.update(page))
        left = await fx_databases().open(book_id).read(group_key)

        expect(grouped.group_label == GROUP)
        expect(made is not None)
        expect(left is None)
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
