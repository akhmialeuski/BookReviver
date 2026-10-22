"""Call the history of the changes of a page page_history.

The table of the changes of the settings and the edits of the steps of a page is the history of the page, so it is
named ``page_history``. SQLite renames a table but keeps the names of its keys, checks and indexes, which the naming
convention takes from the table, so the table is made again under the new name, its rows copied whole, and the old
table dropped. The downgrade does the same the other way.

Revision ID: 6c4ad9b7af37
Revises: 179088fbcd7b
Create Date: 2026-10-22 22:08:16.918525
"""

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
revision: str = '6c4ad9b7af37'
down_revision: str | Sequence[str] | None = '179088fbcd7b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
OLD: Final = 'page_step_changes'
NEW: Final = 'page_history'
PAGE_ID: Final = 'page_id'
COLUMNS: Final = (
    'id, page_id, stage, step_id, layer, scope, group_label, before, after, source, batch_id, undoes_id, created_at, '
    'sequence'
)
STAGES: Final = (
    'import',
    'page-split',
    'page-order',
    'geometry',
    'cleanup',
    'layout',
    'background',
    'recognition',
    'proofreading',
    'typesetting',
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
    """Make the table of the history under ``target``, copy the rows of ``source`` into it and drop ``source``.

    :param source: The table that holds the history now.
    :type source: str
    :param target: The table the history moves to.
    :type target: str
    """
    op.create_table(
        target,
        sa.Column('id', advanced_alchemy.types.guid.GUID(length=16), nullable=False),
        sa.Column(PAGE_ID, advanced_alchemy.types.guid.GUID(length=16), nullable=False),
        sa.Column('stage', _checked('stage', *STAGES), nullable=False),
        sa.Column('step_id', advanced_alchemy.types.guid.GUID(length=16), nullable=False),
        sa.Column('layer', _checked('steplayer', 'settings', 'found', 'hand'), nullable=False),
        sa.Column(
            'scope', _checked('valuescope', 'pages', 'group', 'odd', 'even'), server_default='pages', nullable=False
        ),
        sa.Column('group_label', sa.String(), server_default='', nullable=False),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('source', _checked('changesource', 'user', 'run', 'carry-over', 'undo'), nullable=False),
        sa.Column('batch_id', advanced_alchemy.types.guid.GUID(length=16), nullable=True),
        sa.Column('undoes_id', advanced_alchemy.types.guid.GUID(length=16), nullable=True),
        sa.Column('created_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=False),
        sa.Column('sequence', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint([PAGE_ID], ['pages.id'], name=op.f(f'fk_{target}_page_id_pages'), ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id', name=op.f(f'pk_{target}')),
        sa.UniqueConstraint(PAGE_ID, 'sequence', name=op.f(f'uq_{target}_page_id')),
    )
    op.execute(sa.text(f'INSERT INTO "{target}" ({COLUMNS}) SELECT {COLUMNS} FROM "{source}"'))
    op.drop_table(source)
    with op.batch_alter_table(target, schema=None) as batch_op:
        batch_op.create_index(batch_op.f(f'ix_{target}_batch_id'), ['batch_id'], unique=False)
        batch_op.create_index(batch_op.f(f'ix_{target}_page_id'), [PAGE_ID, 'stage'], unique=False)
        batch_op.create_index(batch_op.f(f'ix_{target}_undoes_id'), ['undoes_id'], unique=False)


def schema_upgrades() -> None:
    """Change the schema to this revision, the rows of the history moved whole."""
    _move(OLD, NEW)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, the rows of the history moved whole."""
    _move(NEW, OLD)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted."""
