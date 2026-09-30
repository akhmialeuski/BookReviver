"""Provider of the SQL database, used by the SQLAlchemy persistence backend and by the account tables.

The application never changes the schema. Migrations are applied by hand, after a copy of the data directory if it
matters, and the provider only compares the revision of the database with the head of the migrations the code
ships. A mismatch stops the start with the command that fixes it, because running against another schema would fail
on the first query of a changed column or, worse, write rows the code does not expect.
"""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncSession

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.settings import Settings

# The command applying the migrations to the database the settings name, a script of pyproject.toml
MIGRATE_COMMAND: str = 'uv run bookreviver-migrate upgrade head'
# How the message names a database no revision was ever applied to
NO_REVISION: str = 'none'


class DatabaseProvider(Provider):
    """One database per application whose schema is at the head revision, and one session per request."""

    @provide(scope=Scope.APP)
    async def database(self, settings: Settings) -> AsyncIterator[SqlDatabase]:
        """Open the configured database, check its schema revision, and close it with the application.

        :param settings: Application settings, of which the database URL and the data directory are read.
        :type settings: Settings
        :returns: Iterator yielding the open database and disposing of its engine afterwards.
        :rtype: AsyncIterator[SqlDatabase]
        :raises RuntimeError: When the database is not at the head revision of the migrations.
        """
        database = SqlDatabase(settings.resolved_database_url)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            revisions = await database.schema_revisions()
            if not revisions.is_current:
                err_msg = (
                    f'Database schema is at revision {", ".join(revisions.database) or NO_REVISION}, '
                    f'code expects {", ".join(revisions.code)}: run `{MIGRATE_COMMAND}`'
                )
                raise RuntimeError(err_msg)
            yield database
        finally:
            await database.dispose()

    @provide(scope=Scope.REQUEST)
    async def session(self, database: SqlDatabase) -> AsyncIterator[AsyncSession]:
        """Open a session for one request or job, rolled back unless its owner committed.

        :param database: The application's database.
        :type database: SqlDatabase
        :returns: Iterator yielding the session and closing it afterwards.
        :rtype: AsyncIterator[AsyncSession]
        """
        # try/finally rather than `async with` around the yield: dishka drives this generator (ASYNC119)
        session = database.sessions()
        try:
            yield session
        finally:
            await session.close()
