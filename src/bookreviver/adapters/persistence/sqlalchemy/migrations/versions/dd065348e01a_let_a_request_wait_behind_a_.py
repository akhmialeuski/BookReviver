"""Let a request wait behind a housekeeping job.

The partial unique index ``ix_jobs_one_active_processing`` allowed one queued or running job that processes the versions
of a project, so a run, a preview or a measure was refused while a tile cutting or a collection was active. It is
replaced by three indexes. A project has one queued or running job of the kinds the user asks for, one queued or running
housekeeping job, and one job running of all these kinds. A request made during a housekeeping job is stored as queued
and is queued to a worker when that job ends. Alembic does not compare the condition of a partial index, so the
conditions are written here by hand.

Revision ID: dd065348e01a
Revises: 38db886cbb30
Create Date: 2026-10-03 08:40:44.450833
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
COLUMN = 'project_id'
PROCESSING_INDEX = 'ix_jobs_one_active_processing'
REQUESTED_INDEX = 'ix_jobs_one_active_requested'
HOUSEKEEPING_INDEX = 'ix_jobs_one_active_housekeeping'
RUNNING_INDEX = 'ix_jobs_one_running_processing'
# The rows of the index before this revision: any job that processes the versions, queued or running
ACTIVE_PROCESSING = sa.text(
    "kind IN ('collect-versions', 'cut-tiles', 'measure-book', 'preview-step', 'run-stage') "
    "AND state IN ('queued', 'running')"
)
# The jobs the user asks for, queued or running
ACTIVE_REQUESTED = sa.text("kind IN ('measure-book', 'preview-step', 'run-stage') AND state IN ('queued', 'running')")
# The jobs the application queues for itself, queued or running
ACTIVE_HOUSEKEEPING = sa.text("kind IN ('collect-versions', 'cut-tiles') AND state IN ('queued', 'running')")
# Every kind that processes the versions, running
RUNNING_PROCESSING = sa.text(
    "kind IN ('collect-versions', 'cut-tiles', 'measure-book', 'preview-step', 'run-stage') AND state = 'running'"
)

# Revision identifiers, used by Alembic
revision: str = 'dd065348e01a'
down_revision: str | Sequence[str] | None = '38db886cbb30'
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


def schema_upgrades() -> None:
    """Change the schema to this revision."""
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        batch_op.drop_index(PROCESSING_INDEX, sqlite_where=ACTIVE_PROCESSING, postgresql_where=ACTIVE_PROCESSING)
        for name, condition in (
            (REQUESTED_INDEX, ACTIVE_REQUESTED),
            (HOUSEKEEPING_INDEX, ACTIVE_HOUSEKEEPING),
            (RUNNING_INDEX, RUNNING_PROCESSING),
        ):
            batch_op.create_index(name, [COLUMN], unique=True, sqlite_where=condition, postgresql_where=condition)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision.

    A project that has a request waiting behind a housekeeping job has two active jobs the old index refuses, so the
    waiting request is cancelled first.
    """
    with op.batch_alter_table(TABLE, schema=None) as batch_op:
        for name, condition in (
            (RUNNING_INDEX, RUNNING_PROCESSING),
            (HOUSEKEEPING_INDEX, ACTIVE_HOUSEKEEPING),
            (REQUESTED_INDEX, ACTIVE_REQUESTED),
        ):
            batch_op.drop_index(name, sqlite_where=condition, postgresql_where=condition)
        batch_op.create_index(
            PROCESSING_INDEX, [COLUMN], unique=True, sqlite_where=ACTIVE_PROCESSING, postgresql_where=ACTIVE_PROCESSING
        )


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted: cancel a request that waits behind another job."""
    op.execute(
        sa.text(
            "UPDATE jobs SET state = 'cancelled' WHERE state = 'queued' "
            "AND kind IN ('measure-book', 'preview-step', 'run-stage') AND EXISTS ("
            'SELECT 1 FROM jobs AS other WHERE other.project_id = jobs.project_id AND other.id <> jobs.id '
            "AND other.kind IN ('collect-versions', 'cut-tiles') AND other.state IN ('queued', 'running'))"
        )
    )
