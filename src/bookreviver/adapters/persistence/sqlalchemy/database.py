"""The SQLite engines of one database file, their threads and its migrations.

Every database of the application is SQLite, a file that only the API process writes: the catalogue, one for the
application, and the file of each book. :class:`Schema` names which of the two a file holds, which picks the metadata of
its tables and the directory of its revisions, and an instance of :class:`SqlDatabase` serves one file.

Every transaction runs whole in a worker thread, from its first statement to its commit, on the synchronous engine of
the standard ``sqlite3`` driver. A transaction that the
event loop drove statement by statement would go to a thread and back through the loop for every round trip of every
statement, and a write holding the only writer all that time makes the other writes wait for the loop. Here the loop
hands a function to a thread and gets its result when the transaction has ended, so the writer is held for the work and
the commit and for nothing else. The engines, the sessions and the Alembic commands come from advanced-alchemy's
``SQLAlchemySyncConfig``, so the migrations run over the same connection settings as the application instead of a
second configuration in ``env.py``. The persistence adapter and the fastapi-users account tables use these engines, so
the application opens no other connection to the file.

The database has two engines over the same file, each with a pool of its own, and not one engine with two pools. A pool
belongs to an engine in SQLAlchemy, so a second pool is a second engine anyway, and two engines are the simpler
description of two kinds of transaction: the engine of the writer begins its transactions with ``BEGIN IMMEDIATE`` and
the engine of the readers with a deferred ``BEGIN``, which is a statement of each engine's ``begin`` listener and needs
no option passed down to the transaction.

- The writer pool holds exactly one connection (``pool_size=1, max_overflow=0``), because SQLite has one writer at a
  time. A transaction that writes takes it as :meth:`SqlDatabase.write` runs it, and the connection is used by one
  worker thread at a time but by different threads over its life, so it is opened with ``check_same_thread=False``.
- The reader pool holds :data:`DEFAULT_READERS` connections, which is also how many reads run at once. In the
  write-ahead log a read never waits for the writer and the writer never waits for a read, so a read sees the database
  as committed when its first statement ran, for all the statements of its transaction, while a write commits.

Both engines connect to SQLite with ``isolation_level = None``, which makes the driver begin nothing by itself, the
recipe of the SQLAlchemy documentation for SQLite transactions. The ``begin`` listener of each engine emits the
``BEGIN`` of its kind, and the driver still ends a transaction with ``COMMIT`` or ``ROLLBACK`` when the session asks.
On connect each connection turns on foreign keys, so the ``ON DELETE CASCADE`` of the rows of a book works, puts the
database in the write-ahead log mode with ``synchronous=NORMAL``, and gets a busy timeout, the longest wait limit of a
unit of work, through the ``timeout`` argument of the driver. The busy timeout matters only to a lock held by another
process, such as a migration run by hand, because within the process the write queue is the only contention. The
write-ahead log appends a commit to the log without the ``fsync`` of the database file, which SQLite documents as the
setting for it. A power loss may lose the last commits but never corrupts the file. The mode is stored in the file, so
the first connection switches it and the others find it set. It needs every process that opens the file to share its
memory, which holds for one machine and not for a network file system. ``migrations/env.py`` turns the foreign keys
off for the length of a migration.

The write queue is the ``anyio.Semaphore`` of one that :meth:`SqlDatabase.write` takes before it starts the thread, one
for each file, so a write of one book never waits for a write of another book or of the catalogue.
anyio wakes the waiters of a semaphore in the order they asked, which :func:`hold_in_turn` explains, so a write that
came early is never overtaken by one that came later, and the busy timeout of SQLite, whose waiting connection sleeps
and tries again, is never the queue. The wait
limit of the unit of work bounds only the wait for the queue and never the work, and a running work is never
cancelled, since ``to_thread.run_sync`` shields it, so a transaction is not abandoned half way. The readers share a
capacity limiter of their own that is as large as the reader pool, which a read waits for without a limit.

A write that reaches SQLite's write lock while another process holds it waits for the busy timeout, and then fails with
``BookBusyError``, as one that did not get the queue in time does.

The ``write.block`` span of the :class:`~bookreviver.ports.tracing.Tracer` starts on the event loop when a write asks
for the queue, so its parent is the span current where the caller asked, and ends when the work has ended. It records:

- ``wait.ms``, from the request until the queue gave the writer, measured on the event loop;
- ``hold.ms``, from the ``BEGIN IMMEDIATE`` until the commit began, and ``commit.ms``, the commit, both measured with
  ``time.perf_counter()`` inside the worker thread, where no delay of the loop reaches them;
- ``block.outcome``: committed, rolled back, or not begun when the write was refused the queue or the lock.

The configuration differs from advanced-alchemy's defaults in two places: sessions keep their objects usable after
commit, because a route maps them to a response after the unit of work committed, and the listener that touches
``updated_at`` on every flush is off, because the domain sets ``updated_at`` through its ``Clock`` and the listener
would overwrite it with the wall clock. The options of autogenerate, such as comparing column types, are set in
``migrations/env.py``, because advanced-alchemy 1.11.0 does not hand those of ``AlembicSyncConfig`` to Alembic.

The schema of each kind of file is created and changed only by the revisions in its directory under ``migrations/``.
Those of the catalogue are applied by hand, and the application reads :meth:`SqlDatabase.schema_revisions` at start
and refuses to run against a catalogue it was not written for, which costs one read of the version table. A book is
brought to the head revision of its schema when it is opened, by
:class:`~bookreviver.adapters.persistence.sqlalchemy.books.BookDatabases`.
"""

