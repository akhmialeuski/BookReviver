"""Choosing the recipe each page of a run is processed by.

A run of a stage that names no recipe processes every page by its own recipe, chosen in this order: the recipe pinned to
the page, else the recipe of the first rule of the stage that matches the page, else the active recipe of the stage. A
run that names a recipe never asks here, since its pages are processed by that recipe.

The choice for all the pages of a run is made from four reads that do not depend on how many pages the run has: the
active recipe, the recipes of the stage, the rules of the stage, and the records of the stage over the whole book. The
parity of a page needs its place in the book, which is read window by window only while a rule of the stage tests the
parity, so a stage without such a rule never reads the order of the book.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.enums import RuleCondition
from bookreviver.domain.values import SliceRequest

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Page, Recipe, RecipeRule
    from bookreviver.domain.enums import Stage
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.services.recipes import RecipeBook

# How many pages are read from the book at a time, to work out the place of a page in it
PAGE_WINDOW: int = 1_000
# The conditions that test the place of a page in the book
PARITY_CONDITIONS: frozenset[RuleCondition] = frozenset({RuleCondition.ODD, RuleCondition.EVEN})


class RecipePicker:
    """Chooses the recipe of each page of a run of a stage, by the pin, the rules and the active recipe."""

    def __init__(self, *, uow: UnitOfWork, recipes: RecipeBook) -> None:
        """Choose over the ports of one job.

        :param uow: Unit of work the recipes, rules, pages and stage records are read through.
        :type uow: UnitOfWork
        :param recipes: The recipes of the project, which supply the active recipe of the stage.
        :type recipes: RecipeBook
        """
        self._uow = uow
        self._recipes = recipes

    async def pick(self, project_id: ProjectId, stage: Stage, pages: Sequence[Page]) -> dict[PageId, Recipe]:
        """Choose the recipe of every page.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param stage: The stage being run.
        :type stage: Stage
        :param pages: The pages of the run.
        :type pages: Sequence[Page]
        :returns: The recipe of each page, by page identifier.
        :rtype: dict[PageId, Recipe]
        :raises NotFoundError: If the stage has no recipe.
        """
        active = await self._recipes.active(project_id, stage)
        recipes = {recipe.id: recipe for recipe in await self._uow.recipes.list_for_stage(project_id, stage)}
        rules = [
            rule for rule in await self._uow.recipe_rules.list_for_stage(project_id, stage) if rule.recipe_id in recipes
        ]
        records = {
            record.page_id: record for record in await self._uow.page_stages.list_for_project_stage(project_id, stage)
        }
        positions = (
            await self._positions(project_id) if any(rule.condition in PARITY_CONDITIONS for rule in rules) else {}
        )
        picked: dict[PageId, Recipe] = {}
        for page in pages:
            record = records.get(page.id)
            pinned_id = None if record is None else record.pinned_recipe_id
            if pinned_id is not None and pinned_id in recipes:
                picked[page.id] = recipes[pinned_id]
                continue
            rule = self._first_match(rules, page, positions.get(page.id, 0))
            picked[page.id] = active if rule is None else recipes[rule.recipe_id]
        return picked

    @staticmethod
    def _first_match(rules: Sequence[RecipeRule], page: Page, position: int) -> RecipeRule | None:
        """Find the first rule, in the order the rules are tried, that matches the page.

        :param rules: The rules of the stage in the order they are tried.
        :type rules: Sequence[RecipeRule]
        :param page: The page.
        :type page: Page
        :param position: Place of the page in the book counted from 1, or 0 when it was not read.
        :type position: int
        :returns: The rule, or None when none matches.
        :rtype: RecipeRule | None
        """
        return next((rule for rule in rules if rule.matches(page, position)), None)

    async def _positions(self, project_id: ProjectId) -> dict[PageId, int]:
        """Read the place of every page of the book, counted from 1 in book order, window by window.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :returns: The place of each page, by page identifier.
        :rtype: dict[PageId, int]
        """
        positions: dict[PageId, int] = {}
        while True:
            offset = len(positions)
            window = await self._uow.pages.list_for_project(project_id, SliceRequest(offset=offset, limit=PAGE_WINDOW))
            positions.update((page.id, offset + index + 1) for index, page in enumerate(window.items))
            if len(positions) >= window.total or not window.items:
                return positions
