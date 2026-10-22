"""Keep each change of a step once.

A change of the values of the odd pages, the even pages or a group was written into the history of every page it
reached, the step, the scope, the group, both values, the source, the batch and the time repeated on each. Every change
is now one row of ``step_changes``, and ``page_history`` places it in the history of each page it reached, keyed by
page and sequence. The entries of one batch that hold the same change become that one change, under the identifier of
the entry of its first page by identifier, and an undo names the change it takes back. The downgrade gives each page
an entry of its own again, the first page keeping the identifier of the change, so the two directions agree.

Revision ID: e5d75f899dce
Revises: 6ca3e79586aa
Create Date: 2026-10-22 23:40:00.000000
"""

import warnings
from typing import TYPE_CHECKING, Any, Final

import advanced_alchemy.types.datetime
import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = 'e5d75f899dce'
down_revision: str | Sequence[str] | None = '6ca3e79586aa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
CHANGES: Final = 'step_changes'
HISTORY: Final = 'page_history'
# The table of the history while the revision moves its rows, under a name of its own
MOVED: Final = 'page_history_moved'
ID: Final = 'id'
PAGE_ID: Final = 'page_id'
SEQUENCE: Final = 'sequence'
CHANGE_ID: Final = 'change_id'
GROUP_ID: Final = 'group_id'
BATCH_ID: Final = 'batch_id'
UNDOES_ID: Final = 'undoes_id'
STAGE: Final = 'stage'
CASCADE: Final = 'CASCADE'
GROUP_KEY: Final = 'page_groups.id'
PAGE_KEY: Final = 'pages.id'
HISTORY_PAGE: Final = 'fk_page_history_page_id_pages'
HISTORY_KEY: Final = 'pk_page_history'
HISTORY_UNIQUE: Final = 'uq_page_history_page_id'
GROUP_OF_A_GROUP: Final = "(scope = 'group') = (group_id IS NOT NULL)"
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
LAYERS: Final = ('settings', 'found', 'hand')
SCOPES: Final = ('pages', 'group', 'odd', 'even')
SOURCES: Final = ('user', 'run', 'carry-over', 'undo')
# The columns of a change, which the history held for each page before this revision
CHANGE_COLUMNS: Final = 'stage, step_id, layer, scope, group_id, before, after, source, batch_id'
# The change each entry of the history is part of: an entry of the page's own layer is a change of its own, and the
# entries of one batch that change the same values from the same to the same are one change, that of the first page
CHANGE_OF: Final = sa.text(
    'CREATE TEMP TABLE change_of AS SELECT h.id AS entry_id, '
    "CASE WHEN h.scope = 'pages' THEN h.id ELSE ("
    'SELECT o.id FROM page_history_moved o WHERE o.scope = h.scope AND o.step_id = h.step_id '
    'AND o.stage = h.stage AND o.layer = h.layer AND o.group_id IS h.group_id AND o.source = h.source '
    'AND coalesce(o.batch_id, o.id) = coalesce(h.batch_id, h.id) AND o.before IS h.before AND o.after IS h.after '
    'AND o.created_at = h.created_at ORDER BY o.page_id LIMIT 1) END AS change_id FROM page_history_moved h'
)
MOVE_CHANGES: Final = sa.text(
    f'INSERT INTO step_changes (id, {CHANGE_COLUMNS}, undoes_id, created_at) '
    f'SELECT h.id, {", ".join(f"h.{column.strip()}" for column in CHANGE_COLUMNS.split(","))}, '
    'coalesce((SELECT c.change_id FROM change_of c WHERE c.entry_id = h.undoes_id), h.undoes_id), h.created_at '
    'FROM page_history_moved h WHERE h.id IN (SELECT change_id FROM change_of)'
)
MOVE_LINKS: Final = sa.text(
    'INSERT INTO page_history (page_id, sequence, change_id) '
    'SELECT h.page_id, h.sequence, c.change_id FROM page_history_moved h JOIN change_of c ON c.entry_id = h.id'
)
# The entry of each page a change reached: the first page keeps the identifier of the change, the others get one each
ENTRY_OF: Final = sa.text(
    'CREATE TEMP TABLE entry_of AS SELECT l.page_id, l.change_id, '
    'CASE WHEN l.page_id = (SELECT min(m.page_id) FROM page_history_moved m WHERE m.change_id = l.change_id) '
    'THEN l.change_id ELSE randomblob(16) END AS entry_id FROM page_history_moved l'
)
MOVE_ENTRIES: Final = sa.text(
    f'INSERT INTO page_history (id, page_id, {CHANGE_COLUMNS}, undoes_id, created_at, sequence) '
    f'SELECT e.entry_id, l.page_id, {", ".join(f"c.{column.strip()}" for column in CHANGE_COLUMNS.split(","))}, '
    'coalesce((SELECT u.entry_id FROM entry_of u WHERE u.change_id = c.undoes_id AND u.page_id = l.page_id), '
    'c.undoes_id), c.created_at, l.sequence FROM page_history_moved l JOIN step_changes c ON c.id = l.change_id '
    'JOIN entry_of e ON e.page_id = l.page_id AND e.change_id = l.change_id'
)
DROP_CHANGE_OF: Final = sa.text('DROP TABLE change_of')
DROP_ENTRY_OF: Final = sa.text('DROP TABLE entry_of')


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


