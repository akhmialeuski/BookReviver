"""Repositories of the recipes of a book and of its recipe profiles."""

from typing import TYPE_CHECKING, override

from bookreviver.adapters.persistence.sqlalchemy.mappers import RecipeMapper
from bookreviver.adapters.persistence.sqlalchemy.profile_mappers import RecipeProfileMapper
from bookreviver.adapters.persistence.sqlalchemy.repositories.base import SqlAlchemyRepository
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
    from collections.abc import Sequence

    from sqlalchemy.orm import Session

    from bookreviver.domain.stages import Stage


class SqlAlchemyRecipeRepository(SqlAlchemyRepository[Recipe, RecipeId, RecipeRow], RecipeRepository):
    """Recipes of the books, of which a stage has one for each kind."""

    def __init__(self, session: Session) -> None:
        """Create the repository over the ``recipes`` table.

        :param session: Session of the transaction.
        :type session: Session
        """
        super().__init__(rows=RecipeRows(session=session), mapper=RecipeMapper())

    @override
    def list_for_stage(self, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage by creation, ties by identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes of the stage.
        :rtype: Sequence[Recipe]
        """
        rows = self._rows.get_many(order_by=[RecipeRow.created_at.asc(), RecipeRow.id.asc()], stage=stage)
        return [self._mapper.to_entity(row) for row in rows]

    @override
    def list_all(self) -> Sequence[Recipe]:
        """Return the recipes of every stage of a book, in the order of the stages and then by creation.

        :returns: The recipes of the stages that have been used.
        :rtype: Sequence[Recipe]
        """
        rows = self._rows.get_many(order_by=[RecipeRow.created_at.asc(), RecipeRow.id.asc()])
        return sorted((self._mapper.to_entity(row) for row in rows), key=lambda recipe: recipe.stage.position)


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
