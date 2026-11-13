"""Keep what the pages hold for a step under its type.

A step has one type at a time, the processor ``steps.type_key`` names, and the type of a step can be replaced and come
back. What the pages keep for a step, the settings and the edits of ``page_step_states``, the values of
``step_values`` and ``group_values`` and the changes of ``step_changes``, gains a column ``type_key`` that names the
type the row was written for, which joins the primary key of the three tables of state, so a step keeps the rows of
each type it has had. Every row takes the type its step has, which is the only type it has had. The layer of a change
may now be the type of the step itself.

The downgrade keeps the rows of the active type of each step and drops the rows of the earlier types, with the changes
of the types and their places in the histories of the pages, and logs how many rows it dropped.

Revision ID: ff7beab877bc
Revises: edca77c509a2
Create Date: 2026-11-13 18:42:07.446263
"""

import logging
import warnings
from typing import TYPE_CHECKING, Final

import sqlalchemy as sa
from alembic import op

from bookreviver.adapters.persistence.sqlalchemy.migrations.operations import REBUILD

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = 'ff7beab877bc'
down_revision: str | Sequence[str] | None = 'edca77c509a2'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
TYPE_KEY: Final = 'type_key'
STEP_ID: Final = 'step_id'
CHANGES: Final = 'step_changes'
HISTORY: Final = 'page_history'
# The tables of what a page keeps for a step, with the columns of their primary keys before and after the revision
KEYED: Final = {
    'page_step_states': (('page_id', 'stage', STEP_ID), ('page_id', 'stage', STEP_ID, TYPE_KEY)),
    'step_values': ((STEP_ID, 'scope'), (STEP_ID, TYPE_KEY, 'scope')),
    'group_values': ((STEP_ID, 'group_id'), (STEP_ID, TYPE_KEY, 'group_id')),
}
# The check of the layer of a change, as the naming convention names it, and its values before and after the revision
LAYER_CHECK: Final = 'ck_step_changes_steplayer'
LAYER: Final = 'layer'
LAYERS_BEFORE: Final = ('settings', 'found', 'hand')
TYPE_LAYER: Final = 'type'
LAYERS_AFTER: Final = (*LAYERS_BEFORE, TYPE_LAYER)
# The rows of a table that are of an earlier type than their step's, which only exist after the revision
EARLIER_TYPE: Final = 'type_key <> (SELECT steps.type_key FROM steps WHERE steps.id = {table}.step_id)'


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


def _layers(values: Sequence[str]) -> str:
    """Give the condition of the check of the layer of a change.

    :param values: The layers the check lets through.
    :type values: Sequence[str]
    :returns: The condition over the column of the layer.
    :rtype: str
    """
    quoted = ', '.join(f"'{value}'" for value in values)
    return f'{LAYER} IN ({quoted})'


def schema_upgrades() -> None:
    """Change the schema to this revision, every row given the type of its step on the way, which loses nothing."""
    connection = op.get_bind()
    tables = (*KEYED, CHANGES)
    for table in tables:
        op.add_column(table, sa.Column(TYPE_KEY, sa.String(), nullable=True))
    for table in tables:
        connection.execute(
            sa.text(f'UPDATE {table} SET {TYPE_KEY} = (SELECT steps.type_key FROM steps WHERE steps.id = {STEP_ID})')
        )
    for table, (_, key) in KEYED.items():
        with op.batch_alter_table(table, schema=None, recreate=REBUILD) as batch_op:
            batch_op.alter_column(TYPE_KEY, existing_type=sa.String(), nullable=False)
            batch_op.create_primary_key(batch_op.f(f'pk_{table}'), list(key))
    with op.batch_alter_table(CHANGES, schema=None, recreate=REBUILD) as batch_op:
        batch_op.alter_column(TYPE_KEY, existing_type=sa.String(), nullable=False)
        batch_op.drop_constraint(op.f(LAYER_CHECK), type_='check')
        batch_op.create_check_constraint(op.f(LAYER_CHECK), _layers(LAYERS_AFTER))


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, whose rows are of the active type of their step alone."""
    with op.batch_alter_table(CHANGES, schema=None, recreate=REBUILD) as batch_op:
        batch_op.drop_constraint(op.f(LAYER_CHECK), type_='check')
        batch_op.create_check_constraint(op.f(LAYER_CHECK), _layers(LAYERS_BEFORE))
        batch_op.drop_column(TYPE_KEY)
    for table, (key, _) in KEYED.items():
        with op.batch_alter_table(table, schema=None, recreate=REBUILD) as batch_op:
            batch_op.drop_column(TYPE_KEY)
            batch_op.create_primary_key(batch_op.f(f'pk_{table}'), list(key))


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it, which the schema change has typed already."""


def data_downgrades() -> None:
    """Drop the rows of the earlier types of the steps and the changes of the types, before the column goes."""
    connection = op.get_bind()
    dropped = 0
    for table in KEYED:
        dropped += connection.execute(sa.text(f'DELETE FROM {table} WHERE {EARLIER_TYPE.format(table=table)}')).rowcount
    gone = f'SELECT id FROM {CHANGES} WHERE {LAYER} = :layer OR {EARLIER_TYPE.format(table=CHANGES)}'
    # The migration runs with the foreign keys off, so the places of the changes in the histories go first
    connection.execute(sa.text(f'DELETE FROM {HISTORY} WHERE change_id IN ({gone})'), {LAYER: TYPE_LAYER})
    changes = connection.execute(sa.text(f'DELETE FROM {CHANGES} WHERE id IN ({gone})'), {LAYER: TYPE_LAYER}).rowcount
    logger.warning(
        '%d rows the pages kept for earlier types of their steps and %d changes of those types or of the types of the '
        'steps were dropped.',
        dropped,
        changes,
    )
