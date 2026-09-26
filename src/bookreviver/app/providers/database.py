"""Provider of the SQL database, used by the SQLAlchemy persistence backend and by the account tables."""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.settings import Settings


class DatabaseProvider(Provider):
    """One database per application with its schema created, and one session per request."""

    @provide(scope=Scope.APP)
    async def database(self, settings: Settings) -> AsyncIterator[SqlDatabase]:
        """Open the configured database, create missing tables, and close it with the application."""
        database = SqlDatabase(settings.resolved_database_url)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        await database.create_schema()
        yield database
        await database.dispose()

    @provide(scope=Scope.REQUEST)
    async def session(self, database: SqlDatabase) -> AsyncIterator[AsyncSession]:
        """Open a session for one request or job, rolled back unless its owner committed."""
        # try/finally rather than `async with` around the yield: dishka drives this generator (ASYNC119)
        session = database.sessions()
        try:
            yield session
        finally:
            await session.close()
