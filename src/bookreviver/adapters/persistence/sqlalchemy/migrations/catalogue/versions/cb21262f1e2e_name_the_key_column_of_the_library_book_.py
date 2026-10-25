"""Name the key column of the library book_id.

The library named the column of its book ``project_id`` after the time the application called a book a project. The
column is renamed with its rows, and its primary key, the key to ``user``, the index of the owners and the unique
directory keep their names and their definitions, since none of them carried the old name. The downgrade renames the
column back.

Revision ID: cb21262f1e2e
Revises: 7306aaee3202
Create Date: 2026-10-25 18:11:22.962061
"""

import warnings
from typing import TYPE_CHECKING, Final

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = 'cb21262f1e2e'
down_revision: str | Sequence[str] | None = '7306aaee3202'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LIBRARY: Final = 'library'
OLD: Final = 'project_id'
NEW: Final = 'book_id'


def upgrade() -> None:
    """Change the schema, then the data, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        schema_upgrades()
        data_upgrades()


def downgrade() -> None:
    """Revert the data, then the schema, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', category=UserWarning)
        data_downgrades()
        schema_downgrades()


def schema_upgrades() -> None:
    """Rename the column of the book, which is the primary key of the library."""
    with op.batch_alter_table(LIBRARY, schema=None) as batch_op:
        batch_op.alter_column(OLD, new_column_name=NEW)


def schema_downgrades() -> None:
    """Rename the column of the book back."""
    with op.batch_alter_table(LIBRARY, schema=None) as batch_op:
        batch_op.alter_column(NEW, new_column_name=OLD)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
