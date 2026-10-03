"""Name the manual edits by the step of a recipe.

A step of a recipe gets an identifier and a condition, and a manual edit names the step that reads it instead of the key
of its processor, so two steps of one processor keep their own edits.

The upgrade gives every stored step of a recipe and of a profile a new identifier and the condition that processes
every page. It then moves each edit to the step of its processor in the recipe the page was last processed by, or in the
active recipe of the stage for a page that was not processed yet. An edit whose recipe has no step of its processor is
deleted, and the number of those is printed by the command. The files of the masks stay where they are, since an edit
keeps the key of its mask.

The downgrade moves each edit back to the key of the processor of its step, and keeps one edit when two steps of one
processor had one. An edit whose step is in no recipe of the stage is deleted. The identifiers and the conditions are
taken out of the steps.

Revision ID: 5ab9b35523fe
Revises: 075dd13dca68
Create Date: 2026-10-03 14:44:47.496365
"""

import warnings
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op, util
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '5ab9b35523fe'
down_revision: str | Sequence[str] | None = '075dd13dca68'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Keys of a step in the JSON of a recipe, and the condition that processes every page
PROCESSOR_KEY = 'processor_key'
STEP_ID = 'step_id'
APPLIES_TO = 'applies_to'
ALL_PAGES = 'all'

# Names in the table of the edits
EDITS_TABLE = 'page_edits'
EDITS_KEY = 'pk_page_edits'
PAGE_ID = 'page_id'
STAGE = 'stage'
PRIMARY = 'primary'
IGNORE: Final = 'ignore'

GUID = advanced_alchemy.types.guid.GUID(length=16)
STEPS = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

# The columns of the tables this revision reads and writes, as the revision before it left them
RECIPES = sa.table(
    'recipes',
    sa.column('id', GUID),
    sa.column('project_id', GUID),
    sa.column(STAGE, sa.String()),
    sa.column('steps', STEPS),
    sa.column('active', sa.Boolean()),
)
PROFILES = sa.table('recipe_profiles', sa.column('id', GUID), sa.column('steps', STEPS))
PAGES = sa.table('pages', sa.column('id', GUID), sa.column('project_id', GUID))
PAGE_STAGES = sa.table(
    'page_stages', sa.column(PAGE_ID, GUID), sa.column(STAGE, sa.String()), sa.column('recipe_id', GUID)
)
EDITS = sa.table(
    EDITS_TABLE,
    sa.column(PAGE_ID, GUID),
    sa.column(STAGE, sa.String()),
    sa.column(PROCESSOR_KEY, sa.String()),
    sa.column(STEP_ID, GUID),
)


def upgrade() -> None:
    """Change the schema, the data and the key, in the transaction of the migration, which commits or rolls back."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        schema_upgrades()
        data_upgrades()
        with op.batch_alter_table(EDITS_TABLE, schema=None) as batch_op:
            batch_op.drop_constraint(EDITS_KEY, type_=PRIMARY)
            batch_op.alter_column(STEP_ID, existing_type=GUID, nullable=False)
            batch_op.drop_column(PROCESSOR_KEY)
            batch_op.create_primary_key(EDITS_KEY, [PAGE_ID, STAGE, STEP_ID])


def downgrade() -> None:
    """Revert the key, the data and the schema, in the transaction of the migration, which commits or rolls back."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        with op.batch_alter_table(EDITS_TABLE, schema=None) as batch_op:
            batch_op.add_column(sa.Column(PROCESSOR_KEY, sa.String(), nullable=True))
            batch_op.drop_constraint(EDITS_KEY, type_=PRIMARY)
        data_downgrades()
        schema_downgrades()


def schema_upgrades() -> None:
    """Change the schema to this revision: the column that names the step, empty until the data is moved."""
    with op.batch_alter_table(EDITS_TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column(STEP_ID, GUID, nullable=True))


def schema_downgrades() -> None:
    """Change the schema back to the previous revision: the key by the processor and no column for the step."""
    with op.batch_alter_table(EDITS_TABLE, schema=None) as batch_op:
        batch_op.alter_column(PROCESSOR_KEY, existing_type=sa.String(), nullable=False)
        batch_op.drop_column(STEP_ID)
        batch_op.create_primary_key(EDITS_KEY, [PAGE_ID, STAGE, PROCESSOR_KEY])


