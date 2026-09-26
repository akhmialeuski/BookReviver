"""The async engine and session factory shared by every SQLAlchemy table of the application."""

from typing import TYPE_CHECKING

from advanced_alchemy.base import metadata_registry
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from sqlite3 import Connection

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

SQLITE_DIALECT: str = 'sqlite'


class SqlDatabase:
    """One engine per application, with sessions that keep objects usable after commit."""

    def __init__(self, url: str) -> None:
        self.engine: AsyncEngine = create_async_engine(url)
        if self.engine.dialect.name == SQLITE_DIALECT:
            event.listen(self.engine.sync_engine, 'connect', self._enable_sqlite_foreign_keys)
        self.sessions: async_sessionmaker[AsyncSession] = async_sessionmaker(self.engine, expire_on_commit=False)

    @staticmethod
    def _enable_sqlite_foreign_keys(dbapi_connection: Connection, _record: object) -> None:
        """Make SQLite enforce foreign keys and their cascades, which it skips by default."""
        dbapi_connection.execute('PRAGMA foreign_keys=ON')

    async def create_schema(self) -> None:
        """Create every table registered on the shared metadata that does not exist yet."""
        async with self.engine.begin() as connection:
            await connection.run_sync(metadata_registry.get(None).create_all)

    async def dispose(self) -> None:
        """Close every pooled connection."""
        await self.engine.dispose()
