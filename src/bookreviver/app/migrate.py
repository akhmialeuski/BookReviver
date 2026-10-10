"""The ``bookreviver-migrate`` command: advanced-alchemy's migration commands on the database of the settings.

advanced-alchemy's own ``alchemy`` command requires ``--config`` with the dotted path of a configuration object that
exists at import, while the database URL of BookReviver is read from the environment and ``.env`` when the command
runs. This group reads the settings the way the application does and hands the database's configuration to the same
commands, as advanced-alchemy's FastAPI extension does for its ``database`` group, so
``uv run bookreviver-migrate upgrade head`` needs no flag. Every command of ``alchemy`` is available: ``upgrade``,
``downgrade``, ``make-migrations``, ``check``, ``stamp``, ``show-current-revision`` and the others.
"""

from advanced_alchemy.cli import add_migration_commands
from advanced_alchemy.utils.cli_tools import click, group

from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.app.settings import Settings


@group(name='bookreviver-migrate')
@click.pass_context
def migrate(context: click.Context) -> None:
    """Create and apply revisions of the schema of the database the settings of BookReviver name.

    \N{FORM FEED}
    :param context: Click context, whose object hands the database's configuration to advanced-alchemy's commands.
    :type context: click.Context
    """
    settings = Settings()
    # SQLite creates the database file but not the directory holding it
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    context.ensure_object(dict)
    context.obj['configs'] = [
        SqlDatabase(settings.resolved_database_url, wait_seconds=settings.change_wait_seconds).config
    ]


add_migration_commands(migrate)
