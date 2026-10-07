"""Measuring the book: the size of its text and of its page, from the content box every page was placed by.

``geometry.normalize`` records for every page the content box it found on its input and the distance between the
lines of the box. The pages of one book are made alike by the same step, which needs a target for that distance and a
size for the page, and no step sees more than one page. ``BookBlocks`` reads what the step recorded on the current
version of every page, and ``BookSize`` works the target and the page out of it, in one place for the two ways it is
used. The ``measure-book`` job writes them into the parameters of the normalize step of the active Geometry recipe,
where the user sees them in the form and may change them. A run of the stage, and the preview of the step, lay them over
the parameters that are 0, which is the size by the book, without writing them anywhere, so a book needs no press of the
button to have one page size.

A page whose lines were photographed larger than another's has a larger block too, so each block is brought to the
median line height before the blocks are compared, as the normalize step will bring it: by the same rule, which leaves a
block whose line height is farther from the target than the step allows (``max_scale_change``) at its own size, since
the step leaves its page so. The page is the largest of those
blocks with the margins round it, so no page has a block that does not fit. The margins are lengths of the paper, in
millimetres, which the step turns into pixels by the resolution of each page; the page is sized by the largest of those
resolutions, or by the width of the block for pages that have none, as ``MarginScale`` works it out for the step too.
While the margins are measured they are shares of the block, written in millimetres; once the user sets one by hand the
margins are left as they are, and the page is the largest block with the margins the step has. The line height and the
page size are written either way. Changing the parameters of a recipe marks the pages it processed stale by the rules
every change of a recipe follows, and runs nothing.
"""

import math
import statistics
from collections import Counter
from typing import TYPE_CHECKING, ClassVar, Self

from attrs import evolve, frozen

from bookreviver.domain.enums import MarginsSource, NormalizeParam, OrderMode, Stage, VersionData, VersionState
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.geometry import Rect
from bookreviver.domain.margins import DEFAULT_MARGINS_MM, MarginScale
from bookreviver.domain.text_scale import DEFAULT_MAX_SCALE_CHANGE, scale_factor
from bookreviver.domain.values import RecipeDraft

if TYPE_CHECKING:
    from collections.abc import Collection, Mapping, Sequence

    from bookreviver.domain.entities import PageStage, PageVersion, Recipe
    from bookreviver.domain.ids import PageId, PageVersionId, ProjectId
    from bookreviver.domain.values import MetadataMap, Step
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.services.recipes import RecipeBook
    from bookreviver.services.stage_records import StageRecords

NORMALIZE_KEY: str = 'geometry.normalize'
NO_NORMALIZE_STEP: str = 'The active recipe of the Geometry stage has no {key} step to write the measures into.'
NOTHING_TO_MEASURE: str = (
    'Margins has not placed any page of the book yet, so there is nothing to measure. Run the Geometry stage first.'
)
PERCENT: float = 100.0
# The fields of the normalize step that are 0 until the book gives them a value
BOOK_FIELDS: tuple[NormalizeParam, ...] = (
    NormalizeParam.PAGE_WIDTH,
    NormalizeParam.PAGE_HEIGHT,
    NormalizeParam.LINE_HEIGHT,
)


def wants_the_book(params: MetadataMap) -> bool:
    """Tell whether a field of the normalize step is 0, which the book fills in when the step runs.

    :param params: The parameters of the step with the defaults of the processor filled in.
    :type params: MetadataMap
    :returns: Whether the page size or the line height is left to the book.
    :rtype: bool
    """
    return not all(params.get(name) for name in BOOK_FIELDS)


@frozen(kw_only=True)
class BlockMeasure:
    """What the normalize step recorded of the content box of one page.

    :ivar width: Width of the content box in pixels of the full image the step read.
    :ivar height: Height of the content box in the same pixels.
    :ivar line_height: Distance between the lines of the box in those pixels, or None when the step found none.
    :ivar dpi: Resolution of the pixels of the full image, or None when the page has none.
    """

    width: float
    height: float
    line_height: float | None
    dpi: float | None = None

    @classmethod
    def of(cls, version: PageVersion) -> Self | None:
        """Read the content box and the line height a version of the normalize step recorded.

        :param version: The version the step made.
        :type version: PageVersion
        :returns: The measure, or None for a page the step placed whole for want of content, or passed unchanged.
        :rtype: Self | None
        """
        box = version.data.get(VersionData.CONTENT_BOX)
        if version.data.get(VersionData.SKIPPED) is True or not isinstance(box, dict):
            return None
        rect = Rect.from_data(box)
        recorded = version.data.get(VersionData.LINE_HEIGHT_PX)
        factor = version.data.get(VersionData.BLOCK_SCALE)
        recorded_dpi = version.data.get(VersionData.DPI)
        line_height = dpi = None
        # The step records the distance and the resolution after it scaled the box, so they are brought back to the
        # pixels of the page itself
        if isinstance(factor, int | float) and factor > 0:
            if isinstance(recorded, int | float) and recorded > 0:
                line_height = float(recorded) / float(factor)
            if isinstance(recorded_dpi, int | float) and recorded_dpi > 0:
                dpi = float(recorded_dpi) / float(factor)
        return cls(width=rect.width, height=rect.height, line_height=line_height, dpi=dpi)


