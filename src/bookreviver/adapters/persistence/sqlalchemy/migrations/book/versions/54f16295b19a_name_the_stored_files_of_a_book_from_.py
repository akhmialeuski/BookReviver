"""Name the stored files of a book from its own directory.

A book kept the keys of some of its files with the directory of the book in front, ``projects/<id>/assets/...``: the
mask of a manual edit in ``page_step_states.mask_key`` and in the snapshots of ``step_changes``, and the mesh of a
dewarping in the transform of ``page_versions``. A book imported under a new identifier kept the old directory in
them, so it looked for its masks and meshes in the directory of the book it was exported from. The keys lose the
directory of the book, which only the stores of the book add, and the schema stays as it was.

The downgrade puts the directory of the one book of the file back in front of every such key.

Revision ID: 54f16295b19a
Revises: ba87bba973f4
Create Date: 2026-10-24 11:36:24.533381
"""

import warnings
from typing import TYPE_CHECKING, Final

from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '54f16295b19a'
down_revision: str | Sequence[str] | None = 'ba87bba973f4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
# A key that names the directory of its book, and one that names a derived file from that directory
WITH_BOOK: Final = "'projects/%'"
FROM_BOOK: Final = "'assets/%'"
# The key without ``projects/<id>/``: the identifier ends at the first slash after the ten characters of ``projects/``
WITHOUT_BOOK: Final = "substr({key}, instr(substr({key}, 10), '/') + 10)"
# The identifier of the one book of the file, which SQLite keeps as sixteen bytes, written as the text of a UUID
BOOK_ID: Final = (
    "(SELECT lower(substr(hex(id), 1, 8) || '-' || substr(hex(id), 9, 4) || '-' || substr(hex(id), 13, 4) || '-' "
    "|| substr(hex(id), 17, 4) || '-' || substr(hex(id), 21)) FROM projects)"
)
WITH_DIRECTORY: Final = "'projects/' || " + BOOK_ID + " || '/' || {key}"
# The statements that rewrite a key held whole by a column, and one held by a JSON column under a path
KEY_UPDATE: Final = 'UPDATE {table} SET {column} = {value} WHERE {column} LIKE {pattern}'
JSON_VALUE: Final = "json_extract({column}, '{path}')"
JSON_UPDATE: Final = "UPDATE {table} SET {column} = json_set({column}, '{path}', {value}) WHERE {stored} LIKE {pattern}"
# The columns that hold a key whole, and the JSON columns that hold one under a path, by their table
KEY_COLUMNS: Final = (('page_step_states', 'mask_key'),)
JSON_KEYS: Final = (
    ('page_versions', 'transform', '$.mesh_key'),
    ('step_changes', 'before', '$.mask_key'),
    ('step_changes', 'after', '$.mask_key'),
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
    """Change the schema to this revision, which keeps the schema as it was."""


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, which keeps the schema as it was."""


def data_upgrades() -> None:
    """Take the directory of the book off every stored key that has it."""
    _rewrite(WITHOUT_BOOK, WITH_BOOK)


def data_downgrades() -> None:
    """Put the directory of the book of the file in front of every stored key of a derived file."""
    _rewrite(WITH_DIRECTORY, FROM_BOOK)


def _rewrite(new_key: str, pattern: str) -> None:
    """Rewrite every stored key that matches a pattern, in the columns and the JSON paths that hold keys.

    :param new_key: SQL expression of the new key, with ``{key}`` standing for the stored one.
    :type new_key: str
    :param pattern: SQL ``LIKE`` pattern of the keys to rewrite.
    :type pattern: str
    """
    for table, column in KEY_COLUMNS:
        op.execute(KEY_UPDATE.format(table=table, column=column, value=new_key.format(key=column), pattern=pattern))
    for table, column, path in JSON_KEYS:
        stored = JSON_VALUE.format(column=column, path=path)
        value = new_key.format(key=stored)
        statement = JSON_UPDATE.format(
            table=table, column=column, path=path, value=value, stored=stored, pattern=pattern
        )
        op.execute(statement)