import enum
from contextlib import AsyncExitStack, asynccontextmanager
from functools import partial
from pathlib import Path
from sqlite3 import OperationalError as SqliteOperationalError
from time import perf_counter
from typing import TYPE_CHECKING

import anyio
from advanced_alchemy.alembic.commands import AlembicCommands
from advanced_alchemy.config import AlembicSyncConfig, EngineConfig, SQLAlchemySyncConfig, SyncSessionConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from anyio import to_thread
from attrs import define, field, frozen
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.tables import BookTable
from bookreviver.domain.enums import BlockOutcome, SpanAttribute, SpanName
from bookreviver.domain.errors import BookBusyError
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS, NoChangeOpenError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from sqlite3 import Connection as DriverConnection

    from sqlalchemy import MetaData
    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.orm import ORMExecuteState, Session
    from sqlalchemy.pool import ConnectionPoolEntry

    from bookreviver.domain.enums import BlockKind
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.tracing import StartedSpan, Tracer

SQLITE_DIALECT: str = 'sqlite'
# The driver name of the synchronous engines, the standard library's sqlite3 module
SQLITE_DRIVER: str = 'sqlite'
# The name SQLite gives the error of a statement that waited the busy timeout and then gave up
SQLITE_BUSY: str = 'SQLITE_BUSY'
# The statements that begin a transaction, which the driver is told not to begin by itself
BEGIN_WRITE: str = 'BEGIN IMMEDIATE'
BEGIN_READ: str = 'BEGIN'
# How many reads of one database run at once, which is the size of the pool of its readers
DEFAULT_READERS: int = 4
MILLISECONDS_PER_SECOND: int = 1_000
# The revisions of the schemas, shipped inside the package next to the tables they change, a directory for each schema
MIGRATIONS_DIR: Path = Path(__file__).with_name('migrations')


class Schema(enum.StrEnum):
    """The kind of database a file holds, which decides the tables in it and the revisions that build them."""

    CATALOGUE = 'catalogue'
    BOOK = 'book'

    @property
    def bind_key(self) -> str | None:
        """The bind key under which advanced-alchemy keeps the metadata of the tables, None for its default metadata."""
        return BookTable.__bind_key__ if self is Schema.BOOK else None

    @property
    def metadata(self) -> MetaData:
        """The metadata of the tables of the schema, which the revisions build and autogenerate compares with."""
        return (BookTable if self is Schema.BOOK else LibraryRow).metadata

    @property
    def migrations(self) -> Path:
        """The directory of the revisions of the schema, whose ``env.py`` Alembic runs."""
        return MIGRATIONS_DIR / self.value


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
    """One write block, with what was measured of it, which goes on its span when the block has ended.

    :ivar span: The ``write.block`` span, started when the block asked for the writer.
    :ivar asked_at: Reading of the performance counter when the block asked for the writer.
    :ivar outcome: How the block ended, not begun until the transaction has begun.
    :ivar waited_seconds: Seconds from the request until the queue gave the writer, measured on the event loop, or None
                          while the block has not had it.
    :ivar hold_seconds: Seconds from ``BEGIN IMMEDIATE`` until the commit, or the rollback of a failed work, began,
                        measured in the writer thread, or None while the block has not begun.
    :ivar commit_seconds: Seconds the commit took, measured in the writer thread, or None for a block that did not
                          commit.
    """

    span: StartedSpan
    asked_at: float = field(factory=perf_counter)
    outcome: BlockOutcome = BlockOutcome.NOT_BEGUN
    waited_seconds: float | None = None
    hold_seconds: float | None = None
    commit_seconds: float | None = None

    def had_the_writer(self) -> None:
        """Record that the queue has given the writer to the block."""
        self.waited_seconds = perf_counter() - self.asked_at

    def end(self) -> None:
        """Put the measurements on the span and end it, once the block has ended in whichever way.

        A block that never had the writer, because the queue refused it or its task was cancelled, waited until now.
        """
        waited = perf_counter() - self.asked_at if self.waited_seconds is None else self.waited_seconds
        self.span.set_attribute(SpanAttribute.WAIT_MS, waited * MILLISECONDS_PER_SECOND)
        self.span.set_attribute(SpanAttribute.BLOCK_OUTCOME, self.outcome)
        if self.hold_seconds is not None:
            self.span.set_attribute(SpanAttribute.HOLD_MS, self.hold_seconds * MILLISECONDS_PER_SECOND)
        if self.commit_seconds is not None:
            self.span.set_attribute(SpanAttribute.COMMIT_MS, self.commit_seconds * MILLISECONDS_PER_SECOND)
        self.span.end()


