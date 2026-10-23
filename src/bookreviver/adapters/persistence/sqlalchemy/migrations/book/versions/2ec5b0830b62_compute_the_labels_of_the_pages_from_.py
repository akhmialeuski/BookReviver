"""Compute the labels of the pages from the sections.

The application computes the label a page shows from the pagination sections when it reads the page, so ``pages`` keeps
only the label written by hand or taken from the scan, as ``manual_label``, null for the number of the sections. The
labels the sections gave are dropped with ``label`` and ``label_manual``. A manual label is never empty, which a check
holds, and no row can break it, since the copy leaves an empty label out.

The downgrade brings ``label`` back holding the manual labels and empty elsewhere, and logs how many pages had a number
of their sections, which the earlier release writes again on the next change of the pages or the sections of the book.

Revision ID: 2ec5b0830b62
Revises: 9bcd8efc6540
Create Date: 2026-10-22 18:46:02.839611
"""

import logging
import warnings
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

from bookreviver.adapters.persistence.sqlalchemy.migrations.operations import REBUILD

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '2ec5b0830b62'
down_revision: str | Sequence[str] | None = '9bcd8efc6540'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PAGES = 'pages'
LABEL = 'label'
LABEL_MANUAL = 'label_manual'
MANUAL_LABEL = 'manual_label'
# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
# The name the naming convention gives the check of a manual label, final so it is not named again
MANUAL_LABEL_GIVEN = 'ck_pages_manual_label_given'
KEEP_MANUAL_LABELS = text("UPDATE pages SET manual_label = label WHERE label_manual AND label <> ''")
RESTORE_LABELS = text("UPDATE pages SET label = coalesce(manual_label, ''), label_manual = manual_label IS NOT NULL")
SECTION_NUMBERS = text('SELECT count(*) FROM pages WHERE manual_label IS NULL')
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


def schema_upgrades() -> None:
    """Change the schema to this revision, moving the manual labels into their column before the old columns go."""
    with op.batch_alter_table(PAGES, schema=None) as batch_op:
        batch_op.add_column(sa.Column(MANUAL_LABEL, sa.String(), nullable=True))
    op.execute(KEEP_MANUAL_LABELS)
    with op.batch_alter_table(PAGES, schema=None, recreate=REBUILD) as batch_op:
        batch_op.drop_column(LABEL)
        batch_op.drop_column(LABEL_MANUAL)
        batch_op.create_check_constraint(op.f(MANUAL_LABEL_GIVEN), "manual_label <> ''")


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, with the manual labels in the old columns."""
    if count := int(op.get_bind().execute(SECTION_NUMBERS).scalar_one()):
        logger.warning('%d pages get an empty label until the pages or the sections of the book change.', count)
    with op.batch_alter_table(PAGES, schema=None) as batch_op:
        batch_op.add_column(sa.Column(LABEL_MANUAL, sa.BOOLEAN(), server_default=sa.text('0'), nullable=False))
        batch_op.add_column(sa.Column(LABEL, sa.VARCHAR(), server_default=sa.text("''"), nullable=False))
    op.execute(RESTORE_LABELS)
    with op.batch_alter_table(PAGES, schema=None) as batch_op:
        batch_op.alter_column(LABEL, server_default=None)
        batch_op.drop_constraint(op.f(MANUAL_LABEL_GIVEN), type_='check')
        batch_op.drop_column(MANUAL_LABEL)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
