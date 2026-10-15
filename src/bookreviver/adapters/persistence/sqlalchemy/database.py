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
several clients can use one file at once. A block whose limit is shorter than that value, which is a block of a
request, since the settings refuse a request limit that is not shorter than the job limit, sets the busy timeout again
right before it begins, to what is left of its limit after the time it spent in
the queue, so the queue and the lock of SQLite together take no longer than the limit. Any other block, which is a
block of a job, waits for the queue up to its limit and then for the lock up to the value the connection opened with,
so a job may wait its time in the queue plus its whole limit. That is accepted, because the limit of a job is long by
design, and sparing every block of a job a statement keeps the queue short: through the async driver one statement is
several round trips to its thread, each resumed by the event loop, and the block holds the queue all the while. So the
statement is sent only when the connection has another value than the block wants, and a connection remembers the
value it was last given. The busy timeout a block leaves on a pooled connection does not affect a statement outside a
block: in the write-ahead log a read never waits for the write lock, and every write runs inside a block.

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
  is left of the limit of a request's block, or up to the busy timeout the connection opened with for a job's block. A
  connection without the option begins nothing, so a read outside a block runs in SQLite's autocommit mode and holds no
  lock.
- ``commit`` and ``rollback`` emit ``COMMIT`` and ``ROLLBACK`` while the driver connection is in a transaction, because
  the commit and rollback of the driver do nothing in this mode.

The ``write.block`` span of the :class:`~bookreviver.ports.tracing.Tracer` starts when the block joins the queue and
ends when the block gives up its turn, so the span covers the whole time the block occupied the queue. Its wait is the
time in the queue and in ``BEGIN IMMEDIATE`` together, split into the turn, the start of the block up to the ``begin``
listener, which is the resumption after the turn and the connection taken from the pool, and the statements of that
listener. The queue hands the span to the block with the execution option, and ``begin`` keeps the block in
``connection.info`` until the commit or the rollback begins, which ends the holding and, for a commit, times the
``COMMIT`` statement. The time from the end of that statement until the turn is given up, in which the session hands its
connection back to the pool, is the release. A block that never took the lock is ended as not begun.

These durations are measured on the event loop, not inside SQLite. aiosqlite runs a statement in a thread of its own and
wakes the coroutine through the loop, so the clock is read only when the loop reaches that wake-up. A loop that is busy
makes the wait longer by its delay, the holding shorter by the same amount, and the commit longer, and the ``loop.lag``
spans of the probe in ``bookreviver.app.tracing`` show how long that delay was.

