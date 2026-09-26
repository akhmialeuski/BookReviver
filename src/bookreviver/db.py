"""Async database engine and schema migrations."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import anyio.to_thread
from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from sqlite3 import Connection

    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

MIGRATIONS_DIR: Path = Path(__file__).parent / 'migrations'
HEAD_REVISION: str = 'head'


def create_engine(*, database_url: str) -> AsyncEngine:
    """Create the engine and make SQLite enforce foreign keys on every connection."""
    engine = create_async_engine(database_url)

    @event.listens_for(engine.sync_engine, 'connect')
    def _enable_foreign_keys(dbapi_connection: Connection, _record: object) -> None:
        dbapi_connection.execute('PRAGMA foreign_keys=ON')

    return engine


def migration_config(*, database_url: str) -> Config:
    """Build an Alembic configuration pointing at the packaged migrations."""
    config = Config()
    config.set_main_option('script_location', str(MIGRATIONS_DIR))
    config.set_main_option('sqlalchemy.url', database_url)
    return config


async def upgrade_schema(*, database_url: str) -> None:
    """Apply all pending migrations; Alembic runs its own event loop, so it gets a worker thread."""
    await anyio.to_thread.run_sync(command.upgrade, migration_config(database_url=database_url), HEAD_REVISION)


@asynccontextmanager
async def database(*, database_url: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Migrate the schema, then yield a session factory bound to a live engine."""
    await upgrade_schema(database_url=database_url)
    engine = create_engine(database_url=database_url)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()
