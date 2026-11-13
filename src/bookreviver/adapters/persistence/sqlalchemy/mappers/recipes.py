"""The mappers of the recipes of a book and of the steps they hold."""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import RecipeRow, RecipeStepParamsRow, RecipeStepRow, StepRow
from bookreviver.domain.ids import RecipeId, RecipeProfileId, StepId
from bookreviver.domain.recipes import Recipe, Step

if TYPE_CHECKING:
    from datetime import datetime

    from bookreviver.domain.metadata import MetadataMap


class RecipeMapper(RowMapper[Recipe, RecipeRow]):
    """Translation of the row of a recipe, which holds no step: the repository reads the steps from their own tables."""

    @override
    def to_entity(self, row: RecipeRow) -> Recipe:
        """Build the recipe stored in ``row``, with no step yet.

        :param row: Recipe row loaded from the database.
        :type row: RecipeRow
        :returns: The recipe, whose steps the repository adds from the rows of its steps.
        :rtype: Recipe
        """
        return Recipe(
            id=RecipeId(row.id),
            stage=row.stage,
            kind=row.kind,
            steps=(),
            profile_id=None if row.profile_id is None else RecipeProfileId(row.profile_id),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: Recipe) -> RecipeRow:
        """Build the row of ``entity``, which leaves the steps to their own tables.

        :param entity: Recipe to store.
        :type entity: Recipe
        :returns: Transient recipe row.
        :rtype: RecipeRow
        """
        return RecipeRow(
            id=entity.id,
            stage=entity.stage,
            kind=entity.kind,
            profile_id=entity.profile_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


class RecipeStepMapper:
    """Translation of a step of a recipe, which three rows hold: the step, its place and its parameters."""

    @staticmethod
    def to_step(place: RecipeStepRow, step: StepRow, params: MetadataMap | None) -> Step:
        """Build the step a recipe holds at one place.

        :param place: The place of the step in the recipe.
        :type place: RecipeStepRow
        :param step: The row of the step.
        :type step: StepRow
        :param params: The parameters of the active type of the step in the recipe, or None when the recipe has none
                       for that type, which runs the step with the defaults of its processor.
        :type params: MetadataMap | None
        :returns: The step.
        :rtype: Step
        """
        return Step(
            processor_key=step.type_key,
            params={} if params is None else dict(params),
            enabled=place.enabled,
            step_id=StepId(step.id),
            origin_step_id=None if step.origin_step_id is None else StepId(step.origin_step_id),
        )

    @staticmethod
    def step_row(recipe: Recipe, step: Step, moment: datetime) -> StepRow:
        """Build the row of a step that no row holds yet.

        :param recipe: The recipe that first holds the step, whose stage the step belongs to.
        :type recipe: Recipe
        :param step: The step.
        :type step: Step
        :param moment: Time the step is stored.
        :type moment: datetime
        :returns: Transient step row.
        :rtype: StepRow
        """
        return StepRow(
            id=step.step_id,
            stage=recipe.stage,
            type_key=step.processor_key,
            origin_step_id=step.origin_step_id,
            created_at=moment,
            removed_at=None,
        )

    @staticmethod
    def place_row(recipe: Recipe, step: Step, order_key: str) -> RecipeStepRow:
        """Build the row of the place of a step in a recipe.

        :param recipe: The recipe.
        :type recipe: Recipe
        :param step: The step.
        :type step: Step
        :param order_key: The order key of the place.
        :type order_key: str
        :returns: Transient place row.
        :rtype: RecipeStepRow
        """
        return RecipeStepRow(
            recipe_id=recipe.id, step_id=step.step_id, stage=recipe.stage, order_key=order_key, enabled=step.enabled
        )

    @staticmethod
    def params_row(recipe: Recipe, step: Step) -> RecipeStepParamsRow:
        """Build the row of the parameters of the active type of a step in a recipe.

        :param recipe: The recipe.
        :type recipe: Recipe
        :param step: The step.
        :type step: Step
        :returns: Transient parameters row.
        :rtype: RecipeStepParamsRow
        """
        return RecipeStepParamsRow(
            recipe_id=recipe.id, step_id=step.step_id, type_key=step.processor_key, params=dict(step.params)
        )
