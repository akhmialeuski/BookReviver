"""The Alembic environment shared by the two schemas, from advanced-alchemy's synchronous template.

The catalogue and the file of a book each have a directory of revisions, and the ``env.py`` of each directory calls
:func:`run` with its :class:`~bookreviver.adapters.persistence.sqlalchemy.database.Schema`, which picks the metadata of
the tables the revisions build. Alembic runs that module for every command that touches a database or compares it with
the tables. The engine and
the version table come from ``SqlDatabase.config`` through advanced-alchemy's ``AlembicCommandConfig``, so a
migration runs on the application's own engine of the writer, with no ``alembic.ini`` and no second connection
setting. The options of autogenerate are set here, where Alembic reads them: advanced-alchemy 1.11.0 builds its
``AlembicCommandConfig`` without the ``compare_type`` and ``render_as_batch`` of ``AlembicSyncConfig``, so the
config always carries the class defaults, and ``compare_type`` would be off whatever the application configured.

Every command runs its revisions in one transaction together with the row of the version table, so a migration
applies whole or not at all. The transaction begins with ``BEGIN IMMEDIATE``, as every transaction of that engine does,
and the driver commits or rolls it back. A migration also runs with foreign keys off. The engine turns them on for
every connection, and batch mode changes a table by copying it, dropping the old one and renaming the copy, so
dropping the old ``projects`` with keys on would cascade and delete every source, scan and page of the book.
SQLite ignores the pragma inside a transaction, so it is set on the driver connection before the transaction begins,
and ``PRAGMA foreign_key_check`` runs in the transaction before its commit: a migration that left a row whose key
points nowhere is rolled back and fails the command. See https://alembic.sqlalchemy.org/en/latest/batch.html.
"""

from contextlib import closing
from typing import TYPE_CHECKING, Any, Literal, cast

import sqlalchemy
from alembic import context
from sqlalchemy.dialects.sqlite.base import SQLiteDialect

if TYPE_CHECKING:
    from advanced_alchemy.alembic.commands import AlembicCommandConfig
    from alembic.autogenerate.api import AutogenContext
    from alembic.runtime.migration import MigrationContext
    from sqlalchemy import Column, MetaData
    from sqlalchemy.engine import Connection, Engine
    from sqlalchemy.types import TypeEngine

    from bookreviver.adapters.persistence.sqlalchemy.database import Schema

__all__ = ('compare_type', 'do_run_migrations', 'render_item', 'run', 'set_foreign_keys')

# SQLite's rules for the affinity of a declared type, applied in order, see
# https://www.sqlite.org/datatype3.html#determination_of_column_affinity; a type matching none is NUMERIC
SQLITE_BLOB_AFFINITY: str = 'BLOB'
SQLITE_NUMERIC_AFFINITY: str = 'NUMERIC'
SQLITE_AFFINITY_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (('INT',), 'INTEGER'),
    (('CHAR', 'CLOB', 'TEXT'), 'TEXT'),
    ((SQLITE_BLOB_AFFINITY,), SQLITE_BLOB_AFFINITY),
    (('REAL', 'FLOA', 'DOUB'), 'REAL'),
)


def render_item(type_: str, obj: object, autogen_context: AutogenContext) -> str | Literal[False]:
    """Render a column type from outside SQLAlchemy by its own module, and import that module in the revision.

    advanced-alchemy renders such types as ``sa.<name>`` and binds its own types to those names in the revision, which
    gives a ``GUID`` of another library the ``GUID`` of advanced-alchemy and leaves any other name unbound. Naming the
    module keeps every type the one the table declares.

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


def _sqlite_affinity(declared: str) -> str:
    """Return the affinity SQLite gives a column of the declared type, by its rules in their order.

    :param declared: Type as a ``CREATE TABLE`` declares it, such as ``BINARY(16)``.
    :type declared: str
    :returns: ``INTEGER``, ``TEXT``, ``BLOB``, ``REAL`` or ``NUMERIC``.
    :rtype: str
    """
    name = declared.upper()
    if not name:
        return SQLITE_BLOB_AFFINITY
    matches = (affinity for parts, affinity in SQLITE_AFFINITY_RULES if any(part in name for part in parts))
    return next(matches, SQLITE_NUMERIC_AFFINITY)


def compare_type(
    migration_context: MigrationContext,
    _inspected_column: Column[Any],
    _metadata_column: Column[Any],
    inspected_type: TypeEngine[Any],
    metadata_type: TypeEngine[Any],
) -> bool | None:
    """Compare a type SQLite cannot reflect by its affinity, and leave every other type to Alembic.

    SQLAlchemy reflects a declared type whose name it does not know on SQLite, such as the ``BINARY(16)`` of
    advanced-alchemy's ``GUID``, as the type of its affinity, ``NUMERIC(16)``. Alembic's comparison of type names then
    reports every such column as changed. For those names the reflected type says no more than the affinity, so the
    affinities are compared; a column moved to a type of another affinity is still reported.

    :param migration_context: Context of the comparison, whose dialect is the database's.
    :type migration_context: MigrationContext
    :param _inspected_column: Column as the database holds it, passed by Alembic and unused.
    :type _inspected_column: Column[Any]
    :param _metadata_column: Column as the tables declare it, passed by Alembic and unused.
    :type _metadata_column: Column[Any]
    :param inspected_type: Type reflected from the database.
    :type inspected_type: TypeEngine[Any]
    :param metadata_type: Type the tables declare.
    :type metadata_type: TypeEngine[Any]
    :returns: Whether the types differ, or None to let Alembic compare them.
    :rtype: bool | None
    """
    dialect = migration_context.dialect
    if not isinstance(dialect, SQLiteDialect):
        return None
    declared = metadata_type.compile(dialect=dialect)
    if declared.partition('(')[0].upper() in dialect.ischema_names:
        return None
    return _sqlite_affinity(declared) != _sqlite_affinity(inspected_type.compile(dialect=dialect))


def run_migrations_offline(config: AlembicCommandConfig, target_metadata: MetaData) -> None:
    """Write the SQL of the migrations to the output instead of running it, with only the URL of the database.

    The foreign key pragmas of SQLite are not written, so SQL meant for SQLite runs with the keys off.

    :param config: The Alembic configuration advanced-alchemy builds from ``SqlDatabase.config``.
    :type config: AlembicCommandConfig
    :param target_metadata: Metadata of the tables of the schema.
    :type target_metadata: MetaData
    """
    context.configure(
        url=config.db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_type=compare_type,
        version_table=config.version_table_name,
        version_table_pk=config.version_table_pk,
        render_as_batch=True,
        render_item=render_item,
    )

    with context.begin_transaction():
        context.run_migrations()


def set_foreign_keys(connection: Connection, *, enabled: bool) -> None:
    """Turn the enforcement of foreign keys on or off on the driver connection, outside any transaction.

    SQLite ignores the pragma inside a transaction, and SQLAlchemy begins one with the first statement it runs, so the
    pragma goes to the driver connection directly.

    :param connection: Connection of the application's engine, not yet in a transaction.
    :type connection: Connection
    :param enabled: Whether the keys are enforced.
    :type enabled: bool
    """
    with closing(connection.connection.cursor()) as cursor:
        cursor.execute(f'PRAGMA foreign_keys={"ON" if enabled else "OFF"}')


def do_run_migrations(connection: Connection, config: AlembicCommandConfig, target_metadata: MetaData) -> None:
    """Run the migrations on ``connection`` in one transaction, with foreign keys off and checked in it.

    :param connection: Connection of the application's engine, not yet in a transaction.
    :type connection: Connection
    :param config: The Alembic configuration advanced-alchemy builds from ``SqlDatabase.config``.
    :type config: AlembicCommandConfig
    :param target_metadata: Metadata of the tables of the schema.
    :type target_metadata: MetaData
    :raises RuntimeError: When a migration left rows whose foreign key refers to no row, after the transaction holding
        the migrations and their version row is rolled back.
    """
    set_foreign_keys(connection, enabled=False)
    # The engine begins its transactions with BEGIN IMMEDIATE, so every CREATE, DROP and ALTER of a migration runs in
    # one transaction; Alembic, which takes SQLite for a database without transactional DDL, then leaves the commit
    # to this function
    connection.begin()
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=compare_type,
        version_table=config.version_table_name,
        version_table_pk=config.version_table_pk,
        render_as_batch=True,
        render_item=render_item,
    )

    try:
        with context.begin_transaction():
            context.run_migrations()
        if violations := connection.exec_driver_sql('PRAGMA foreign_key_check').fetchall():
            err_msg = (
                'The migration left rows with a foreign key to no row, as (table, rowid, parent, key): '
                f'{violations}. Nothing was changed.'
            )
            raise RuntimeError(err_msg)
        connection.commit()
    finally:
        # Undoes the migrations when they or the check failed, and does nothing after the commit
        connection.rollback()
        set_foreign_keys(connection, enabled=True)


def run(schema: Schema) -> None:
    """Run the command Alembic was asked for on the database of the configuration, with the tables of ``schema``.

    Online, the migrations run on a connection of the application's engine of the writer, whose connections are then
    closed.

    :param schema: The schema of the directory whose ``env.py`` Alembic runs.
    :type schema: Schema
    """
    config = cast('AlembicCommandConfig', context.config)
    target_metadata = schema.metadata
    if context.is_offline_mode():
        run_migrations_offline(config, target_metadata)
        return
    connectable = cast('Engine', config.engine)
    with connectable.connect() as connection:
        do_run_migrations(connection, config, target_metadata)
    connectable.dispose()
