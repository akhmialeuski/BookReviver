"""Keep the steps of the recipes as rows.

The steps of a recipe were a JSON list in the row of the recipe, and the record of a stage on a page named the step a
run stopped at by its index in that list, which pointed at another step once the steps were reordered. Every step is
now a row of ``steps``, a recipe places it through a row of ``recipe_steps`` under a fractional order key and keeps its
parameters in a row of ``recipe_step_params`` for its type, and the record names the step by its identifier in
``through_step_id``. The tables that name a step, ``page_step_states``, ``step_values``, ``group_values`` and
``step_changes``, gain a key of the step and its stage.

A step identifier is unique in the book. One that recipes of two stages share keeps it in the stage of the recipe made
first and gets a new one in the other, which every row of that stage naming it takes too, and one a recipe holds twice,
or that two recipes of a stage hold with two processors, gets a new one where it repeats. The index of a record is
read through the recipe the record names, and a record whose recipe is gone or shorter loses it. A step that rows name
but no recipe holds becomes a row marked removed, with no type, so nothing is lost and the keys hold. The downgrade
writes the lists back in the order of the keys and the indexes from the identifiers, and drops the steps no recipe
holds, with the types and origins of the steps. Both directions log what they changed and what they could not keep.

Revision ID: edca77c509a2
Revises: 07e2df4566fa
Create Date: 2026-11-13 13:09:44.755152
"""

import logging
import warnings
from collections import defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID, uuid4

import advanced_alchemy.types.datetime
import advanced_alchemy.types.guid
import sqlalchemy as sa
from alembic import op
from fractional_indexing import generate_n_keys_between

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ('data_downgrades', 'data_upgrades', 'downgrade', 'schema_downgrades', 'schema_upgrades', 'upgrade')

# Revision identifiers, used by Alembic
revision: str = 'edca77c509a2'
down_revision: str | Sequence[str] | None = '07e2df4566fa'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger(__name__)

# Action of the warning filter that the template of the revisions runs the steps under
SILENCE: Final = 'ignore'
STEPS: Final = 'steps'
RECIPE_STEPS: Final = 'recipe_steps'
PARAMS: Final = 'recipe_step_params'
RECIPES: Final = 'recipes'
PAGE_STAGES: Final = 'page_stages'
# The tables that name a step of their stage, which gain a key of the step and the stage
NAMING: Final = ('page_step_states', 'step_values', 'group_values', 'step_changes')
ID: Final = 'id'
STAGE: Final = 'stage'
STEP_ID: Final = 'step_id'
RECIPE_ID: Final = 'recipe_id'
THROUGH: Final = 'through_step'
THROUGH_ID: Final = 'through_step_id'
CASCADE: Final = 'CASCADE'
SET_NULL: Final = 'SET NULL'
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
# The keys of a step in the JSON list of a recipe
PROCESSOR_KEY: Final = 'processor_key'
STEP_PARAMS: Final = 'params'
ENABLED: Final = 'enabled'
# The type of a step that rows name but no recipe holds, which nothing records
NO_TYPE: Final = ''


