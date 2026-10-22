"""Compute the cover of a book.

The cover of a book is the first page of the kind ``cover``, else its first page, which the application computes when
it reads the book, so ``projects.cover_page_id`` goes with its key, and the revision logs whether the book had a cover
chosen by hand. The downgrade brings the cover column back empty.

Revision ID: 9bcd8efc6540
Revises: 1060478e5d99
Create Date: 2026-10-22 18:05:00.000000
"""

import logging
import warnings
from typing import TYPE_CHECKING

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '9bcd8efc6540'
down_revision: str | Sequence[str] | None = '1060478e5d99'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Whether the project of the book had a cover chosen by hand, which the computed cover replaces
CHOSEN_COVER = text('SELECT count(*) FROM projects WHERE cover_page_id IS NOT NULL')
logger = logging.getLogger(__name__)


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
    """Change the schema to this revision, after logging the cover it drops."""
    if op.get_bind().execute(CHOSEN_COVER).scalar_one():
        logger.warning('The cover chosen by hand goes; the book shows its first page of the kind cover instead.')
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_projects_cover_page_id_pages'), type_='foreignkey')
        batch_op.drop_column('cover_page_id')


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, with an empty cover."""
    with op.batch_alter_table('projects', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cover_page_id', advanced_alchemy.types.guid.GUID(length=16), nullable=True))
        batch_op.create_foreign_key(
            batch_op.f('fk_projects_cover_page_id_pages'), 'pages', ['cover_page_id'], ['id'], ondelete='SET NULL'
        )


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
