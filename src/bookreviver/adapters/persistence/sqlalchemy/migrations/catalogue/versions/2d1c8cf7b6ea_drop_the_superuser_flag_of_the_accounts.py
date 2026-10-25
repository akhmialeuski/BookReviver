"""Drop the superuser flag of the accounts.

The application has no superuser and no route that asks for it. The downgrade puts the column back with false in
every row.

Revision ID: 2d1c8cf7b6ea
Revises: 87d63fb283ae
Create Date: 2026-10-25 15:48:05.380847
"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '2d1c8cf7b6ea'
down_revision: str | Sequence[str] | None = '87d63fb283ae'
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
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('is_superuser')


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_superuser', sa.Boolean(), server_default=sa.false(), nullable=False))


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
