"""Store the accounts with the identifier and time types of the other tables.

The tables of the accounts were declared by the fastapi-users library, which keeps an identifier as ``CHAR(36)`` text
with dashes and the time of a token as ``TIMESTAMP``. The adapter now declares them with the types of every other
table, ``GUID`` and ``DateTimeUTC`` of advanced-alchemy.

On SQLite the upgrade changes the declared type of every column that holds the identifier of an account, which are
``user.id``, ``oauth_account.id``, ``oauth_account.user_id``, ``accesstoken.user_id``, ``projects.owner_id``,
``book_places.account_id`` and ``recipe_profiles.account_id``, and rewrites each value from the text with dashes to the
16 bytes ``GUID`` stores (``GUID.process_bind_param`` writes ``UUID.bytes``). The declared type of
``accesstoken.created_at`` becomes ``DATETIME`` with no change of the value, because ``TIMESTAMP`` and ``DATETIME``
both store the UTC time as ``YYYY-MM-DD HH:MM:SS.ffffff`` text (``DATETIME`` of SQLite's dialect writes that format
for either type, and ``DateTimeUTC.process_bind_param`` converts to UTC first), and the library wrote the UTC time.

Alembic's batch mode copies a table with ``CAST(column AS type)`` when it is asked to change the type of a column to
one of another affinity, and ``CAST(text AS BINARY(16))`` turns the text of an identifier into the number its first
digits make. So the rebuild changes the types as the table is reflected, through a ``column_reflect`` listener, which
copies the values as they are, and the values are rewritten afterwards.

The downgrade is the exact inverse: the 16 bytes become the text with dashes, and the declared types return. Foreign
keys are off while a migration runs and ``PRAGMA foreign_key_check`` runs after it, so the rewritten values have to
match on both sides of every key, and a key left pointing nowhere rolls the migration back.

PostgreSQL stores an identifier as ``UUID`` and a time as ``TIMESTAMP WITH TIME ZONE`` under either declaration, so on
PostgreSQL the revision changes nothing.

Revision ID: 674f0fbcad00
Revises: ba28fd6fb352
Create Date: 2026-10-09 11:41:47.208362
"""

import warnings
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import UUID

import advanced_alchemy.types.datetime
import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sqlalchemy import ColumnClause, Table
    from sqlalchemy.engine.reflection import Inspector
    from sqlalchemy.types import TypeEngine

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '674f0fbcad00'
down_revision: str | Sequence[str] | None = 'ba28fd6fb352'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SQLITE: str = 'sqlite'
type Declaration = tuple[TypeEngine[Any], TypeEngine[Any]]
GUID = advanced_alchemy.types.guid.GUID(length=16)
ACCOUNT_ID: str = 'account_id'
# The declaration before this revision, which the fastapi-users library wrote, and the one after it
IDENTIFIER: Declaration = (sa.CHAR(36), GUID)
TIME: Declaration = (sa.TIMESTAMP(), advanced_alchemy.types.datetime.DateTimeUTC(timezone=True))
# The columns whose declared type changes, by table and column
RETYPED: dict[str, dict[str, Declaration]] = {
    'accesstoken': {'created_at': TIME, 'user_id': IDENTIFIER},
    'book_places': {ACCOUNT_ID: IDENTIFIER},
    'oauth_account': {'id': IDENTIFIER, 'user_id': IDENTIFIER},
    'projects': {'owner_id': IDENTIFIER},
    'recipe_profiles': {ACCOUNT_ID: IDENTIFIER},
    'user': {'id': IDENTIFIER},
}


def upgrade() -> None:
    """Change the schema, then the data, in the transaction of the migration, which commits or rolls back whole."""
    if op.get_bind().dialect.name != SQLITE:
        return
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        schema_upgrades()
        data_upgrades()


def downgrade() -> None:
    """Revert the data, then the schema, in the transaction of the migration, which commits or rolls back whole."""
    if op.get_bind().dialect.name != SQLITE:
        return
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        data_downgrades()
        schema_downgrades()


def _declare(_inspector: Inspector, table: Table, column_info: dict[str, Any], *, forward: bool) -> None:
    """Give a column of ``RETYPED`` its new declared type, or its old one, as the table is reflected.

    :param _inspector: Inspector reflecting the table, required by the event signature and unused.
    :type _inspector: Inspector
    :param table: Table being reflected.
    :type table: Table
    :param column_info: Reflected description of the column, whose ``type`` is replaced.
    :type column_info: dict[str, Any]
    :param forward: Whether to declare the new type, or the old one.
    :type forward: bool
    """
    if kind := RETYPED.get(table.name, {}).get(column_info['name']):
        column_info['type'] = kind[1] if forward else kind[0]


def _rebuild(*, forward: bool) -> None:
    """Rebuild every table of ``RETYPED`` by copying it, with its columns declared as the new types, or the old ones.

    :param forward: Whether to declare the new types, or the old ones.
    :type forward: bool
    """
    listeners = [('column_reflect', partial(_declare, forward=forward))]
    for table in RETYPED:
        with op.batch_alter_table(table, recreate='always', reflect_kwargs={'listeners': listeners}):
            pass


def _rewrite_identifiers(*, forward: bool) -> None:
    """Rewrite every identifier of an account from the text with dashes to the bytes of ``GUID``, or back.

    Each distinct value is rewritten by one statement, so the rows that refer to an account are updated together. A
    value that is in the form asked for already is left as it is, so the rewrite is safe to run again over a table that
    an earlier run left half rewritten.

    :param forward: Whether to write bytes, or text with dashes.
    :type forward: bool
    """
    bind = op.get_bind()
    written: type = str if forward else bytes
    rewrite: Callable[[Any], Any] = (lambda text: UUID(text).bytes) if forward else (lambda raw: str(UUID(bytes=raw)))
    for table, columns in RETYPED.items():
        for name in (name for name, kind in columns.items() if kind is IDENTIFIER):
            column: ColumnClause[Any] = sa.column(name)
            rows = sa.table(table, column)
            stored: list[Any] = list(bind.execute(sa.select(column).select_from(rows).distinct()).scalars())
            for value in stored:
                if isinstance(value, written):
                    bind.execute(sa.update(rows).where(column == value).values({name: rewrite(value)}))


def schema_upgrades() -> None:
    """Change the schema to this revision."""
    _rebuild(forward=True)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    _rebuild(forward=False)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""
    _rewrite_identifiers(forward=True)


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
    _rewrite_identifiers(forward=False)
