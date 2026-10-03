"""Keep the settings and the edit of a step in one state of the page and add the history of the steps.

A page keeps for each step of a recipe one state: the fields of the parameters it changes for itself and the manual edit
the step reads. The state replaces the table of the manual edits, and the history of the steps is a table of its own
that is only added to.

The upgrade makes both tables and moves every row of ``page_edits`` into ``page_step_states`` with its edit in the
columns of the manual layer and no settings, then drops ``page_edits``. Nothing is lost, since every column of an edit
has a column in the state.

The downgrade makes ``page_edits`` again and moves back every state that has an edit. The settings of the pages and the
history of the steps have no place in the older schema and are dropped, and the number of the settings dropped is
printed by the command.

Revision ID: 2fd4f715e483
Revises: 5ab9b35523fe
Create Date: 2026-10-03 21:21:50.226113
"""

import warnings
from typing import TYPE_CHECKING, Final

import advanced_alchemy.types.datetime
import advanced_alchemy.types.guid
import advanced_alchemy.types.json
import sqlalchemy as sa
from alembic import op, util
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '2fd4f715e483'
down_revision: str | Sequence[str] | None = '5ab9b35523fe'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Names of the tables and of the columns the two tables of edits and of states share
EDITS_TABLE = 'page_edits'
STATES_TABLE = 'page_step_states'
CHANGES_TABLE = 'page_step_changes'
ID = 'id'
PAGE_ID = 'page_id'
STAGE = 'stage'
STEP_ID = 'step_id'
KIND = 'kind'
GEOMETRY = 'geometry'
MASK_KEY = 'mask_key'
EDIT_HASH = 'edit_hash'
UPDATED_AT = 'updated_at'
SETTINGS = 'params'
BATCH_ID = 'batch_id'
EDIT_SAVED_AT = 'edit_saved_at'
PAGES_ID = 'pages.id'
CASCADE = 'CASCADE'
IGNORE: Final = 'ignore'
CHANGE_PAGE_INDEX = 'ix_page_step_changes_page_id'
CHANGE_BATCH_INDEX = 'ix_page_step_changes_batch_id'

GUID = advanced_alchemy.types.guid.GUID(length=16)
DATETIME = advanced_alchemy.types.datetime.DateTimeUTC(timezone=True)
JSON_OBJECT = (
    sa.JSON()
    .with_variant(postgresql.JSONB(astext_type=Text()), 'cockroachdb')
    .with_variant(advanced_alchemy.types.json.ORA_JSONB(), 'oracle')
    .with_variant(postgresql.JSONB(astext_type=Text()), 'postgresql')
)
STAGE_TYPE = sa.Enum(
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
    name='stage',
    native_enum=False,
)
EDITOR_TYPE = sa.Enum(
    'none',
    'rect',
    'quad',
    'line',
    'rotation',
    'split',
    'mesh',
    'brush-mask',
    'regions',
    name='editorkind',
    native_enum=False,
)

# The columns of the two tables the rows are moved between, which are all the columns of an edit
EDITS = sa.table(
    EDITS_TABLE,
    sa.column(PAGE_ID),
    sa.column(STAGE),
    sa.column(STEP_ID),
    sa.column(KIND),
    sa.column(GEOMETRY),
    sa.column(MASK_KEY),
    sa.column(EDIT_HASH),
    sa.column(UPDATED_AT),
)
STATES = sa.table(
    STATES_TABLE,
    sa.column(PAGE_ID),
    sa.column(STAGE),
    sa.column(STEP_ID),
    sa.column(KIND),
    sa.column(GEOMETRY),
    sa.column(MASK_KEY),
    sa.column(EDIT_HASH),
    sa.column(EDIT_SAVED_AT),
    sa.column(UPDATED_AT),
    sa.column(SETTINGS, JSON_OBJECT),
)


def upgrade() -> None:
    """Make the tables, move the edits, then drop the table of the edits, in the transaction of the migration."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        schema_upgrades()
        data_upgrades()
        op.drop_table(EDITS_TABLE)


def downgrade() -> None:
    """Make the table of the edits, move the edits back, then drop the new tables, in the migration transaction."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        op.create_table(
            EDITS_TABLE,
            sa.Column(PAGE_ID, GUID, nullable=False),
            sa.Column(STAGE, STAGE_TYPE, nullable=False),
            sa.Column(STEP_ID, GUID, nullable=False),
            sa.Column(KIND, EDITOR_TYPE, nullable=False),
            sa.Column(GEOMETRY, JSON_OBJECT, nullable=True),
            sa.Column(MASK_KEY, sa.String(), nullable=True),
            sa.Column(EDIT_HASH, sa.String(), nullable=False),
            sa.Column(UPDATED_AT, DATETIME, nullable=False),
            sa.ForeignKeyConstraint([PAGE_ID], [PAGES_ID], name=op.f('fk_page_edits_page_id_pages'), ondelete=CASCADE),
            sa.PrimaryKeyConstraint(PAGE_ID, STAGE, STEP_ID, name=op.f('pk_page_edits')),
        )
        data_downgrades()
        schema_downgrades()


