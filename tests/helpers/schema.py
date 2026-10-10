"""Creating the schema a migrated database has, without running the migrations.

The application refuses a database that is not at the head revision, so every test that opens the SQL database
first creates its tables from the models and records the head revision, as ``alembic stamp head`` does. That takes a
fraction of running the migrations for each test, and ``test_migrations.py`` proves once that the migrations build
the same schema as the models.
"""

from typing import TYPE_CHECKING

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.tables import ProjectRow

if TYPE_CHECKING:
    from sqlalchemy import Connection

    from bookreviver.app.settings import Settings


async def create_schema(settings: Settings) -> None:
    """Create every table of the models in the database the settings name, and record it at the head revision.

    :param settings: Settings naming the database and the data directory holding it.
    :type settings: Settings
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = SqlDatabase(settings.resolved_database_url, wait_seconds=settings.change_wait_seconds)
    script = ScriptDirectory.from_config(database.migrations.config)
    options = {'version_table': database.config.alembic_config.version_table_name}

    def create_and_stamp(connection: Connection) -> None:
        """Create the tables on ``connection`` and write the head revision into its version table.

        :param connection: Connection in the transaction that creates the schema.
        :type connection: Connection
        """
        ProjectRow.metadata.create_all(connection)
        MigrationContext.configure(connection, opts=options).stamp(script, 'head')

    try:
        async with database.engine.begin() as connection:
            await connection.run_sync(create_and_stamp)
    finally:
        await database.dispose()
