"""Keep the groups of the pages as rows.

A group was its label, written into every page of the group, into the key of every value of the group and into every
change of those values. The group is now a row of ``page_groups``, which the three tables name by its key. The values
of a group move to ``group_values``, keyed by step and group, so ``step_values`` keeps the odd and the even pages under
a key with no empty label in it. The revision stops before any change when a row holds a group the new checks refuse.
The downgrade writes the labels back from the rows.

Revision ID: 6ca3e79586aa
Revises: 6c4ad9b7af37
Create Date: 2026-10-22 23:13:11.657332
"""

import warnings
from typing import TYPE_CHECKING, Final

import advanced_alchemy.types.datetime
import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

from bookreviver.adapters.persistence.sqlalchemy.migrations.operations import REBUILD

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '6ca3e79586aa'
down_revision: str | Sequence[str] | None = '6c4ad9b7af37'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
GROUPS: Final = 'page_groups'
GROUP_VALUES: Final = 'group_values'
STEP_VALUES: Final = 'step_values'
PAGES: Final = 'pages'
HISTORY: Final = 'page_history'
GROUP_ID: Final = 'group_id'
GROUP_LABEL: Final = 'group_label'
ID: Final = 'id'
LABEL: Final = 'label'
STEP_ID: Final = 'step_id'
SCOPE: Final = 'scope'
STAGE: Final = 'stage'
CHECK: Final = 'check'
# Server default of the label column that the downgrade brings back, as SQLite reports the previous revision's
NO_LABEL: Final = "('')"
STEP_VALUES_KEY: Final = 'pk_step_values'
PARITY_CHECK: Final = 'ck_step_values_parity_scope'
GROUP_CHECK: Final = 'ck_page_history_group_of_a_group'
INDEX_NAME: Final = 'ix_{table}_group_id'
FOREIGN_KEY_NAME: Final = 'fk_{table}_group_id_page_groups'
KEY_OF_LABEL: Final = 'UPDATE {table} SET group_id = (SELECT id FROM page_groups WHERE label = {table}.group_label)'
LABEL_OF_KEY: Final = (
    "UPDATE {table} SET group_label = coalesce((SELECT label FROM page_groups WHERE id = {table}.group_id), '')"
)
# The tables that name a group, by label before this revision and by key after it
NAMING: Final = (PAGES, HISTORY)
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
GROUP_OF_A_GROUP: Final = "(scope = 'group') = (group_id IS NOT NULL)"
PARITY_SCOPE: Final = "scope IN ('odd', 'even')"
# The rows the checks of this revision would refuse: a group without a label, or a label outside a group
REFUSED: Final = (
    (HISTORY, "(scope = 'group') <> (group_label <> '')"),
    (STEP_VALUES, "(scope = 'group') <> (group_label <> '')"),
)
LABELS: Final = sa.text(
    'INSERT INTO page_groups (label) SELECT label FROM ('
    'SELECT group_label AS label FROM pages UNION SELECT group_label FROM step_values '
    "UNION SELECT group_label FROM page_history) WHERE label <> '' ORDER BY label"
)
MOVE_GROUP_VALUES: Final = sa.text(
    'INSERT INTO group_values (step_id, group_id, stage, params, updated_at) '
    'SELECT v.step_id, g.id, v.stage, v.params, v.updated_at FROM step_values v '
    "JOIN page_groups g ON g.label = v.group_label WHERE v.scope = 'group'"
)
DROP_GROUP_VALUES: Final = sa.text("DELETE FROM step_values WHERE scope = 'group'")
RETURN_GROUP_VALUES: Final = sa.text(
    'INSERT INTO step_values (step_id, scope, group_label, stage, params, updated_at) '
    "SELECT v.step_id, 'group', g.label, v.stage, v.params, v.updated_at FROM group_values v "
    'JOIN page_groups g ON g.id = v.group_id'
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


def _refuse_ungrouped() -> None:
    """Stop the revision when a row would break a check it adds, before anything changes.

    :raises RuntimeError: If a change or a value of a group has no label, or one of another scope has a label.
    """
    connection = op.get_bind()
    refused = {
        table: count
        for table, condition in REFUSED
        if (count := int(connection.execute(sa.text(f'SELECT count(*) FROM {table} WHERE {condition}')).scalar_one()))
    }
    if refused:
        err_msg = f'Rows name a group against their scope, by table: {refused}. Nothing was changed.'
        raise RuntimeError(err_msg)


def schema_upgrades() -> None:
    """Change the schema to this revision, the groups moved into rows of their own."""
    _refuse_ungrouped()
    op.create_table(
        GROUPS,
        sa.Column(ID, sa.Integer(), nullable=False),
        sa.Column(LABEL, sa.String(), nullable=False),
        sa.CheckConstraint("label <> ''", name=op.f('ck_page_groups_label_given')),
        sa.PrimaryKeyConstraint(ID, name=op.f('pk_page_groups')),
        sa.UniqueConstraint(LABEL, name=op.f('uq_page_groups_label')),
    )
    op.execute(LABELS)
    op.create_table(
        GROUP_VALUES,
        sa.Column(STEP_ID, advanced_alchemy.types.guid.GUID(length=16), nullable=False),
        sa.Column(GROUP_ID, sa.Integer(), nullable=False),
        sa.Column(STAGE, sa.Enum(*STAGES, name=STAGE, native_enum=False, create_constraint=True), nullable=False),
        sa.Column('params', sa.JSON(), server_default='{}', nullable=False),
        sa.Column('updated_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=False),
        sa.ForeignKeyConstraint([GROUP_ID], ['page_groups.id'], name=op.f('fk_group_values_group_id_page_groups')),
        sa.PrimaryKeyConstraint(STEP_ID, GROUP_ID, name=op.f('pk_group_values')),
    )
    with op.batch_alter_table(GROUP_VALUES, schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_group_values_group_id'), [GROUP_ID], unique=False)
        batch_op.create_index(batch_op.f('ix_group_values_stage'), [STAGE], unique=False)
    op.execute(MOVE_GROUP_VALUES)
    op.execute(DROP_GROUP_VALUES)
    with op.batch_alter_table(STEP_VALUES, schema=None, recreate=REBUILD) as batch_op:
        batch_op.drop_column(GROUP_LABEL)
        batch_op.create_primary_key(batch_op.f(STEP_VALUES_KEY), [STEP_ID, SCOPE])
        batch_op.create_check_constraint(op.f(PARITY_CHECK), PARITY_SCOPE)
    for table in NAMING:
        op.add_column(table, sa.Column(GROUP_ID, sa.Integer(), nullable=True))
        op.execute(sa.text(KEY_OF_LABEL.format(table=table)))
        with op.batch_alter_table(table, schema=None, recreate=REBUILD) as batch_op:
            batch_op.drop_column(GROUP_LABEL)
            batch_op.create_index(batch_op.f(INDEX_NAME.format(table=table)), [GROUP_ID], unique=False)
            batch_op.create_foreign_key(batch_op.f(FOREIGN_KEY_NAME.format(table=table)), GROUPS, [GROUP_ID], [ID])
            if table == HISTORY:
                batch_op.create_check_constraint(op.f(GROUP_CHECK), GROUP_OF_A_GROUP)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, the label of each group written back into every row."""
    for table in NAMING:
        op.add_column(table, sa.Column(GROUP_LABEL, sa.VARCHAR(), server_default=sa.text(NO_LABEL), nullable=False))
        op.execute(sa.text(LABEL_OF_KEY.format(table=table)))
        with op.batch_alter_table(table, schema=None, recreate=REBUILD) as batch_op:
            if table == HISTORY:
                batch_op.drop_constraint(op.f(GROUP_CHECK), type_=CHECK)
            batch_op.drop_constraint(batch_op.f(FOREIGN_KEY_NAME.format(table=table)), type_='foreignkey')
            batch_op.drop_index(batch_op.f(INDEX_NAME.format(table=table)))
            batch_op.drop_column(GROUP_ID)
    with op.batch_alter_table(STEP_VALUES, schema=None, recreate=REBUILD) as batch_op:
        batch_op.drop_constraint(op.f(PARITY_CHECK), type_=CHECK)
        batch_op.add_column(sa.Column(GROUP_LABEL, sa.VARCHAR(), server_default=sa.text(NO_LABEL), nullable=False))
        batch_op.create_primary_key(batch_op.f(STEP_VALUES_KEY), [STEP_ID, SCOPE, GROUP_LABEL])
    op.execute(RETURN_GROUP_VALUES)
    op.drop_table(GROUP_VALUES)
    op.drop_table(GROUPS)


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it, which the schema change has moved already."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted, which the schema change moves back itself."""
