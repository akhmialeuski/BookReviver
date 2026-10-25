"""Name the table of the record of a book book_records.

The one row that describes a book was kept in a table called ``projects`` after the time the application called a book
a project. SQLite renames a table but keeps the names of its keys and checks, which the naming convention takes from
the table, so the table is made again under the new name, its row copied whole, and the old table dropped. Nothing
refers to the table, so no key of another table changes. The downgrade does the same the other way.

Revision ID: 07e2df4566fa
Revises: acac97b29548
Create Date: 2026-10-25 18:15:17.857871
"""

import warnings
from typing import TYPE_CHECKING, Final

import advanced_alchemy.types.datetime
import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '07e2df4566fa'
down_revision: str | Sequence[str] | None = 'acac97b29548'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
OLD: Final = 'projects'
NEW: Final = 'book_records'
COLUMNS: Final = (
    'id, title, subtitle, parallel_titles, original_title, contributors, publisher, printer, publication_place, '
    'publication_year, edition, censorship, series, series_number, volume, languages, orthography, script, '
    'printed_pagination, height_cm, illustrations, binding, identifiers, subjects, rights, copy_holder, copy_notes, '
    'notes, image_policy, created_at, updated_at, version'
)
EMPTY_TEXT: Final = ''
EMPTY_LIST: Final = '[]'


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


def _checked(name: str, *values: str) -> sa.Enum:
    """Build the type of a column of one enum, whose check the naming convention names after the type.

    :param name: Name of the type.
    :type name: str
    :param values: The values the column may hold.
    :type values: str
    :returns: The type.
    :rtype: sa.Enum
    """
    return sa.Enum(*values, name=name, native_enum=False, create_constraint=True)


def _move(source: str, target: str) -> None:
    """Make the table of the record under ``target``, copy the row of ``source`` into it and drop ``source``.

    :param source: The table that holds the record now.
    :type source: str
    :param target: The table the record moves to.
    :type target: str
    """
    op.create_table(
        target,
        sa.Column('id', sa.Integer(), autoincrement=False, nullable=False),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('subtitle', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('parallel_titles', sa.JSON(), server_default=EMPTY_LIST, nullable=False),
        sa.Column('original_title', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('contributors', sa.JSON(), server_default=EMPTY_LIST, nullable=False),
        sa.Column('publisher', sa.String(), nullable=False),
        sa.Column('printer', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('publication_place', sa.String(), nullable=False),
        sa.Column('publication_year', sa.String(), nullable=False),
        sa.Column('edition', sa.String(), nullable=False),
        sa.Column('censorship', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('series', sa.String(), nullable=False),
        sa.Column('series_number', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('volume', sa.String(), nullable=False),
        sa.Column('languages', sa.JSON(), server_default=EMPTY_LIST, nullable=False),
        sa.Column('orthography', _checked('orthography', 'unknown', 'pre-reform', 'modern'), nullable=False),
        sa.Column(
            'script',
            _checked('script', 'unknown', 'cyrillic', 'latin', 'mixed'),
            server_default='unknown',
            nullable=False,
        ),
        sa.Column('printed_pagination', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('height_cm', sa.Integer(), nullable=True),
        sa.Column('illustrations', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('binding', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('identifiers', sa.JSON(), server_default=EMPTY_LIST, nullable=False),
        sa.Column('subjects', sa.JSON(), server_default=EMPTY_LIST, nullable=False),
        sa.Column(
            'rights',
            _checked('rightsstatus', 'unknown', 'public-domain', 'in-copyright'),
            server_default='unknown',
            nullable=False,
        ),
        sa.Column('copy_holder', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('copy_notes', sa.String(), server_default=EMPTY_TEXT, nullable=False),
        sa.Column('notes', sa.String(), nullable=False),
        sa.Column('image_policy', _checked('imagepolicy', 'compact', 'lossless'), nullable=False),
        sa.Column('created_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=False),
        sa.Column('updated_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=False),
        sa.Column('version', sa.Integer(), server_default=sa.text('0'), nullable=False),
        sa.CheckConstraint('id = 1', name=op.f(f'ck_{target}_one_book')),
        sa.PrimaryKeyConstraint('id', name=op.f(f'pk_{target}')),
    )
    op.execute(sa.text(f'INSERT INTO "{target}" ({COLUMNS}) SELECT {COLUMNS} FROM "{source}"'))
    op.drop_table(source)


def schema_upgrades() -> None:
    """Change the schema to this revision, the row of the record moved whole."""
    _move(OLD, NEW)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, the row of the record moved whole."""
    _move(NEW, OLD)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
