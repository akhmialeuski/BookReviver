"""Tests for SqlDatabase: the engines, their connections and the SQLite transactions the engines own.

The statements a transaction sends are read from SQLite itself. A trace callback on the driver connection of each
engine records the text of every statement SQLite runs, ``BEGIN``, ``COMMIT`` and ``ROLLBACK`` included, which the
statement events of SQLAlchemy do not show for the ones the driver sends by itself.
"""

import sqlite3
from contextlib import closing, contextmanager
from typing import TYPE_CHECKING, Any

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from sqlalchemy import text

from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.app.settings import ChangeWaitSettings
from bookreviver.domain.library import LibraryEntry
from tests.helpers.builders import EPOCH, directory_of, make_record, new_book_id
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from sqlalchemy.engine import Engine

    from bookreviver.app.settings import Settings
    from bookreviver.domain.ids import AccountId
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory

pytestmark = pytest.mark.anyio

# A wait limit of a job that is not the default of the settings, so a test cannot pass on the default by chance
WAIT_SECONDS: float = 2.5
BUSY_TIMEOUT_MS: int = 2_500
# The limit of a catalogue database of a request, which the settings give
SHORT_WAIT_SECONDS: float = 0.5
BUSY_TIMEOUT_PRAGMA: str = 'PRAGMA busy_timeout'
FOREIGN_KEYS_PRAGMA: str = 'PRAGMA foreign_keys'
BEGIN_WRITE: str = 'BEGIN IMMEDIATE'
BEGIN_READ: str = 'BEGIN'
COMMIT_STATEMENT: str = 'COMMIT'
ROLLBACK_STATEMENT: str = 'ROLLBACK'
# What a statement of SQLite that starts or ends a transaction begins with
TRANSACTION_WORDS: tuple[str, ...] = (BEGIN_READ, COMMIT_STATEMENT, ROLLBACK_STATEMENT)
# The title a test gives a stored book
NEW_TITLE: str = 'Renamed'
# The pragma reading the journal mode, its answer for a database in the write-ahead log, and that of synchronous=NORMAL
JOURNAL_MODE_PRAGMA: str = 'PRAGMA journal_mode'
WAL_MODE: str = 'wal'
SYNCHRONOUS_NORMAL: int = 1
# Any statement that reads, which makes a connection hold its snapshot for as long as its transaction is open
SELECT_SCHEMA: str = 'SELECT count(*) FROM sqlite_master'
# The driver the settings name and the one the engines run on, and the URL of a database this adapter does not run on
ASYNC_DRIVER_URL: str = 'sqlite+aiosqlite:///{path}'
SYNC_DRIVER: str = 'sqlite'
POSTGRESQL_URL: str = 'postgresql+asyncpg://user@localhost/books'


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Give the settings of the suite a wait limit of a job that is not the default.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: The same settings with the wait limit of this module.
    :rtype: Settings
    """
    change_wait = ChangeWaitSettings(request_seconds=SHORT_WAIT_SECONDS, job_seconds=WAIT_SECONDS)
    return fx_settings.model_copy(update={'change_wait': change_wait})


def set_trace(engine: Engine, callback: Any) -> None:
    """Hand a trace callback to the driver connection of an engine, or take the one it has away.

    The connection is taken from the pool and given back, so the callback stays on the connection the pool keeps. The
    engine of the writer keeps exactly one, and the engine of the readers is not used by a test that traces it.

    :param engine: Engine whose pooled connection is traced.
    :type engine: Engine
    :param callback: Function that receives the text of each statement SQLite runs, or None to stop the tracing.
    :type callback: Any
    """
    with closing(engine.raw_connection()) as raw:
        assert raw.driver_connection is not None
        raw.driver_connection.set_trace_callback(callback)


@contextmanager
def traced_sql(engine: Engine) -> Iterator[list[str]]:
    """Record the text of every statement SQLite runs on the pooled connection of an engine.

    :param engine: Engine whose connection is traced.
    :type engine: Engine
    :returns: Iterator yielding the list the statements are appended to, in the order SQLite ran them.
    :rtype: Iterator[list[str]]
    """
    statements: list[str] = []
    set_trace(engine, statements.append)
    try:
        yield statements
    finally:
        set_trace(engine, None)


def transaction_statements(statements: list[str]) -> list[str]:
    """Keep the statements that begin or end a transaction.

    :param statements: Every statement SQLite ran.
    :type statements: list[str]
    :returns: Those that begin with ``BEGIN``, ``COMMIT`` or ``ROLLBACK``, in the order they ran.
    :rtype: list[str]
    """
    return [statement for statement in statements if statement.startswith(TRANSACTION_WORDS)]


class TestSqlDatabase:
    """Tests for the engines SqlDatabase opens."""

    async def test_update_stores_the_updated_at_of_the_domain(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a book updated with a new title keeps the ``updated_at`` the domain gave it, and no other.

        advanced-alchemy's touch listener, on by default, replaces an ``updated_at`` the flush does not change with
        the current time, which is not the time the domain gave it.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        renamed = evolve(book_record, details=evolve(book_record.details, title=NEW_TITLE))
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, owner=fx_owner_id, databases=fx_databases())
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_record.update(renamed))
        stored = await fx_databases().open(book_id).read(lambda repos: repos.book_record.get())
        assert stored.updated_at == renamed.updated_at

    async def test_url_of_the_async_driver_gets_the_driver_of_the_standard_library(self, tmp_path: Path) -> None:
        """Verify the engines of the writer and of the readers run on the ``sqlite3`` driver, whatever the URL says.

        A database URL written for the asynchronous driver is still valid configuration, and neither engine may use
        that driver.

        :param tmp_path: Temporary directory of the test, holding the file the URL names.
        :type tmp_path: Path
        """
        database = SqlDatabase(
            ASYNC_DRIVER_URL.format(path=tmp_path / 'driver.db'), schema=Schema.CATALOGUE, tracer=NullTracer()
        )
        expect(database.engine.url.drivername == SYNC_DRIVER, 'the writer')
        expect(database.read_engine.url.drivername == SYNC_DRIVER, 'the readers')
        assert_expectations()

    async def test_database_other_than_sqlite_is_refused(self) -> None:
        """Verify a URL that names PostgreSQL is refused with an error that says the data lives in SQLite."""
        with pytest.raises(ValueError, match='SQLite'):
            SqlDatabase(POSTGRESQL_URL, schema=Schema.CATALOGUE, tracer=NullTracer())

    async def test_connections_leave_transactions_to_the_engine(self, fx_database: SqlDatabase) -> None:
        """Verify the driver connection of each engine begins no transaction by itself.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        for engine in (fx_database.engine, fx_database.read_engine):
            with closing(engine.raw_connection()) as raw:
                assert raw.driver_connection is not None
                expect(raw.driver_connection.isolation_level is None, f'a connection of {engine}')
        assert_expectations()

    async def test_connections_wait_for_a_lock_and_enforce_foreign_keys(self, fx_database: SqlDatabase) -> None:
        """Verify every SQLite connection waits the limit of a job for a lock when it opens, and enforces foreign keys.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        for engine in (fx_database.engine, fx_database.read_engine):
            with engine.connect() as connection:
                timeout: Any = connection.execute(text(BUSY_TIMEOUT_PRAGMA)).scalar_one()
                foreign_keys: Any = connection.execute(text(FOREIGN_KEYS_PRAGMA)).scalar_one()
            expect((timeout, foreign_keys) == (BUSY_TIMEOUT_MS, 1), f'settings of {engine}')
        assert_expectations()

    async def test_connections_write_ahead_with_normal_sync_and_the_file_keeps_the_mode(
        self, fx_database: SqlDatabase
    ) -> None:
        """Verify a connection of the engine runs in the WAL with ``synchronous=NORMAL``, and the file keeps the WAL.

        A plain ``sqlite3`` connection opened after the engines are disposed sets no pragma of its own, so it reads the
        mode the file stores, which a restarted application finds.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        with fx_database.engine.connect() as connection:
            mode: Any = connection.execute(text(JOURNAL_MODE_PRAGMA)).scalar_one()
            synchronous: Any = connection.execute(text('PRAGMA synchronous')).scalar_one()
        await fx_database.dispose()
        path = fx_database.engine.url.database
        assert path is not None
        with closing(sqlite3.connect(path)) as reopened:
            (stored_mode,) = reopened.execute(JOURNAL_MODE_PRAGMA).fetchone()

        assert (mode, synchronous, stored_mode) == (WAL_MODE, SYNCHRONOUS_NORMAL, WAL_MODE)


