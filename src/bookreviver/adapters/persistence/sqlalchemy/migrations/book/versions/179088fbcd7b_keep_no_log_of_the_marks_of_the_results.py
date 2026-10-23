"""Keep no log of the marks of the results.

A result keeps its mark and its comment on its version, and ``result_mark_changes`` repeated every change of them with
the values before and after, which nothing reads. The table goes, and the revision logs how many rows of the log go
with it. The downgrade brings the table back empty.

Revision ID: 179088fbcd7b
Revises: 3961f96ad3d8
Create Date: 2026-10-22 21:58:09.164228
"""

import logging
import warnings
from typing import TYPE_CHECKING, Final

import advanced_alchemy.types.datetime
import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '179088fbcd7b'
down_revision: str | Sequence[str] | None = '3961f96ad3d8'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
LOG: Final = 'result_mark_changes'
VERSION_ID: Final = 'version_id'
MARK_VALUES: Final = ('good', 'bad')
COUNT_LOG: Final = sa.text('SELECT count(*) FROM result_mark_changes')
logger = logging.getLogger(__name__)


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


def _mark(column: str) -> sa.Column[str]:
    """Build a column of the log that holds a mark, checked as the revision of the checks named it.

    :param column: Name of the column.
    :type column: str
    :returns: The column.
    :rtype: sa.Column[str]
    """
    checked = sa.Enum(*MARK_VALUES, name=column, native_enum=False, create_constraint=True)
    return sa.Column(column, checked, nullable=True)


def schema_upgrades() -> None:
    """Change the schema to this revision, after logging how many changes of marks go."""
    if count := int(op.get_bind().execute(COUNT_LOG).scalar_one()):
        logger.warning('%d changes of the marks of results go with their log.', count)
    op.drop_table(LOG)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, with an empty log."""
    op.create_table(
        LOG,
        sa.Column('id', advanced_alchemy.types.guid.GUID(length=16), nullable=False),
        sa.Column(VERSION_ID, sa.String(length=16), nullable=False),
        _mark('mark_before'),
        _mark('mark_after'),
        sa.Column('comment_before', sa.String(), server_default='', nullable=False),
        sa.Column('comment_after', sa.String(), server_default='', nullable=False),
        sa.Column('created_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            [VERSION_ID],
            ['page_versions.id'],
            name=op.f('fk_result_mark_changes_version_id_page_versions'),
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_result_mark_changes')),
        sa.UniqueConstraint(VERSION_ID, 'sequence', name=op.f('uq_result_mark_changes_version_id')),
    )


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
