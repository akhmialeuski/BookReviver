"""Placing one step of a stage on a window of pages: what the step read and made, and why a page asks for a look.

The versions that made the current version of each page are found by walking back from it, a level at a time for all the
pages together, until a version of an earlier stage is reached, which is what the first step read. A step that finds a
number on each page, such as the angle of the deskew, is compared with the whole book, so its median is taken over every
page of the stage and not over the window alone, and the rule that tells a page that departs is the one of
``bookreviver.domain.step_measures``.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.stage_summaries import StepRow
from bookreviver.domain.step_measures import median_of, read_measure

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from bookreviver.domain.entities import Page, PageStage, PageVersion, Recipe
    from bookreviver.domain.enums import Stage, StepMeasure
    from bookreviver.domain.ids import PageId, PageVersionId, RecipeId, StepId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog


class StepRows:
    """Places one step of one stage on pages."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog, stage: Stage, step_id: StepId) -> None:
        """Place the step over the ports of one request.

        :param uow: Unit of work of the request, which is only read.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run, which say what a step measures.
        :type catalogue: ProcessorCatalog
        :param stage: The stage.
        :type stage: Stage
        :param step_id: The step, which a recipe of the stage must have.
        :type step_id: StepId
        """
        self._uow = uow
        self._catalogue = catalogue
        self._stage = stage
        self._step_id = step_id

    async def of(
        self,
        pages: Sequence[Page],
        records: Mapping[PageId, PageStage],
        heads: Mapping[PageVersionId, PageVersion],
    ) -> dict[PageId, StepRow]:
        """Place the step on each page of a window.

        :param pages: The pages of the window.
        :type pages: Sequence[Page]
        :param records: The records of the stage of those of the pages that have one, by page.
        :type records: Mapping[PageId, PageStage]
        :param heads: The current versions of the stage, by identifier.
        :type heads: Mapping[PageVersionId, PageVersion]
        :returns: The row of the step for each page of the window, with the flags that say why a page asks for a look.
        :rtype: dict[PageId, StepRow]
        :raises NotFoundError: If no recipe of the stage has the step.
        """
        if not pages:
            return {}
        project_id = pages[0].project_id
        recipes = {recipe.id: recipe for recipe in await self._uow.recipes.list_for_stage(project_id, self._stage)}
        key = next(
            (
                step.processor_key
                for recipe in recipes.values()
                for step in recipe.steps
                if step.step_id == self._step_id
            ),
            None,
        )
        if key is None:
            raise NotFoundError(self._step_id)
        measure = next((spec.measure for spec in self._catalogue.specs() if spec.key == key), None)
        if measure is None:
            placed = await self._place([page.id for page in pages], recipes, records, heads)
            return {page.id: placed[page.id] for page in pages}
        # What a step found is compared with the whole book, so the step is placed on every page of the stage
        book = {
            record.page_id: record
            for record in await self._uow.page_stages.list_for_project_stage(project_id, self._stage)
        }
        book_heads = {
            version.id: version
            for version in await self._uow.page_versions.list_by_ids(
                {record.head_version_id for record in book.values() if record.head_version_id is not None}
            )
        }
        placed = await self._place([*{page.id for page in pages} | set(book)], recipes, book, book_heads)
        median = self._median(measure, placed.values())
        return {page.id: placed[page.id].compared_with(measure, median) for page in pages}

    async def _place(
        self,
        page_ids: Sequence[PageId],
        recipes: Mapping[RecipeId, Recipe],
        records: Mapping[PageId, PageStage],
        heads: Mapping[PageVersionId, PageVersion],
    ) -> dict[PageId, StepRow]:
        """Place the step on pages, with the mark of those that have settings or an edit of their own for it.

        :param page_ids: The pages.
        :type page_ids: Sequence[PageId]
        :param recipes: The recipes of the stage, by identifier.
        :type recipes: Mapping[RecipeId, Recipe]
        :param records: The records of the stage of those of the pages that have one, by page.
        :type records: Mapping[PageId, PageStage]
        :param heads: The current versions of the stage, by identifier.
        :type heads: Mapping[PageVersionId, PageVersion]
        :returns: The row of the step for each page.
        :rtype: dict[PageId, StepRow]
        """
        found = await self._uow.page_step_states.list_for_step(page_ids, self._stage, self._step_id)
        edited = {state.page_id for state in found if state.edit is not None}
        adjusted = {state.page_id for state in found if state.params}
        chains, before = await self._chains(heads.values())
        placed: dict[PageId, StepRow] = {}
        for page_id in page_ids:
            record = records.get(page_id)
            recipe = None if record is None or record.recipe_id is None else recipes.get(record.recipe_id)
            head_id = None if record is None or record.head_version_id not in heads else record.head_version_id
            row = StepRow.of(
                self._step_id,
                recipe,
                [] if head_id is None else chains[head_id],
                None if head_id is None else before.get(head_id),
                edited=page_id in edited,
            )
            placed[page_id] = row.with_settings() if page_id in adjusted else row
        return placed

    @staticmethod
    def _median(measure: StepMeasure, rows: Collection[StepRow]) -> tuple[float, ...] | None:
        """Take the median of what the step found over the pages of the book.

        :param measure: What the step finds.
        :type measure: StepMeasure
        :param rows: The rows of the step on every page of the stage.
        :type rows: Collection[StepRow]
        :returns: The median of each number the step found, or None when too few pages have one.
        :rtype: tuple[float, ...] | None
        """
        return median_of(
            [
                reading
                for row in rows
                if row.version is not None and (reading := read_measure(measure, row.version.data)) is not None
            ]
        )

    async def _chains(
        self, heads: Collection[PageVersion]
    ) -> tuple[dict[PageVersionId, list[PageVersion]], dict[PageVersionId, PageVersion]]:
        """Find the versions that made each current version of a stage, and the version the first of them read.

        The versions are walked back a level at a time for all the pages together, as the marks of the review are.

        :param heads: The current versions of the stage over a window of pages.
        :type heads: Collection[PageVersion]
        :returns: For each current version, by its identifier, the versions of the stage that made it with the first
                  step first, and the version of an earlier stage that the first of them read, which a first version
                  without an input has none of.
        :rtype: tuple[dict[PageVersionId, list[PageVersion]], dict[PageVersionId, PageVersion]]
        """
        chains = {head.id: [head] for head in heads}
        before: dict[PageVersionId, PageVersion] = {}
        walking = {head.id: head for head in heads}
        while walking:
            inputs = {
                version.id: version
                for version in await self._uow.page_versions.list_by_ids(
                    {first.input_id for first in walking.values() if first.input_id is not None}
                )
            }
            ahead: dict[PageVersionId, PageVersion] = {}
            for head_id, first in walking.items():
                earlier = None if first.input_id is None else inputs.get(first.input_id)
                if earlier is None:
                    continue
                if earlier.stage is first.stage and earlier not in chains[head_id]:
                    chains[head_id].insert(0, earlier)
                    ahead[head_id] = earlier
                else:
                    before[head_id] = earlier
            walking = ahead
        return chains, before
