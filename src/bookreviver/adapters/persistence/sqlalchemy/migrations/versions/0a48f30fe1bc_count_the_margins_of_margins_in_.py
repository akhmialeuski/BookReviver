"""Count the margins of Margins in millimetres.

The four margins of the step ``geometry.normalize`` were numbers of pixels and are lengths of the paper now, in
millimetres. The schema stays, and every stored value of a margin is converted once, in the places that hold the
parameters of the step: the steps of the recipes and of the recipe profiles, the settings a page keeps for the step, and
the history of those settings, whose undo writes a layer back as it was.

A pixel has no length without a resolution, which one value of a recipe or of a page does not carry, so the conversion
takes the one every scan of a book is most often made at, 300 dpi. A margin of 150 pixels becomes 12.7 millimetres, and
the margins a book measured are measured again by the button of the measure, which writes them in millimetres from the
resolution of the pages. The versions the step already made keep the parameters they ran with, since they record what
was run. The processor version of the step is raised with this change, so the pages it made are marked as out of date.

The downgrade converts the margins back at the same resolution and rounds them to whole pixels.

Revision ID: 0a48f30fe1bc
Revises: 59387d89514e
Create Date: 2026-10-05 00:41:59.844308
"""

import warnings
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = '0a48f30fe1bc'
down_revision: str | Sequence[str] | None = '59387d89514e'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NORMALIZE = 'geometry.normalize'
PROCESSOR_KEY = 'processor_key'
PARAMS = 'params'
STEP_ID = 'step_id'
SETTINGS = 'settings'
IGNORE: Final = 'ignore'
MARGINS = ('margin_top', 'margin_bottom', 'margin_inner', 'margin_outer')
# The resolution a margin in pixels is taken to have been set at, and the bounds of the margins on either side
MM_PER_INCH = 25.4
ASSUMED_DPI = 300.0
MAX_MARGIN_MM = 50.0
MAX_MARGIN_PX = 10_000

GUID = advanced_alchemy.types.guid.GUID(length=16)
JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql')

# The columns of the tables this revision reads and writes, as the revision before it left them
RECIPES = sa.table('recipes', sa.column('id', GUID), sa.column('steps', JSON))
PROFILES = sa.table('recipe_profiles', sa.column('id', GUID), sa.column('steps', JSON))
STATES = sa.table(
    'page_step_states',
    sa.column('page_id', GUID),
    sa.column('stage', sa.String()),
    sa.column(STEP_ID, GUID),
    sa.column(PARAMS, JSON),
)
CHANGES = sa.table(
    'page_step_changes',
    sa.column('id', GUID),
    sa.column(STEP_ID, GUID),
    sa.column('layer', sa.String()),
    sa.column('before', JSON),
    sa.column('after', JSON),
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


def schema_upgrades() -> None:
    """Change the schema to this revision, which keeps it as it is."""


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, which keeps it as it is."""


def _to_millimetres(pixels: float) -> float:
    """Convert a margin in pixels to millimetres at the assumed resolution, to a tenth, within the bound.

    :param pixels: The margin in pixels.
    :type pixels: float
    :returns: The margin in millimetres.
    :rtype: float
    """
    return min(round(pixels / ASSUMED_DPI * MM_PER_INCH, 1), MAX_MARGIN_MM)


def _to_pixels(millimetres: float) -> int:
    """Convert a margin in millimetres to whole pixels at the assumed resolution, within the bound.

    :param millimetres: The margin in millimetres.
    :type millimetres: float
    :returns: The margin in pixels.
    :rtype: int
    """
    return min(round(millimetres / MM_PER_INCH * ASSUMED_DPI), MAX_MARGIN_PX)


def _converted(params: Mapping[str, Any], convert: Callable[[float], float]) -> dict[str, Any]:
    """Convert the margins a set of parameters holds and leave every other field as it is.

    :param params: The parameters of the step, or the fields a page changes for it.
    :type params: Mapping[str, Any]
    :param convert: The conversion of one margin.
    :type convert: Callable[[float], float]
    :returns: The parameters with each margin that is a number converted.
    :rtype: dict[str, Any]
    """
    return {
        name: convert(value)
        if name in MARGINS and isinstance(value, int | float) and not isinstance(value, bool)
        else value
        for name, value in params.items()
    }


def _convert_steps(table: sa.TableClause, convert: Callable[[float], float]) -> list[UUID]:
    """Convert the margins of every normalize step of the recipes or the profiles in a table.

    :param table: The table of the recipes or of the recipe profiles.
    :type table: sa.TableClause
    :param convert: The conversion of one margin.
    :type convert: Callable[[float], float]
    :returns: The identifiers the normalize steps of the table have, which the states of the pages name them by.
    :rtype: list[UUID]
    """
    bind = op.get_bind()
    found: list[UUID] = []
    for row in bind.execute(sa.select(table.c.id, table.c.steps)).all():
        steps = [
            {**step, PARAMS: _converted(step[PARAMS], convert)} if step[PROCESSOR_KEY] == NORMALIZE else step
            for step in row.steps
        ]
        found.extend(UUID(step[STEP_ID]) for step in row.steps if step[PROCESSOR_KEY] == NORMALIZE and STEP_ID in step)
        if steps != row.steps:
            bind.execute(sa.update(table).where(table.c.id == row.id).values(steps=steps))
    return found


def _convert(convert: Callable[[float], float]) -> None:
    """Convert the margins in every place the parameters of a normalize step are stored.

    :param convert: The conversion of one margin.
    :type convert: Callable[[float], float]
    """
    bind = op.get_bind()
    _convert_steps(PROFILES, convert)
    steps = _convert_steps(RECIPES, convert)
    if not steps:
        return
    for state in bind.execute(
        sa.select(STATES.c.page_id, STATES.c.stage, STATES.c[STEP_ID], STATES.c.params).where(
            STATES.c[STEP_ID].in_(steps)
        )
    ).all():
        own = (
            (STATES.c.page_id == state.page_id) & (STATES.c.stage == state.stage) & (STATES.c[STEP_ID] == state.step_id)
        )
        bind.execute(sa.update(STATES).where(own).values(params=_converted(state.params or {}, convert)))
    changes = sa.select(CHANGES.c.id, CHANGES.c.before, CHANGES.c.after).where(
        CHANGES.c[STEP_ID].in_(steps) & (CHANGES.c.layer == SETTINGS)
    )
    for change in bind.execute(changes).all():
        bind.execute(
            sa.update(CHANGES)
            .where(CHANGES.c.id == change.id)
            .values(
                before=None if change.before is None else _converted(change.before, convert),
                after=None if change.after is None else _converted(change.after, convert),
            )
        )


def data_upgrades() -> None:
    """Convert every stored margin from pixels to millimetres."""
    _convert(_to_millimetres)


def data_downgrades() -> None:
    """Convert every stored margin from millimetres back to pixels."""
    _convert(_to_pixels)