def _guid(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    """Build a column of an identifier.

    :param name: Name of the column.
    :type name: str
    :param nullable: Whether the column may be empty.
    :type nullable: bool
    :returns: The column.
    :rtype: sa.Column[Any]
    """
    return sa.Column(name, advanced_alchemy.types.guid.GUID(length=16), nullable=nullable)


def _moment(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    """Build a column of a time.

    :param name: Name of the column.
    :type name: str
    :param nullable: Whether the column may be empty.
    :type nullable: bool
    :returns: The column.
    :rtype: sa.Column[Any]
    """
    return sa.Column(name, advanced_alchemy.types.datetime.DateTimeUTC(timezone=True), nullable=nullable)


def _stage() -> sa.Column[Any]:
    """Build the column of a stage, whose check the naming convention names after the type.

    :returns: The column.
    :rtype: sa.Column[Any]
    """
    return sa.Column(STAGE, sa.Enum(*STAGES, name=STAGE, native_enum=False, create_constraint=True), nullable=False)


# The tables as the data moves read and write them, typed so identifiers, times and JSON convert
RECIPE_TABLE: Final = sa.table(
    RECIPES,
    sa.column(ID, advanced_alchemy.types.guid.GUID(length=16)),
    sa.column(STAGE, sa.String()),
    sa.column(STEPS, sa.JSON()),
    sa.column('created_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True)),
)
STEP_TABLE: Final = sa.table(
    STEPS,
    sa.column(ID, advanced_alchemy.types.guid.GUID(length=16)),
    sa.column(STAGE, sa.String()),
    sa.column('type_key', sa.String()),
    sa.column('origin_step_id', advanced_alchemy.types.guid.GUID(length=16)),
    sa.column('created_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True)),
    sa.column('removed_at', advanced_alchemy.types.datetime.DateTimeUTC(timezone=True)),
)
PLACE_TABLE: Final = sa.table(
    RECIPE_STEPS,
    sa.column(RECIPE_ID, advanced_alchemy.types.guid.GUID(length=16)),
    sa.column(STEP_ID, advanced_alchemy.types.guid.GUID(length=16)),
    sa.column(STAGE, sa.String()),
    sa.column('order_key', sa.String()),
    sa.column(ENABLED, sa.Boolean()),
)
PARAMS_TABLE: Final = sa.table(
    PARAMS,
    sa.column(RECIPE_ID, advanced_alchemy.types.guid.GUID(length=16)),
    sa.column(STEP_ID, advanced_alchemy.types.guid.GUID(length=16)),
    sa.column('type_key', sa.String()),
    sa.column(STEP_PARAMS, sa.JSON()),
)
RECORD_TABLE: Final = sa.table(
    PAGE_STAGES,
    sa.column('page_id', advanced_alchemy.types.guid.GUID(length=16)),
    sa.column(STAGE, sa.String()),
    sa.column(RECIPE_ID, advanced_alchemy.types.guid.GUID(length=16)),
    sa.column(THROUGH, sa.Integer()),
    sa.column(THROUGH_ID, advanced_alchemy.types.guid.GUID(length=16)),
)


def _naming_table(name: str) -> sa.TableClause:
    """Give a table that names a step of its stage, as the data moves read and write it.

    :param name: Name of the table.
    :type name: str
    :returns: The table with its step and stage.
    :rtype: sa.TableClause
    """
    step_id = sa.column(STEP_ID, advanced_alchemy.types.guid.GUID(length=16))
    return sa.table(name, step_id, sa.column(STAGE, sa.String()))


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


class StepsOfRecipes:
    """The steps the JSON lists of the recipes hold, each given an identifier unique in the book.

    :ivar owners: The stage and the processor of each identifier, as the first recipe that holds it gives them.
    :ivar replaced: The new identifier of each identifier of a stage that another stage holds, by stage and old one.
    :ivar renamed: How many steps of the lists got a new identifier.
    """

    def __init__(self) -> None:
        """Start with no step."""
        self.owners: dict[UUID, tuple[str, str]] = {}
        self.replaced: dict[tuple[str, UUID], UUID] = {}
        self.renamed = 0

    def identify(self, stage: str, step: dict[str, Any], held: set[UUID]) -> UUID:
        """Give a step of a recipe of a stage its identifier in the book.

        :param stage: The stage of the recipe.
        :type stage: str
        :param step: The step as the list holds it.
        :type step: dict[str, Any]
        :param held: The identifiers the recipe has given so far, which grows by the one given.
        :type held: set[UUID]
        :returns: The identifier.
        :rtype: UUID
        """
        stored = step.get(STEP_ID)
        step_id = uuid4() if stored is None else UUID(stored)
        step_id = self.replaced.get((stage, step_id), step_id)
        owner = self.owners.get(step_id)
        if step_id in held or (owner is not None and owner[1] != step[PROCESSOR_KEY] and owner[0] == stage):
            step_id = self._renamed(uuid4())
        elif owner is not None and owner[0] != stage:
            self.replaced[stage, step_id] = step_id = self._renamed(uuid4())
        self.owners.setdefault(step_id, (stage, step[PROCESSOR_KEY]))
        held.add(step_id)
        return step_id

    def _renamed(self, step_id: UUID) -> UUID:
        """Count a step that gets a new identifier.

        :param step_id: The new identifier.
        :type step_id: UUID
        :returns: The identifier.
        :rtype: UUID
        """
        self.renamed += 1
        return step_id


def _move_recipes(connection: sa.Connection, steps: StepsOfRecipes) -> dict[UUID, list[UUID]]:
    """Write the steps of every recipe as rows, in the order of the recipes' creation.

    :param connection: Connection of the migration.
    :type connection: sa.Connection
    :param steps: The identifiers given so far.
    :type steps: StepsOfRecipes
    :returns: The identifiers of the steps of each recipe, in order.
    :rtype: dict[UUID, list[UUID]]
    """
    recipes = connection.execute(sa.select(RECIPE_TABLE).order_by(RECIPE_TABLE.c.created_at, RECIPE_TABLE.c.id))
    orders: dict[UUID, list[UUID]] = {}
    stored: set[UUID] = set()
    for recipe in recipes.all():
        listed: list[dict[str, Any]] = recipe.steps or []
        held: set[UUID] = set()
        order = [steps.identify(recipe.stage, step, held) for step in listed]
        keys = generate_n_keys_between(None, None, len(order))
        for step_id, step, key in zip(order, listed, keys, strict=True):
            if step_id not in stored:
                stored.add(step_id)
                connection.execute(
                    sa.insert(STEP_TABLE).values(
                        id=step_id, stage=recipe.stage, type_key=step[PROCESSOR_KEY], created_at=recipe.created_at
                    )
                )
            place = {RECIPE_ID: recipe.id, STEP_ID: step_id}
            enabled = step.get(ENABLED, True)
            connection.execute(
                sa.insert(PLACE_TABLE).values(**place, stage=recipe.stage, order_key=key, enabled=enabled)
            )
            values = {'type_key': step[PROCESSOR_KEY], STEP_PARAMS: step[STEP_PARAMS]}
            connection.execute(sa.insert(PARAMS_TABLE).values(**place, **values))
        orders[recipe.id] = order
    return orders


def _replace_in_stage(connection: sa.Connection, replaced: dict[tuple[str, UUID], UUID]) -> int:
    """Give the rows of a stage that name a replaced identifier the new one.

    :param connection: Connection of the migration.
    :type connection: sa.Connection
    :param replaced: The new identifier of each replaced one, by stage and old identifier.
    :type replaced: dict[tuple[str, UUID], UUID]
    :returns: How many rows changed.
    :rtype: int
    """
    changed = 0
    for name in NAMING:
        table = _naming_table(name)
        for (stage, old), new in replaced.items():
            statement = sa.update(table).where(table.c.stage == stage, table.c.step_id == old).values(step_id=new)
            changed += connection.execute(statement).rowcount
    return changed


def _keep_unheld(connection: sa.Connection, steps: StepsOfRecipes, moment: datetime) -> tuple[int, int]:
    """Store a removed step for each step that rows name and no recipe holds, giving a new identifier where needed.

    :param connection: Connection of the migration.
    :type connection: sa.Connection
    :param steps: The identifiers the recipes hold, which grows by the removed ones.
    :type steps: StepsOfRecipes
    :param moment: Time the steps are marked removed.
    :type moment: datetime
    :returns: How many removed steps were stored, and how many rows took a new identifier.
    :rtype: tuple[int, int]
    """
    named: dict[tuple[str, UUID], None] = {}
    for name in NAMING:
        table = _naming_table(name)
        for row in connection.execute(sa.select(table.c.stage, table.c.step_id).distinct()):
            named[row.stage, row.step_id] = None
    replaced: dict[tuple[str, UUID], UUID] = {}
    kept = 0
    for stage, step_id in named:
        owner = steps.owners.get(step_id)
        if owner is not None and owner[0] == stage:
            continue
        new = step_id if owner is None else uuid4()
        if owner is not None:
            replaced[stage, step_id] = new
        steps.owners[new] = (stage, NO_TYPE)
        kept += 1
        connection.execute(
            sa.insert(STEP_TABLE).values(id=new, stage=stage, type_key=NO_TYPE, created_at=moment, removed_at=moment)
        )
    return kept, _replace_in_stage(connection, replaced)


def _translate_records(connection: sa.Connection, orders: dict[UUID, list[UUID]]) -> tuple[int, int]:
    """Name the step each record stopped at by its identifier, through the recipe the record names.

    :param connection: Connection of the migration.
    :type connection: sa.Connection
    :param orders: The identifiers of the steps of each recipe, in order.
    :type orders: dict[UUID, list[UUID]]
    :returns: How many records were translated, and how many could not be and lost the step.
    :rtype: tuple[int, int]
    """
    translated: list[UUID | None] = []
    records = connection.execute(sa.select(RECORD_TABLE).where(RECORD_TABLE.c.through_step.is_not(None))).all()
    for record in records:
        order = orders.get(record.recipe_id, []) if record.recipe_id is not None else []
        step_id = order[record.through_step] if 0 <= record.through_step < len(order) else None
        translated.append(step_id)
        where = sa.and_(RECORD_TABLE.c.page_id == record.page_id, RECORD_TABLE.c.stage == record.stage)
        connection.execute(sa.update(RECORD_TABLE).where(where).values(through_step_id=step_id))
    lost = translated.count(None)
    return len(translated) - lost, lost


def _create_tables() -> None:
    """Create the tables of the steps, their places and their parameters."""
    op.create_table(
        STEPS,
        _guid(ID),
        _stage(),
        sa.Column('type_key', sa.String(), nullable=False),
        _guid('origin_step_id', nullable=True),
        _moment('created_at'),
        _moment('removed_at', nullable=True),
        sa.PrimaryKeyConstraint(ID, name=op.f('pk_steps')),
        sa.UniqueConstraint(ID, STAGE, name=op.f('uq_steps_id')),
    )
    op.create_table(
        RECIPE_STEPS,
        _guid(RECIPE_ID),
        _guid(STEP_ID),
        _stage(),
        sa.Column('order_key', sa.String(), nullable=False),
        sa.Column(ENABLED, sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            [RECIPE_ID, STAGE],
            ['recipes.id', 'recipes.stage'],
            name=op.f('fk_recipe_steps_recipe_id_recipes'),
            ondelete=CASCADE,
        ),
        sa.ForeignKeyConstraint(
            [STEP_ID, STAGE], ['steps.id', 'steps.stage'], name=op.f('fk_recipe_steps_step_id_steps')
        ),
        sa.PrimaryKeyConstraint(RECIPE_ID, STEP_ID, name=op.f('pk_recipe_steps')),
    )
    op.create_index(op.f('ix_recipe_steps_step_id'), RECIPE_STEPS, [STEP_ID, STAGE], unique=False)
    op.create_table(
        PARAMS,
        _guid(RECIPE_ID),
        _guid(STEP_ID),
        sa.Column('type_key', sa.String(), nullable=False),
        sa.Column(STEP_PARAMS, sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            [RECIPE_ID, STEP_ID],
            ['recipe_steps.recipe_id', 'recipe_steps.step_id'],
            name=op.f('fk_recipe_step_params_recipe_id_recipe_steps'),
            ondelete=CASCADE,
        ),
        sa.PrimaryKeyConstraint(RECIPE_ID, STEP_ID, 'type_key', name=op.f('pk_recipe_step_params')),
    )


def _key_the_naming_tables() -> None:
    """Give each table that names a step a key of the step and the stage, and an index of the key."""
    for name in NAMING:
        with op.batch_alter_table(name) as batch_op:
            batch_op.create_index(batch_op.f(f'ix_{name}_step_id'), [STEP_ID, STAGE], unique=False)
            batch_op.create_foreign_key(batch_op.f(f'fk_{name}_step_id_steps'), STEPS, [STEP_ID, STAGE], [ID, STAGE])


def _unkey_the_naming_tables() -> None:
    """Drop the key of the step and the stage of each table that names a step, and its index."""
    for name in NAMING:
        with op.batch_alter_table(name) as batch_op:
            batch_op.drop_constraint(batch_op.f(f'fk_{name}_step_id_steps'), type_='foreignkey')
            batch_op.drop_index(batch_op.f(f'ix_{name}_step_id'))


def schema_upgrades() -> None:
    """Change the schema to this revision, the steps moved out of the lists of the recipes into rows."""
    connection = op.get_bind()
    # The pair the places of the steps name exists before they are written
    with op.batch_alter_table(RECIPES) as batch_op:
        batch_op.create_unique_constraint(batch_op.f('uq_recipes_id'), [ID, STAGE])
    _create_tables()
    with op.batch_alter_table(PAGE_STAGES) as batch_op:
        batch_op.add_column(_guid(THROUGH_ID, nullable=True))
    steps = StepsOfRecipes()
    orders = _move_recipes(connection, steps)
    shared = _replace_in_stage(connection, steps.replaced)
    removed, renamed = _keep_unheld(connection, steps, datetime.now(UTC))
    translated, lost = _translate_records(connection, orders)
    with op.batch_alter_table(PAGE_STAGES) as batch_op:
        batch_op.create_index(batch_op.f('ix_page_stages_through_step_id'), [THROUGH_ID], unique=False)
        batch_op.create_foreign_key(
            batch_op.f('fk_page_stages_through_step_id_steps'), STEPS, [THROUGH_ID], [ID], ondelete=SET_NULL
        )
        batch_op.drop_column(THROUGH)
    _key_the_naming_tables()
    with op.batch_alter_table(RECIPES) as batch_op:
        batch_op.drop_column(STEPS)
    places = sum(len(order) for order in orders.values())
    logger.warning(
        '%d steps of %d recipes became rows; %d took a new identifier, which %d rows of their stage took too.',
        places,
        len(orders),
        steps.renamed,
        shared,
    )
    logger.warning('%d steps no recipe holds were kept as removed; %d rows took a new identifier.', removed, renamed)
    logger.warning(
        '%d records name the step they stopped at; %d could not be translated and lost it.', translated, lost
    )


def _step_lists(connection: sa.Connection) -> dict[UUID, list[dict[str, Any]]]:
    """Read the steps of every recipe as the JSON list the previous revision holds, in the order of the keys.

    :param connection: Connection of the migration.
    :type connection: sa.Connection
    :returns: The list of each recipe that holds a step.
    :rtype: dict[UUID, list[dict[str, Any]]]
    """
    params = sa.and_(
        PARAMS_TABLE.c.recipe_id == PLACE_TABLE.c.recipe_id,
        PARAMS_TABLE.c.step_id == PLACE_TABLE.c.step_id,
        PARAMS_TABLE.c.type_key == STEP_TABLE.c.type_key,
    )
    place = PLACE_TABLE.c
    statement = (
        sa.select(place.recipe_id, place.step_id, place.enabled, STEP_TABLE.c.type_key, PARAMS_TABLE.c.params)
        .join(STEP_TABLE, STEP_TABLE.c.id == PLACE_TABLE.c.step_id)
        .outerjoin(PARAMS_TABLE, params)
        .order_by(PLACE_TABLE.c.recipe_id, PLACE_TABLE.c.order_key, PLACE_TABLE.c.step_id)
    )
    lists: defaultdict[UUID, list[dict[str, Any]]] = defaultdict(list)
    for row in connection.execute(statement):
        step = {PROCESSOR_KEY: row.type_key, STEP_PARAMS: row.params or {}, ENABLED: bool(row.enabled)}
        lists[row.recipe_id].append({**step, STEP_ID: str(row.step_id)})
    return dict(lists)


def _write_lists(connection: sa.Connection, lists: dict[UUID, list[dict[str, Any]]]) -> None:
    """Write the list of each recipe into its row, and the index of the step each record stopped at.

    :param connection: Connection of the migration.
    :type connection: sa.Connection
    :param lists: The list of each recipe that holds a step.
    :type lists: dict[UUID, list[dict[str, Any]]]
    """
    recipe_ids: list[UUID] = list(connection.execute(sa.select(RECIPE_TABLE.c.id)).scalars())
    for recipe_id in recipe_ids:
        listed = lists.get(recipe_id, [])
        connection.execute(sa.update(RECIPE_TABLE).where(RECIPE_TABLE.c.id == recipe_id).values(steps=listed))
    records = connection.execute(sa.select(RECORD_TABLE).where(RECORD_TABLE.c.through_step_id.is_not(None))).all()
    for record in records:
        order = [UUID(step[STEP_ID]) for step in lists.get(record.recipe_id, [])]
        index = order.index(record.through_step_id) if record.through_step_id in order else None
        where = sa.and_(RECORD_TABLE.c.page_id == record.page_id, RECORD_TABLE.c.stage == record.stage)
        connection.execute(sa.update(RECORD_TABLE).where(where).values(through_step=index))


def schema_downgrades() -> None:
    """Change the schema back to the previous revision, the steps of each recipe a JSON list in its row again."""
    connection = op.get_bind()
    lists = _step_lists(connection)
    unheld = sa.select(sa.func.count()).select_from(STEP_TABLE).where(STEP_TABLE.c.removed_at.is_not(None))
    removed = connection.execute(unheld).scalar_one()
    with op.batch_alter_table(RECIPES) as batch_op:
        batch_op.add_column(sa.Column(STEPS, sa.JSON(), nullable=True))
        batch_op.drop_constraint(batch_op.f('uq_recipes_id'), type_='unique')
    with op.batch_alter_table(PAGE_STAGES) as batch_op:
        batch_op.add_column(sa.Column(THROUGH, sa.Integer(), nullable=True))
    _write_lists(connection, lists)
    with op.batch_alter_table(RECIPES) as batch_op:
        batch_op.alter_column(STEPS, existing_type=sa.JSON(), nullable=False)
    with op.batch_alter_table(PAGE_STAGES) as batch_op:
        batch_op.drop_constraint(batch_op.f('fk_page_stages_through_step_id_steps'), type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_page_stages_through_step_id'))
        batch_op.drop_column(THROUGH_ID)
    _unkey_the_naming_tables()
    op.drop_table(PARAMS)
    op.drop_index(op.f('ix_recipe_steps_step_id'), table_name=RECIPE_STEPS)
    op.drop_table(RECIPE_STEPS)
    op.drop_table(STEPS)
    logger.warning(
        '%d steps no recipe holds were dropped with the types and origins of every step; rows still name them.', removed
    )


def data_upgrades() -> None:
    """Change the rows the schema change affects, after it, which the schema change has moved already."""


def data_downgrades() -> None:
    """Change the rows back, before the schema change is reverted, which the schema change moves back itself."""
