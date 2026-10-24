"""Keep the directory of each book in its library entry.

The directory of a book in the storage was ``projects/<id>``, named by the identifier of the book, and every part of the
application that reached a file of a book built that name from the identifier. The library entry now keeps the name of
the directory, which the storage made, so the storage alone lays the directories out. Every entry the earlier releases
wrote names the directory those releases made, the text of the identifier of its book, and no two entries share one.

The downgrade drops the column, and the books keep their directories, which the earlier releases name the same way.

Revision ID: 4d8abc745604
Revises: 0734365bee74
Create Date: 2026-10-24 14:43:57.203906
"""

import warnings
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

from bookreviver.adapters.persistence.sqlalchemy.migrations.operations import REBUILD

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '4d8abc745604'
down_revision: str | Sequence[str] | None = '0734365bee74'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
LIBRARY: Final = 'library'
DIRECTORY: Final = 'directory'
ONE_BOOK_A_DIRECTORY: Final = 'uq_library_directory'
# The directory of every entry, the text of the identifier of its book, which SQLite keeps as sixteen bytes
NAME_THE_DIRECTORIES: Final = (
    "UPDATE library SET directory = lower(substr(hex(project_id), 1, 8) || '-' || substr(hex(project_id), 9, 4) || '-' "
    "|| substr(hex(project_id), 13, 4) || '-' || substr(hex(project_id), 17, 4) || '-' || substr(hex(project_id), 21))"
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
    """Add the directory to every entry, named as the earlier releases named it, required and unique."""
    with op.batch_alter_table(LIBRARY, schema=None) as batch_op:
        batch_op.add_column(sa.Column(DIRECTORY, sa.String(), nullable=True))
    # The column is filled before the rebuild that makes it required, which copies the rows and checks them
    op.execute(sa.text(NAME_THE_DIRECTORIES))
    with op.batch_alter_table(LIBRARY, schema=None, recreate=REBUILD) as batch_op:
        batch_op.alter_column(DIRECTORY, existing_type=sa.String(), nullable=False)
        batch_op.create_unique_constraint(batch_op.f(ONE_BOOK_A_DIRECTORY), [DIRECTORY])


def schema_downgrades() -> None:
    """Drop the directory of the entries, which the earlier releases build from the identifier of the book."""
    with op.batch_alter_table(LIBRARY, schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f(ONE_BOOK_A_DIRECTORY), type_='unique')
        batch_op.drop_column(DIRECTORY)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it, which the change of the schema already did."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted, which dropping the column does."""
