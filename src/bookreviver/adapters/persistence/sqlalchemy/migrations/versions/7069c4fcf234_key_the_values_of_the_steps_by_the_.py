"""Key the values of the steps by the project and give each page the recipe of its kind.

Books built from one profile share the identifiers of its steps, so the values of a step kept for the odd pages, the
even pages or a group are told apart by the project as well, and ``step_values`` takes ``project_id`` first in its key.
The table is copied for the change, since SQLite cannot alter a key.

A page that the recipe of text processed is linked to the recipe of the kind it has, and stays fresh since the recipes
of the kinds were made as copies of the recipe of text with the same steps, so a change of a recipe reaches exactly the
pages of its kind. The kind of a page is told by the same rule as ``RecipeKind.of``, restated on the columns of the
page.

The downgrade brings back the key without the project, which fails when two books keep values for the same step in the
same scope, and links the pages of every kind back to the recipe of text of their stage.

Revision ID: 7069c4fcf234
Revises: b8e258b21279
Create Date: 2026-10-08 17:10:12.508344
"""

import warnings
from typing import TYPE_CHECKING, Final

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '7069c4fcf234'
down_revision: str | Sequence[str] | None = 'b8e258b21279'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

IGNORE: Final = 'ignore'
GUID = advanced_alchemy.types.guid.GUID(length=16)

# The key of the values of the steps, with the project and without it
VALUES_TABLE: Final = 'step_values'
VALUES_KEY: Final = 'pk_step_values'
KEY_WITH_PROJECT: Final = ('project_id', 'step_id', 'scope', 'group_label')
KEY_WITHOUT_PROJECT: Final = ('step_id', 'scope', 'group_label')

# The kinds of page, as the recipes and the pages store them
TEXT: Final = 'text'
COLOR_PICTURE: Final = 'color-picture'
BW_PICTURE: Final = 'bw-picture'
BLANK: Final = 'blank'
# The roles of a page that make it a picture whatever the program found on it, as ``PICTURE_KINDS`` has them
PICTURE_ROLES: Final = ('plate', 'frontispiece')

# The columns of the tables the data change reads and writes, as the revision before left them
RECIPES = sa.table(
    'recipes',
    sa.column('id', GUID),
    sa.column('project_id', GUID),
    sa.column('stage', sa.String()),
    sa.column('kind', sa.String()),
)
PAGE_STAGES = sa.table(
    'page_stages', sa.column('page_id', GUID), sa.column('stage', sa.String()), sa.column('recipe_id', GUID)
)
PAGES = sa.table(
    'pages',
    sa.column('id', GUID),
    sa.column('project_id', GUID),
    sa.column('kind', sa.String()),
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


def replace_values_key(columns: Sequence[str]) -> None:
    """Drop the primary key of the values of the steps and create it again over other columns.

    :param columns: The columns the new key is made of, in the order of the key.
    :type columns: Sequence[str]
    """
    with op.batch_alter_table(VALUES_TABLE, schema=None, recreate='always') as batch_op:
        batch_op.drop_constraint(op.f(VALUES_KEY), type_='primary')
        batch_op.create_primary_key(op.f(VALUES_KEY), list(columns))


def schema_upgrades() -> None:
    """Change the schema to this revision."""
    replace_values_key(KEY_WITH_PROJECT)


def schema_downgrades() -> None:
    """Change the schema back to the previous revision."""
    replace_values_key(KEY_WITHOUT_PROJECT)


def data_upgrades() -> None:
    """Link each page that the recipe of text processes to the recipe of its kind, which stays as fresh as it was."""
    text_recipe_ids = sa.select(RECIPES.c.id).where(RECIPES.c.kind == TEXT)
    recipe_of_kind = (
        sa.select(RECIPES.c.id)
        .where(
            PAGES.c.id == PAGE_STAGES.c.page_id,
            RECIPES.c.project_id == PAGES.c.project_id,
            RECIPES.c.stage == PAGE_STAGES.c.stage,
            RECIPES.c.kind == RECIPE_KIND_OF_PAGE,
        )
        .scalar_subquery()
    )
    op.execute(
        sa.update(PAGE_STAGES)
        .where(PAGE_STAGES.c.recipe_id.in_(text_recipe_ids), recipe_of_kind.is_not(None))
        .values(recipe_id=recipe_of_kind)
    )


def data_downgrades() -> None:
    """Link each page that a recipe of another kind processes to the recipe of text of its stage."""
    other_recipe_ids = sa.select(RECIPES.c.id).where(RECIPES.c.kind != TEXT)
    kind_recipe = RECIPES.alias('kind_recipe')
    text_recipe = RECIPES.alias('text_recipe')
    recipe_of_text = (
        sa.select(text_recipe.c.id)
        .where(
            kind_recipe.c.id == PAGE_STAGES.c.recipe_id,
            text_recipe.c.project_id == kind_recipe.c.project_id,
            text_recipe.c.stage == kind_recipe.c.stage,
            text_recipe.c.kind == TEXT,
        )
        .scalar_subquery()
    )
    op.execute(
        sa.update(PAGE_STAGES)
        .where(PAGE_STAGES.c.recipe_id.in_(other_recipe_ids), recipe_of_text.is_not(None))
        .values(recipe_id=recipe_of_text)
    )