The schema is created and changed only by the revisions in ``migrations/``, applied by hand. The application reads
:meth:`SqlDatabase.schema_revisions` at start and refuses to run against a schema it was not written for, which
costs one read of the version table and needs no event loop of Alembic's own.
"""

from contextlib import AsyncExitStack, asynccontextmanager
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
from sqlalchemy.pool import QueuePool

from bookreviver.adapters.persistence.turns import hold_in_turn
from bookreviver.domain.enums import BlockOutcome, SpanAttribute, SpanName
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from sqlite3 import Connection as DriverConnection

    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
    from sqlalchemy.pool import ConnectionPoolEntry

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
# The key of connection.info under which a connection keeps the busy timeout it was last given, in milliseconds
BUSY_TIMEOUT_INFO: str = 'bookreviver_busy_timeout_ms'
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
    """A block of the port, from the moment it got its turn in the write queue until it gives the turn up.

    :ivar span: The ``write.block`` span, started when the block joined the queue.
    :ivar asked_at: Reading of the performance counter when the block joined the queue, which the wait is counted from.
    :ivar granted_at: Reading of the performance counter when the block got its turn, which the start of the block is
                      counted from.
    :ivar wait_seconds: The limit of the unit of work that opened the block, counted from ``asked_at``; the module
                        docstring says what it covers.
    :ivar locked_at: Reading of the performance counter when the block resumed after ``BEGIN IMMEDIATE``, which is
                     when the holding is counted from and which lags the lock actually taken by the delay of the event
                     loop, or None while the block has not taken the lock.
    :ivar ended_at: Reading of the performance counter when the statement that ended the transaction returned, which
                    the release of the turn is counted from, or None while the transaction of the block is open.
    """

    span: StartedSpan
    asked_at: float
    granted_at: float
    wait_seconds: float
    locked_at: float | None = None
    ended_at: float | None = None


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

        The span of the block starts here, before the wait, and ends here once the turn is given up, with the time
        since the transaction ended as the release. When the block ends without ``BEGIN IMMEDIATE`` having taken the
        lock, because its turn did not come in time, SQLite refused the lock or the connection failed, the span ends as
        not begun, with the whole time it waited.

        :param kind: Kind of the block.
        :type kind: BlockKind
        :param book: Project the block holds, or None for a block outside the content of a book.
        :type book: ProjectId | None
        :param wait_seconds: The limit of the unit of work that opened the block, which the turn may take all of; the
                             module docstring says what it covers.
        :type wait_seconds: float
        :returns: Iterator yielding the block once its turn has come, which the block hands to its connection through
                  :data:`CHANGE_BLOCK_OPTION`.
        :rtype: AsyncIterator[WriteBlock]
        :raises BookBusyError: If the turn did not come within the wait limit.
        """
        attributes: dict[SpanAttribute, str] = {SpanAttribute.BLOCK_KIND: kind}
        if book is not None:
            attributes[SpanAttribute.BOOK] = str(book)
        span = self._tracer.start(SpanName.WRITE_BLOCK, attributes)
        asked_at = perf_counter()
        async with AsyncExitStack() as held:
            try:
                await held.enter_async_context(hold_in_turn(self._turn, wait_seconds=wait_seconds))
            except BaseException:
                self._end_not_begun(span, asked_at)
                raise
            block = WriteBlock(span=span, asked_at=asked_at, granted_at=perf_counter(), wait_seconds=wait_seconds)
            span.set_attribute(SpanAttribute.TURN_MS, (block.granted_at - asked_at) * MILLISECONDS_PER_SECOND)
            try:
                yield block
            finally:
                # The turn is given up right after this, by the exit of the stack, which awaits nothing more
                if block.locked_at is None:
                    self._end_not_begun(span, asked_at)
                else:
                    if block.ended_at is not None:
                        released_ms = (perf_counter() - block.ended_at) * MILLISECONDS_PER_SECOND
                        span.set_attribute(SpanAttribute.RELEASE_MS, released_ms)
                    span.end()

    @staticmethod
    def _end_not_begun(span: StartedSpan, asked_at: float) -> None:
        """End the span of a block that never took the lock, with the whole time it waited.

        :param span: The ``write.block`` span of the block.
        :type span: StartedSpan
        :param asked_at: Reading of the performance counter when the block joined the queue.
        :type asked_at: float
        """
        span.set_attribute(SpanAttribute.WAIT_MS, (perf_counter() - asked_at) * MILLISECONDS_PER_SECOND)
        span.set_attribute(SpanAttribute.BLOCK_OUTCOME, BlockOutcome.NOT_BEGUN)
        span.end()


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
                             work may have; the module docstring says how a block with a shorter one changes it.
                             PostgreSQL takes no part of it.
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

    def _configure_sqlite(self, dbapi_connection: DriverConnection, record: ConnectionPoolEntry) -> None:
        """Make SQLite wait for a lock, enforce foreign keys and keep its journal in the write-ahead log.

        A statement that meets the lock of a writer in another process, such as an import committing its pages, waits
        for it up to the wait limit instead of failing with "database is locked" after the few seconds of the default.
        The busy timeout is set first, so that switching the journal waits for such a writer too. The value is kept in
        the ``info`` of the pool record, which SQLAlchemy documents as the dictionary that lives as long as the driver
        connection and that ``connection.info`` returns, so that ``_begin_sqlite`` knows what the connection has.

        :param dbapi_connection: New SQLite connection, before any statement runs on it.
        :type dbapi_connection: DriverConnection
        :param record: Pool record of the connection, whose ``info`` keeps the busy timeout the connection was given.
        :type record: ConnectionPoolEntry
        """
        record.info[BUSY_TIMEOUT_INFO] = self._busy_timeout_ms
        dbapi_connection.execute(BUSY_TIMEOUT_STATEMENT.format(milliseconds=self._busy_timeout_ms))
        dbapi_connection.execute('PRAGMA foreign_keys=ON')
        dbapi_connection.execute('PRAGMA journal_mode=WAL')
        dbapi_connection.execute('PRAGMA synchronous=NORMAL')

    def _begin_sqlite(self, connection: Connection) -> None:
        """Take SQLite's write lock at the start of a transaction that a block of the port opened, and time the wait.

        A connection without the block's execution option begins nothing, so its reads run in SQLite's autocommit mode
        and hold no lock. The wait is counted from the moment the block joined the queue, so it covers the queue and the
        statement. A statement that fails leaves the span to the queue, which ends it as not begun.

        The statement is awaited on the event loop, and aiosqlite wakes the coroutine through it, so the wait is the
        time from the request until the loop resumed the block. It includes the delay of the loop, which the
        ``loop.lag`` spans show, and the holding that begins at the same reading is shortened by it.

        Before the statement, the busy timeout the block wants is sent when the connection has another one, as the
        module docstring describes: what is left of a request's limit, never below zero, so that ``BEGIN IMMEDIATE``
        fails with SQLITE_BUSY at once when nothing is left, or the value the connection opened with for a job.

        The span records the start of the block in two parts: from its turn until this listener runs, which is the
        resumption after the turn and the connection taken from the pool, and the statements here. It records how many
        connections of the pool were checked out at this moment, this one included, when the pool counts them.

        :param connection: Connection that begins a transaction.
        :type connection: Connection
        """
        if not isinstance(block := connection.get_execution_options().get(CHANGE_BLOCK_OPTION), WriteBlock):
            return
        begun_at = perf_counter()
        if isinstance(pool := connection.engine.pool, QueuePool):
            block.span.set_attribute(SpanAttribute.POOL_CHECKED_OUT, pool.checkedout())
        if round(block.wait_seconds * MILLISECONDS_PER_SECOND) < self._busy_timeout_ms:
            remaining_seconds = block.wait_seconds - (perf_counter() - block.asked_at)
            busy_timeout_ms = max(round(remaining_seconds * MILLISECONDS_PER_SECOND), 0)
        else:
            busy_timeout_ms = self._busy_timeout_ms
        if connection.info.get(BUSY_TIMEOUT_INFO) != busy_timeout_ms:
            connection.exec_driver_sql(BUSY_TIMEOUT_STATEMENT.format(milliseconds=busy_timeout_ms))
            connection.info[BUSY_TIMEOUT_INFO] = busy_timeout_ms
        connection.exec_driver_sql('BEGIN IMMEDIATE')
        block.locked_at = perf_counter()
        block.span.set_attribute(SpanAttribute.WAIT_MS, (block.locked_at - block.asked_at) * MILLISECONDS_PER_SECOND)
        block.span.set_attribute(SpanAttribute.CONNECT_MS, (begun_at - block.granted_at) * MILLISECONDS_PER_SECOND)
        block.span.set_attribute(SpanAttribute.BEGIN_MS, (block.locked_at - begun_at) * MILLISECONDS_PER_SECOND)
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
        """Run the statement that ends the transaction, and record on the span of the block how the block ended.

        The holding lasts until the statement begins. The statement of a commit is timed too, as the cost of making the
        writes of the block durable, which a rollback has none of. Both durations are read on the event loop, so they
        include its delay, as the wait of ``_begin_sqlite`` does. A block whose statement did not run, because the
        connection was invalidated, is recorded as rolled back. A commit that failed keeps the time it took and is
        recorded as rolled back, since the unit of work rolls it back next. The span is ended by the queue, once the
        block gives up its turn, so the end of the statement is kept on the block for the release.

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
                block.ended_at = perf_counter()
                span = block.span
                span.set_attribute(SpanAttribute.HOLD_MS, (ending_at - block.locked_at) * MILLISECONDS_PER_SECOND)
                # A commit that raised, on a full disk for instance, is timed as well
                if attempted and outcome is BlockOutcome.COMMITTED:
                    span.set_attribute(SpanAttribute.COMMIT_MS, (block.ended_at - ending_at) * MILLISECONDS_PER_SECOND)
                span.set_attribute(SpanAttribute.BLOCK_OUTCOME, ended_as)

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
