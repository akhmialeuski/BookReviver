"""The tables of the recipes of a book and of the recipe profiles they can be made from."""

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from bookreviver.adapters.persistence.sqlalchemy.columns import enum_by_value
from bookreviver.adapters.persistence.sqlalchemy.tables.base import (
    ID_COLUMN,
    RECIPE_PROFILES_TABLE,
    RECIPES_TABLE,
    STAGE_COLUMN,
    BookTable,
)
from bookreviver.domain.pages import RecipeKind
from bookreviver.domain.recipes import OrderMode
from bookreviver.domain.stages import Stage


class RecipeRow(BookTable):
    """Row of one recipe of a stage of a book, of which there is one for each kind of page.

    The steps are rows of ``steps``, which the recipe places in its order through rows of ``recipe_steps``.

    :ivar id: Recipe identifier, assigned by the domain.
    :ivar stage: Stage the recipe processes, stored by value.
    :ivar kind: Kind of the pages the recipe processes, stored by value.
    :ivar profile_id: Profile of the book the recipe was made from, which the service empties with the profile.
    :ivar created_at: Time the recipe was created.
    :ivar updated_at: Time the recipe last changed.
    """

    __tablename__ = RECIPES_TABLE
    # The database keeps two requests that both make the recipes of a stage from both succeeding
    # The pair the places of the steps name, so a recipe holds only steps of its own stage
    __table_args__ = (UniqueConstraint(STAGE_COLUMN, 'kind'), UniqueConstraint(ID_COLUMN, STAGE_COLUMN))

    id: Mapped[UUID] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    kind: Mapped[RecipeKind] = mapped_column(enum_by_value(RecipeKind))
    # No key, so the recipes that name a profile do not block its deletion, and the service empties the name
    profile_id: Mapped[UUID | None] = mapped_column(index=True)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]


class RecipeProfileRow(BookTable):
    """Row of one recipe profile of a book.

    The steps are a JSON list of ``{processor_key, params, enabled, step_id}`` objects, since a profile is a template
    whose steps no row names. A recipe may name the profile it was made from, with no key to check the name, and a step
    made from a step of the profile names that step in ``steps.origin_step_id``.

    :ivar id: Profile identifier, assigned by the domain.
    :ivar stage: Stage whose recipes the profile can be applied to, stored by value.
    :ivar name: Name the user sees.
    :ivar steps: The steps in order, as JSON.
    :ivar order: Whether the steps were saved in the usual order or the free one, stored by value.
    :ivar created_at: Time the profile was saved.
    :ivar updated_at: Time the profile last changed.
    """

    __tablename__ = RECIPE_PROFILES_TABLE

    id: Mapped[UUID] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(enum_by_value(Stage))
    name: Mapped[str]
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    order: Mapped[OrderMode] = mapped_column(enum_by_value(OrderMode), server_default=OrderMode.USUAL.value)
    created_at: Mapped[datetime]
    updated_at: Mapped[datetime]
