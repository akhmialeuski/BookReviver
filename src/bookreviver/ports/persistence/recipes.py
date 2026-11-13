"""The repository ports of the recipes of a book and of the recipe profiles they can be made from."""

from abc import abstractmethod
from typing import TYPE_CHECKING

from bookreviver.domain.ids import (
    RecipeId,
    RecipeProfileId,
)
from bookreviver.domain.recipes import Recipe, RecipeProfile
from bookreviver.ports.persistence.base import Repository

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.stages import Stage


class RecipeRepository(Repository[Recipe, RecipeId]):
    """Recipes of the book; a stage has at most one recipe for each kind, which the database keeps.

    Deleting a recipe leaves the page stages it processed without their recipe.
    """

    @abstractmethod
    def list_for_stage(self, stage: Stage) -> Sequence[Recipe]:
        """Return the recipes of one stage of a book, by creation, ties by identifier.

        :param stage: The stage.
        :type stage: Stage
        :returns: The recipes of the stage, one for each kind, or none before the stage has been used.
        :rtype: Sequence[Recipe]
        """

    @abstractmethod
    def list_all(self) -> Sequence[Recipe]:
        """Return the recipes of every stage of a book, in the order of the stages and then as ``list_for_stage``.

        :returns: The recipes of the stages that have been used.
        :rtype: Sequence[Recipe]
        """


class RecipeProfileRepository(Repository[RecipeProfile, RecipeProfileId]):
    """The recipe profiles of the book, which the recipes of the book may name as the profile they were made from.

    A recipe names its profile by identifier alone, so deleting a profile clears the name in the recipes of the book in
    the same transaction, which the service that deletes it does.
    """

    @abstractmethod
    def list_all(self, stage: Stage | None = None) -> Sequence[RecipeProfile]:
        """Return the profiles of the book in the order of the stages, then by creation, ties by identifier.

        :param stage: The stage whose profiles are wanted, or None for every stage.
        :type stage: Stage | None
        :returns: The profiles of the book.
        :rtype: Sequence[RecipeProfile]
        """
