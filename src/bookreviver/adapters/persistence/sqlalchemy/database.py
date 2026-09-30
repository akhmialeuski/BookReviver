"""The async engine, the session factory and the migrations shared by every SQLAlchemy table of the application.

The persistence adapter and the fastapi-users account tables use the same engine, so the application keeps one
connection pool whatever it stores. The engine, the sessions and the Alembic commands all come from one
advanced-alchemy ``SQLAlchemyAsyncConfig``, so the migrations run over the same connection settings as the
application instead of a second configuration in ``env.py``.

The configuration differs from advanced-alchemy's defaults in three places:

- Sessions keep their objects usable after commit, because a route maps them to a response after the unit of work
  committed.
- The listener that touches ``updated_at`` on every flush is off, because the domain sets ``updated_at`` through its
  ``Clock`` and the listener would overwrite it with the wall clock.
- Autogenerate compares column types, so a revision notices a column whose type changed.

SQLite ignores foreign keys unless each connection turns them on, so the engine does that on connect, and the
``ON DELETE CASCADE`` of pages and jobs works the same as on PostgreSQL.
"""

from pathlib import Path
from typing import TYPE_CHECKING

from advanced_alchemy.alembic.commands import AlembicCommands
from advanced_alchemy.base import metadata_registry
from advanced_alchemy.config import AlembicAsyncConfig, AsyncSessionConfig, SQLAlchemyAsyncConfig
from sqlalchemy import event

if TYPE_CHECKING:
    from collections.abc import Callable
    from sqlite3 import Connection

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

SQLITE_DIALECT: str = 'sqlite'
# The revisions of the schema, shipped inside the package next to the tables they change
MIGRATIONS_DIR: Path = Path(__file__).with_name('migrations')


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
            alembic_config=AlembicAsyncConfig(script_location=str(MIGRATIONS_DIR), compare_type=True),
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

    async def create_schema(self) -> None:
        """Create every table registered on the shared metadata that does not exist yet."""
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata_registry.get(None).create_all)

    async def dispose(self) -> None:
        """Close every pooled connection."""
        await self.engine.dispose()
