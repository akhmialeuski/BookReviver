"""Give each kind of page its own recipe and drop the rules, the pins and the conditions.

A stage that has recipes has exactly one for each kind of page: text, colour picture, black-and-white picture and blank
page. A page is processed by the recipe of its kind, so the active recipe, the variants, the rules that sent pages to
variants, the pin of a recipe to a page and the condition of a step have no place any more.

The upgrade gives the active recipe of each stage of each book the kind text, and adds the three other kinds as copies
of it, with the same steps and the same identifiers of them, so the settings and the edits a page kept for a step stay
its own when the page moves to another kind. A page that the active recipe processed is then linked to the copy of the
kind it has, and stays fresh since the steps that made its result are the same, so a change of a recipe reaches
exactly the pages of its kind. The variants are deleted, and the pages that were processed by one lose
the link to their recipe and become stale, since the steps that made their result are gone. The conditions are taken
out of the steps of the recipes and of the profiles. The rules and the pins are dropped with their table and their
column. The number of the variants deleted is printed by the command.

The downgrade makes the recipe of text pages the active recipe of its stage and the recipes of the other kinds variants
named by the label of their kind, and brings back the empty table of the rules and the column of the pins.

Revision ID: 77356245ed16
Revises: 0a48f30fe1bc
Create Date: 2026-10-07 22:39:14.298392
"""