def _sqlite_config(url: str, *, schema: Schema, connections: int, busy_seconds: float) -> SQLAlchemySyncConfig:
    """Configure one engine of the database with a pool of the given size.

    :param url: URL of the database with the driver of the synchronous engine.
    :type url: str
    :param schema: The kind of database the file holds, whose metadata and revisions the configuration carries.
    :type schema: Schema
    :param connections: The size of the pool, which is also the most connections the engine ever opens.
    :type connections: int
    :param busy_seconds: How long a statement waits for a lock that another process holds, which sqlite3 takes as its
                         ``timeout``.
    :type busy_seconds: float
    :returns: The configuration the engine, the sessions and the migrations are made from.
    :rtype: SQLAlchemySyncConfig
    """
    return SQLAlchemySyncConfig(
        connection_string=url,
        # A connection is used by one thread at a time, but a pool hands it to different threads over its life
        engine_config=EngineConfig(
            connect_args={'check_same_thread': False, 'timeout': busy_seconds}, pool_size=connections, max_overflow=0
        ),
        session_config=SyncSessionConfig(expire_on_commit=False),
        enable_touch_updated_timestamp_listener=False,
        bind_key=schema.bind_key,
        alembic_config=AlembicSyncConfig(script_location=str(schema.migrations)),
    )


def _configure_connection(dbapi_connection: DriverConnection, _record: ConnectionPoolEntry) -> None:
    """Take transaction control from the driver, enforce foreign keys and keep the journal in the write-ahead log.

    :param dbapi_connection: New SQLite connection, before any statement runs on it.
    :type dbapi_connection: DriverConnection
    :param _record: Pool record of the connection, required by the event signature and unused.
    :type _record: ConnectionPoolEntry
    """
    # The driver then begins no transaction, which the begin listener of the engine does instead
    dbapi_connection.isolation_level = None
    dbapi_connection.execute('PRAGMA foreign_keys=ON')
    dbapi_connection.execute('PRAGMA journal_mode=WAL')
    dbapi_connection.execute('PRAGMA synchronous=NORMAL')


def _refuse_flush(session: Session, _context: object, _instances: object) -> None:
    """Refuse a flush of a session of a read, which has a row to write.

    :param session: Session about to flush.
    :type session: Session
    :param _context: Flush context, required by the event signature and unused.
    :type _context: object
    :param _instances: Objects the flush was asked for, required by the event signature and unused.
    :type _instances: object
    :raises NoChangeOpenError: If a row is to be inserted, updated or deleted.
    """
    if session.new or session.deleted or any(session.is_modified(row) for row in session.dirty):
        raise NoChangeOpenError


def _refuse_statement(state: ORMExecuteState) -> None:
    """Refuse a bulk insert, update or delete of a session of a read.

    :param state: The statement the session is about to execute.
    :type state: ORMExecuteState
    :raises NoChangeOpenError: If the statement writes.
    """
    if state.is_insert or state.is_update or state.is_delete:
        raise NoChangeOpenError


@asynccontextmanager
async def hold_in_turn(lock: anyio.Semaphore, *, wait_seconds: float) -> AsyncIterator[None]:
    """Wait for a single-holder lock in the order asked, and hold it until the block ends.

    ``anyio.Semaphore`` wakes its waiters first in, first out, because it keeps them in a deque, which anyio 4.15 shows
    in ``anyio/_backends/_asyncio.py``. An ``anyio.Lock`` is no choice here: a lock belongs to the task that took it and
    refuses that task a second time at once, while the holder of the semaphore is the unit of work, as a database holds
    its lock for a connection. Only the wait is limited, never the holding.

    :param lock: Semaphore of one token, which the block takes.
    :type lock: anyio.Semaphore
    :param wait_seconds: How long the block waits for the lock before it gives up.
    :type wait_seconds: float
    :returns: Iterator yielding once the lock is held, and releasing it when the block ends.
    :rtype: AsyncIterator[None]
    :raises BookBusyError: If the lock was not given within the wait limit.
    """
    async with AsyncExitStack() as held:
        try:
            with anyio.fail_after(wait_seconds):
                await held.enter_async_context(lock)
        except TimeoutError as error:
            raise BookBusyError from error
        yield


