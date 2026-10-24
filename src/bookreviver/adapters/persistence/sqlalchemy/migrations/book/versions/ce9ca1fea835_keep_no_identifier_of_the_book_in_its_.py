"""Keep no identifier of the book in its own file.

The one row of ``projects`` held the identifier of its book as its key, so the file named the library entry it belonged
to, and a book imported under a new identifier had its row renamed. The library already names the book of every file,
so the row is keyed by ``id``, always 1, which a check holds, as the one row of ``book_places`` is.

The downgrade takes the identifier back from the name of the directory the file lies in, which is how the stores of the
books lay them out. A file outside the directory of a book, such as a copy made for a test, gets a new identifier.

Revision ID: ce9ca1fea835
Revises: 54f16295b19a
Create Date: 2026-10-24 13:21:10.924281
"""

import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import UUID, uuid4

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

from bookreviver.adapters.persistence.sqlalchemy.migrations.operations import REBUILD

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = 'ce9ca1fea835'
down_revision: str | Sequence[str] | None = '54f16295b19a'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
PROJECTS: Final = 'projects'
ID: Final = 'id'
# The key of the one row of projects, and the check that holds it
ONE_BOOK: Final = 'ck_projects_one_book'
RECORD_ID: Final = 1
# The identifier of a book as SQLite keeps it, sixteen bytes
BOOK_ID_TYPE: Final = advanced_alchemy.types.guid.GUID(length=16)
SET_ID: Final = 'UPDATE projects SET id = :id'


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
    """Key the one row of ``projects`` by 1, which a check holds."""
    # The rebuilt table copies the rows before it checks them, so the key is 1 before the table is rebuilt
    op.execute(sa.text(SET_ID).bindparams(id=RECORD_ID))
    with op.batch_alter_table(PROJECTS, schema=None, recreate=REBUILD) as batch_op:
        batch_op.alter_column(
            ID, existing_type=BOOK_ID_TYPE, type_=sa.Integer(), existing_nullable=False, autoincrement=False
        )
        batch_op.create_check_constraint(op.f(ONE_BOOK), f'{ID} = {RECORD_ID}')


def schema_downgrades() -> None:
    """Key the one row of ``projects`` by the identifier of its book again, read from the directory of the file."""
    with op.batch_alter_table(PROJECTS, schema=None, recreate=REBUILD) as batch_op:
        batch_op.drop_constraint(op.f(ONE_BOOK), type_='check')
        batch_op.alter_column(
            ID, existing_type=sa.Integer(), type_=BOOK_ID_TYPE, existing_nullable=False, autoincrement=False
        )
    op.execute(sa.text(SET_ID).bindparams(id=_book_of_the_file().bytes))


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it, which the change of the key already did."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted, which the change of the key does after it."""


def _book_of_the_file() -> UUID:
    """Return the identifier of the book whose directory holds the file being migrated.

    :returns: The identifier the directory is named by, or a new one for a file outside the directory of a book.
    :rtype: UUID
    """
    name = Path(str(op.get_bind().engine.url.database)).parent.name
    try:
        return UUID(name)
    except ValueError:
        return uuid4()
