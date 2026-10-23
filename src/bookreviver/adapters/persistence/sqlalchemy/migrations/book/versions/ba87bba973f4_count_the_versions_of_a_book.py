"""Count the versions of a book.

Every committed change that writes a part of the book a reader asks for raises the version on the row of its project
by one, and the readers of the book are answered from it. A book starts at version 0, which its existing rows take. The
downgrade drops the column.

Revision ID: ba87bba973f4
Revises: e5d75f899dce
Create Date: 2026-10-23 01:31:54.776758
"""

import warnings
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = 'ba87bba973f4'
down_revision: str | Sequence[str] | None = 'e5d75f899dce'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
PROJECTS: Final = 'projects'
VERSION: Final = 'version'


def upgrade() -> None:
    """Change the schema, then the data, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings(SILENCE, category=UserWarning)
        schema_upgrades()
        data_upgrades()


def downgrade() -> None:
    """Revert the data, then the schema, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings(SILENCE, category=UserWarning)
        data_downgrades()
        schema_downgrades()


def schema_upgrades() -> None:
    """Change the schema to this revision, every book at version 0."""
    with op.batch_alter_table(PROJECTS, schema=None) as batch_op:
        batch_op.add_column(sa.Column(VERSION, sa.Integer(), server_default=sa.text('0'), nullable=False))


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    with op.batch_alter_table(PROJECTS, schema=None) as batch_op:
        batch_op.drop_column(VERSION)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
