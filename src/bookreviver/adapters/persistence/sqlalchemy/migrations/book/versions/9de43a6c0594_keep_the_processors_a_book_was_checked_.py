"""Keep the processors a book was checked against.

A book stored before this revision has an empty list, so the first opening checks its results against the installed
processors and records them.

Revision ID: 9de43a6c0594
Revises: ff7beab877bc
Create Date: 2026-11-28 13:40:12.608011
"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '9de43a6c0594'
down_revision: str | Sequence[str] | None = 'ff7beab877bc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
    """Change the schema to this revision."""
    with op.batch_alter_table('book_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('checked_processors', sa.JSON(), server_default='[]', nullable=False))


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    with op.batch_alter_table('book_records', schema=None) as batch_op:
        batch_op.drop_column('checked_processors')


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
