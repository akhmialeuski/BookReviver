"""Add the content box editor, and drop the frames Margins read as the place of its block on the page.

The step ``geometry.normalize`` reads the box of the content of its input instead of a frame that says where on the page
its block goes. A frame saved for it before meant the place of the block on the page the step made, which no box of the
content of the input can be made of, so the upgrade deletes those edits and the changes of the history that put them on
a page or took them off. The settings of the pages stay, and so do the edits of every other step. The editor column is a
little wider for the name of the new editor.

The downgrade deletes the content box edits, which the previous revision has no editor for, and keeps everything else.

Revision ID: 59387d89514e
Revises: a8b6015ddb1b
Create Date: 2026-10-04 20:55:02.829541
"""

import warnings
from typing import TYPE_CHECKING, Final
from uuid import UUID

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '59387d89514e'
down_revision: str | Sequence[str] | None = 'a8b6015ddb1b'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NORMALIZE = 'geometry.normalize'
PROCESSOR_KEY = 'processor_key'
STEP_ID = 'step_id'
RECT = 'rect'
CONTENT_BOX = 'content-box'
HAND = 'hand'
IGNORE: Final = 'ignore'

GUID = advanced_alchemy.types.guid.GUID(length=16)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
EDITOR_KINDS = ('none', 'rect', 'quad', 'line', 'rotation', 'split', 'mesh', 'brush-mask', 'regions')

# The columns of the tables this revision reads and writes, as the revision before it left them
RECIPES = sa.table('recipes', sa.column('steps', JSON))
STATES = sa.table(
    'page_step_states',
    sa.column('page_id', GUID),
    sa.column('stage', sa.String()),
    sa.column(STEP_ID, GUID),
    sa.column('params', JSON),
    sa.column('kind', sa.String()),
    sa.column('geometry', JSON),
    sa.column('mask_key', sa.String()),
    sa.column('edit_hash', sa.String()),
    sa.column('edit_saved_at', sa.DateTime()),
)
CHANGES = sa.table('page_step_changes', sa.column(STEP_ID, GUID), sa.column('layer', sa.String()))


def upgrade() -> None:
    """Change the schema, then the data, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        schema_upgrades()
        data_upgrades()


def downgrade() -> None:
    """Revert the data, then the schema, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        data_downgrades()
        schema_downgrades()


def schema_upgrades() -> None:
    """Change the schema to this revision."""
    with op.batch_alter_table('page_step_states', schema=None) as batch_op:
        batch_op.alter_column(
            'kind',
            existing_type=sa.VARCHAR(length=10),
            type_=sa.Enum(*EDITOR_KINDS, CONTENT_BOX, name='editorkind', native_enum=False),
            existing_nullable=True,
        )


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    with op.batch_alter_table('page_step_states', schema=None) as batch_op:
        batch_op.alter_column(
            'kind',
            existing_type=sa.Enum(*EDITOR_KINDS, CONTENT_BOX, name='editorkind', native_enum=False),
            type_=sa.VARCHAR(length=10),
            existing_nullable=True,
        )


def _take_edits_away(kind: str, step_ids: Sequence[UUID] | None) -> None:
    """Take the manual edits of a kind away from the states of some steps, and delete the states left with nothing.

    :param kind: The editor of the edits to take away.
    :type kind: str
    :param step_ids: The steps whose states are changed, or None for every step.
    :type step_ids: Sequence[UUID] | None
    """
    bind = op.get_bind()
    edited = STATES.c.kind == kind
    if step_ids is not None:
        edited &= STATES.c[STEP_ID].in_(step_ids)
    states = bind.execute(
        sa.select(STATES.c.page_id, STATES.c.stage, STATES.c[STEP_ID], STATES.c.params).where(edited)
    ).all()
    for state in states:
        own = (
            (STATES.c.page_id == state.page_id) & (STATES.c.stage == state.stage) & (STATES.c[STEP_ID] == state.step_id)
        )
        if state.params:
            bind.execute(
                sa.update(STATES)
                .where(own)
                .values(kind=None, geometry=None, mask_key=None, edit_hash=None, edit_saved_at=None)
            )
        else:
            bind.execute(sa.delete(STATES).where(own))


def data_upgrades() -> None:
    """Take the frames of the normalize steps away, with the changes of the history that moved them."""
    bind = op.get_bind()
    steps = [
        UUID(step[STEP_ID])
        for recipe in bind.execute(sa.select(RECIPES.c.steps)).all()
        for step in recipe.steps
        if step[PROCESSOR_KEY] == NORMALIZE
    ]
    if not steps:
        return
    _take_edits_away(RECT, steps)
    bind.execute(sa.delete(CHANGES).where(CHANGES.c[STEP_ID].in_(steps) & (CHANGES.c.layer == HAND)))


def data_downgrades() -> None:
    """Take the content box edits away, which the editors of the previous revision do not know."""
    _take_edits_away(CONTENT_BOX, None)
