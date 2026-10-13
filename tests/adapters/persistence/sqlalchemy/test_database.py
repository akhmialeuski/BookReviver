"""Tests for SqlDatabase: the engine, the sessions and the SQLite transactions the listeners of the engine own."""

import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING, Any

import pytest
from attrs import evolve
from sqlalchemy import inspect, text

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.helpers.builders import make_project
from tests.helpers.seeding import store_project
from tests.helpers.statements import StatementLog

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.app.settings import Settings
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

# A wait limit that is not the default of the settings, so a test cannot pass on the default by chance
WAIT_SECONDS: float = 2.5
BUSY_TIMEOUT_MS: int = 2_500
BEGIN_STATEMENT: str = 'BEGIN IMMEDIATE'
COMMIT_STATEMENT: str = 'COMMIT'
ROLLBACK_STATEMENT: str = 'ROLLBACK'
# What a statement of SQLite that starts or ends a transaction begins with
TRANSACTION_WORDS: tuple[str, ...] = ('BEGIN', COMMIT_STATEMENT, ROLLBACK_STATEMENT)
# The title a test gives a stored project
NEW_TITLE: str = 'Renamed'
# The pragma reading the journal mode, its answer for a database in the write-ahead log, and that of synchronous=NORMAL
JOURNAL_MODE_PRAGMA: str = 'PRAGMA journal_mode'
WAL_MODE: str = 'wal'
SYNCHRONOUS_NORMAL: int = 1
# Any statement that reads, which makes a connection hold its snapshot for as long as its transaction is open
SELECT_SCHEMA: str = 'SELECT count(*) FROM sqlite_master'


@pytest.fixture
def fx_settings(fx_settings: Settings) -> Settings:
    """Give the settings of the suite a wait limit that is not the default.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: The same settings with the wait limit of this module.
    :rtype: Settings
    """
    return fx_settings.model_copy(update={'change_wait_seconds': WAIT_SECONDS})


