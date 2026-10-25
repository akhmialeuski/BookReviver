"""Drop the tokens of the provider accounts.

The sign-in uses the tokens of a provider only to read the account it vouches for, and nothing reads the stored ones.
The downgrade puts the columns back, with an empty access token in every row.

Revision ID: df4b7a534833
Revises: 2d1c8cf7b6ea
Create Date: 2026-10-25 15:56:35.798807
"""

import warnings
from typing import TYPE_CHECKING

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = 'df4b7a534833'
down_revision: str | Sequence[str] | None = '2d1c8cf7b6ea'
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
    with op.batch_alter_table('oauth_account', schema=None) as batch_op:
        batch_op.drop_column('access_token')
        batch_op.drop_column('expires_at')
        batch_op.drop_column('refresh_token')


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    with op.batch_alter_table('oauth_account', schema=None) as batch_op:
        batch_op.add_column(sa.Column('refresh_token', sa.String(length=1024), nullable=True))
        batch_op.add_column(sa.Column('expires_at', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('access_token', sa.String(length=1024), server_default='', nullable=False))


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
