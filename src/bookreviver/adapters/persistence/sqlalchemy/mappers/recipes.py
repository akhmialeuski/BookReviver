"""The mapper of the recipes of a book."""

from typing import override

from bookreviver.adapters.persistence.sqlalchemy.mappers.base import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import RecipeRow
from bookreviver.domain.ids import (
    RecipeId,
    RecipeProfileId,
)
from bookreviver.domain.recipes import Recipe, Step


class RecipeMapper(RowMapper[Recipe, RecipeRow]):
    """Translation of a recipe, whose steps are a JSON list."""

    @override
    def to_entity(self, row: RecipeRow) -> Recipe:
        """Build the recipe stored in ``row``.

        :param row: Recipe row loaded from the database.
        :type row: RecipeRow
        :returns: The recipe with its steps.
        :rtype: Recipe
        """
        return Recipe(
            id=RecipeId(row.id),
            stage=row.stage,
            kind=row.kind,
            steps=tuple(Step.from_map(step) for step in row.steps),
            profile_id=None if row.profile_id is None else RecipeProfileId(row.profile_id),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: Recipe) -> RecipeRow:
        """Build the row of ``entity``.

        :param entity: Recipe to store.
        :type entity: Recipe
        :returns: Transient recipe row.
        :rtype: RecipeRow
        """
        return RecipeRow(
            id=entity.id,
            stage=entity.stage,
            kind=entity.kind,
            steps=[step.to_map() for step in entity.steps],
            profile_id=entity.profile_id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
