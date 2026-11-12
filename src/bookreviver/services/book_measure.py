"""Measuring the book: writing what the steps that read the whole book found into the recipes that hold them.

A step whose result on a page depends on the other pages has a half that reads the book (``BookStep``), such as the one
that places every page on one page size. A run of a stage lays what that half works out over the parameters the step
leaves to the book, without writing it anywhere. The ``measure-book`` job writes it into the parameters of the step in
every recipe that has one, where the user sees it in the form and may change it. What is measured and how is the
plugin's own: ``BookMeasure`` finds the steps, asks each plugin once for the steps of a stage together, since a plugin
may give the recipes of a stage one value they share, and writes the answers.

Changing the parameters of a recipe marks the pages it processed stale by the rules every change of a recipe follows,
and runs nothing.
"""

from typing import TYPE_CHECKING

from attrs import evolve, frozen

from bookreviver.core.content import BookContent
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.recipes import OrderMode, RecipeDraft

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.core.book import Book
    from bookreviver.core.plugins import BookStep
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.recipes import Recipe, Step
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.recipes import RecipeBook
    from bookreviver.services.stage_records import StageRecords

NO_MEASURING_STEP: str = 'No recipe of the book has a step that measures the book to write the measures into.'
MEASURE_OUTDATED: str = (
    'A recipe was changed or deleted while the book was measured, so nothing was written. Measure the book again.'
)


@frozen(kw_only=True)
class _Holder:
    """A step of a recipe whose plugin reads the book, which a measure writes into.

    :ivar recipe: The recipe as it was read.
    :ivar position: Place of the step among the steps of the recipe.
    :ivar book_step: The half of the plugin of the step that reads the book.
    """

    recipe: Recipe
    position: int
    book_step: BookStep

    @property
    def step(self) -> Step:
        """The step of the recipe the measure writes into."""
        return self.recipe.steps[self.position]


class BookMeasure:
    """Asks the steps that read the whole book what they found, and writes it into the recipes that hold them."""

    def __init__(self, *, recipes: RecipeBook, records: StageRecords) -> None:
        """Work over the ports of one job.

        :param recipes: The recipes of the book, which supply the recipes of a stage, rewrite them, and find the steps
                        whose plugin reads the book.
        :type recipes: RecipeBook
        :param records: Writer of the stage records, which marks the stage of the pages stale.
        :type records: StageRecords
        """
        self._recipes = recipes
        self._records = records
        self._steps = recipes.book_steps

    async def run(self, book: Book) -> None:
        """Measure the pages of the book and write the result into the parameters of the steps that read the book.

        The job reads and computes with no change open, since the results of the pages take long to read, and writes
        in one short ``Book.change`` that reads the recipes again. A recipe that was changed meanwhile, which a user
        may have done by hand while the pages were measured, is left as it is and nothing is written.

        :param book: The open book whose pages are measured.
        :type book: Book
        :raises ConflictError: If no recipe has a step that reads the book, the step has no result on any page yet, or
                               a recipe was changed or deleted while the pages were measured.
        :raises InvalidParametersError: If what was measured does not fit the bounds of the step.
        """
        holders = await self._holders(book)
        if not holders:
            raise ConflictError(NO_MEASURING_STEP)
        measured = await book.read(lambda repos: self._measured(BookContent(repos), holders))

        def work(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Write the measured steps into the recipes that were not changed since they were read.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the writing does not read.
            :type _book_record: BookRecord
            :raises ConflictError: If a recipe was changed or deleted while the pages were measured.
            :raises InvalidParametersError: If what was measured does not fit the bounds of the step.
            """
            # The steps were measured from the recipes as they were read, so a recipe saved or deleted since is not
            # written over
            stored = {recipe.id: recipe.updated_at for recipe in repos.recipes.list_all()}
            if any(stored.get(holder.recipe.id) != holder.recipe.updated_at for holder in holders):
                raise ConflictError(MEASURE_OUTDATED)
            for holder, fields in zip(holders, measured, strict=True):
                written = evolve(holder.step, params={**holder.step.params, **fields})
                if written.params != holder.step.params:
                    self._write(repos, holder, written)

        await book.change(work)

    @staticmethod
    def _measured(content: BookContent, holders: Sequence[_Holder]) -> list[MetadataMap]:
        """Ask each plugin what a measure writes into its steps, the steps of one stage together.

        :param content: The book as the transaction of the caller sees it.
        :type content: BookContent
        :param holders: The steps a measure writes into.
        :type holders: Sequence[_Holder]
        :returns: The fields to write over the parameters of each step, in the order of the holders.
        :rtype: list[MetadataMap]
        :raises ConflictError: If a step has no result on any page yet.
        """
        fields: dict[int, MetadataMap] = {}
        groups: dict[tuple[str, str], list[int]] = {}
        for index, holder in enumerate(holders):
            groups.setdefault((holder.recipe.stage, holder.step.processor_key), []).append(index)
        for indexes in groups.values():
            first = holders[indexes[0]]
            answers = first.book_step.measured(
                content, first.recipe.stage, [holders[index].step.params for index in indexes]
            )
            fields.update(zip(indexes, answers, strict=True))
        return [fields[index] for index in range(len(holders))]

    async def _holders(self, book: Book) -> list[_Holder]:
        """Return each recipe that has a step whose plugin reads the book, with the place of the step in it.

        Only the stages that an installed plugin of that kind belongs to are asked for their recipes.

        :param book: The open book whose recipes are read.
        :type book: Book
        :returns: The steps a measure writes into, the first of each recipe.
        :rtype: list[_Holder]
        :raises NotFoundError: If a stage of such a plugin has no recipe.
        """
        holders: list[_Holder] = []
        for stage in self._steps.stages():
            for recipe in await self._recipes.recipes(book, stage):
                position = self._steps.position(recipe.steps)
                book_step = None if position is None else self._steps.of(recipe.steps[position])
                if position is not None and book_step is not None:
                    holders.append(_Holder(recipe=recipe, position=position, book_step=book_step))
        return holders

    def _write(self, repos: BookRepositories, holder: _Holder, step: Step) -> None:
        """Store the recipe with the measured step, and mark the pages it processed stale, for the caller.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param holder: The recipe and the place of the step in it.
        :type holder: _Holder
        :param step: The step with the measured parameters.
        :type step: Step
        :raises InvalidParametersError: If what was measured does not fit the bounds of the step.
        """
        recipe, position = holder.recipe, holder.position
        steps = (*recipe.steps[:position], step, *recipe.steps[position + 1 :])
        # The steps keep the order they were saved in, whatever mode that was
        self._recipes.rewrite(repos, recipe, RecipeDraft(steps=steps, order=OrderMode.FREE))
        self._records.mark_recipe_stale(repos, recipe.id)
