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
for the length of a migration. The engine also sets a busy timeout on every connection when it opens, the longest wait
limit of a unit of work, so that a statement which meets the lock of a writer in another process waits for it, and
several clients can use one file at once. A block of the port sets the busy timeout again right before it begins, to
what is left of its limit after the time it spent in the queue, so the queue and the lock of SQLite together take no
longer than the limit. The value differs from block to block, so every block sends the statement. The busy timeout a
block leaves on a pooled connection does not affect a statement outside a block: in the write-ahead log a read never
waits for the write lock, and every write runs inside a block.

Every connection puts the database in the write-ahead log mode with ``synchronous=NORMAL``. In the rollback journal a
commit waits for every reader to leave the file, and readers wait for a commit; in the WAL neither waits for the other,
and a commit appends to the log without the ``fsync`` of the database file, which SQLite documents as the setting for
WAL. A power loss may lose the last commits but never corrupts the file. The mode is stored in the file, so the first
connection switches it and the others find it set. WAL needs every process that opens the file to share its memory,
which holds for one machine and not for a network file system. One writer at a time remains, as in any mode.

That one writer is why the blocks of a process queue for it in a :class:`WriteQueue` before they ask SQLite. The busy
timeout of SQLite is no queue: a waiting connection sleeps and tries again, so a block that came later may take the
lock first and one that came early may lose for seconds, and it holds a connection of the pool all the while. The
queue lets the blocks in the order they came, and a block takes its connection only when its turn comes. Every
session of SQLite finds the queue of its database in ``Session.info`` under :data:`WRITE_QUEUE_INFO`. Blocks of other
processes still meet at the busy timeout.

The SQLite transactions are owned by the engine, not by the driver. The engine connects with ``autocommit=True``,
the attribute that Python's ``sqlite3`` documents instead of the legacy ``isolation_level``, so the driver begins,
commits and rolls back nothing by itself. Three listeners on the engine put that back where it belongs:

- ``begin`` emits ``BEGIN IMMEDIATE`` for a connection that a block of the persistence port procured with the
  execution option :data:`CHANGE_BLOCK_OPTION`, which takes SQLite's write lock at the start and waits for it up to what
  is left of the limit of the block. A connection without the option begins nothing, so a read outside a block runs in
  SQLite's autocommit mode and holds no lock.
- ``commit`` and ``rollback`` emit ``COMMIT`` and ``ROLLBACK`` while the driver connection is in a transaction, because
  the commit and rollback of the driver do nothing in this mode.

The ``write.block`` span of the :class:`~bookreviver.ports.tracing.Tracer` starts when the block joins the queue, so its
wait is the time in the queue and in ``BEGIN IMMEDIATE`` together, and goes on while the block holds the lock. The
queue hands it to the block with the execution option, and ``begin`` keeps it in ``connection.info`` until the commit or
the rollback begins, which ends the holding and, for a commit, times the ``COMMIT`` statement. A block that never took
the lock has its span ended by the queue.

These durations are measured on the event loop, not inside SQLite. aiosqlite runs a statement in a thread of its own and
wakes the coroutine through the loop, so the clock is read only when the loop reaches that wake-up. A loop that is busy
makes the wait longer by its delay, the holding shorter by the same amount, and the commit longer, and the ``loop.lag``
spans of the probe in ``bookreviver.app.tracing`` show how long that delay was.

The schema is created and changed only by the revisions in ``migrations/``, applied by hand. The application reads
:meth:`SqlDatabase.schema_revisions` at start and refuses to run against a schema it was not written for, which
costs one read of the version table and needs no event loop of Alembic's own.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING

import anyio
from advanced_alchemy.alembic.commands import AlembicCommands
from advanced_alchemy.config import AlembicAsyncConfig, AsyncSessionConfig, EngineConfig, SQLAlchemyAsyncConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from anyio import to_thread
from attrs import define, frozen
from sqlalchemy import event
from sqlalchemy.engine import make_url

from bookreviver.adapters.persistence.turns import hold_in_turn
from bookreviver.domain.enums import BlockOutcome, SpanAttribute, SpanName
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from sqlite3 import Connection as DriverConnection

    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

    from bookreviver.domain.enums import BlockKind
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.tracing import StartedSpan, Tracer

