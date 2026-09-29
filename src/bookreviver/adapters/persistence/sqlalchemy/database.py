"""The async engine and session factory shared by every SQLAlchemy table of the application.

The persistence adapter and the fastapi-users account tables use the same engine, so the application keeps one
connection pool whatever it stores. Sessions keep their objects usable after commit, because a route maps them to a
response after the unit of work committed. SQLite ignores foreign keys unless each connection turns them on, so the
engine does that on connect, and the ``ON DELETE CASCADE`` of pages and jobs works the same as on PostgreSQL.
"""

from typing import TYPE_CHECKING

from advanced_alchemy.base import metadata_registry
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from sqlite3 import Connection

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

SQLITE_DIALECT: str = 'sqlite'


class SqlDatabase:
    """One engine per application, with sessions that keep objects usable after commit.

    :ivar engine: Async engine with the application's connection pool.
    :ivar sessions: Factory of sessions bound to the engine, which keep objects loaded after commit.
    """

    def __init__(self, url: str) -> None:
        """Create the engine for ``url`` and, on SQLite, turn foreign keys on for every connection.

        :param url: SQLAlchemy async database URL, such as ``sqlite+aiosqlite:///data/bookreviver.db``.
        :type url: str
        """
        self.engine: AsyncEngine = create_async_engine(url)
        if self.engine.dialect.name == SQLITE_DIALECT:
            event.listen(self.engine.sync_engine, 'connect', self._enable_sqlite_foreign_keys)
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(self.engine, expire_on_commit=False)

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
