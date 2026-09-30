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
for the length of a migration.

The schema is created and changed only by the revisions in ``migrations/``, applied by hand. The application reads
:meth:`SqlDatabase.schema_revisions` at start and refuses to run against a schema it was not written for, which
costs one read of the version table and needs no event loop of Alembic's own.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from advanced_alchemy.alembic.commands import AlembicCommands
from advanced_alchemy.config import AlembicAsyncConfig, AsyncSessionConfig, SQLAlchemyAsyncConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from asyncer import asyncify
from attrs import frozen
from sqlalchemy import event

if TYPE_CHECKING:
    from collections.abc import Callable
    from sqlite3 import Connection

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

SQLITE_DIALECT: str = 'sqlite'
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

    def __init__(self, url: str) -> None:
        """Configure the engine for ``url`` and, on SQLite, turn foreign keys on for every connection.

        :param url: SQLAlchemy async database URL, such as ``sqlite+aiosqlite:///data/bookreviver.db``.
        :type url: str
        """
        self.config = SQLAlchemyAsyncConfig(
            connection_string=url,
            session_config=AsyncSessionConfig(expire_on_commit=False),
            enable_touch_updated_timestamp_listener=False,
            alembic_config=AlembicAsyncConfig(script_location=str(MIGRATIONS_DIR)),
        )
        self.engine: AsyncEngine = self.config.get_engine()
        if self.engine.dialect.name == SQLITE_DIALECT:
            event.listen(self.engine.sync_engine, 'connect', self._enable_sqlite_foreign_keys)
        self.sessions: Callable[[], AsyncSession] = self.config.create_session_maker()
        self.migrations = AlembicCommands(self.config)

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: Connection, _record: object) -> None:
        """Make SQLite enforce foreign keys and their cascades, which it skips by default.

        :param dbapi_connection: New SQLite connection, before any statement runs on it.
        :type dbapi_connection: Connection
        :param _record: Pool record of the connection, required by the event signature and unused.
        :type _record: object
        """
        dbapi_connection.execute('PRAGMA foreign_keys=ON')

    async def schema_revisions(self) -> SchemaRevisions:
        """Read the revisions the database records and the head revisions of the migrations directory.

        Only the version table is read, through Alembic's ``MigrationContext`` on a connection of the running event
        loop, so no migration environment runs.

        :returns: The revisions of the database and of the code.
        :rtype: SchemaRevisions
        """
        script = ScriptDirectory.from_config(self.migrations.config)
        # Walking the revisions reads every revision file once; the heads then come from the loaded map
        shipped = frozenset(await asyncify(lambda: [revision.revision for revision in script.walk_revisions()])())
        options = {'version_table': self.config.alembic_config.version_table_name}
        async with self.engine.connect() as connection:
            database = await connection.run_sync(
                lambda sync_connection: MigrationContext.configure(sync_connection, opts=options).get_current_heads()
            )
        return SchemaRevisions(database=database, code=tuple(script.get_heads()), shipped=shipped)

    async def dispose(self) -> None:
        """Close every pooled connection."""
        await self.engine.dispose()
