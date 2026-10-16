"""Provider of the SQL database, used by the SQLAlchemy persistence backend and by the account tables.

The database is one per application, and the unit of work over it is built per request or job, so that the books and
the accounts share it.

The application never changes the schema. Migrations are applied by hand, after a copy of the data directory if it
matters, and the provider only compares the revision of the database with the head of the migrations the code
ships. A mismatch stops the start with the command that fixes it, because running against another schema would fail
on the first query of a changed column or, worse, write rows the code does not expect. A database behind the code is
upgraded. A database at a revision the code does not ship, migrated on another branch, cannot be: Alembic does not
know that revision, so the message names the downgrade to run with the code that ships it, or the copy to restore.
"""

from collections.abc import AsyncIterator

from dishka import Provider, Scope, provide

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.app.settings import Settings
from bookreviver.ports.persistence import ChangeWaitSeconds
from bookreviver.ports.tracing import Tracer

# The command applying the migrations to the database the settings name, a script of pyproject.toml
MIGRATE_COMMAND: str = 'uv run bookreviver-migrate upgrade head'
# The command returning a database to a revision of this code, run with the code that ships the database's revision
MIGRATE_DOWNGRADE_COMMAND: str = 'uv run bookreviver-migrate downgrade'
# How the message names a database no revision was ever applied to
NO_REVISION: str = 'none'
# What stands between the revisions of a database that is at several of them
REVISION_SEPARATOR: str = ', '


class DatabaseProvider(Provider):
    """One database per application whose schema is at the head revision, and one unit of work per request."""

    @provide(scope=Scope.APP)
    async def database(self, settings: Settings, tracer: Tracer) -> AsyncIterator[SqlDatabase]:
        """Open the configured database, check its schema revision, and close it with the application.

        :param settings: Application settings, of which the database URL, the data directory and the wait limit of a
                         job are read, which every connection opens with as its busy timeout.
        :type settings: Settings
        :param tracer: Tracer the write blocks are timed with.
        :type tracer: Tracer
        :returns: Iterator yielding the open database and disposing of its engine afterwards.
        :rtype: AsyncIterator[SqlDatabase]
        :raises RuntimeError: When the database is not at the head revision of the migrations, naming the upgrade to
            run, or the downgrade when the database is at a revision the code does not ship.
        """
        database = SqlDatabase(
            settings.resolved_database_url, tracer=tracer, wait_seconds=settings.change_wait.job_seconds
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        try:
            revisions = await database.schema_revisions()
            at, expected = (
                REVISION_SEPARATOR.join(revisions.database) or NO_REVISION,
                REVISION_SEPARATOR.join(revisions.code),
            )
            state = f'Database schema is at revision {at}'
            # upgrade head cannot move a database from a revision it cannot locate, so that case names the way back
            if unknown := REVISION_SEPARATOR.join(revisions.unknown):
                problem = f'{state}, which this code does not ship, and code expects {expected}'
                command = f'{MIGRATE_DOWNGRADE_COMMAND} {expected}'
                alternative = (
                    f' with the code that ships {unknown}, or restore the copy of the data directory made before '
                    f'migrating'
                )
            elif not revisions.is_current:
                problem = f'{state}, code expects {expected}'
                command, alternative = MIGRATE_COMMAND, ''
            else:
                yield database
                return
            err_msg = f'{problem}: run `{command}`{alternative}'
            raise RuntimeError(err_msg)
        finally:
            await database.dispose()

    @provide(scope=Scope.REQUEST)
    def unit_of_work(self, database: SqlDatabase, wait_seconds: ChangeWaitSeconds) -> SqlAlchemyUnitOfWork:
        """Build the unit of work of a request or job, which keeps no state but the wait limit of its scope.

        The repositories of the books and the account database both take this one, whatever backend the books are
        stored in. It holds no session or connection, so it needs no closing.

        :param database: The application's database, whose threads and connections the transactions use.
        :type database: SqlDatabase
        :param wait_seconds: How long a change of the unit of work waits for the writer, by the scope it lives in.
        :type wait_seconds: ChangeWaitSeconds
        :returns: Unit of work over the application's database.
        :rtype: SqlAlchemyUnitOfWork
        """
        return SqlAlchemyUnitOfWork(database, wait_seconds=wait_seconds)
