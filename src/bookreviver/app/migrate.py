"""The ``bookreviver-migrate`` command: advanced-alchemy's migration commands on the two schemas of the settings.

advanced-alchemy's own ``alchemy`` command requires ``--config`` with the dotted path of a configuration object that
exists at import, while the data directory of BookReviver is read from the environment and ``.env`` when the command
runs. This group reads the settings the way the application does and hands the configurations to the same commands, as
advanced-alchemy's FastAPI extension does for its ``database`` group. Every command of ``alchemy`` is available:
``upgrade``, ``downgrade``, ``make-migrations``, ``check``, ``stamp``, ``show-current-revision`` and the others.

The first configuration is the catalogue, so ``uv run bookreviver-migrate upgrade head`` needs no flag. The second,
chosen with ``--bind-key book``, is the schema of a book. A book is brought to the head revision when the application
opens it, so there is no one file of that schema to migrate by hand, and the commands of ``--bind-key book`` run on a
file made at the head revision for the command in a temporary directory: that is the file ``make-migrations
--autogenerate`` compares the tables of a book with.

``split-shared-database`` moves the shared database of the releases before the catalogue into the catalogue and the
files of the books, once. ``move-book-directories`` moves the directories of the books from ``projects/`` to ``books/``
under the storage root, once.
"""

from collections.abc import Callable
from pathlib import Path
from tempfile import TemporaryDirectory

import anyio
from advanced_alchemy.cli import add_migration_commands
from advanced_alchemy.utils.cli_tools import click, group
from dishka import make_async_container

from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.split import SharedDatabaseSplit
from bookreviver.adapters.storage.move import OLD_BOOKS_DIRECTORY, BookDirectoriesMove, MoveRefusedError
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.app.providers.storage import STORAGE_PROVIDERS
from bookreviver.app.settings import Settings
from bookreviver.domain.ids import BookDirectoryName
from bookreviver.domain.keys import KeySegment
from bookreviver.ports.storage import BookDirectory

# The shared database of the releases before the catalogue, in the data directory
SHARED_DATABASE: str = 'bookreviver.db'
# The key of the object of the Click context that advanced-alchemy's commands read their configurations from
CONFIGS_KEY: str = 'configs'


def open_catalogue(settings: Settings) -> SqlDatabase:
    """Configure the catalogue the settings name, creating the data directory SQLite does not create.

    A migration is not measured, so the catalogue gets the tracer that records nothing.

    :param settings: Settings of the command.
    :type settings: Settings
    :returns: The catalogue, which opens no connection yet.
    :rtype: SqlDatabase
    """
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return SqlDatabase(
        settings.resolved_catalogue_url,
        schema=Schema.CATALOGUE,
        tracer=NullTracer(),
        wait_seconds=settings.change_wait.job_seconds,
    )


@group(name='bookreviver-migrate')
@click.pass_context
def migrate(context: click.Context) -> None:
    """Create and apply revisions of the schemas of the catalogue and of a book that the settings of BookReviver name.

    \N{FORM FEED}
    :param context: Click context, whose object hands the configurations to advanced-alchemy's commands.
    :type context: click.Context
    """
    settings = Settings()
    catalogue = open_catalogue(settings)
    reference = Path(context.with_resource(TemporaryDirectory())) / KeySegment.DATABASE
    book = SqlDatabase(
        f'sqlite:///{reference}', schema=Schema.BOOK, tracer=NullTracer(), wait_seconds=settings.change_wait.job_seconds
    )
    book.migrations.upgrade()
    # Registered after the directory, so the engines close before the directory is removed
    for database in (catalogue, book):
        context.call_on_close(database.engine.dispose)
        context.call_on_close(database.read_engine.dispose)
    context.ensure_object(dict)
    context.obj[CONFIGS_KEY] = [catalogue.config, book.config]


@migrate.command(name='split-shared-database')
def split_shared_database() -> None:
    """Move the shared database of the earlier releases into the catalogue and a database for each book.

    The shared ``bookreviver.db`` of the data directory is read and left as it is, and the catalogue must hold no
    account yet.
    """
    settings = Settings()
    catalogue = open_catalogue(settings)
    books = BookRegistry(
        root=settings.storage_root,
        tracer=NullTracer(),
        wait_seconds=settings.change_wait.job_seconds,
        idle_seconds=settings.book_idle_seconds,
    )
    split = SharedDatabaseSplit(settings.data_dir.resolve() / SHARED_DATABASE, catalogue_db=catalogue, books=books)
    storage = make_async_container(STORAGE_PROVIDERS[settings.storage](), context={Settings: settings})

    async def run() -> None:
        """Register the directories of the configured storage, run the split, and close what it opened."""
        try:
            BookDirectory.register(await storage.get(Callable[[BookDirectoryName], BookDirectory]))
            report = await split.run()
        finally:
            await books.dispose()
            await catalogue.dispose()
            await storage.close()
        click.echo(f'Moved {report.accounts} accounts and {report.books} books.')

    anyio.run(run)


@migrate.command(name='move-book-directories')
def move_book_directories() -> None:
    """Move the directories of the books from ``projects/`` to ``books/`` under the storage root.

    A storage root with no ``projects/`` directory is left as it is, so the command runs again without harm.

    :raises click.ClickException: If a directory of ``projects/`` would overwrite one in ``books/``, or ``projects/``
        holds something that is not the directory of a book.
    """
    root = Settings().storage_root
    try:
        report = anyio.run(BookDirectoriesMove(root).run)
    except MoveRefusedError as error:
        raise click.ClickException(str(error)) from error
    if report.found:
        click.echo(
            f'Moved {report.moved} book directories from {root / OLD_BOOKS_DIRECTORY} to {root / KeySegment.BOOKS}.'
        )
    else:
        click.echo(f'There is no {root / OLD_BOOKS_DIRECTORY}, so there is nothing to move.')


add_migration_commands(migrate)