def schema_upgrades() -> None:
    """Change the schema to this revision: the state of a step on a page, and the history of the steps."""
    op.create_table(
        CHANGES_TABLE,
        sa.Column(ID, GUID, nullable=False),
        sa.Column(PAGE_ID, GUID, nullable=False),
        sa.Column(STAGE, STAGE_TYPE, nullable=False),
        sa.Column(STEP_ID, GUID, nullable=False),
        sa.Column('layer', sa.Enum('settings', 'found', 'hand', name='steplayer', native_enum=False), nullable=False),
        sa.Column('before', JSON_OBJECT, nullable=True),
        sa.Column('after', JSON_OBJECT, nullable=True),
        sa.Column(
            'source',
            sa.Enum('user', 'run', 'carry-over', 'reset', name='changesource', native_enum=False),
            nullable=False,
        ),
        sa.Column(BATCH_ID, GUID, nullable=True),
        sa.Column('created_at', DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            [PAGE_ID], [PAGES_ID], name=op.f('fk_page_step_changes_page_id_pages'), ondelete=CASCADE
        ),
        sa.PrimaryKeyConstraint(ID, name=op.f('pk_page_step_changes')),
    )
    with op.batch_alter_table(CHANGES_TABLE, schema=None) as batch_op:
        batch_op.create_index(batch_op.f(CHANGE_BATCH_INDEX), [BATCH_ID], unique=False)
        batch_op.create_index(batch_op.f(CHANGE_PAGE_INDEX), [PAGE_ID, STAGE], unique=False)

    op.create_table(
        STATES_TABLE,
        sa.Column(PAGE_ID, GUID, nullable=False),
        sa.Column(STAGE, STAGE_TYPE, nullable=False),
        sa.Column(STEP_ID, GUID, nullable=False),
        sa.Column(SETTINGS, JSON_OBJECT, server_default='{}', nullable=False),
        sa.Column(KIND, EDITOR_TYPE, nullable=True),
        sa.Column(GEOMETRY, JSON_OBJECT, nullable=True),
        sa.Column(MASK_KEY, sa.String(), nullable=True),
        sa.Column(EDIT_HASH, sa.String(), nullable=True),
        sa.Column(EDIT_SAVED_AT, DATETIME, nullable=True),
        sa.Column(UPDATED_AT, DATETIME, nullable=False),
        sa.ForeignKeyConstraint(
            [PAGE_ID], [PAGES_ID], name=op.f('fk_page_step_states_page_id_pages'), ondelete=CASCADE
        ),
        sa.PrimaryKeyConstraint(PAGE_ID, STAGE, STEP_ID, name=op.f('pk_page_step_states')),
    )


def schema_downgrades() -> None:
    """Change the schema back to the previous revision: no state of a step and no history of the steps."""
    op.drop_table(STATES_TABLE)
    with op.batch_alter_table(CHANGES_TABLE, schema=None) as batch_op:
        batch_op.drop_index(batch_op.f(CHANGE_PAGE_INDEX))
        batch_op.drop_index(batch_op.f(CHANGE_BATCH_INDEX))

    op.drop_table(CHANGES_TABLE)


def data_upgrades() -> None:
    """Move every edit into the state of its step on its page, with no settings, which the column defaults to."""
    columns = [PAGE_ID, STAGE, STEP_ID, KIND, GEOMETRY, MASK_KEY, EDIT_HASH, EDIT_SAVED_AT, UPDATED_AT]
    moved = sa.select(
        EDITS.c.page_id,
        EDITS.c.stage,
        EDITS.c.step_id,
        EDITS.c.kind,
        EDITS.c.geometry,
        EDITS.c.mask_key,
        EDITS.c.edit_hash,
        EDITS.c.updated_at,
        EDITS.c.updated_at,
    )
    op.get_bind().execute(sa.insert(STATES).from_select(columns, moved))


def data_downgrades() -> None:
    """Move every edit of a state back into the table of the edits, and count the settings that are dropped."""
    bind = op.get_bind()
    columns = [PAGE_ID, STAGE, STEP_ID, KIND, GEOMETRY, MASK_KEY, EDIT_HASH, UPDATED_AT]
    moved = sa.select(
        STATES.c.page_id,
        STATES.c.stage,
        STATES.c.step_id,
        STATES.c.kind,
        STATES.c.geometry,
        STATES.c.mask_key,
        STATES.c.edit_hash,
        STATES.c.edit_saved_at,
    ).where(STATES.c.kind.is_not(None))
    bind.execute(sa.insert(EDITS).from_select(columns, moved))
    # A setting of a page is work the user did, so the count is printed by the command and not left in a log
    dropped = sum(len(row.params) for row in bind.execute(sa.select(STATES.c.params)))
    util.msg(f'Dropped {dropped} settings of pages, which the older schema has no place for.')
