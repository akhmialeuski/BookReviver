"""The tables of the steps of a book and of their places in its recipes.

A step is a row of ``steps``, keyed by the identifier the domain gives it, which is unique in the book. A recipe holds
a step through a row of ``recipe_steps``, which places it by a fractional order key, so moving a step rewrites that one
row, and the parameters the step runs with in that recipe are a row of ``recipe_step_params`` for its type. A step
taken out of every recipe keeps its row, marked removed, since the settings, the edits and the history of the pages
name it and are found again when it comes back.

A row of a stage names a step together with its own stage, through a key of two columns to the unique pair of the
identifier and the stage of the step, so the database refuses a recipe, a record or a value of one stage naming a step
of another.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, ForeignKeyConstraint, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bookreviver.adapters.persistence.sqlalchemy.columns import CASCADE, enum_by_value
from bookreviver.adapters.persistence.sqlalchemy.tables.base import (
    ID_COLUMN,
    RECIPE_ID_COLUMN,
    RECIPE_STEPS_TABLE,
    RECIPES_TABLE,
    STAGE_COLUMN,
    STEP_ID_COLUMN,
    STEP_KEY_OF_STAGE,
    STEP_OF_STAGE,
    STEPS_TABLE,
    BookTable,
)
from bookreviver.domain.stages import Stage


class StepRow(BookTable):
    """Row of one step of a book, whichever recipes of its stage hold it.

    :ivar id: Step identifier, assigned by the domain, unique in the book.
    :ivar stage: Stage of the step, stored by value.
    :ivar type_key: Key of the processor that is the active type of the step.
    :ivar origin_step_id: The step of a recipe profile the step was made from, with no key, since the steps of a
                          profile are a JSON list in its row, or null.
    :ivar created_at: Time the step was first stored.
    :ivar removed_at: Time the step was taken out of the last recipe that held it, or null while a recipe holds it.
    """

    __tablename__ = STEPS_TABLE
    # The pair the composite keys of the rows of a stage name, so they name only a step of their own stage
    __table_args__ = (UniqueConstraint(ID_COLUMN, STAGE_COLUMN),)

    id: Mapped[UUID] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    type_key: Mapped[str]
    origin_step_id: Mapped[UUID | None]
    created_at: Mapped[datetime]
    removed_at: Mapped[datetime | None]


class RecipeStepRow(BookTable):
    """Row of the place of one step in one recipe, keyed by the recipe and the step.

    :ivar recipe_id: The recipe.
    :ivar step_id: The step.
    :ivar stage: Stage of both, which the two keys hold equal to the stage of the recipe and of the step.
    :ivar order_key: Fractional index string, compared byte by byte, whose order is the order of the steps.
    :ivar enabled: Whether a run and a preview run the step.
    """

    __tablename__ = RECIPE_STEPS_TABLE
    __table_args__ = (
        ForeignKeyConstraint(
            (RECIPE_ID_COLUMN, STAGE_COLUMN),
            (f'{RECIPES_TABLE}.{ID_COLUMN}', f'{RECIPES_TABLE}.{STAGE_COLUMN}'),
            ondelete=CASCADE,
        ),
        ForeignKeyConstraint(STEP_OF_STAGE, STEP_KEY_OF_STAGE),
        Index(None, *STEP_OF_STAGE),
    )

    recipe_id: Mapped[UUID] = mapped_column(primary_key=True)
    step_id: Mapped[UUID] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    # Order keys compare byte by byte, which SQLite's default BINARY collation does
    order_key: Mapped[str] = mapped_column(String)
    enabled: Mapped[bool]


class RecipeStepParamsRow(BookTable):
    """Row of the parameters one step runs with in one recipe for one type, keyed by the recipe, the step and the type.

    A step has a row for each type it has had in the recipe, and the row of its active type is the one it runs with.
    The rows go with the place of the step in the recipe.

    :ivar recipe_id: The recipe.
    :ivar step_id: The step.
    :ivar type_key: Key of the processor the parameters are for.
    :ivar params: Parameters of the step, following the processor's JSON Schema, as JSON.
    """

    __tablename__ = 'recipe_step_params'
    __table_args__ = (
        ForeignKeyConstraint(
            (RECIPE_ID_COLUMN, STEP_ID_COLUMN),
            (f'{RECIPE_STEPS_TABLE}.{RECIPE_ID_COLUMN}', f'{RECIPE_STEPS_TABLE}.{STEP_ID_COLUMN}'),
            ondelete=CASCADE,
        ),
    )

    recipe_id: Mapped[UUID] = mapped_column(primary_key=True)
    step_id: Mapped[UUID] = mapped_column(primary_key=True)
    type_key: Mapped[str] = mapped_column(primary_key=True)
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