SQLITE_DIALECT: str = 'sqlite'
# The execution option a block of the persistence port sets when it procures its connection, which makes a connection
# to SQLite begin with BEGIN IMMEDIATE. Its value is the WriteBlock the queue gave the block
CHANGE_BLOCK_OPTION: str = 'bookreviver_change_block'
# The key of Session.info under which a session of SQLite finds the WriteQueue of its database
WRITE_QUEUE_INFO: str = 'bookreviver_write_queue'
# The key of connection.info under which the block that holds a connection waits for its commit or rollback
WRITE_BLOCK_INFO: str = 'bookreviver_write_block'
# The statement that gives a connection its busy timeout, in milliseconds
BUSY_TIMEOUT_STATEMENT: str = 'PRAGMA busy_timeout={milliseconds}'
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


@define
class WriteBlock:
    """A block of the port, from the moment it joined the write queue until its commit or rollback.

    :ivar span: The ``write.block`` span, started when the block joined the queue.
    :ivar asked_at: Reading of the performance counter when the block joined the queue, which the wait is counted from.
    :ivar wait_seconds: How long the block waits for its turn and for the write lock of SQLite together, counted from
                        ``asked_at``. It is the limit of the unit of work that opened it, and ``begin`` sets what is
                        left of it as the busy timeout of the connection.
    :ivar locked_at: Reading of the performance counter when the block resumed after ``BEGIN IMMEDIATE``, which is
                     when the holding is counted from and which lags the lock actually taken by the delay of the event
                     loop, or None while the block has not taken the lock.
    """

    span: StartedSpan
    asked_at: float
    wait_seconds: float
    locked_at: float | None = None


