"""Alembic environment running migrations over the async SQLite engine."""

import asyncio
from typing import TYPE_CHECKING

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from bookreviver.config import Settings
from bookreviver.models import Base

if TYPE_CHECKING:
    from sqlalchemy.engine import Connection


def _run_migrations(connection: Connection) -> None:
    """Configure the context on a sync connection and run the migrations in one transaction."""
    # Batch mode lets autogenerate emit ALTER TABLE through table copies, which SQLite requires
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_online() -> None:
    """Connect with the configured URL, falling back to the application settings."""
    url = context.config.get_main_option('sqlalchemy.url') or Settings().database_url
    engine = create_async_engine(url)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


asyncio.run(_run_online())
