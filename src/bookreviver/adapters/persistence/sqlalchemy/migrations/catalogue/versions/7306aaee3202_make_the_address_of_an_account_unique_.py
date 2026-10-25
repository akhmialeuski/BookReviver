"""Make the address of an account unique whatever its case.

The unique index of the addresses compared them as typed, while the search by an address compares them lower-cased, so
two accounts could hold one address. The index now holds the lower-cased address. The revision stops before it changes
anything when two accounts already hold addresses that differ only in case.

Revision ID: 7306aaee3202
Revises: df4b7a534833
Create Date: 2026-10-25 16:02:08.642631
"""

import warnings
from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import literal_column, text

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '7306aaee3202'
down_revision: str | Sequence[str] | None = 'df4b7a534833'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The addresses held by more than one account once their case is ignored, which the unique index refuses
SAME_ADDRESS = text('SELECT lower(email), count(*) FROM user GROUP BY lower(email) HAVING count(*) > 1')


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
    """Change the schema to this revision, after the check that it can.

    :raises RuntimeError: If two accounts hold addresses that differ only in case.
    """
    if same := op.get_bind().execute(SAME_ADDRESS).all():
        err_msg = f'Accounts hold addresses that differ only in case, as (lower-cased address, accounts): {same}.'
        raise RuntimeError(err_msg)
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_email'))
        batch_op.create_index('ix_user_email', [literal_column('lower(email)')], unique=True)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_index('ix_user_email')
        batch_op.create_index(batch_op.f('ix_user_email'), ['email'], unique=True)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
