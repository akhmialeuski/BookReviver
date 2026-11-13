"""The type of a step, the processor it runs, which an operation of the recipes replaces and an undo brings back.

A step has one type, which every recipe of its stage that holds the step shares, so a replacement gives the step the new
type in each of them. The step keeps its identifier and its place, and each recipe keeps the parameters the step had
there under every type, so the parameters of a type come back with it. A type the step has not had in a recipe starts
from the defaults of its processor. What the pages keep for the step, their settings, edits and values, belongs to the
type it was made for, and the persistence reads that of the active type alone, so it is laid over the parameters again
when its type comes back and never over another type's.

A replacement is a batch of the history: one change of the type, which every page that keeps a setting or an edit of
the step under the type it leaves or the type it takes names, since what the page runs with changes on each of them, so
the undo of the batch brings the earlier type back whole. The pages that run the step, through a
recipe that has it switched on, are marked stale as a change of the recipe marks them, and no page is processed.

``StepRetyping`` is the replacement of the type of one step inside one transaction, which ``StepTypes`` opens from the
work of a ``Book.change``.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.history import ChangeSource
from bookreviver.domain.recipes import OrderMode, StepTypeChange
from bookreviver.services.page_batches import PageBatch

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.history import PageHistoryEntry
    from bookreviver.domain.ids import PageId, StepId
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.recipes import Recipe, Step
    from bookreviver.domain.stages import Stage
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.recipes import RecipeBook
    from bookreviver.services.stage_records import StageRecords


class StepTypes:
    """Replaces the types of the steps of a book, in the transactions it is handed.

    :ivar recipes: The recipes of the book and the check of their steps.
    :ivar records: Writer of the stage records, which a replacement marks stale.
    """

    def __init__(self, *, recipes: RecipeBook, records: StageRecords) -> None:
        """Work with the recipes and the records of the application.

        :param recipes: The recipes of the book and the check of their steps.
        :type recipes: RecipeBook
        :param records: Writer of the stage records.
        :type records: StageRecords
        """
        self.recipes = recipes
        self.records = records

    def open(self, repos: BookRepositories, stage: Stage, step_id: StepId) -> StepRetyping:
        """Open the replacement of the type of a step in the transaction of the caller.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param stage: Stage of the step.
        :type stage: Stage
        :param step_id: The step.
        :type step_id: StepId
        :returns: The replacement, over the recipes of the stage that hold the step.
        :rtype: StepRetyping
        :raises NotFoundError: If no recipe of the stage holds the step.
        """
        return StepRetyping(self, repos, stage, step_id)


class StepRetyping:
    """The replacement of the type of one step inside one transaction.

    :ivar types: The replacer of the types, through which the recipes and the records are reached.
    :ivar repos: Repositories of the transaction.
    :ivar stage: Stage of the step.
    :ivar step_id: The step.
    :ivar holders: The recipes of the stage that hold the step, in the order the stage lists them.
    :ivar moment: When the type is replaced.
    """

    def __init__(self, types: StepTypes, repos: BookRepositories, stage: Stage, step_id: StepId) -> None:
        """Read the recipes of the stage that hold the step.

        :param types: The replacer of the types.
        :type types: StepTypes
        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param stage: Stage of the step.
        :type stage: Stage
        :param step_id: The step.
        :type step_id: StepId
        :raises NotFoundError: If no recipe of the stage holds the step.
        """
        self.types = types
        self.repos = repos
        self.stage = stage
        self.step_id = step_id
        self.holders = [recipe for recipe in repos.recipes.list_for_stage(stage) if recipe.holds(step_id)]
        if not self.holders:
            raise NotFoundError(step_id)
        self.moment = datetime.now(UTC)

    @property
    def type_key(self) -> str:
        """Key of the processor that is the type of the step now, which every recipe that holds it shares."""
        return self._step_in(self.holders[0]).processor_key

    def replace(self, processor_key: str, order: OrderMode) -> Sequence[PageHistoryEntry]:
        """Give the step another type, as the user asks, and keep the replacement in the histories of its pages.

        :param processor_key: Key of the processor of the new type.
        :type processor_key: str
        :param order: Whether the new type is refused where it would stand off a required place, or only warned of.
        :type order: OrderMode
        :returns: The changes of the history, one for each page that keeps a setting or an edit of the step under the
                  earlier type or the new one, which are none when no page does or the type is the one the step has.
        :rtype: Sequence[PageHistoryEntry]
        :raises InvalidParametersError: If the processor does not exist or belongs to another stage, or would stand off
                                        a required place in the usual order of a recipe that holds the step.
        """
        before = self.type_key
        if processor_key == before:
            return ()
        book = self.repos.pages.order().page_ids
        leaving = self._pages_keeping_the_step(book)
        change = self._write(processor_key, order)
        taking = self._pages_keeping_the_step(book)
        pages = [page_id for page_id in book if page_id in leaving or page_id in taking]
        batch = PageBatch(repos=self.repos, source=ChangeSource.USER, moment=self.moment)
        batch.write_type(self.stage, change, before, pages)
        return batch.flush()

    def restore(self, processor_key: str) -> None:
        """Give the step back a type it had, which an undo does, wherever the step stands now.

        :param processor_key: Key of the processor of the earlier type.
        :type processor_key: str
        :raises InvalidParametersError: If the processor is no longer installed or belongs to another stage.
        """
        self._write(processor_key, OrderMode.FREE)

    def _write(self, processor_key: str, order: OrderMode) -> StepTypeChange:
        """Check the new type in every recipe that holds the step, store it, and mark the pages that run the step stale.

        :param processor_key: Key of the processor of the new type.
        :type processor_key: str
        :param order: Whether a place that the order refuses is refused or only warned of.
        :type order: OrderMode
        :returns: The replacement as stored.
        :rtype: StepTypeChange
        :raises InvalidParametersError: If the processor does not exist or belongs to another stage, or would stand off
                                        a required place in the usual order of a recipe that holds the step.
        """
        kept = self.repos.recipes.kept_params(self.step_id, processor_key)
        params = {
            recipe.id: self._checked(recipe, processor_key, kept.get(recipe.id), order).params
            for recipe in self.holders
        }
        change = StepTypeChange(step_id=self.step_id, processor_key=processor_key, params=params, moment=self.moment)
        self.repos.recipes.replace_type(change)
        for recipe in self.holders:
            if self._step_in(recipe).enabled:
                self.types.records.mark_recipe_stale(self.repos, recipe.id)
        return change

    def _checked(self, recipe: Recipe, processor_key: str, kept: MetadataMap | None, order: OrderMode) -> Step:
        """Give the step of a recipe its new type, with the parameters it had under the type there, or the defaults.

        The parameters a step starts a type with are chosen here alone: those the recipe kept for the type, or, for a
        type the step has not had in the recipe or whose kept parameters its processor refuses now, the defaults of the
        processor. A hook of a type that carries the parameters of the earlier type over would be asked here.

        :param recipe: A recipe that holds the step.
        :type recipe: Recipe
        :param processor_key: Key of the processor of the new type.
        :type processor_key: str
        :param kept: The parameters the recipe kept for the type, or None.
        :type kept: MetadataMap | None
        :param order: Whether a place that the order refuses is refused or only warned of.
        :type order: OrderMode
        :returns: The step of the new type with its checked parameters.
        :rtype: Step
        :raises InvalidParametersError: If the processor does not exist or belongs to another stage, or would stand off
                                        a required place in the usual order of the recipe.
        """
        step = evolve(self._step_in(recipe), processor_key=processor_key, params={})
        if kept is not None:
            try:
                return self.types.recipes.retyped(recipe, evolve(step, params=kept), order)
            except InvalidParametersError:
                pass
        return self.types.recipes.retyped(recipe, step, order)

    def _step_in(self, recipe: Recipe) -> Step:
        """Find the step in a recipe that holds it.

        :param recipe: A recipe that holds the step.
        :type recipe: Recipe
        :returns: The step as the recipe holds it.
        :rtype: Step
        """
        return next(step for step in recipe.steps if step.step_id == self.step_id)

    def _pages_keeping_the_step(self, pages: Sequence[PageId]) -> set[PageId]:
        """Find the pages that keep a setting or an edit of the step under the type it has now.

        :param pages: The pages of the book.
        :type pages: Sequence[PageId]
        :returns: Those of them that keep one.
        :rtype: set[PageId]
        """
        return {state.page_id for state in self.repos.page_step_states.list_for_step(pages, self.stage, self.step_id)}
