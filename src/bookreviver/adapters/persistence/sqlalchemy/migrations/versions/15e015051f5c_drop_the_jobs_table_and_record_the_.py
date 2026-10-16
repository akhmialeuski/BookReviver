"""Drop the jobs table and record the status of every uploaded file.

The state of the background jobs lives in the memory of the API process now, so the ``jobs`` table, its five partial
unique indexes and ``sources.import_job_id`` are dropped. Every file of an upload is a row of ``sources`` with a
status: ``imported`` for a source of the book, ``rejected`` with a reason for a file the import refused, and
``skipped`` for a file a cancelled import never reached. ``kind`` and ``file_type`` may be empty for a file whose type
is unknown. The unique key of a project and a digest holds among the imported sources only, so it becomes a partial
unique index, whose condition autogenerate does not compare and is written here by hand. Existing rows are imported
sources. The downgrade deletes the rejected and skipped rows, which the old schema has no place for, and recreates an
empty ``jobs`` table with its indexes.

Revision ID: 15e015051f5c
Revises: a6cd07915dcf
Create Date: 2026-10-16 20:58:43.731595
"""

import warnings
from typing import TYPE_CHECKING

import advanced_alchemy.types.datetime
import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '15e015051f5c'
down_revision: str | Sequence[str] | None = 'a6cd07915dcf'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IMPORTED_INDEX = 'ix_sources_one_imported_per_digest'
IMPORTED = sa.text("status = 'imported'")
# The rows of the five partial unique indexes of ``jobs``, which the downgrade writes again
ACTIVE_IMPORT = sa.text("kind = 'import-source' AND state IN ('queued', 'running')")
ACTIVE_PREPARE = sa.text("kind = 'prepare-pages' AND state IN ('queued', 'running')")
ACTIVE_REQUESTED = sa.text("kind IN ('measure-book', 'preview-step', 'run-stage') AND state IN ('queued', 'running')")
ACTIVE_HOUSEKEEPING = sa.text("kind IN ('collect-versions', 'cut-tiles') AND state IN ('queued', 'running')")
RUNNING_PROCESSING = sa.text(
    "kind IN ('collect-versions', 'cut-tiles', 'measure-book', 'preview-step', 'run-stage') AND state = 'running'"
)
JOB_INDEXES = (
    ('ix_jobs_one_running_processing', RUNNING_PROCESSING),
    ('ix_jobs_one_active_requested', ACTIVE_REQUESTED),
    ('ix_jobs_one_active_prepare', ACTIVE_PREPARE),
    ('ix_jobs_one_active_import', ACTIVE_IMPORT),
    ('ix_jobs_one_active_housekeeping', ACTIVE_HOUSEKEEPING),
)


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
    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'status',
                sa.Enum('imported', 'rejected', 'skipped', name='sourcestatus', native_enum=False),
                server_default='imported',
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                'rejection',
                sa.Enum(
                    'duplicate',
                    'unreadable',
                    'unsupported-type',
                    'system-file',
                    name='rejectionreason',
                    native_enum=False,
                ),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column('detail', sa.String(), server_default='', nullable=False))
        batch_op.alter_column('kind', existing_type=sa.VARCHAR(length=5), nullable=True)
        batch_op.alter_column('file_type', existing_type=sa.VARCHAR(length=9), nullable=True)
        batch_op.drop_index(batch_op.f('ix_sources_import_job_id'))
        batch_op.drop_constraint(batch_op.f('uq_sources_project_id'), type_='unique')
        batch_op.create_index(IMPORTED_INDEX, ['project_id', 'sha256'], unique=True, sqlite_where=IMPORTED)
        batch_op.drop_constraint(batch_op.f('fk_sources_import_job_id_jobs'), type_='foreignkey')
        batch_op.drop_column('import_job_id')

    with op.batch_alter_table('jobs', schema=None) as batch_op:
        for name, condition in JOB_INDEXES:
            batch_op.drop_index(batch_op.f(name), sqlite_where=condition)
        batch_op.drop_index(batch_op.f('ix_jobs_project_id'))

    op.drop_table('jobs')


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    op.create_table(
        'jobs',
        sa.Column('id', advanced_alchemy.types.guid.GUID(length=16), nullable=False),
        sa.Column('project_id', advanced_alchemy.types.guid.GUID(length=16), nullable=False),
        sa.Column(
            'kind',
            sa.Enum(
                'import-source',
                'prepare-pages',
                'run-stage',
                'preview-step',
                'cut-tiles',
                'collect-versions',
                'measure-book',
                'detect-content',
                name='jobkind',
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            'state',
            sa.Enum('queued', 'running', 'succeeded', 'failed', 'cancelled', name='jobstate', native_enum=False),
            nullable=False,
        ),
        sa.Column('progress_done', sa.Integer(), nullable=False),
        sa.Column('progress_total', sa.Integer(), nullable=False),
        sa.Column('error', sa.String(), nullable=False),
        sa.Column('request', sa.JSON(), nullable=True),
        sa.Column('params', sa.JSON(), server_default=sa.text("'{}'"), nullable=False),
        sa.Column('result', sa.JSON(), nullable=True),
        sa.Column('created_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=False),
        sa.Column('started_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=True),
        sa.Column('finished_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ['project_id'], ['projects.id'], name=op.f('fk_jobs_project_id_projects'), ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_jobs')),
    )
    with op.batch_alter_table('jobs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_jobs_project_id'), ['project_id'], unique=False)
        for name, condition in JOB_INDEXES:
            batch_op.create_index(batch_op.f(name), ['project_id'], unique=True, sqlite_where=condition)

    with op.batch_alter_table('sources', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('import_job_id', advanced_alchemy.types.guid.GUID(length=16), nullable=True),
        )
        batch_op.create_foreign_key(
            batch_op.f('fk_sources_import_job_id_jobs'), 'jobs', ['import_job_id'], ['id'], ondelete='SET NULL'
        )
        batch_op.drop_index(IMPORTED_INDEX, sqlite_where=IMPORTED)
        batch_op.create_unique_constraint(batch_op.f('uq_sources_project_id'), ['project_id', 'sha256'])
        batch_op.create_index(batch_op.f('ix_sources_import_job_id'), ['import_job_id'], unique=False)
        batch_op.alter_column('file_type', existing_type=sa.VARCHAR(length=9), nullable=False)
        batch_op.alter_column('kind', existing_type=sa.VARCHAR(length=5), nullable=False)
        batch_op.drop_column('detail')
        batch_op.drop_column('rejection')
        batch_op.drop_column('status')


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted: delete the files no import took in.

    The old schema holds the imported sources only. A rejected or skipped file may have no kind and no file type, and
    may repeat the digest of an imported source, so keeping it would fail the columns and the unique key it returns to.
    """
    op.execute(sa.text("DELETE FROM sources WHERE status <> 'imported'"))