class SqlDatabase:
    """The engines of the database, the threads its transactions run in, and its migrations.

    :ivar config: advanced-alchemy configuration of the writer, from which its engine, its sessions and the migrations
                  are built.
    :ivar engine: Engine of the writer, whose pool holds one connection.
    :ivar read_engine: Engine of the readers.
    :ivar schema: The kind of database the file holds.
    :ivar migrations: Alembic commands over the engine of the writer and the revisions of the schema.
    """

    def __init__(
        self,
        url: str,
        *,
        schema: Schema,
        tracer: Tracer,
        wait_seconds: float = DEFAULT_CHANGE_WAIT_SECONDS,
        readers: int = DEFAULT_READERS,
    ) -> None:
        """Configure the engines for ``url``, which must name SQLite.

        :param url: SQLAlchemy database URL of an SQLite file, such as ``sqlite:///data/bookreviver.db``. A URL that
                    names another SQLite driver, such as ``sqlite+aiosqlite``, is given the driver of the synchronous
                    engine.
        :type url: str
        :param schema: The kind of database the file holds.
        :type schema: Schema
        :param tracer: Tracer the write blocks are timed with.
        :type tracer: Tracer
        :param wait_seconds: The busy timeout every connection gets when it opens, which is the longest limit a unit of
                             work may have; the module docstring says what it is for.
        :type wait_seconds: float
        :param readers: How many reads run at once.
        :type readers: int
        :raises ValueError: If the URL names a database other than SQLite.
        """
        sqlite_url = make_url(url)
        if (backend := sqlite_url.get_backend_name()) != SQLITE_DIALECT:
            err_msg = f'BookReviver stores its data in SQLite, and the database URL names {backend}.'
            raise ValueError(err_msg)
        sync_url = sqlite_url.set(drivername=SQLITE_DRIVER).render_as_string(hide_password=False)
        self.schema = schema
        self._tracer = tracer
        self._write_queue = anyio.Semaphore(1)
        # A thread limiter of its own, so a write never waits behind other to_thread calls in anyio's shared default
        # limiter; the queue admits one write at a time, so this limiter itself never makes a write wait
        self._write_threads = anyio.CapacityLimiter(1)
        self._read_threads = anyio.CapacityLimiter(readers)
        self.config = _sqlite_config(sync_url, schema=schema, connections=1, busy_seconds=wait_seconds)
        self.engine: Engine = self.config.get_engine()
        read_config = _sqlite_config(sync_url, schema=schema, connections=readers, busy_seconds=wait_seconds)
        self.read_engine: Engine = read_config.get_engine()
        for engine in (self.engine, self.read_engine):
            event.listen(engine, 'connect', _configure_connection)
            event.listen(engine, 'begin', self._begin)
        self._write_sessions: Callable[[], Session] = self.config.create_session_maker()
        self._read_sessions: Callable[[], Session] = read_config.create_session_maker()
        event.listen(self._read_sessions, 'before_flush', _refuse_flush)
        event.listen(self._read_sessions, 'do_orm_execute', _refuse_statement)
        self.migrations = AlembicCommands(self.config)

    def _begin(self, connection: Connection) -> None:
        """Begin a transaction of SQLite, which takes the write lock at once on the engine of the writer.

        :param connection: Connection that begins a transaction.
        :type connection: Connection
        """
        connection.exec_driver_sql(BEGIN_WRITE if connection.engine is self.engine else BEGIN_READ)

    async def read[T](self, work: Callable[[Session], T]) -> T:
        """Run ``work`` in a worker thread on a session of a transaction that only reads, and return its result.

        Every statement of the work sees the database as committed when its first statement ran. The transaction ends
        when the work returns or raises, and the session is closed, which gives its connection back to the pool of the
        readers. A write is refused with ``NoChangeOpenError``. The call waits for a free reader connection without a
        limit.

        :param work: Synchronous function over the session; it must not use the session after it returns.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        :raises NoChangeOpenError: If the work writes.
        """
        return await to_thread.run_sync(partial(self._run_read, work), limiter=self._read_threads)

    def _run_read[T](self, work: Callable[[Session], T]) -> T:
        """Run the work of a read in the worker thread, which ends the transaction by closing the session.

        :param work: Synchronous function over the session.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        """
        with self._read_sessions() as session:
            return work(session)

    async def write[T](
        self, work: Callable[[Session], T], *, kind: BlockKind, book: BookId | None, wait_seconds: float
    ) -> T:
        """Run ``work`` in a worker thread on a session of a transaction that writes, and commit it when it returns.

        The call waits for the write queue, in the order the writes asked and for at most ``wait_seconds``, and then
        runs the work from ``BEGIN IMMEDIATE`` to the commit without leaving the thread. When the work raises, the
        transaction rolls back and the exception goes on. The wait limit bounds the wait for the queue and for SQLite's
        write lock, never the work, and the work is not cancelled with the task that awaits it.

        :param work: Synchronous function over the session; it must not use the session after it returns.
        :type work: Callable[[Session], T]
        :param kind: Kind of the block, which its span carries.
        :type kind: BlockKind
        :param book: Book the block holds, or None for a block outside the content of a book.
        :type book: BookId | None
        :param wait_seconds: How long the write waits for the queue before it gives up.
        :type wait_seconds: float
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If the queue or the write lock of SQLite was not free within the wait limit.
        """
        attributes: dict[SpanAttribute, str] = {SpanAttribute.BLOCK_KIND: kind}
        if book is not None:
            attributes[SpanAttribute.BOOK] = str(book)
        block = WriteBlock(span=self._tracer.start(SpanName.WRITE_BLOCK, attributes))
        try:
            async with hold_in_turn(self._write_queue, wait_seconds=wait_seconds):
                block.had_the_writer()
                return await to_thread.run_sync(partial(self._run_write, work, block), limiter=self._write_threads)
        finally:
            block.end()

    def _run_write[T](self, work: Callable[[Session], T], block: WriteBlock) -> T:
        """Run the work of a write in the worker thread, between ``BEGIN IMMEDIATE`` and the commit.

        A work that raises is rolled back by closing the session, and the exception goes on. The durations are read
        here, in the thread, so the loop cannot delay them.

        :param work: Synchronous function over the session.
        :type work: Callable[[Session], T]
        :param block: The block, where the outcome and the durations are put for the event loop to read.
        :type block: WriteBlock
        :returns: What the work returns.
        :rtype: T
        :raises BookBusyError: If SQLite's write lock was not free within the busy timeout.
        """
        with self._write_sessions() as session:
            try:
                # The first use of the session begins its transaction, and with it takes the write lock
                session.connection()
            except OperationalError as error:
                if isinstance(error.orig, SqliteOperationalError) and error.orig.sqlite_errorname == SQLITE_BUSY:
                    raise BookBusyError from error
                raise
            block.outcome = BlockOutcome.ROLLED_BACK
            begun_at = perf_counter()
            try:
                result = work(session)
            finally:
                committing_at = perf_counter()
                block.hold_seconds = committing_at - begun_at
            try:
                session.commit()
            finally:
                # A commit that raised, on a full disk for instance, is timed as well, and the block stays rolled back
                block.commit_seconds = perf_counter() - committing_at
            block.outcome = BlockOutcome.COMMITTED
            return result

    async def schema_revisions(self) -> SchemaRevisions:
        """Read the revisions the database records and the head revisions of the migrations directory.

        Only the version table is read, through Alembic's ``MigrationContext`` on a connection of the readers, so no
        migration environment runs.

        :returns: The revisions of the database and of the code.
        :rtype: SchemaRevisions
        """
        script = ScriptDirectory.from_config(self.migrations.config)
        # Walking the revisions reads every revision file once; the heads then come from the loaded map
        shipped = frozenset(
            await to_thread.run_sync(lambda: [revision.revision for revision in script.walk_revisions()])
        )
        return SchemaRevisions(database=await self.current_revisions(), code=tuple(script.get_heads()), shipped=shipped)

    async def current_revisions(self) -> tuple[str, ...]:
        """Read the revisions the version table of the file records, in one read and with no migration environment.

        :returns: The revisions of the file, none for a file no revision has touched.
        :rtype: tuple[str, ...]
        """
        options = {'version_table': self.config.alembic_config.version_table_name}
        return await self.read(
            lambda session: MigrationContext.configure(session.connection(), opts=options).get_current_heads()
        )

    async def dispose(self) -> None:
        """Close every pooled connection of both engines."""
        await to_thread.run_sync(self.engine.dispose)
        await to_thread.run_sync(self.read_engine.dispose)
