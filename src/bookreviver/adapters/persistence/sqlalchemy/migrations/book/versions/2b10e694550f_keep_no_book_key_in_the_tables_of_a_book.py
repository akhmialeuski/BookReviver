"""Keep no book key in the tables of a book.

A file holds one book, so the column ``project_id`` of ``sources``, ``scans``, ``pages``, ``pagination_sections``,
``recipes``, ``step_values`` and ``book_places`` repeated one value in every row, with a key to ``projects`` that the
file of the book never needed. The column goes with its key and its indexes, the unique keys it opened keep the
columns after it, ``step_values`` keeps the rest of its primary key, and ``book_places``, which holds the one place of
the book, is keyed by ``id``, always 1, which a check holds.

The downgrade brings the column back on every row with the identifier of the one project of the file.

Revision ID: 2b10e694550f
Revises: 2ec5b0830b62
Create Date: 2026-10-22 20:06:17.846124
"""

import warnings
from typing import TYPE_CHECKING, Final

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

from bookreviver.adapters.persistence.sqlalchemy.migrations.operations import REBUILD

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '2b10e694550f'
down_revision: str | Sequence[str] | None = '2ec5b0830b62'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
BOOK: Final = 'project_id'
PROJECTS: Final = 'projects'
ID: Final = 'id'
CASCADE: Final = 'CASCADE'
UNIQUE: Final = 'unique'
SOURCES: Final = 'sources'
STEP_VALUES: Final = 'step_values'
BOOK_PLACES: Final = 'book_places'
STAGE: Final = 'stage'
# The partial unique index of the imported sources, whose condition both revisions keep
DIGEST_INDEX: Final = 'ix_sources_one_imported_per_digest'
DIGEST: Final = 'sha256'
IMPORTED: Final = sa.text("status = 'imported'")
# The key of the one row of book_places, and the check that holds it
ONE_PLACE: Final = 'ck_book_places_one_place'
PLACE_ID: Final = '1'
# The tables whose book column has an index of its own
INDEXED: Final = ('pagination_sections', 'scans', SOURCES)
# The tables whose unique key opened with the book column, by their old and new names, and the columns after it
UNIQUE_KEYS: Final = (
    ('pages', 'uq_pages_project_id', 'uq_pages_order_key', ('order_key',)),
    ('recipes', 'uq_recipes_project_id', 'uq_recipes_stage', (STAGE, 'kind')),
)
# Every table with the book column, in the order the upgrade drops it
TABLES: Final = (BOOK_PLACES, 'pages', 'pagination_sections', 'recipes', 'scans', SOURCES, STEP_VALUES)
STEP_VALUES_KEY: Final = ('step_id', 'scope', 'group_label')
FILL_BOOK: Final = 'UPDATE "{table}" SET project_id = (SELECT id FROM projects)'


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


def _foreign_key(table: str) -> str:
    """Name the key of the book column of a table, as the naming convention gave it.

    :param table: The table.
    :type table: str
    :returns: The name of the foreign key.
    :rtype: str
    """
    return f'fk_{table}_{BOOK}_{PROJECTS}'


def _index(table: str, column: str) -> str:
    """Name the index of a column of a table, as the naming convention gives it.

    :param table: The table.
    :type table: str
    :param column: The first column of the index.
    :type column: str
    :returns: The name of the index.
    :rtype: str
    """
    return f'ix_{table}_{column}'


def _primary(table: str) -> str:
    """Name the primary key of a table, as the naming convention gives it.

    :param table: The table.
    :type table: str
    :returns: The name of the primary key.
    :rtype: str
    """
    return f'pk_{table}'


def schema_upgrades() -> None:
    """Change the schema to this revision."""
    for table in TABLES:
        with op.batch_alter_table(table, schema=None, recreate=REBUILD) as batch_op:
            if table in INDEXED:
                batch_op.drop_index(batch_op.f(_index(table, BOOK)))
            for unique_table, old, new, columns in UNIQUE_KEYS:
                if unique_table == table:
                    batch_op.drop_constraint(batch_op.f(old), type_=UNIQUE)
                    batch_op.create_unique_constraint(batch_op.f(new), list(columns))
            if table == SOURCES:
                batch_op.drop_index(batch_op.f(DIGEST_INDEX), sqlite_where=IMPORTED)
                batch_op.create_index(DIGEST_INDEX, [DIGEST], unique=True, sqlite_where=IMPORTED)
            if table == STEP_VALUES:
                batch_op.drop_index(batch_op.f(_index(STEP_VALUES, BOOK)))
                batch_op.create_index(batch_op.f(_index(STEP_VALUES, STAGE)), [STAGE], unique=False)
            if table == BOOK_PLACES:
                batch_op.add_column(
                    sa.Column(ID, sa.Integer(), autoincrement=False, nullable=False, server_default=PLACE_ID)
                )
            batch_op.drop_constraint(batch_op.f(_foreign_key(table)), type_='foreignkey')
            batch_op.drop_column(BOOK)
            if table == STEP_VALUES:
                batch_op.create_primary_key(batch_op.f(_primary(STEP_VALUES)), list(STEP_VALUES_KEY))
            if table == BOOK_PLACES:
                batch_op.create_primary_key(batch_op.f(_primary(BOOK_PLACES)), [ID])
                batch_op.create_check_constraint(op.f(ONE_PLACE), f'{ID} = {PLACE_ID}')
    with op.batch_alter_table(BOOK_PLACES, schema=None) as batch_op:
        batch_op.alter_column(ID, server_default=None)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, the book column holding the one project of the file."""
    for table in reversed(TABLES):
        with op.batch_alter_table(table, schema=None) as batch_op:
            batch_op.add_column(sa.Column(BOOK, advanced_alchemy.types.guid.GUID(length=16), nullable=True))
        op.execute(sa.text(FILL_BOOK.format(table=table)))
        with op.batch_alter_table(table, schema=None, recreate=REBUILD) as batch_op:
            batch_op.alter_column(BOOK, existing_type=advanced_alchemy.types.guid.GUID(length=16), nullable=False)
            batch_op.create_foreign_key(batch_op.f(_foreign_key(table)), PROJECTS, [BOOK], [ID], ondelete=CASCADE)
            if table in INDEXED:
                batch_op.create_index(batch_op.f(_index(table, BOOK)), [BOOK], unique=False)
            for unique_table, old, new, columns in UNIQUE_KEYS:
                if unique_table == table:
                    batch_op.drop_constraint(batch_op.f(new), type_=UNIQUE)
                    batch_op.create_unique_constraint(batch_op.f(old), [BOOK, *columns])
            if table == SOURCES:
                batch_op.drop_index(DIGEST_INDEX, sqlite_where=IMPORTED)
                batch_op.create_index(batch_op.f(DIGEST_INDEX), [BOOK, DIGEST], unique=True, sqlite_where=IMPORTED)
            if table == STEP_VALUES:
                batch_op.drop_index(batch_op.f(_index(STEP_VALUES, STAGE)))
                batch_op.create_index(batch_op.f(_index(STEP_VALUES, BOOK)), [BOOK, STAGE], unique=False)
                batch_op.create_primary_key(batch_op.f(_primary(STEP_VALUES)), [BOOK, *STEP_VALUES_KEY])
            if table == BOOK_PLACES:
                batch_op.drop_constraint(op.f(ONE_PLACE), type_='check')
                batch_op.drop_column(ID)
                batch_op.create_primary_key(batch_op.f(_primary(BOOK_PLACES)), [BOOK])


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