class WriteQueue:
    """The turns of the write blocks of one process at the write lock of SQLite, given in the order they were asked.

    A second block opened by the same task waits for the wait limit, as it would at the busy timeout of SQLite, for the
    reason :mod:`bookreviver.adapters.persistence.turns` gives.
    """

    def __init__(self, *, tracer: Tracer) -> None:
        """Start with no block in the queue.

        :param tracer: Tracer the ``write.block`` spans are started with.
        :type tracer: Tracer
        """
        self._tracer = tracer
        self._turn = anyio.Semaphore(1)

    @asynccontextmanager
    async def turn(self, kind: BlockKind, book: ProjectId | None, *, wait_seconds: float) -> AsyncIterator[WriteBlock]:
        """Wait for the turn of a block and hold it until the block ends.

        The span of the block starts here, before the wait. When the block ends without ``BEGIN IMMEDIATE`` having
        taken the lock, because its turn did not come in time, SQLite refused the lock or the connection failed, the
        span ends here as not begun, with the whole time it waited.

        :param kind: Kind of the block.
        :type kind: BlockKind
        :param book: Project the block holds, or None for a block outside the content of a book.
        :type book: ProjectId | None
        :param wait_seconds: How long this block waits for its turn and for the write lock of SQLite together before it
                             gives up with ``BookBusyError``, which is the limit of the unit of work that opened it.
                             The turn may take all of it, and ``begin`` then leaves the lock none.
        :type wait_seconds: float
        :returns: Iterator yielding the block once its turn has come, which the block hands to its connection through
                  :data:`CHANGE_BLOCK_OPTION`.
        :rtype: AsyncIterator[WriteBlock]
        :raises BookBusyError: If the turn did not come within the wait limit.
        """
        attributes: dict[SpanAttribute, str] = {SpanAttribute.BLOCK_KIND: kind}
        if book is not None:
            attributes[SpanAttribute.BOOK] = str(book)
        block = WriteBlock(
            span=self._tracer.start(SpanName.WRITE_BLOCK, attributes),
            asked_at=perf_counter(),
            wait_seconds=wait_seconds,
        )
        try:
            async with hold_in_turn(self._turn, wait_seconds=wait_seconds):
                yield block
        finally:
            if block.locked_at is None:
                block.span.set_attribute(
                    SpanAttribute.WAIT_MS, (perf_counter() - block.asked_at) * MILLISECONDS_PER_SECOND
                )
                block.span.set_attribute(SpanAttribute.BLOCK_OUTCOME, BlockOutcome.NOT_BEGUN)
                block.span.end()


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
        :param wait_seconds: The busy timeout every connection gets when it opens, which is the longest limit a unit of
                             work may have. A block sets the busy timeout again before it begins, to what is left of
                             its own limit, which the unit of work passes to the queue; PostgreSQL takes no part of
                             it.
        :type wait_seconds: float
        """
        on_sqlite = make_url(url).get_backend_name() == SQLITE_DIALECT
        # aiosqlite hands the argument to sqlite3.connect, which then leaves every transaction to the listeners below
        engine_config = EngineConfig(connect_args={'autocommit': True}) if on_sqlite else EngineConfig()
        # PostgreSQL orders the blocks by the row lock of the book, so its sessions carry no queue
        session_info = {WRITE_QUEUE_INFO: WriteQueue(tracer=tracer)} if on_sqlite else None
        self._busy_timeout_ms = round(wait_seconds * MILLISECONDS_PER_SECOND)
        self.config = SQLAlchemyAsyncConfig(
            connection_string=url,
            engine_config=engine_config,
            session_config=AsyncSessionConfig(expire_on_commit=False, info=session_info),
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
        """Make SQLite wait for a lock, enforce foreign keys and keep its journal in the write-ahead log.

        A statement that meets the lock of a writer in another process, such as an import committing its pages, waits
        for it up to the wait limit instead of failing with "database is locked" after the few seconds of the default.
        The busy timeout is set first, so that switching the journal waits for such a writer too.

        :param dbapi_connection: New SQLite connection, before any statement runs on it.
        :type dbapi_connection: DriverConnection
        :param _record: Pool record of the connection, required by the event signature and unused.
        :type _record: object
        """
        dbapi_connection.execute(BUSY_TIMEOUT_STATEMENT.format(milliseconds=self._busy_timeout_ms))
        dbapi_connection.execute('PRAGMA foreign_keys=ON')
        dbapi_connection.execute('PRAGMA journal_mode=WAL')
        dbapi_connection.execute('PRAGMA synchronous=NORMAL')

    @staticmethod
    def _begin_sqlite(connection: Connection) -> None:
        """Take SQLite's write lock at the start of a transaction that a block of the port opened, and time the wait.

        A connection without the block's execution option begins nothing, so its reads run in SQLite's autocommit mode
        and hold no lock. The wait is counted from the moment the block joined the queue, so it covers the queue and the
        statement. A statement that fails leaves the span to the queue, which ends it as not begun.

        The statement is awaited on the event loop, and aiosqlite wakes the coroutine through it, so the wait is the
        time from the request until the loop resumed the block. It includes the delay of the loop, which the
        ``loop.lag`` spans show, and the holding that begins at the same reading is shortened by it.

        The limit of the block covers the queue and the lock together. Right before the statement, the busy timeout of
        the connection is set to what is left of the limit after the time since the block joined the queue, never below
        zero, so a writer in another process cannot make the block wait twice its limit. With nothing left,
        ``BEGIN IMMEDIATE`` takes a free lock at once and fails with SQLITE_BUSY at once when the lock is held. The
        remaining time differs on nearly every block, so the statement is sent every time.

        :param connection: Connection that begins a transaction.
        :type connection: Connection
        """
        if not isinstance(block := connection.get_execution_options().get(CHANGE_BLOCK_OPTION), WriteBlock):
            return
        remaining_seconds = block.wait_seconds - (perf_counter() - block.asked_at)
        busy_timeout_ms = max(round(remaining_seconds * MILLISECONDS_PER_SECOND), 0)
        connection.exec_driver_sql(BUSY_TIMEOUT_STATEMENT.format(milliseconds=busy_timeout_ms))
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        block.locked_at = perf_counter()
        block.span.set_attribute(SpanAttribute.WAIT_MS, (block.locked_at - block.asked_at) * MILLISECONDS_PER_SECOND)
        connection.info[WRITE_BLOCK_INFO] = block

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
        writes of the block durable, which a rollback has none of. Both durations are read on the event loop, so they
        include its delay, as the wait of ``_begin_sqlite`` does. A block whose statement did not run, because the
        connection was invalidated, is recorded as rolled back. A commit that failed keeps the time it took and is
        recorded as rolled back, since the unit of work rolls it back next.

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
        attempted = False
        try:
            # An invalidated connection has no driver connection left, and nothing to end
            if (driver := connection.connection.driver_connection) is not None and driver.in_transaction:
                attempted = True
                connection.exec_driver_sql(statement)
                ended_as = outcome
        finally:
            # Only a block that took the lock is kept in connection.info, so its locked_at is always read
            if isinstance(block, WriteBlock) and block.locked_at is not None:
                span = block.span
                span.set_attribute(SpanAttribute.HOLD_MS, (ending_at - block.locked_at) * MILLISECONDS_PER_SECOND)
                # A commit that raised, on a full disk for instance, is timed as well
                if attempted and outcome is BlockOutcome.COMMITTED:
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
