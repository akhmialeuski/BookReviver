"""Add the measure of the book to the one processing job index.

The partial unique index ``ix_jobs_one_active_processing`` now covers the ``measure-book`` kind too, so a measure of the
book and a run, a preview, a tile cutting or a collection never overlap, and two requests that both pass the check
for an active job cannot both store one. Alembic does not compare the condition of a partial index, so autogenerate
found no change and the index is dropped and created here by hand.

Revision ID: 13662fc5c17e
Revises: dc4a30549b71
Create Date: 2026-10-03 02:12:59.310290
"""

import warnings
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

IGNORE: Final = 'ignore'
TABLE = 'jobs'
INDEX = 'ix_jobs_one_active_processing'
COLUMN = 'project_id'
# The rows of the partial unique index that keeps a project to one job processing the versions of its pages
ACTIVE_PROCESSING = sa.text(
    "kind IN ('collect-versions', 'cut-tiles', 'measure-book', 'preview-step', 'run-stage') "
    "AND state IN ('queued', 'running')"
)
# The same rows before the measure of the book joined the jobs that process the versions
PREVIOUS_ACTIVE_PROCESSING = sa.text(
    "kind IN ('collect-versions', 'cut-tiles', 'preview-step', 'run-stage') AND state IN ('queued', 'running')"
)

# Revision identifiers, used by Alembic
revision: str = '13662fc5c17e'
down_revision: str | Sequence[str] | None = 'dc4a30549b71'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Change the schema, then the data, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        schema_upgrades()
        data_upgrades()


def downgrade() -> None:
    """Revert the data, then the schema, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        data_downgrades()
        schema_downgrades()


def replace_index(old: sa.TextClause, new: sa.TextClause) -> None:
    """Drop the processing index with one condition and create it again with another.

    :param old: The condition the index has now.
    :type old: sa.TextClause
    :param new: The condition the index is to have.
    :type new: sa.TextClause
    """
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(INDEX, sqlite_where=old, postgresql_where=old)
        batch_op.create_index(INDEX, [COLUMN], unique=True, sqlite_where=new, postgresql_where=new)


def schema_upgrades() -> None:
    """Change the schema to this revision."""
    replace_index(PREVIOUS_ACTIVE_PROCESSING, ACTIVE_PROCESSING)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    replace_index(ACTIVE_PROCESSING, PREVIOUS_ACTIVE_PROCESSING)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
