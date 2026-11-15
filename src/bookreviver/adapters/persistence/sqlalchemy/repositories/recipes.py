"""Repositories of the recipes of a book and of its recipe profiles."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

from attrs import evolve

from bookreviver.adapters.persistence.sqlalchemy.mappers import RecipeMapper
from bookreviver.adapters.persistence.sqlalchemy.profile_mappers import RecipeProfileMapper
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyRepository
from bookreviver.adapters.persistence.sqlalchemy.repositories.recipe_steps import RecipeSteps
from bookreviver.adapters.persistence.sqlalchemy.rows import (
    RecipeProfileRows,
    RecipeRows,
)
from bookreviver.adapters.persistence.sqlalchemy.tables import (
    RecipeProfileRow,
    RecipeRow,
)
from bookreviver.domain.ids import (
    RecipeId,
    RecipeProfileId,
)
from bookreviver.domain.recipes import Recipe, RecipeProfile
from bookreviver.ports.persistence import (
    RecipeProfileRepository,
    RecipeRepository,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from sqlalchemy.orm import Session

    from bookreviver.domain.ids import StepId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.recipes import StepTypeChange
    from bookreviver.domain.stages import Stage
    from bookreviver.ports.ordering import OrderKeys


class SqlAlchemyRecipeRepository(SqlAlchemyRepository[Recipe, RecipeId, RecipeRow], RecipeRepository):
    """Recipes of the books, of which a stage has one for each kind, with their steps in the tables of the steps.

    The row of a recipe goes through the generic operations, and the steps through :class:`RecipeSteps`, which reads
    them in order and writes back the rows that changed.
    """

    def __init__(self, session: Session, order_keys: OrderKeys) -> None:
        """Create the repository over the ``recipes`` table and the tables of the steps.

        :param session: Session of the transaction.
        :type session: Session
        :param order_keys: The order keys that place the steps of a recipe.
        :type order_keys: OrderKeys
        """
        super().__init__(rows=RecipeRows(session=session), mapper=RecipeMapper())
        self._steps = RecipeSteps(session, order_keys)

    @override
    def get(self, entity_id: RecipeId) -> Recipe:
        """Return the recipe with this identifier, with its steps in order.

        :param entity_id: Identifier of the recipe.
        :type entity_id: RecipeId
        :returns: The stored recipe.
        :rtype: Recipe
        :raises NotFoundError: If no recipe has this identifier.
        """
        return self._with_steps([self._rows.get(entity_id)])[0]

    @override
    def add(self, entity: Recipe) -> Recipe:
        """Store a new recipe and its steps.

        :param entity: Recipe to store, with its identifier already assigned.
        :type entity: Recipe
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises ConflictError: If the recipe or another of its kind in its stage is stored, or it holds a step twice.
        :raises NotFoundError: If a step of the recipe is a step of another stage.
        """
        return self.add_many([entity])[0]

    @override
    def add_many(self, entities: Sequence[Recipe]) -> Sequence[Recipe]:
        """Store several new recipes and their steps.

        :param entities: Recipes to store, with their identifiers already assigned.
        :type entities: Sequence[Recipe]
        :returns: The recipes as stored, in the given order.
        :rtype: Sequence[Recipe]
        :raises ConflictError: If one of the recipes or another of its kind in its stage is stored, or a recipe holds a
                               step twice.
        :raises NotFoundError: If a step of a recipe is a step of another stage.
        """
        if not entities:
            return entities
        rows = self._rows.add_many([self._mapper.to_row(entity) for entity in entities])
        for entity in entities:
            self._steps.write(entity)
        return self._with_steps(rows)

    @override
    def update(self, entity: Recipe) -> Recipe:
        """Replace the stored state of a recipe, writing back the rows of its steps that changed.

        :param entity: Recipe with its new state.
        :type entity: Recipe
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the recipe is not stored, or a step of it is a step of another stage.
        :raises ConflictError: If the recipe holds a step twice.
        """
        row = self._rows.update(self._mapper.to_row(entity))
        self._steps.write(entity)
        return self._with_steps([row])[0]

    @override
    def delete(self, entity_id: RecipeId) -> None:
        """Remove the recipe and the places of its steps, and mark removed the steps no other recipe holds.

        :param entity_id: Identifier of the recipe.
        :type entity_id: RecipeId
        :raises NotFoundError: If no recipe has this identifier.
        """
        held = self._steps.held_by(entity_id)
        self._rows.delete(entity_id)
        self._steps.release(held, datetime.now(UTC))

    @override
    def list_for_stage(self, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage by creation, ties by identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes of the stage.
        :rtype: Sequence[Recipe]
        """
        return self._with_steps(
            self._rows.get_many(order_by=[RecipeRow.created_at.asc(), RecipeRow.id.asc()], stage=stage)
        )

    @override
    def list_all(self) -> Sequence[Recipe]:
        """Return the recipes of every stage of a book, in the order of the stages and then by creation.

        :returns: The recipes of the stages that have been used.
        :rtype: Sequence[Recipe]
        """
        recipes = self._with_steps(self._rows.get_many(order_by=[RecipeRow.created_at.asc(), RecipeRow.id.asc()]))
        return sorted(recipes, key=lambda recipe: recipe.stage.position)

    @override
    def kept_params(self, step_id: StepId, processor_key: str) -> Mapping[RecipeId, MetadataMap]:
        """Return the parameters a step had under a type in the recipes that hold it, as each recipe kept them.

        :param step_id: The step.
        :type step_id: StepId
        :param processor_key: Key of the processor the step had, or has, as its type.
        :type processor_key: str
        :returns: The parameters of each recipe that holds the step and kept parameters of that type, by recipe.
        :rtype: Mapping[RecipeId, MetadataMap]
        """
        kept = self._steps.kept_params(step_id, processor_key)
        return {RecipeId(recipe_id): params for recipe_id, params in kept.items()}

    @override
    def replace_type(self, change: StepTypeChange) -> None:
        """Give a step another type in every recipe that holds it, each recipe with its parameters of the new type.

        :param change: The step, its new type and the parameters each recipe that holds it runs it with.
        :type change: StepTypeChange
        :raises NotFoundError: If the book has no such step, or a recipe of the parameters does not hold it.
        """
        self._steps.replace_type(change)

    def _with_steps(self, rows: Sequence[RecipeRow]) -> list[Recipe]:
        """Build the recipes of some rows, each with its steps in order.

        :param rows: Recipe rows loaded from the database.
        :type rows: Sequence[RecipeRow]
        :returns: The recipes, in the order of the rows.
        :rtype: list[Recipe]
        """
        steps = self._steps.read([row.id for row in rows])
        return [evolve(self._mapper.to_entity(row), steps=steps.get(row.id, ())) for row in rows]


class SqlAlchemyRecipeProfileRepository(
    SqlAlchemyRepository[RecipeProfile, RecipeProfileId, RecipeProfileRow], RecipeProfileRepository
):
    """The recipe profiles of a book."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``recipe_profiles`` table of a book.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=RecipeProfileRows(session=session), mapper=RecipeProfileMapper())

    @override
    def list_all(self, stage: Stage | None = None) -> Sequence[RecipeProfile]:
        """Return the profiles of the book in the order of the stages, then by creation, ties by identifier.

        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :returns: The profiles of the book.
        :rtype: Sequence[RecipeProfile]
        """
        rows = self._rows.get_many(order_by=[RecipeProfileRow.created_at.asc(), RecipeProfileRow.id.asc()])
        return sorted(
            (self._mapper.to_entity(row) for row in rows if stage is None or row.stage is stage),
            key=lambda profile: profile.stage.position,
        )
