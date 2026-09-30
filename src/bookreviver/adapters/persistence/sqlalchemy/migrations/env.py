"""The Alembic environment of the schema, from advanced-alchemy's asynchronous template.

Alembic runs this module for every command that touches the database or compares it with the tables. The
configuration comes from ``SqlDatabase.config`` through advanced-alchemy's ``AlembicCommandConfig``, so a migration
runs on the application's own engine, with no ``alembic.ini`` and no second connection setting.

On SQLite a migration runs with foreign keys off. The engine turns them on for every connection, and batch mode
changes a table by copying it, dropping the old one and renaming the copy, so dropping the old ``projects`` with keys
on would cascade and delete every source, scan, page and job of every book. SQLite ignores the pragma inside a
transaction, so it is set before the migrations begin theirs. ``PRAGMA foreign_key_check`` afterwards fails the
command when a migration left a row whose key points nowhere. See https://alembic.sqlalchemy.org/en/latest/batch.html.
PostgreSQL changes tables in place and needs neither step.
"""

import asyncio
from typing import TYPE_CHECKING, Literal, cast

import sqlalchemy
from alembic import context
from alembic.autogenerate import rewriter
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from bookreviver.adapters.persistence.sqlalchemy.database import SQLITE_DIALECT
from bookreviver.adapters.persistence.sqlalchemy.tables import ProjectRow

if TYPE_CHECKING:
    from advanced_alchemy.alembic.commands import AlembicCommandConfig
    from alembic.autogenerate.api import AutogenContext
    from sqlalchemy import MetaData
    from sqlalchemy.engine import Connection
    from sqlalchemy.ext.asyncio import AsyncEngine

__all__ = ('do_run_migrations', 'render_item', 'run_migrations_offline', 'run_migrations_online')

# The Alembic configuration advanced-alchemy builds from SqlDatabase.config
config = cast('AlembicCommandConfig', context.config)
writer = rewriter.Rewriter()
# Every table of the adapter shares one metadata with the account tables, and importing the tables registers them
target_metadata: MetaData = ProjectRow.metadata


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render a column type from outside SQLAlchemy by its own module, and import that module in the revision.

    advanced-alchemy renders such types as ``sa.<name>`` and binds its own types to those names in the revision,
    which gives fastapi-users' ``GUID`` the ``GUID`` of advanced-alchemy and leaves its ``TIMESTAMPAware`` unbound.
    Naming the module keeps every type the one the table declares.

    :param type_: Kind of the item Alembic renders, such as ``type`` or ``column``.
    :type type_: str
    :param obj: The item, a column type when ``type_`` is ``type``.
    :type obj: object
    :param autogen_context: Context of the revision being written, which collects its imports.
    :type autogen_context: AutogenContext
    :returns: Python source of the type, or False to leave the item to Alembic.
    :rtype: str | Literal[False]
    """
    module = type(obj).__module__
    if type_ != 'type' or module.partition('.')[0] == sqlalchemy.__name__:
        return False
    autogen_context.imports.add(f'import {module}')
    return f'{module}.{obj!r}'


def run_migrations_offline() -> None:
    """Write the SQL of the migrations to the output instead of running it, with only the URL of the database.

    The foreign key pragmas of SQLite are not written, so SQL meant for SQLite runs with the keys off.
    """
    context.configure(
        url=config.db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=config.compare_type,
        version_table=config.version_table_name,
        version_table_pk=config.version_table_pk,
        user_module_prefix=config.user_module_prefix,
        render_as_batch=config.render_as_batch,
        render_item=render_item,
        process_revision_directives=writer,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run the migrations on ``connection``, on SQLite with foreign keys off and checked afterwards.

    :param connection: Connection of the application's engine, not yet in a transaction.
    :type connection: Connection
    :raises RuntimeError: When a migration on SQLite left rows whose foreign key refers to no row.
    """
    on_sqlite = connection.dialect.name == SQLITE_DIALECT
    if on_sqlite:
        connection.exec_driver_sql('PRAGMA foreign_keys=OFF')
        # Ends the transaction SQLAlchemy began, so Alembic begins and commits its own
        connection.commit()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=config.compare_type,
        version_table=config.version_table_name,
        version_table_pk=config.version_table_pk,
        user_module_prefix=config.user_module_prefix,
        render_as_batch=config.render_as_batch,
        render_item=render_item,
        process_revision_directives=writer,
    )

    try:
        with context.begin_transaction():
            context.run_migrations()
        if on_sqlite and (violations := connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall()):
            err_msg = (
                f'The migration left rows with a foreign key to no row, as (table, rowid, parent, key): {violations}'
            )
            raise RuntimeError(err_msg)
    finally:
        if on_sqlite:
            connection.exec_driver_sql('PRAGMA foreign_keys=ON')
            connection.commit()


async def run_migrations_online() -> None:
    """Run the migrations on a connection of the application's engine, and close its connections afterwards."""
    configuration = config.get_section(config.config_ini_section) or {}
    configuration['sqlalchemy.url'] = config.db_url

    connectable = cast(
        'AsyncEngine',
        config.engine
        or async_engine_from_config(
            configuration,
            prefix='sqlalchemy.',
            poolclass=pool.NullPool,
            future=True,
        ),
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
