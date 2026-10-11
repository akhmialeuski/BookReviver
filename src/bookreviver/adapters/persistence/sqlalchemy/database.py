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

Because the listeners own the transaction, they are also where a block is timed exactly. The ``write.block`` span of the
:class:`~bookreviver.ports.tracing.Tracer` starts before ``BEGIN IMMEDIATE``, so the time that statement takes is the
wait for the lock, and goes on while the block holds it. It is kept in ``connection.info`` until the commit or the
rollback begins, which ends the holding and, for a commit, times the ``COMMIT`` statement. The kind of the block and the
book it holds come with the execution options of the block.

The schema is created and changed only by the revisions in ``migrations/``, applied by hand. The application reads
:meth:`SqlDatabase.schema_revisions` at start and refuses to run against a schema it was not written for, which
costs one read of the version table and needs no event loop of Alembic's own.
"""

from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

from advanced_alchemy.alembic.commands import AlembicCommands
from advanced_alchemy.config import AlembicAsyncConfig, AsyncSessionConfig, EngineConfig, SQLAlchemyAsyncConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from anyio import to_thread
from attrs import frozen
from sqlalchemy import event
from sqlalchemy.engine import make_url

from bookreviver.domain.enums import BlockOutcome, SpanAttribute, SpanName
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS

if TYPE_CHECKING:
    from collections.abc import Callable
    from sqlite3 import Connection as DriverConnection

    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from bookreviver.ports.tracing import StartedSpan, Tracer

SQLITE_DIALECT: str = 'sqlite'
# The execution option a block of the persistence port sets when it procures its connection, which makes a connection
# to SQLite begin with BEGIN IMMEDIATE. Its value is the BlockKind of the block, which is always truthy
CHANGE_BLOCK_OPTION: str = 'bookreviver_change_block'
# The execution option that names the project a block of a book holds, which the span of the block carries
CHANGE_BOOK_OPTION: str = 'bookreviver_change_book'
# The key of connection.info under which the block that holds a connection waits for its commit or rollback
WRITE_BLOCK_INFO: str = 'bookreviver_write_block'
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


@frozen
class WriteBlock:
    """A block that holds the write lock of a connection, between the end of its wait and its commit or rollback.

    :ivar span: The ``write.block`` span, started before the lock was asked for.
    :ivar locked_at: Reading of the performance counter when ``BEGIN IMMEDIATE`` returned, which is when the holding
                     began.
    """

    span: StartedSpan
    locked_at: float


class SqlDatabase:
    """One engine per application, with sessions that keep objects usable after commit, and its migrations.

    :ivar config: advanced-alchemy configuration the engine, the sessions and the migrations are built from.
    :ivar engine: Async engine with the application's connection pool.
    :ivar sessions: Factory of sessions bound to the engine, which keep objects loaded after commit.
    :ivar migrations: Alembic commands over the engine and the revisions in ``migrations/``.
    """

    def __init__(self, url: str, *, tracer: Tracer, wait_seconds: float = DEFAULT_CHANGE_WAIT_SECONDS) -> None:
        """Configure the engine for ``url`` and, on SQLite, hand it the connections and transactions of the port.

        :param url: SQLAlchemy async database URL, such as ``sqlite+aiosqlite:///data/bookreviver.db``.
        :type url: str
        :param tracer: Tracer the write blocks of SQLite are timed with; PostgreSQL takes no part of it.
        :type tracer: Tracer
        :param wait_seconds: How long a block of the persistence port waits for the write lock of SQLite, which is the
                             busy timeout of every connection; PostgreSQL takes no part of it.
        :type wait_seconds: float
        """
        on_sqlite = make_url(url).get_backend_name() == SQLITE_DIALECT
        self._tracer = tracer
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

    def _begin_sqlite(self, connection: Connection) -> None:
        """Take SQLite's write lock at the start of a transaction that a block of the port opened, and time the wait.

        A connection without the block's execution option begins nothing, so its reads run in SQLite's autocommit mode
        and hold no lock. The span of the block starts before the statement, so the statement lasts as long as the
        wait for the lock, which is set as ``wait.ms`` whether the lock was taken or the busy timeout ran out.

        :param connection: Connection that begins a transaction.
        :type connection: Connection
        """
        options = connection.get_execution_options()
        if not (kind := options.get(CHANGE_BLOCK_OPTION)):
            return
        attributes: dict[SpanAttribute, str] = {SpanAttribute.BLOCK_KIND: kind}
        if (book := options.get(CHANGE_BOOK_OPTION)) is not None:
            attributes[SpanAttribute.BOOK] = str(book)
        span = self._tracer.start(SpanName.WRITE_BLOCK, attributes)
        asked_at = perf_counter()
        try:
            connection.exec_driver_sql('BEGIN IMMEDIATE')
        except BaseException:
            span.set_attribute(SpanAttribute.WAIT_MS, (perf_counter() - asked_at) * MILLISECONDS_PER_SECOND)
            span.set_attribute(SpanAttribute.BLOCK_OUTCOME, BlockOutcome.NOT_BEGUN)
            span.end()
            raise
        locked_at = perf_counter()
        span.set_attribute(SpanAttribute.WAIT_MS, (locked_at - asked_at) * MILLISECONDS_PER_SECOND)
        connection.info[WRITE_BLOCK_INFO] = WriteBlock(span=span, locked_at=locked_at)

    def _commit_sqlite(self, connection: Connection) -> None:
        """End the transaction with COMMIT, which the driver's own commit does not do in autocommit mode.

        :param connection: Connection that commits.
        :type connection: Connection
        """
        self._end_transaction(connection, 'COMMIT', BlockOutcome.COMMITTED)

    def _rollback_sqlite(self, connection: Connection) -> None:
        """End the transaction with ROLLBACK, which the driver's own rollback does not do in autocommit mode.

        :param connection: Connection that rolls back.
        :type connection: Connection
        """
        self._end_transaction(connection, 'ROLLBACK', BlockOutcome.ROLLED_BACK)

    @staticmethod
    def _end_transaction(connection: Connection, statement: str, outcome: BlockOutcome) -> None:
        """Run the statement that ends the transaction, and end the span of the block that held the connection.

        The holding lasts until the statement begins. The statement of a commit is timed too, as the cost of making the
        writes of the block durable, which a rollback has none of. A block whose statement did not run, because the
        connection was invalidated, or failed, is recorded as rolled back, since SQLite gave its transaction up.

        :param connection: Connection that commits or rolls back.
        :type connection: Connection
        :param statement: The SQL statement that ends the transaction.
        :type statement: str
        :param outcome: How the block ends when the statement runs.
        :type outcome: BlockOutcome
        """
        block = connection.info.pop(WRITE_BLOCK_INFO, None)
        ending_at = perf_counter()
        ended_as = BlockOutcome.ROLLED_BACK
        try:
            # An invalidated connection has no driver connection left, and nothing to end
            if (driver := connection.connection.driver_connection) is not None and driver.in_transaction:
                connection.exec_driver_sql(statement)
                ended_as = outcome
        finally:
            if isinstance(block, WriteBlock):
                span = block.span
                span.set_attribute(SpanAttribute.HOLD_MS, (ending_at - block.locked_at) * MILLISECONDS_PER_SECOND)
                if ended_as is BlockOutcome.COMMITTED:
                    span.set_attribute(SpanAttribute.COMMIT_MS, (perf_counter() - ending_at) * MILLISECONDS_PER_SECOND)
                span.set_attribute(SpanAttribute.BLOCK_OUTCOME, ended_as)
                span.end()

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
