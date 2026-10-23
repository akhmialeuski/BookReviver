"""Count the scans of a source when it is read.

The number of scans of a source was stored in ``sources.scan_count`` beside the scans that give it, so the application
now counts them when it reads the source and the column goes. The downgrade brings the column back with the count of
the scans of every source.

Revision ID: 3961f96ad3d8
Revises: 2b10e694550f
Create Date: 2026-10-22 21:10:34.358873
"""

import warnings
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '3961f96ad3d8'
down_revision: str | Sequence[str] | None = '2b10e694550f'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
SOURCES: Final = 'sources'
SCAN_COUNT: Final = 'scan_count'
COUNT_SCANS: Final = sa.text(
    'UPDATE sources SET scan_count = (SELECT count(*) FROM scans WHERE scans.source_id = sources.id)'
)


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
    """Change the schema to this revision."""
    with op.batch_alter_table(SOURCES, schema=None) as batch_op:
        batch_op.drop_column(SCAN_COUNT)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, with the count of the scans of every source."""
    with op.batch_alter_table(SOURCES, schema=None) as batch_op:
        batch_op.add_column(sa.Column(SCAN_COUNT, sa.INTEGER(), server_default=sa.text('0'), nullable=False))
    op.execute(COUNT_SCANS)
    with op.batch_alter_table(SOURCES, schema=None) as batch_op:
        batch_op.alter_column(SCAN_COUNT, server_default=None)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
