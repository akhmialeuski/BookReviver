"""The SQLite database of a test: a schema built once per session, copied for every test, and the types that open it.

The application refuses a database that is not at the head revision, so a test cannot start from an empty file.
Creating the tables from the models and recording the head revision, as ``alembic stamp head`` does, takes about a
second, and copying a file that already holds them takes a couple of milliseconds. So ``create_schema_template`` builds
that file once for each test process, ``copy_schema_template`` puts a copy where the settings of a test name their
database, and ``test_migrations.py`` proves once that the migrations build the same schema as the models.
"""

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import anyio
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from dishka import Provider, Scope, provide
from sqlalchemy.engine import make_url

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.tables import ProjectRow
from bookreviver.adapters.tracing.null import NullTracer

if TYPE_CHECKING:
    from bookreviver.app.settings import Settings
    from bookreviver.ports.persistence import UnitOfWork

NO_DATABASE_FILE: str = (
    'The settings name a database without a file, so there is nowhere to copy the schema template to.'
)


class UnitOfWorkFactory(Protocol):
    """A function opening a new unit of work over the database of the test."""

    def __call__(self, *, wait_seconds: float | None = None) -> UnitOfWork:
        """Open a unit of work over the database of the test.

        :param wait_seconds: How long a change of the unit of work waits for its turn before it gives up with
                             ``BookBusyError``, which a request gets shorter than a job. None for the limit of a job
                             in the settings of the test.
        :type wait_seconds: float | None
        :returns: A new unit of work.
        :rtype: UnitOfWork
        """


class SharedDatabaseProvider(Provider):
    """Hands the application the database the test opened, so both have one write queue and one pool.

    The application would open a second database over the same file, with a write queue of its own, and a change of
    the test and a change of the application would meet at the lock of SQLite instead of in one queue. The test owns
    the database, so the container neither opens nor disposes of it.
    """

    scope = Scope.APP

    def __init__(self, database: SqlDatabase) -> None:
        """Provide ``database`` in place of the one the application would open.

        :param database: Database of the test.
        :type database: SqlDatabase
        """
        super().__init__()
        self._database = database

    @provide(override=True)
    def database(self) -> SqlDatabase:
        """Return the database of the test.

        :returns: The database every request and job of the application runs on.
        :rtype: SqlDatabase
        """
        return self._database


def create_schema_template(path: Path) -> None:
    """Create every table of the models in a new SQLite file, and record the file at the head revision.

    The template is built once per test process, before any event loop runs, so both engines are disposed of here
    directly. ``SqlDatabase.dispose`` is the same disposal moved to a worker thread to keep a running loop free.

    :param path: Where the file is created, which must not exist yet.
    :type path: Path
    """
    database = SqlDatabase(f'sqlite:///{path}', tracer=NullTracer())
    script = ScriptDirectory.from_config(database.migrations.config)
    options = {'version_table': database.config.alembic_config.version_table_name}
    try:
        with database.engine.begin() as connection:
            ProjectRow.metadata.create_all(connection)
            MigrationContext.configure(connection, opts=options).stamp(script, 'head')
    finally:
        database.engine.dispose()
        database.read_engine.dispose()


def database_file(settings: Settings) -> Path:
    """Name the SQLite file the settings of a test point the application at.

    :param settings: Settings naming the database.
    :type settings: Settings
    :returns: The path of the file, which may not exist yet.
    :rtype: Path
    :raises ValueError: If the settings name a database without a file.
    """
    if (file_name := make_url(settings.resolved_database_url).database) is None:
        raise ValueError(NO_DATABASE_FILE)
    return Path(file_name)


async def copy_schema_template(template: Path, target: Path) -> None:
    """Copy the schema template to the place a test opens its database at, and create the directory that holds it.

    :param template: File ``create_schema_template`` made.
    :type template: Path
    :param target: Where the database of the test is to be, such as ``database_file(fx_settings)``.
    :type target: Path
    """
    await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
    await anyio.Path(template).copy(target)