class TestSqliteTransactions:
    """Tests for the transactions the engines put around the work of a catalogue database."""

    async def test_change_begins_immediate_and_ends_with_commit(
        self, fx_database: SqlDatabase, fx_catalogue: CatalogueDatabaseFactory
    ) -> None:
        """Verify a change takes the write lock of SQLite when it begins and commits when it ends, and no more.

        :param fx_database: Fresh catalogue with every table created, whose statements are traced.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        """
        catalogue = fx_catalogue(wait_seconds=WAIT_SECONDS)
        with traced_sql(fx_database.engine) as statements:
            await catalogue.change(lambda _repos: None)
        assert statements == [BEGIN_WRITE, COMMIT_STATEMENT]

    async def test_change_left_by_an_exception_ends_with_rollback(
        self, fx_database: SqlDatabase, fx_catalogue: CatalogueDatabaseFactory
    ) -> None:
        """Verify a change whose work raises rolls the transaction back, so it holds the lock no longer.

        :param fx_database: Fresh catalogue with every table created, whose statements are traced.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        """

        def leave_by_exception(_repos: object) -> None:
            """Leave the work by an exception.

            :param _repos: Repositories of the transaction, which this work does not use.
            :type _repos: object
            :raises ValueError: Always, to leave the work.
            """
            raise ValueError

        catalogue = fx_catalogue(wait_seconds=WAIT_SECONDS)
        with traced_sql(fx_database.engine) as statements, pytest.raises(ValueError, match=r'^$'):
            await catalogue.change(leave_by_exception)
        assert statements == [BEGIN_WRITE, ROLLBACK_STATEMENT]

    async def test_read_begins_deferred_and_ends_with_rollback_on_a_connection_of_the_readers(
        self, fx_database: SqlDatabase, fx_catalogue: CatalogueDatabaseFactory
    ) -> None:
        """Verify a read begins a deferred transaction on the engine of the readers and ends it with a rollback.

        The read takes no write lock, since its transaction is not ``BEGIN IMMEDIATE``, and commits nothing, and the
        engine of the writer sees no statement of it.

        :param fx_database: Fresh catalogue with every table created, whose statements are traced.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        """
        catalogue = fx_catalogue()
        with traced_sql(fx_database.read_engine) as reading, traced_sql(fx_database.engine) as writing:
            await catalogue.read(lambda repos: repos.library.list_all())
        assert (transaction_statements(reading), writing) == ([BEGIN_READ, ROLLBACK_STATEMENT], [])

    async def test_change_commits_while_a_reader_sits_in_a_read_transaction(
        self, fx_database: SqlDatabase, fx_catalogue: CatalogueDatabaseFactory, fx_owner_id: AccountId
    ) -> None:
        """Verify a change commits at once while another connection holds a read transaction open.

        In the rollback journal a commit waits for every reader to leave the file, so this one would wait for the wait
        limit and fail. In the write-ahead log the reader keeps its snapshot and the commit appends to the log.

        :param fx_database: Fresh catalogue with every table created, which a plain connection holds a read on.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_owner_id: Committed account the entry of the change names.
        :type fx_owner_id: AccountId
        """
        entry = LibraryEntry(
            book_id=new_book_id(), owner_id=fx_owner_id, directory=directory_of(new_book_id()), added_at=EPOCH
        )
        catalogue = fx_catalogue()
        path = fx_database.engine.url.database
        assert path is not None
        with closing(sqlite3.connect(path, autocommit=True)) as reader:
            reader.execute(BEGIN_READ)
            reader.execute(SELECT_SCHEMA).fetchall()
            await catalogue.change(lambda repos: repos.library.add(entry))
        assert await catalogue.read(lambda repos: repos.library.get(entry.book_id)) == entry

    async def test_book_change_hands_the_work_the_book_as_the_last_change_committed_it(
        self, fx_catalogue: CatalogueDatabaseFactory, fx_owner_id: AccountId, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify a ``Book.change`` reads the book's record at its start, so it sees the title another change stored.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        renamed = evolve(book_record, details=evolve(book_record.details, title=NEW_TITLE))
        catalogue = fx_catalogue()
        book_id = await store_book(catalogue, book_record, owner=fx_owner_id, databases=fx_databases())
        await fx_databases().open(book_id).change(lambda repos, _book_record: repos.book_record.update(renamed))
        held = await fx_databases().open(book_id).change(lambda _repos, current: current)
        assert held.details.title == NEW_TITLE