@frozen(kw_only=True)
class PageSetting:
    """The line height and the page size a page of the book was placed by.

    :ivar line_height: Distance between the lines the page was brought to, in pixels.
    :ivar width: Width of the page in pixels.
    :ivar height: Height of the page in pixels.
    """

    line_height: float
    width: int
    height: int

    @classmethod
    def of(cls, version: PageVersion) -> Self | None:
        """Read what a version of the normalize step was placed by, from the parameters it ran with.

        :param version: The version the step made.
        :type version: PageVersion
        :returns: The setting, or None for a page that ran with a size or a line height of 0, which is no setting.
        :rtype: Self | None
        """
        line_height, width, height = (
            version.params.get(name)
            for name in (NormalizeParam.LINE_HEIGHT, NormalizeParam.PAGE_WIDTH, NormalizeParam.PAGE_HEIGHT)
        )
        if not (
            isinstance(line_height, int | float)
            and isinstance(width, int)
            and isinstance(height, int)
            and line_height > 0
            and width > 0
            and height > 0
        ):
            return None
        return cls(line_height=float(line_height), width=width, height=height)


@frozen(kw_only=True)
class BookBlock:
    """The box that holds the content box of every page of a book, once each is brought to one line height.

    :ivar width: Width of the box in pixels of the page of the book.
    :ivar height: Height of the box in the same pixels.
    :ivar scale: The pixels of the page of the book in a millimetre of its paper.
    """

    width: float
    height: float
    scale: MarginScale


class BookSize:
    """Works the target line height and the size of the page out of the content boxes of a book."""

    MARGIN_SHARES: ClassVar[Mapping[NormalizeParam, float]] = {
        NormalizeParam.MARGIN_TOP: 8.0,
        NormalizeParam.MARGIN_BOTTOM: 10.0,
        NormalizeParam.MARGIN_INNER: 10.0,
        NormalizeParam.MARGIN_OUTER: 8.0,
    }

    def __init__(self, measures: Sequence[BlockMeasure]) -> None:
        """Take the line height of the book and bring every box to it.

        :param measures: The content boxes of the pages, at least one.
        :type measures: Sequence[BlockMeasure]
        """
        heights = [measure.line_height for measure in measures if measure.line_height is not None]
        self._median_line_height = statistics.median(heights) if heights else None
        self._measures = measures

    def measured(self, current: MetadataMap) -> MetadataMap:
        """Work out the parameters the measure of the book writes.

        :param current: The parameters the step has now, whose margins are kept when the user set them.
        :type current: MetadataMap
        :returns: The line height when any page has one, the size of the page, and the margins, in millimetres, when the
                  measure sets them.
        :rtype: MetadataMap
        """
        block = self._block(self._median_line_height, current)
        along = {NormalizeParam.MARGIN_TOP: block.height, NormalizeParam.MARGIN_BOTTOM: block.height}
        # The margins are shares of the block, in the millimetres of the paper, to a tenth of a millimetre
        margins: dict[str, float] = {
            name: round(block.scale.millimetres(along.get(name, block.width) * share / PERCENT), 1)
            for name, share in self.MARGIN_SHARES.items()
        }
        # The margins the user set stay, and the page is the largest block with them
        if current.get(NormalizeParam.MARGINS_SOURCE) == MarginsSource.MANUAL:
            margins = {name: float(current[name]) for name in margins}
            written: dict[str, float] = {}
        else:
            written = margins
        parameters: dict[str, object] = {**self._page(block, margins), **written}
        if self._median_line_height is not None:
            parameters[NormalizeParam.LINE_HEIGHT] = round(self._median_line_height, 1)
        return parameters

    def by_the_book(self, current: MetadataMap, held: PageSetting | None = None) -> MetadataMap:
        """Work out the fields of the step that are 0, which is to say by the book, for a run that lays them over it.

        The margins and every field the user gave stay as they are. The line height the user gave is the one the boxes
        are brought to, so the page holds the boxes as they will be placed. A run of some pages of a book whose other
        pages are placed already takes the line height of those pages and does not make a page smaller than theirs, so
        the pages it makes are of the size the rest have, unless a box of its own does not fit that size.

        :param current: The parameters the step has, with the defaults of the processor filled in.
        :type current: MetadataMap
        :param held: The line height and the page size the other pages of the book have, or None for a run of the whole
                     book, which works them out from the boxes.
        :type held: PageSetting | None
        :returns: The line height, the width and the height of the page, each only where the step has 0.
        :rtype: MetadataMap
        """
        given = current.get(NormalizeParam.LINE_HEIGHT)
        if isinstance(given, int | float) and given > 0:
            line_height: float | None = float(given)
        elif held is not None:
            line_height = held.line_height
        else:
            line_height = self._median_line_height
        # The page is placed with the line height it is written with, which has a tenth of a pixel
        line_height = None if line_height is None else round(line_height, 1)
        margins: dict[str, float] = {
            name: float(current.get(name, default)) for name, default in DEFAULT_MARGINS_MM.items()
        }
        page = self._page(self._block(line_height, current), margins)
        sizes: dict[str, object] = {
            NormalizeParam.PAGE_WIDTH: page[NormalizeParam.PAGE_WIDTH]
            if held is None
            else max(page[NormalizeParam.PAGE_WIDTH], held.width),
            NormalizeParam.PAGE_HEIGHT: page[NormalizeParam.PAGE_HEIGHT]
            if held is None
            else max(page[NormalizeParam.PAGE_HEIGHT], held.height),
        }
        if line_height is not None:
            sizes[NormalizeParam.LINE_HEIGHT] = line_height
        return {name: value for name, value in sizes.items() if not current.get(name)}

    @staticmethod
    def _page(block: BookBlock, margins: Mapping[str, float]) -> dict[str, int]:
        """Give the size of the page that holds a box with margins round it.

        :param block: The box of the book.
        :type block: BookBlock
        :param margins: The four margins in millimetres, by the name of the parameter that holds each.
        :type margins: Mapping[str, float]
        :returns: The width and the height of the page in pixels, by the name of the parameter that holds each.
        :rtype: dict[str, int]
        """
        side = block.scale.pixels(margins[NormalizeParam.MARGIN_INNER] + margins[NormalizeParam.MARGIN_OUTER])
        vertical = block.scale.pixels(margins[NormalizeParam.MARGIN_TOP] + margins[NormalizeParam.MARGIN_BOTTOM])
        return {
            NormalizeParam.PAGE_WIDTH: math.ceil(block.width + side),
            NormalizeParam.PAGE_HEIGHT: math.ceil(block.height + vertical),
        }

    def _block(self, line_height: float | None, current: MetadataMap) -> BookBlock:
        """Give the largest content box of the book once every box is brought to a line height, as the step does.

        A box whose line height is too far from the target is left at its own size, since the step leaves its page so,
        and its resolution stays as it is. The pixels in a millimetre are those of the page that has the most of them,
        whose margins are the longest, so no page of the book has a margin that does not fit the page. Pages that have
        no resolution take the width of the box for ``NOMINAL_BLOCK_MM`` millimetres.

        :param line_height: The distance between the lines the boxes are brought to, or None to leave them as they are.
        :type line_height: float | None
        :param current: The parameters of the step, which hold the largest change of size it allows a page.
        :type current: MetadataMap
        :returns: The box that holds every box, which has the width and the height of the largest of each.
        :rtype: BookBlock
        """
        max_change = float(current.get(NormalizeParam.MAX_SCALE_CHANGE, DEFAULT_MAX_SCALE_CHANGE))
        # A page the step leaves unscaled keeps its own size, which is the factor 1
        factors = [
            1.0
            if line_height is None or measure.line_height is None
            else scale_factor(measure.line_height, line_height, max_change) or 1.0
            for measure in self._measures
        ]
        width = max(measure.width * factor for measure, factor in zip(self._measures, factors, strict=True))
        height = max(measure.height * factor for measure, factor in zip(self._measures, factors, strict=True))
        # The resolution of a box after it is brought to the line height
        dpis = [
            measure.dpi * factor
            for measure, factor in zip(self._measures, factors, strict=True)
            if measure.dpi is not None
        ]
        return BookBlock(
            width=width, height=height, scale=MarginScale.from_dpi(max(dpis)) if dpis else MarginScale.from_block(width)
        )