def data_upgrades() -> None:
    """Give the steps their identifiers and conditions, and move the edits to the steps of their processors."""
    bind = op.get_bind()
    for table in (RECIPES, PROFILES):
        for row in bind.execute(sa.select(table.c.id, table.c.steps)).all():
            steps = [
                {**step, STEP_ID: step.get(STEP_ID) or str(uuid4()), APPLIES_TO: step.get(APPLIES_TO, ALL_PAGES)}
                for step in row.steps
            ]
            bind.execute(sa.update(table).where(table.c.id == row.id).values(steps=steps))

    steps_of = {row.id: row.steps for row in bind.execute(sa.select(RECIPES.c.id, RECIPES.c.steps)).all()}
    active = {
        (row.project_id, row.stage): row.id
        for row in bind.execute(sa.select(RECIPES.c.id, RECIPES.c.project_id, RECIPES.c.stage).where(RECIPES.c.active))
    }
    project_of = {row.id: row.project_id for row in bind.execute(sa.select(PAGES.c.id, PAGES.c.project_id)).all()}
    processed = {
        (row.page_id, row.stage): row.recipe_id
        for row in bind.execute(sa.select(PAGE_STAGES.c.page_id, PAGE_STAGES.c.stage, PAGE_STAGES.c.recipe_id)).all()
        if row.recipe_id is not None
    }
    moved = dropped = 0
    for edit in bind.execute(sa.select(EDITS.c.page_id, EDITS.c.stage, EDITS.c.processor_key)).all():
        recipe_id = processed.get((edit.page_id, edit.stage)) or active.get((project_of.get(edit.page_id), edit.stage))
        step_id = next(
            (step[STEP_ID] for step in steps_of.get(recipe_id, ()) if step[PROCESSOR_KEY] == edit.processor_key), None
        )
        match = (EDITS.c.page_id == edit.page_id) & (EDITS.c.stage == edit.stage)
        match &= EDITS.c.processor_key == edit.processor_key
        if step_id is None:
            bind.execute(sa.delete(EDITS).where(match))
            dropped += 1
        else:
            bind.execute(sa.update(EDITS).where(match).values(step_id=UUID(step_id)))
            moved += 1
    # An edit that is dropped is work the user did, so the count is printed by the command and not left in a log
    util.msg(f'Moved {moved} manual edits to the steps of their processors, dropped {dropped} with no such step.')


def data_downgrades() -> None:
    """Move the edits back to the keys of their processors, and take the identifiers and conditions out of the steps."""
    bind = op.get_bind()
    processor_of: dict[tuple[Any, str, str | None], str] = {}
    for row in bind.execute(sa.select(RECIPES.c.project_id, RECIPES.c.stage, RECIPES.c.steps)).all():
        for step in row.steps:
            processor_of.setdefault((row.project_id, row.stage, step.get(STEP_ID)), step[PROCESSOR_KEY])
    project_of = {row.id: row.project_id for row in bind.execute(sa.select(PAGES.c.id, PAGES.c.project_id)).all()}
    kept: set[tuple[Any, str, str]] = set()
    moved = dropped = 0
    for edit in bind.execute(sa.select(EDITS.c.page_id, EDITS.c.stage, EDITS.c.step_id)).all():
        processor_key = processor_of.get((project_of.get(edit.page_id), edit.stage, str(edit.step_id)))
        match = (EDITS.c.page_id == edit.page_id) & (EDITS.c.stage == edit.stage) & (EDITS.c.step_id == edit.step_id)
        if processor_key is None or (edit.page_id, edit.stage, processor_key) in kept:
            bind.execute(sa.delete(EDITS).where(match))
            dropped += 1
        else:
            kept.add((edit.page_id, edit.stage, processor_key))
            bind.execute(sa.update(EDITS).where(match).values(processor_key=processor_key))
            moved += 1
    util.msg(f'Returned {moved} manual edits to the keys of their processors, dropped {dropped}.')
    for table in (RECIPES, PROFILES):
        for row in bind.execute(sa.select(table.c.id, table.c.steps)).all():
            steps = [
                {key: value for key, value in step.items() if key not in {STEP_ID, APPLIES_TO}} for step in row.steps
            ]
            bind.execute(sa.update(table).where(table.c.id == row.id).values(steps=steps))
