"""Mapper between the row of a recipe profile of a book and its frozen domain entity.

The profiles of the accounts in the catalogue have their own mapper in
:mod:`bookreviver.adapters.persistence.sqlalchemy.catalogue_mappers`.
"""

from typing import override

from bookreviver.adapters.persistence.sqlalchemy.mappers import RowMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import RecipeProfileRow
from bookreviver.domain.entities import RecipeProfile
from bookreviver.domain.ids import RecipeProfileId
from bookreviver.domain.values import Step


class RecipeProfileMapper(RowMapper[RecipeProfile, RecipeProfileRow]):
    """Translation of a recipe profile of a book, whose steps are a JSON list."""

    @override
    def to_entity(self, row: RecipeProfileRow) -> RecipeProfile:
        """Build the profile stored in ``row``.

        :param row: Profile row loaded from the database.
        :type row: RecipeProfileRow
        :returns: The profile with its steps.
        :rtype: RecipeProfile
        """
        return RecipeProfile(
            id=RecipeProfileId(row.id),
            stage=row.stage,
            name=row.name,
            steps=tuple(Step.from_map(step) for step in row.steps),
            order=row.order,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    @override
    def to_row(self, entity: RecipeProfile) -> RecipeProfileRow:
        """Build the row of ``entity``.

        :param entity: Profile to store.
        :type entity: RecipeProfile
        :returns: Transient profile row.
        :rtype: RecipeProfileRow
        """
        return RecipeProfileRow(
            id=entity.id,
            stage=entity.stage,
            name=entity.name,
            steps=[step.to_map() for step in entity.steps],
            order=entity.order,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )
