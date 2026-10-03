"""Measuring the book: the size of its text and of its page, written into the step that makes the pages alike.

``geometry.crop`` records for every page the frame of its block of text and the distance between its lines. The pages
of one book are made alike by ``geometry.normalize``, which needs a target for that distance and a size for the page,
and no step sees more than one page. This job reads what the crop of every page recorded and writes the medians into
the parameters of the normalize step of the active Geometry recipe, where the user sees them in the form and may
change them.

A page whose lines were photographed larger than another's has a larger block too, so each block is brought to the
median line height before the blocks are compared, as the normalize step will bring it. The page is that median block
with the margins round it. While the margins are measured they are shares of the block, written in pixels; once the
user sets one by hand the margins are left as they are, and the page is the median block with the margins the step has.
The line height and the page size are written either way. Changing the parameters of a recipe marks
the pages it processed stale by the rules every change of a recipe follows, and runs nothing.
"""

import math
import statistics
from typing import TYPE_CHECKING, ClassVar

from attrs import evolve, frozen

from bookreviver.domain.enums import MarginsSource, NormalizeParam, OrderMode, Stage, VersionData, VersionState
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Rect
from bookreviver.domain.values import RecipeDraft

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import PageStage, PageVersion, Recipe
    from bookreviver.domain.ids import PageVersionId, ProjectId
    from bookreviver.domain.values import MetadataMap, Step
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.services.recipes import RecipeBook
    from bookreviver.services.stage_records import StageRecords

CROP_KEY: str = 'geometry.crop'
NORMALIZE_KEY: str = 'geometry.normalize'
NO_NORMALIZE_STEP: str = 'The active recipe of the Geometry stage has no {key} step to write the measures into.'
NOTHING_TO_MEASURE: str = 'No page of the book has been cut to its block of text yet, so there is nothing to measure.'
PERCENT: float = 100.0


@frozen(kw_only=True)
class BlockMeasure:
    """What the crop of one page recorded.

    :ivar width: Width of the block of text in pixels.
    :ivar height: Height of the block of text in pixels.
    :ivar line_height: Distance between the lines of the block in pixels, or None when the crop found none.
    """

    width: float
    height: float
    line_height: float | None


class BookMeasure:
    """Reads the crop of every page of a book and writes the medians into its normalize step."""

    MARGIN_TOP_PERCENT: ClassVar[float] = 8.0
    MARGIN_BOTTOM_PERCENT: ClassVar[float] = 10.0
    MARGIN_INNER_PERCENT: ClassVar[float] = 10.0
    MARGIN_OUTER_PERCENT: ClassVar[float] = 8.0

    def __init__(self, *, uow: UnitOfWork, recipes: RecipeBook, records: StageRecords) -> None:
        """Work over the ports of one job.

        :param uow: Unit of work whose commit ends the job.
        :type uow: UnitOfWork
        :param recipes: The recipes of the project, which supply the active recipe and rewrite it.
        :type recipes: RecipeBook
        :param records: Writer of the stage records, which marks the stage of the pages stale.
        :type records: StageRecords
        """
        self._uow = uow
        self._recipes = recipes
        self._records = records

    async def run(self, project_id: ProjectId) -> int:
        """Measure the pages of the book and write the result into the parameters of the normalize step.

        :param project_id: Project whose pages are measured.
        :type project_id: ProjectId
        :returns: The number of pages that were measured.
        :rtype: int
        :raises NotFoundError: If the Geometry stage has no recipe.
        :raises ConflictError: If the active recipe has no normalize step, or no page was cut to its block yet.
        :raises InvalidParametersError: If the measured page does not fit the bounds of the step.
        """
        recipe = await self._recipes.active(project_id, Stage.GEOMETRY)
        position = next((index for index, step in enumerate(recipe.steps) if step.processor_key == NORMALIZE_KEY), None)
        if position is None:
            raise ConflictError(NO_NORMALIZE_STEP.format(key=NORMALIZE_KEY))
        records = await self._uow.page_stages.list_for_project_stage(project_id, Stage.GEOMETRY)
        measures = [
            measure for version in await self._crops(records) if (measure := self._measure_of(version)) is not None
        ]
        if not measures:
            raise ConflictError(NOTHING_TO_MEASURE)
        step = recipe.steps[position]
        measured = evolve(step, params={**step.params, **self._parameters(measures, step.params)})
        if measured.params != step.params:
            await self._write(recipe, position, measured)
        return len(measures)

    async def _crops(self, records: Sequence[PageStage]) -> list[PageVersion]:
        """Find the version the crop made on each page, by following the chain of the current version back to it.

        :param records: The records of the Geometry stage of the pages.
        :type records: Sequence[PageStage]
        :returns: The ready versions of the crop step, one for each page that has one.
        :rtype: list[PageVersion]
        """
        pending = {record.head_version_id for record in records if record.head_version_id is not None}
        crops: list[PageVersion] = []
        while pending:
            following: set[PageVersionId] = set()
            for version in await self._uow.page_versions.list_by_ids(pending):
                if version.processor.key == CROP_KEY:
                    crops.append(version)
                elif version.input_id is not None and version.stage is Stage.GEOMETRY:
                    following.add(version.input_id)
            pending = following
        return [version for version in crops if version.state is VersionState.READY]

    @staticmethod
    def _measure_of(version: PageVersion) -> BlockMeasure | None:
        """Read the block and the line height a crop recorded.

        :param version: The version the crop made.
        :type version: PageVersion
        :returns: The measure, or None for a page the crop left as it was because it found no content.
        :rtype: BlockMeasure | None
        """
        frame = version.data.get(VersionData.FRAME)
        if version.data.get(VersionData.SKIPPED) is True or not isinstance(frame, dict):
            return None
        rect = Rect.from_data(frame)
        line_height = version.data.get(VersionData.LINE_HEIGHT_PX)
        return BlockMeasure(
            width=rect.width,
            height=rect.height,
            line_height=float(line_height) if isinstance(line_height, int | float) and line_height > 0 else None,
        )

    def _parameters(self, measures: Sequence[BlockMeasure], current: MetadataMap) -> MetadataMap:
        """Work out the parameters of the normalize step from the measures of the pages.

        :param measures: The measures of the pages.
        :type measures: Sequence[BlockMeasure]
        :param current: The parameters the step has now, whose margins are kept when the user set them.
        :type current: MetadataMap
        :returns: The line height when any page has one, the size of the page, and the margins when the measure sets
                  them.
        :rtype: MetadataMap
        """
        heights = [measure.line_height for measure in measures if measure.line_height is not None]
        line_height = statistics.median(heights) if heights else None
        # A block is compared at the size the normalize step will give it, which is the one of the median line height
        factors = [
            1.0 if line_height is None or measure.line_height is None else line_height / measure.line_height
            for measure in measures
        ]
        block_width = statistics.median(
            measure.width * factor for measure, factor in zip(measures, factors, strict=True)
        )
        block_height = statistics.median(
            measure.height * factor for measure, factor in zip(measures, factors, strict=True)
        )
        margins: dict[str, int] = {
            NormalizeParam.MARGIN_TOP: math.ceil(block_height * self.MARGIN_TOP_PERCENT / PERCENT),
            NormalizeParam.MARGIN_BOTTOM: math.ceil(block_height * self.MARGIN_BOTTOM_PERCENT / PERCENT),
            NormalizeParam.MARGIN_INNER: math.ceil(block_width * self.MARGIN_INNER_PERCENT / PERCENT),
            NormalizeParam.MARGIN_OUTER: math.ceil(block_width * self.MARGIN_OUTER_PERCENT / PERCENT),
        }
        # The margins the user set stay, and the page is the median block with them
        if current.get(NormalizeParam.MARGINS_SOURCE) == MarginsSource.MANUAL:
            margins = {name: int(current[name]) for name in margins}
            written: dict[str, int] = {}
        else:
            written = margins
        parameters: dict[str, object] = {
            NormalizeParam.PAGE_WIDTH: math.ceil(
                block_width + margins[NormalizeParam.MARGIN_INNER] + margins[NormalizeParam.MARGIN_OUTER]
            ),
            NormalizeParam.PAGE_HEIGHT: math.ceil(
                block_height + margins[NormalizeParam.MARGIN_TOP] + margins[NormalizeParam.MARGIN_BOTTOM]
            ),
            **written,
        }
        if line_height is not None:
            parameters[NormalizeParam.LINE_HEIGHT] = round(line_height, 1)
        return parameters

    async def _write(self, recipe: Recipe, position: int, step: Step) -> None:
        """Store the recipe with the measured step, mark the pages it processed stale, and commit.

        :param recipe: The active recipe of the Geometry stage.
        :type recipe: Recipe
        :param position: Place of the normalize step among the steps of the recipe.
        :type position: int
        :param step: The normalize step with the measured parameters.
        :type step: Step
        :raises InvalidParametersError: If the measured page does not fit the bounds of the step.
        """
        steps = (*recipe.steps[:position], step, *recipe.steps[position + 1 :])
        # The steps keep the order they were saved in, whatever mode that was
        await self._recipes.rewrite(recipe, RecipeDraft(name=recipe.name, steps=steps, order=OrderMode.FREE))
        stale = await self._records.mark_recipe_stale(recipe.id)
        await self._uow.commit()
        await self._records.announce(recipe.project_id, stale)