class BookBlocks:
    """Reads the content boxes the normalize step recorded on the current versions of the pages of a book."""

    def __init__(self, uow: UnitOfWork) -> None:
        """Read through the unit of work of a job or a request.

        :param uow: Unit of work the records and the versions are read through.
        :type uow: UnitOfWork
        """
        self._uow = uow

    async def read(
        self, project_id: ProjectId, stage: Stage, *, leaving_out: Collection[PageId] = ()
    ) -> dict[PageId, BlockMeasure]:
        """Find the measure of every page whose current version came through the normalize step.

        :param project_id: Project whose pages are read.
        :type project_id: ProjectId
        :param stage: The stage of the normalize step.
        :type stage: Stage
        :param leaving_out: Pages whose measure is not wanted, since a fresher one stands in its place.
        :type leaving_out: Collection[PageId]
        :returns: The measure of each page that has one, by page.
        :rtype: dict[PageId, BlockMeasure]
        """
        measures: dict[PageId, BlockMeasure] = {}
        for version in await self._versions(project_id, stage, leaving_out):
            if (measure := BlockMeasure.of(version)) is not None:
                measures[version.page_id] = measure
        return measures

    async def by_the_book(
        self, project_id: ProjectId, stage: Stage, params: MetadataMap, fresh: Mapping[PageId, BlockMeasure]
    ) -> MetadataMap:
        """Work out the fields of the normalize step that are 0, from the boxes of the pages the step has placed.

        When the pages that were not just measured hold a line height and a page size, which they do after a run of
        the whole book, the pages that were are placed by those, so that a page run alone is as large as the others.

        :param project_id: Project whose pages make the book.
        :type project_id: ProjectId
        :param stage: The stage of the step.
        :type stage: Stage
        :param params: The parameters of the normalize step with the defaults of the processor filled in.
        :type params: MetadataMap
        :param fresh: The boxes of the pages a run has just placed, which stand in place of those the pages held.
        :type fresh: Mapping[PageId, BlockMeasure]
        :returns: The fields to lay over the parameters, none when nothing is 0 or no page has a box.
        :rtype: MetadataMap
        """
        if not wants_the_book(params):
            return {}
        others = await self._versions(project_id, stage, fresh)
        held = Counter(setting for version in others if (setting := PageSetting.of(version)) is not None)
        if held and fresh:
            return BookSize(list(fresh.values())).by_the_book(params, held.most_common(1)[0][0])
        measures = [
            *(measure for version in others if (measure := BlockMeasure.of(version)) is not None),
            *fresh.values(),
        ]
        return BookSize(measures).by_the_book(params) if measures else {}

    async def _versions(
        self, project_id: ProjectId, stage: Stage, leaving_out: Collection[PageId]
    ) -> list[PageVersion]:
        """Find the current version of the normalize step of every page but some.

        :param project_id: Project whose pages are read.
        :type project_id: ProjectId
        :param stage: The stage of the step.
        :type stage: Stage
        :param leaving_out: Pages that are not read.
        :type leaving_out: Collection[PageId]
        :returns: The ready versions, one for each page that has one.
        :rtype: list[PageVersion]
        """
        left_out = set(leaving_out)
        records = [
            record
            for record in await self._uow.page_stages.list_for_project_stage(project_id, stage)
            if record.page_id not in left_out
        ]
        return await self._normalized(records)

    async def _normalized(self, records: Sequence[PageStage]) -> list[PageVersion]:
        """Find the version the normalize step made on each page, by following the current version back to it.

        :param records: The records of the stage of the pages.
        :type records: Sequence[PageStage]
        :returns: The ready versions of the normalize step, one for each page that has one.
        :rtype: list[PageVersion]
        """
        pending = {record.head_version_id for record in records if record.head_version_id is not None}
        found: list[PageVersion] = []
        while pending:
            following: set[PageVersionId] = set()
            for version in await self._uow.page_versions.list_by_ids(pending):
                if version.processor.key == NORMALIZE_KEY:
                    found.append(version)
                elif version.input_id is not None and version.stage is Stage.GEOMETRY:
                    following.add(version.input_id)
            pending = following
        return [version for version in found if version.state is VersionState.READY]


class BookMeasure:
    """Reads the content boxes of every page of a book and writes the largest into its normalize step."""

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
        :raises ConflictError: If the active recipe has no normalize step, or the step has placed no page yet.
        :raises InvalidParametersError: If the measured page does not fit the bounds of the step.
        """
        recipe = await self._recipes.active(project_id, Stage.GEOMETRY)
        position = next((index for index, step in enumerate(recipe.steps) if step.processor_key == NORMALIZE_KEY), None)
        if position is None:
            raise ConflictError(NO_NORMALIZE_STEP.format(key=NORMALIZE_KEY))
        measures = list((await BookBlocks(self._uow).read(project_id, Stage.GEOMETRY)).values())
        if not measures:
            raise ConflictError(NOTHING_TO_MEASURE)
        step = recipe.steps[position]
        measured = evolve(step, params={**step.params, **BookSize(measures).measured(step.params)})
        if measured.params != step.params:
            await self._write(recipe, position, measured)
        return len(measures)

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