import warnings
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Final
from uuid import uuid4

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op, util
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '77356245ed16'
down_revision: str | Sequence[str] | None = '0a48f30fe1bc'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IGNORE: Final = 'ignore'
CASCADE: Final = 'CASCADE'
GUID = advanced_alchemy.types.guid.GUID(length=16)
STEPS = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')
STAGE_VALUES = (
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
CONDITION_VALUES = ('plates', 'covers', 'blanks', 'illustrated', 'odd', 'even', 'group')
KIND_ENUM = sa.Enum('text', 'color-picture', 'bw-picture', 'blank', name='recipekind', native_enum=False)

# Names in the tables of the recipes, of the rules and of the stage records
RECIPES_TABLE: Final = 'recipes'
RULES_TABLE: Final = 'recipe_rules'
PAGE_STAGES_TABLE: Final = 'page_stages'
ID: Final = 'id'
PROJECT_ID: Final = 'project_id'
RECIPE_ID: Final = 'recipe_id'
STAGE: Final = 'stage'
KIND: Final = 'kind'
NAME: Final = 'name'
ACTIVE: Final = 'active'
PINNED: Final = 'pinned'
CONDITION: Final = 'condition'
GROUP_LABEL: Final = 'group_label'
ACTIVE_INDEX: Final = 'ix_recipes_one_active'
STAGE_INDEX: Final = 'ix_recipes_project_id_stage'
KIND_KEY: Final = 'uq_recipes_project_id'

# The kinds of page in the order of the recipes of a stage, with the name the recipe of each had as a variant
TEXT: Final = 'text'
COLOR_PICTURE: Final = 'color-picture'
BW_PICTURE: Final = 'bw-picture'
BLANK: Final = 'blank'
KINDS: Final = (
    (TEXT, 'Text'),
    (COLOR_PICTURE, 'Colour picture'),
    (BW_PICTURE, 'Black-and-white picture'),
    (BLANK, 'Blank page'),
)
# The roles of a page that make it a picture whatever the program found on it, as ``PICTURE_KINDS`` has them
PICTURE_ROLES: Final = ('plate', 'frontispiece')
APPLIES_TO: Final = 'applies_to'
STALE: Final = 'stale'
FRESH: Final = 'fresh'

# The columns of the tables this revision reads and writes, as the revision before it left them
RECIPES = sa.table(
    RECIPES_TABLE,
    sa.column(ID, GUID),
    sa.column(PROJECT_ID, GUID),
    sa.column(STAGE, sa.String()),
    sa.column(KIND, KIND_ENUM),
    sa.column(NAME, sa.String()),
    sa.column('steps', STEPS),
    sa.column(ACTIVE, sa.Boolean()),
    sa.column('profile_id', GUID),
    sa.column('created_at', sa.DateTime()),
    sa.column('updated_at', sa.DateTime()),
)
PROFILES = sa.table('recipe_profiles', sa.column(ID, GUID), sa.column('steps', STEPS))
PAGE_STAGES = sa.table(
    PAGE_STAGES_TABLE, sa.column('page_id', GUID), sa.column(RECIPE_ID, GUID), sa.column('state', sa.String())
)
PAGES = sa.table(
    'pages',
    sa.column(ID, GUID),
    sa.column(KIND, sa.String()),
    sa.column('content_type', sa.String()),
    sa.column('content_by_hand', sa.Boolean()),
)

# The kind of recipe that processes a page, restating ``RecipeKind.of`` and ``ContentType.shown_by`` on the columns the
# page has at this revision, since a migration must not import domain code that can change after it: a blank page is
# the blank kind, a page the program or the user found a picture on is that picture, a plate or a frontispiece that
# nobody set by hand is a picture in colour, and any other page is text
RECIPE_KIND_OF_PAGE = sa.case(
    (PAGES.c.kind == BLANK, BLANK),
    (PAGES.c.content_type == COLOR_PICTURE, COLOR_PICTURE),
    (PAGES.c.content_type == BW_PICTURE, BW_PICTURE),
    (sa.and_(PAGES.c.kind.in_(PICTURE_ROLES), PAGES.c.content_by_hand == sa.false()), COLOR_PICTURE),
    else_=TEXT,
)


def upgrade() -> None:
    """Change the schema, the data and the schema again, in the transaction of the migration."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        schema_upgrades()
        data_upgrades()
        with op.batch_alter_table(RECIPES_TABLE, schema=None) as batch_op:
            batch_op.alter_column(KIND, existing_type=KIND_ENUM, nullable=False)
            batch_op.drop_index(batch_op.f(ACTIVE_INDEX), sqlite_where=sa.text(ACTIVE))
            batch_op.drop_index(batch_op.f(STAGE_INDEX))
            batch_op.create_unique_constraint(batch_op.f(KIND_KEY), [PROJECT_ID, STAGE, KIND])
            batch_op.drop_column(NAME)
            batch_op.drop_column(ACTIVE)


def downgrade() -> None:
    """Revert the data and the schema, in the transaction of the migration, which commits or rolls back whole."""
    with warnings.catch_warnings():
        warnings.filterwarnings(IGNORE, category=UserWarning)
        with op.batch_alter_table(RECIPES_TABLE, schema=None) as batch_op:
            batch_op.add_column(sa.Column(ACTIVE, sa.Boolean(), server_default=sa.false(), nullable=False))
            batch_op.add_column(sa.Column(NAME, sa.String(), server_default='', nullable=False))
            batch_op.drop_constraint(batch_op.f(KIND_KEY), type_='unique')
            batch_op.create_index(batch_op.f(STAGE_INDEX), [PROJECT_ID, STAGE], unique=False)
        data_downgrades()
        with op.batch_alter_table(RECIPES_TABLE, schema=None) as batch_op:
            batch_op.create_index(
                batch_op.f(ACTIVE_INDEX),
                [PROJECT_ID, STAGE],
                unique=True,
                sqlite_where=sa.text(ACTIVE),
                postgresql_where=sa.text(ACTIVE),
            )
            batch_op.alter_column(ACTIVE, existing_type=sa.Boolean(), server_default=None)
            batch_op.alter_column(NAME, existing_type=sa.String(), server_default=None)
        schema_downgrades()


def schema_upgrades() -> None:
    """Change the schema to this revision: the column of the kind, empty until the data is moved, no rules, no pins."""
    op.drop_table(RULES_TABLE)
    with op.batch_alter_table(PAGE_STAGES_TABLE, schema=None) as batch_op:
        batch_op.drop_column(PINNED)

    with op.batch_alter_table(RECIPES_TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column(KIND, KIND_ENUM, nullable=True))


def schema_downgrades() -> None:
    """Change the schema back to the previous revision: the pins and the table of the rules, which is empty."""
    with op.batch_alter_table(RECIPES_TABLE, schema=None) as batch_op:
        batch_op.drop_column(KIND)

    with op.batch_alter_table(PAGE_STAGES_TABLE, schema=None) as batch_op:
        batch_op.add_column(sa.Column(PINNED, sa.Boolean(), server_default=sa.false(), nullable=False))

    op.create_table(
        RULES_TABLE,
        sa.Column(ID, GUID, nullable=False),
        sa.Column(PROJECT_ID, GUID, nullable=False),
        sa.Column(STAGE, sa.Enum(*STAGE_VALUES, name=STAGE, native_enum=False), nullable=False),
        sa.Column(CONDITION, sa.Enum(*CONDITION_VALUES, name='rulecondition', native_enum=False), nullable=False),
        sa.Column(GROUP_LABEL, sa.String(), server_default='', nullable=False),
        sa.Column(RECIPE_ID, GUID, nullable=False),
        sa.Column('order', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            [PROJECT_ID], ['projects.id'], name=op.f('fk_recipe_rules_project_id_projects'), ondelete=CASCADE
        ),
        sa.ForeignKeyConstraint(
            [RECIPE_ID], [f'{RECIPES_TABLE}.id'], name=op.f('fk_recipe_rules_recipe_id_recipes'), ondelete=CASCADE
        ),
        sa.PrimaryKeyConstraint(ID, name=op.f('pk_recipe_rules')),
        sa.UniqueConstraint(PROJECT_ID, STAGE, CONDITION, GROUP_LABEL, name=op.f('uq_recipe_rules_project_id')),
    )
    with op.batch_alter_table(RULES_TABLE, schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_recipe_rules_project_id'), [PROJECT_ID], unique=False)
        batch_op.create_index(batch_op.f('ix_recipe_rules_recipe_id'), [RECIPE_ID], unique=False)


def _without_condition(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Take the condition out of stored steps.

    :param steps: The steps of a recipe or of a profile as stored.
    :type steps: list[dict[str, Any]]
    :returns: The steps without the condition.
    :rtype: list[dict[str, Any]]
    """
    return [{key: value for key, value in step.items() if key != APPLIES_TO} for step in steps]


def data_upgrades() -> None:
    """Give the kinds to the recipes, copy the recipe of text pages for the other kinds, and delete the variants."""
    bind = op.get_bind()
    for table in (RECIPES, PROFILES):
        for row in bind.execute(sa.select(table.c.id, table.c.steps)).all():
            bind.execute(sa.update(table).where(table.c.id == row.id).values(steps=_without_condition(row.steps)))

    recipes = bind.execute(sa.select(RECIPES).order_by(RECIPES.c.created_at, RECIPES.c.id)).all()
    by_stage: dict[tuple[Any, str], list[Any]] = {}
    for recipe in recipes:
        by_stage.setdefault((recipe.project_id, recipe.stage), []).append(recipe)
    deleted = 0
    for (project_id, stage), found in by_stage.items():
        text = next((recipe for recipe in found if recipe.active), found[0])
        bind.execute(sa.update(RECIPES).where(RECIPES.c.id == text.id).values(kind=TEXT))
        for variant in (recipe for recipe in found if recipe.id != text.id):
            # The pages a variant processed keep their result, which is out of date now, and are processed by their kind
            bind.execute(
                sa.update(PAGE_STAGES)
                .where(PAGE_STAGES.c.recipe_id == variant.id)
                .values(recipe_id=None, state=sa.case((PAGE_STAGES.c.state == FRESH, STALE), else_=PAGE_STAGES.c.state))
            )
            bind.execute(sa.delete(RECIPES).where(RECIPES.c.id == variant.id))
            deleted += 1
        copies = {kind: uuid4() for kind, _ in KINDS[1:]}
        for index, (kind, name) in enumerate(KINDS[1:], start=1):
            bind.execute(
                sa.insert(RECIPES).values(
                    id=copies[kind],
                    project_id=project_id,
                    stage=stage,
                    kind=kind,
                    name=name,
                    steps=text.steps,
                    active=False,
                    profile_id=text.profile_id,
                    created_at=text.created_at + timedelta(microseconds=index),
                    updated_at=text.updated_at,
                )
            )
        # The copy has the steps of the recipe that made the result, so the result stays fresh under its new recipe
        for kind, copy_id in copies.items():
            bind.execute(
                sa.update(PAGE_STAGES)
                .where(
                    PAGE_STAGES.c.recipe_id == text.id,
                    PAGE_STAGES.c.page_id.in_(sa.select(PAGES.c.id).where(sa.literal(kind) == RECIPE_KIND_OF_PAGE)),
                )
                .values(recipe_id=copy_id)
            )
    # A variant that is deleted is a recipe the user made, so the count is printed by the command and not left in a log
    util.msg(f'Gave the active recipes of {len(by_stage)} stages the kind text and deleted {deleted} variants.')


def data_downgrades() -> None:
    """Make the recipe of text pages the active one, and the recipes of the other kinds variants named by their kind."""
    bind = op.get_bind()
    for kind, name in KINDS:
        bind.execute(sa.update(RECIPES).where(RECIPES.c.kind == kind).values(name=name, active=kind == TEXT))
