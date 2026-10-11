"""The async engine, the session factory and the migrations shared by every SQLAlchemy table of the application.

The persistence adapter and the fastapi-users account tables use the same engine, so the application keeps one
connection pool whatever it stores. The engine, the sessions and the Alembic commands all come from one
advanced-alchemy ``SQLAlchemyAsyncConfig``, so the migrations run over the same connection settings as the
application instead of a second configuration in ``env.py``.

The configuration differs from advanced-alchemy's defaults in two places:

- Sessions keep their objects usable after commit, because a route maps them to a response after the unit of work
  committed.
- The listener that touches ``updated_at`` on every flush is off, because the domain sets ``updated_at`` through its
  ``Clock`` and the listener would overwrite it with the wall clock.

The options of autogenerate, such as comparing column types, are set in ``migrations/env.py``, because
advanced-alchemy 1.11.0 does not hand those of ``AlembicAsyncConfig`` to Alembic.

SQLite ignores foreign keys unless each connection turns them on, so the engine does that on connect, and the
``ON DELETE CASCADE`` of pages and jobs works the same as on PostgreSQL. ``migrations/env.py`` turns them off again
for the length of a migration. The engine also sets a busy timeout on every connection, the wait limit of a change
of the book, so that a block which meets the lock of a writer waits for it, and several clients can use one file at
once.

The SQLite transactions are owned by the engine, not by the driver. The engine connects with ``autocommit=True``,
the attribute that Python's ``sqlite3`` documents instead of the legacy ``isolation_level``, so the driver begins,
commits and rolls back nothing by itself. Three listeners on the engine put that back where it belongs:

- ``begin`` emits ``BEGIN IMMEDIATE`` for a connection that a block of the persistence port procured with the
  execution option :data:`CHANGE_BLOCK_OPTION`, which takes SQLite's write lock at the start and waits for it up to the
  busy timeout. A connection without the option begins nothing, so a read outside a block runs in SQLite's autocommit
  mode and holds no lock.
- ``commit`` and ``rollback`` emit ``COMMIT`` and ``ROLLBACK`` while the driver connection is in a transaction, because
  the commit and rollback of the driver do nothing in this mode.

The schema is created and changed only by the revisions in ``migrations/``, applied by hand. The application reads
:meth:`SqlDatabase.schema_revisions` at start and refuses to run against a schema it was not written for, which
costs one read of the version table and needs no event loop of Alembic's own.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from advanced_alchemy.alembic.commands import AlembicCommands
from advanced_alchemy.config import AlembicAsyncConfig, AsyncSessionConfig, EngineConfig, SQLAlchemyAsyncConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from anyio import to_thread
from attrs import frozen
from sqlalchemy import event
from sqlalchemy.engine import make_url

from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable
    from sqlite3 import Connection as DriverConnection

    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

SQLITE_DIALECT: str = 'sqlite'
# The execution option a block of the persistence port sets when it procures its connection, which makes a connection
# to SQLite begin with BEGIN IMMEDIATE
CHANGE_BLOCK_OPTION: str = 'bookreviver_change_block'
MILLISECONDS_PER_SECOND: int = 1_000
# The revisions of the schema, shipped inside the package next to the tables they change
MIGRATIONS_DIR: Path = Path(__file__).with_name('migrations')


@frozen
class SchemaRevisions:
    """Revisions of the schema: those a database records and the heads of the migrations the code ships.

    :ivar database: Revisions recorded in the database's version table, empty for a database never migrated.
    :ivar code: Head revisions of the migrations directory.
    :ivar shipped: Every revision of the migrations directory, heads and their ancestors.
    """

    database: tuple[str, ...]
    code: tuple[str, ...]
    shipped: frozenset[str]

    @property
    def is_current(self) -> bool:
        """Whether the database is at every head revision of the code and at no other."""
        return sorted(self.database) == sorted(self.code)

    @property
    def unknown(self) -> tuple[str, ...]:
        """Revisions of the database that the code does not ship, left by migrations of other code."""
        return tuple(revision for revision in self.database if revision not in self.shipped)


class SqlDatabase:
    """One engine per application, with sessions that keep objects usable after commit, and its migrations.

    :ivar config: advanced-alchemy configuration the engine, the sessions and the migrations are built from.
    :ivar engine: Async engine with the application's connection pool.
    :ivar sessions: Factory of sessions bound to the engine, which keep objects loaded after commit.
    :ivar migrations: Alembic commands over the engine and the revisions in ``migrations/``.
    """

    def __init__(self, url: str, *, wait_seconds: float = DEFAULT_CHANGE_WAIT_SECONDS) -> None:
        """Configure the engine for ``url`` and, on SQLite, hand it the connections and transactions of the port.

        :param url: SQLAlchemy async database URL, such as ``sqlite+aiosqlite:///data/bookreviver.db``.
        :type url: str
        :param wait_seconds: How long a block of the persistence port waits for the write lock of SQLite, which is the
                             busy timeout of every connection; PostgreSQL takes no part of it.
        :type wait_seconds: float
        """
        on_sqlite = make_url(url).get_backend_name() == SQLITE_DIALECT
        # aiosqlite hands the argument to sqlite3.connect, which then leaves every transaction to the listeners below
        engine_config = EngineConfig(connect_args={'autocommit': True}) if on_sqlite else EngineConfig()
        self._busy_timeout_ms = round(wait_seconds * MILLISECONDS_PER_SECOND)
        self.config = SQLAlchemyAsyncConfig(
            connection_string=url,
            engine_config=engine_config,
            session_config=AsyncSessionConfig(expire_on_commit=False),
            enable_touch_updated_timestamp_listener=False,
            alembic_config=AlembicAsyncConfig(script_location=str(MIGRATIONS_DIR)),
        )
        self.engine: AsyncEngine = self.config.get_engine()
        if on_sqlite:
            event.listen(self.engine.sync_engine, 'connect', self._configure_sqlite)
            event.listen(self.engine.sync_engine, 'begin', self._begin_sqlite)
            event.listen(self.engine.sync_engine, 'commit', self._commit_sqlite)
            event.listen(self.engine.sync_engine, 'rollback', self._rollback_sqlite)
        self.sessions: Callable[[], AsyncSession] = self.config.create_session_maker()
        self.migrations = AlembicCommands(self.config)

    def _configure_sqlite(self, dbapi_connection: DriverConnection, _record: object) -> None:
        """Make SQLite enforce foreign keys and their cascades, which it skips by default, and wait for a lock.

        A block that meets the lock of a writer, such as an import committing its pages, waits for it up to the wait
        limit instead of failing with "database is locked" after the few seconds of the default.

        :param dbapi_connection: New SQLite connection, before any statement runs on it.
        :type dbapi_connection: DriverConnection
        :param _record: Pool record of the connection, required by the event signature and unused.
        :type _record: object
        """
        dbapi_connection.execute('PRAGMA foreign_keys=ON')
        dbapi_connection.execute(f'PRAGMA busy_timeout={self._busy_timeout_ms}')

    @staticmethod
    def _begin_sqlite(connection: Connection) -> None:
        """Take SQLite's write lock at the start of a transaction that a block of the port opened.

        A connection without the block's execution option begins nothing, so its reads run in SQLite's autocommit mode
        and hold no lock.

        :param connection: Connection that begins a transaction.
        :type connection: Connection
        """
        if connection.get_execution_options().get(CHANGE_BLOCK_OPTION):
            connection.exec_driver_sql('BEGIN IMMEDIATE')

    @staticmethod
    def _commit_sqlite(connection: Connection) -> None:
        """End the transaction with COMMIT, which the driver's own commit does not do in autocommit mode.

        :param connection: Connection that commits.
        :type connection: Connection
        """
        # An invalidated connection has no driver connection left, and nothing to commit
        if (driver := connection.connection.driver_connection) is not None and driver.in_transaction:
            connection.exec_driver_sql('COMMIT')

    @staticmethod
    def _rollback_sqlite(connection: Connection) -> None:
        """End the transaction with ROLLBACK, which the driver's own rollback does not do in autocommit mode.

        :param connection: Connection that rolls back.
        :type connection: Connection
        """
        # An invalidated connection has no driver connection left, and nothing to roll back
        if (driver := connection.connection.driver_connection) is not None and driver.in_transaction:
            connection.exec_driver_sql('ROLLBACK')

    async def schema_revisions(self) -> SchemaRevisions:
        """Read the revisions the database records and the head revisions of the migrations directory.

        Only the version table is read, through Alembic's ``MigrationContext`` on a connection of the running event
        loop, so no migration environment runs.

        :returns: The revisions of the database and of the code.
        :rtype: SchemaRevisions
        """
        script = ScriptDirectory.from_config(self.migrations.config)
        # Walking the revisions reads every revision file once; the heads then come from the loaded map
        shipped = frozenset(
            await to_thread.run_sync(lambda: [revision.revision for revision in script.walk_revisions()])
        )
        options = {'version_table': self.config.alembic_config.version_table_name}
        async with self.engine.connect() as connection:
            database = await connection.run_sync(
                lambda sync_connection: MigrationContext.configure(sync_connection, opts=options).get_current_heads()
            )
        return SchemaRevisions(database=database, code=tuple(script.get_heads()), shipped=shipped)

    async def dispose(self) -> None:
        """Close every pooled connection."""
        await self.engine.dispose()