def _guid(name: str, *, nullable: bool) -> sa.Column[Any]:
    """Build a column of an identifier.

    :param name: Name of the column.
    :type name: str
    :param nullable: Whether the column may be empty.
    :type nullable: bool
    :returns: The column.
    :rtype: sa.Column[Any]
    """
    return sa.Column(name, advanced_alchemy.types.guid.GUID(length=16), nullable=nullable)


def _change_columns() -> list[sa.Column[Any]]:
    """Build the columns of a change but its identifiers, which both the change and the old history hold.

    :returns: The columns.
    :rtype: list[sa.Column[Any]]
    """
    return [
        sa.Column(STAGE, _checked(STAGE, *STAGES), nullable=False),
        _guid('step_id', nullable=False),
        sa.Column('layer', _checked('steplayer', *LAYERS), nullable=False),
        sa.Column('scope', _checked('valuescope', *SCOPES), nullable=False),
        sa.Column(GROUP_ID, sa.Integer(), nullable=True),
        sa.Column('before', sa.JSON(), nullable=True),
        sa.Column('after', sa.JSON(), nullable=True),
        sa.Column('source', _checked('changesource', *SOURCES), nullable=False),
        _guid(BATCH_ID, nullable=True),
        _guid(UNDOES_ID, nullable=True),
        sa.Column('created_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=False),
    ]


def schema_upgrades() -> None:
    """Change the schema to this revision, each change moved into a row of its own."""
    op.rename_table(HISTORY, MOVED)
    op.create_table(
        CHANGES,
        _guid(ID, nullable=False),
        *_change_columns(),
        sa.CheckConstraint(GROUP_OF_A_GROUP, name=op.f('ck_step_changes_group_of_a_group')),
        sa.ForeignKeyConstraint([GROUP_ID], [GROUP_KEY], name=op.f('fk_step_changes_group_id_page_groups')),
        sa.PrimaryKeyConstraint(ID, name=op.f('pk_step_changes')),
    )
    op.create_table(
        HISTORY,
        _guid(PAGE_ID, nullable=False),
        sa.Column(SEQUENCE, sa.Integer(), autoincrement=False, nullable=False),
        _guid(CHANGE_ID, nullable=False),
        sa.ForeignKeyConstraint(
            [CHANGE_ID], ['step_changes.id'], name=op.f('fk_page_history_change_id_step_changes'), ondelete=CASCADE
        ),
        sa.ForeignKeyConstraint([PAGE_ID], [PAGE_KEY], name=op.f(HISTORY_PAGE), ondelete=CASCADE),
        sa.PrimaryKeyConstraint(PAGE_ID, SEQUENCE, name=op.f(HISTORY_KEY)),
        sa.UniqueConstraint(PAGE_ID, CHANGE_ID, name=op.f(HISTORY_UNIQUE)),
    )
    op.execute(CHANGE_OF)
    op.execute(MOVE_CHANGES)
    op.execute(MOVE_LINKS)
    op.execute(DROP_CHANGE_OF)
    op.drop_table(MOVED)
    for column in (BATCH_ID, GROUP_ID, UNDOES_ID):
        op.create_index(op.f(f'ix_step_changes_{column}'), CHANGES, [column], unique=False)
    op.create_index(op.f('ix_page_history_change_id'), HISTORY, [CHANGE_ID], unique=False)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, an entry of each change written for every page it reached."""
    op.rename_table(HISTORY, MOVED)
    op.create_table(
        HISTORY,
        _guid(ID, nullable=False),
        _guid(PAGE_ID, nullable=False),
        *_change_columns(),
        sa.Column(SEQUENCE, sa.Integer(), nullable=False),
        sa.CheckConstraint(GROUP_OF_A_GROUP, name=op.f('ck_page_history_group_of_a_group')),
        sa.ForeignKeyConstraint([GROUP_ID], [GROUP_KEY], name=op.f('fk_page_history_group_id_page_groups')),
        sa.ForeignKeyConstraint([PAGE_ID], [PAGE_KEY], name=op.f(HISTORY_PAGE), ondelete=CASCADE),
        sa.PrimaryKeyConstraint(ID, name=op.f(HISTORY_KEY)),
        sa.UniqueConstraint(PAGE_ID, SEQUENCE, name=op.f(HISTORY_UNIQUE)),
    )
    op.execute(ENTRY_OF)
    op.execute(MOVE_ENTRIES)
    op.execute(DROP_ENTRY_OF)
    op.drop_table(MOVED)
    op.drop_table(CHANGES)
    for column in (BATCH_ID, GROUP_ID, UNDOES_ID):
        op.create_index(op.f(f'ix_page_history_{column}'), HISTORY, [column], unique=False)
    op.create_index(op.f('ix_page_history_page_id'), HISTORY, [PAGE_ID, STAGE], unique=False)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it, which the schema change has moved already."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted, which the schema change moves back itself."""