class TestSqlDatabase:
    """Tests for the sessions SqlDatabase opens."""

    async def test_update_stores_the_updated_at_of_the_domain(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a project updated with a new title keeps the ``updated_at`` the domain gave it, and no other.

        advanced-alchemy's touch listener, on by default, replaces an ``updated_at`` the flush does not change with
        the wall clock, which is not the ``Clock`` of the domain.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        renamed = evolve(project, details=evolve(project.details, title=NEW_TITLE))
        async with fx_database.sessions() as session:
            await store_project(SqlAlchemyUnitOfWork(session), project)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change():
                await uow.projects.update(renamed)
        async with fx_database.sessions() as session:
            stored = await SqlAlchemyUnitOfWork(session).projects.get(project.id)
        assert stored.updated_at == renamed.updated_at

    async def test_connections_are_in_autocommit_mode(self, fx_database: SqlDatabase) -> None:
        """Verify the driver connection is in the ``autocommit`` mode, so the driver begins no transaction by itself.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        async with fx_database.engine.connect() as connection:
            driver_connection = (await connection.get_raw_connection()).driver_connection
            assert driver_connection is not None
            assert driver_connection._conn.autocommit is True

    async def test_connections_wait_for_a_lock_and_enforce_foreign_keys(self, fx_database: SqlDatabase) -> None:
        """Verify every SQLite connection waits the wait limit of the settings for a lock and enforces foreign keys.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        async with fx_database.engine.connect() as connection:
            timeout: Any = (await connection.execute(text('PRAGMA busy_timeout'))).scalar_one()
            foreign_keys: Any = (await connection.execute(text('PRAGMA foreign_keys'))).scalar_one()

        assert (timeout, foreign_keys) == (BUSY_TIMEOUT_MS, 1)

    async def test_connections_write_ahead_with_normal_sync_and_the_file_keeps_the_mode(
        self, fx_database: SqlDatabase
    ) -> None:
        """Verify a connection of the engine runs in the WAL with ``synchronous=NORMAL``, and the file keeps the WAL.

        A plain ``sqlite3`` connection opened after the engine is disposed sets no pragma of its own, so it reads the
        mode the file stores, which a restarted application finds.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        async with fx_database.engine.connect() as connection:
            mode: Any = (await connection.execute(text(JOURNAL_MODE_PRAGMA))).scalar_one()
            synchronous: Any = (await connection.execute(text('PRAGMA synchronous'))).scalar_one()
        await fx_database.dispose()
        path = fx_database.engine.url.database
        assert path is not None
        with closing(sqlite3.connect(path)) as reopened:
            (stored_mode,) = reopened.execute(JOURNAL_MODE_PRAGMA).fetchone()

        assert (mode, synchronous, stored_mode) == (WAL_MODE, SYNCHRONOUS_NORMAL, WAL_MODE)


class TestSqliteTransactions:
    """Tests for the transactions the listeners of the engine put around a block of the port."""

    async def test_block_begins_immediate_and_ends_with_commit(self, fx_database: SqlDatabase) -> None:
        """Verify a block takes the write lock of SQLite when it begins and commits when it ends.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            with StatementLog(fx_database.engine.sync_engine) as log:
                async with uow.change():
                    pass
        assert log.statements == [BEGIN_STATEMENT, COMMIT_STATEMENT]

    async def test_block_left_by_an_exception_ends_with_rollback(self, fx_database: SqlDatabase) -> None:
        """Verify a block that an exception leaves rolls the transaction back, so it holds the lock no longer.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """

        async def leave_by_exception(uow: SqlAlchemyUnitOfWork) -> None:
            """Open a block and leave it by an exception.

            :param uow: Unit of work to open the block on.
            :type uow: SqlAlchemyUnitOfWork
            :raises ValueError: Always, to leave the block.
            """
            async with uow.change():
                raise ValueError

        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            with StatementLog(fx_database.engine.sync_engine) as log, pytest.raises(ValueError, match=r'^$'):
                await leave_by_exception(uow)
        assert log.statements == [BEGIN_STATEMENT, ROLLBACK_STATEMENT]

    async def test_read_outside_a_block_begins_no_transaction_and_holds_no_lock(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a read outside a block sends no transaction statement, and a second connection can write meanwhile.

        The reading session stays open while the second session writes a project and commits it. A reader that held a
        lock would make that commit wait for the wait limit of the settings and fail.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the projects.
        :type fx_owner_id: AccountId
        """
        stored, written = make_project(owner_id=fx_owner_id), make_project(owner_id=fx_owner_id)
        async with fx_database.sessions() as session:
            await store_project(SqlAlchemyUnitOfWork(session), stored)
        async with fx_database.sessions() as reading, fx_database.sessions() as writing:
            with StatementLog(fx_database.engine.sync_engine) as log:
                await SqlAlchemyUnitOfWork(reading).projects.get(stored.id)
            await store_project(SqlAlchemyUnitOfWork(writing), written)
            assert await SqlAlchemyUnitOfWork(reading).projects.get(written.id) == written
        assert [statement for statement in log.statements if statement.startswith(TRANSACTION_WORDS)] == []

    async def test_block_commits_while_a_reader_sits_in_a_read_transaction(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a block commits at once while another connection holds a read transaction open.

        In the rollback journal a commit waits for every reader to leave the file, so this one would wait for the wait
        limit and fail. In the write-ahead log the reader keeps its snapshot and the commit appends to the log.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        path = fx_database.engine.url.database
        assert path is not None
        with closing(sqlite3.connect(path, autocommit=True)) as reader:
            reader.execute('BEGIN')
            reader.execute(SELECT_SCHEMA).fetchall()
            async with fx_database.sessions() as session:
                await store_project(SqlAlchemyUnitOfWork(session), project)
        async with fx_database.sessions() as session:
            assert await SqlAlchemyUnitOfWork(session).projects.get(project.id) == project

    async def test_block_reads_a_row_another_session_changed_after_the_session_loaded_it(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a block reads the committed row, not the copy an earlier read left in the session.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        renamed = evolve(project, details=evolve(project.details, title=NEW_TITLE))
        async with fx_database.sessions() as session:
            await store_project(SqlAlchemyUnitOfWork(session), project)
        async with fx_database.sessions() as reading, fx_database.sessions() as writing:
            uow = SqlAlchemyUnitOfWork(reading)
            await uow.projects.get(project.id)
            other = SqlAlchemyUnitOfWork(writing)
            async with other.change():
                await other.projects.update(renamed)
            async with uow.change_book(project.id) as held:
                pass
        assert held.details.title == renamed.details.title

    async def test_block_keeps_the_account_row_the_session_loaded(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a block leaves the account row of the request loaded, so reading it after the block needs no query.

        fastapi-users loads the account of the request into the same session before the route runs, and reads it
        again after the route, for example to delete it after its books. An expired row would need a lazy load there,
        which an async session refuses.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        async with fx_database.sessions() as session:
            await store_project(SqlAlchemyUnitOfWork(session), project)
        async with fx_database.sessions() as session:
            account = await session.get_one(AccountTable, fx_owner_id)
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change_book(project.id):
                pass
            assert inspect(account).expired_attributes == set()
