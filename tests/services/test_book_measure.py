"""Tests for the job that measures the book: the content boxes Margins placed, written into the normalize step."""

import math
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    JobKind,
    JobState,
    MarginsSource,
    NormalizeParam,
    Stage,
    StageState,
    VersionData,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.geometry import Rect
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.margins import MM_PER_INCH
from bookreviver.domain.values import PageStageKey, ProcessorRef, RecipeDraft, StageRun, Step
from tests.helpers.builders import make_page_stage, make_page_version

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Job, Page, Project, Recipe
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

CROP_KEY: str = 'geometry.crop'
NORMALIZE_KEY: str = 'geometry.normalize'
VERSION_ID_DIGITS: int = 16
FIRST_KEY: str = 'a0'
BUSY_MESSAGE: str = 'project is busy'
# The pages of the first test: a block and a line height each, the block being the same size when the lines are brought
# to the median one, which is 30 pixels, so the block is 300 by 600 on all three. Each line height is within the quarter
# the step scales a page by, since a page farther from the target keeps its size and would not be brought to it
BLOCKS: tuple[tuple[float, float, float], ...] = ((250, 500, 25), (300, 600, 30), (360, 720, 36))
MEDIAN_LINE_HEIGHT_PX: float = 30.0
# The block 300 by 600 plus the margins, which are 8, 10, 10 and 8 percent of the block. The pages carry no resolution,
# so the block is taken for 100 mm wide, a millimetre is 3 pixels, and the margins are 16, 20, 10 and 8 mm
PIXELS_PER_MM: int = 3
EXPECTED_PAGE: dict[str, float] = {
    NormalizeParam.PAGE_WIDTH: 354,
    NormalizeParam.PAGE_HEIGHT: 708,
    NormalizeParam.MARGIN_TOP: 16.0,
    NormalizeParam.MARGIN_BOTTOM: 20.0,
    NormalizeParam.MARGIN_INNER: 10.0,
    NormalizeParam.MARGIN_OUTER: 8.0,
}
TOO_WIDE_PX: float = 30_000
# Three pages on the target of 30, one a tenth below it that the step scales, and one a third below it, which the step
# leaves as it is unless it is allowed to change the size by half
FAR_PAGE_BLOCKS: tuple[tuple[float, float, float], ...] = (
    (300, 600, 30),
    (300, 600, 30),
    (300, 600, 30),
    (330, 660, 27),
    (300, 600, 20),
)
# The margins the measure writes add 18 percent to the block each way
MARGINS_SHARE: float = 1.18
SIZE_TOLERANCE_PX: float = 3.0


def version_id() -> PageVersionId:
    """Make the identifier of a version.

    :returns: Sixteen hexadecimal digits that are not any other version's.
    :rtype: PageVersionId
    """
    return PageVersionId(uuid4().hex[:VERSION_ID_DIGITS])


async def placed_page(
    kit: ProcessingKit,
    project: Project,
    recipe: Recipe,
    order_key: str,
    data: dict[str, object],
) -> Page:
    """Commit a page whose Geometry stage stands on a normalize version with the given data.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: Project owning the page.
    :type project: Project
    :param recipe: Active recipe of the Geometry stage, which the page was processed by.
    :type recipe: Recipe
    :param order_key: Order key of the page.
    :type order_key: str
    :param data: The data the normalize version recorded.
    :type data: dict[str, object]
    :returns: The page.
    :rtype: Page
    """
    page, _ = await kit.seed_scan_page(project, order_key=order_key)
    normalize = evolve(
        make_page_version(page_id=page.id),
        id=version_id(),
        stage=Stage.GEOMETRY,
        processor=ProcessorRef(key=NORMALIZE_KEY, version='2'),
        state=VersionState.READY,
        data=data,
    )
    uow = kit.uow()
    await uow.page_versions.add(normalize)
    await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=recipe.id, head_version_id=normalize.id))
    await uow.commit()
    return page


def block_data(width: float, height: float, line_height: float | None, dpi: float | None = None) -> dict[str, object]:
    """Build the data the normalize step records for a page it placed at the scale of the page itself.

    :param width: Width of the content box in pixels.
    :type width: float
    :param height: Height of the content box in pixels.
    :type height: float
    :param line_height: Distance between the lines in pixels, or None when the step found none.
    :type line_height: float | None
    :param dpi: Resolution of the page, or None for a page that has none.
    :type dpi: float | None
    :returns: The box, the factor 1, the confidence, that the page was not skipped, and the line height and the
              resolution when there are some.
    :rtype: dict[str, object]
    """
    data: dict[str, object] = {
        VersionData.CONTENT_BOX: Rect(left=10, top=20, width=width, height=height).to_data(),
        VersionData.BLOCK_SCALE: 1.0,
        VersionData.CONFIDENCE: 1.0,
        VersionData.SKIPPED: False,
    }
    if line_height is not None:
        data[VersionData.LINE_HEIGHT_PX] = line_height
    if dpi is not None:
        data[VersionData.DPI] = dpi
    return data


async def measure(kit: ProcessingKit, actor: Actor, project: Project) -> Job:
    """Start the measure of the book and let the worker do it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to measure.
    :type project: Project
    :returns: The job as it stands when the worker is done.
    :rtype: Job
    """
    job = await kit.service().start_measure(actor, project.id)
    await kit.jobs().measure_book(job.id)
    return await kit.uow().jobs.get(job.id)


async def normalize_params(kit: ProcessingKit, actor: Actor, project: Project) -> dict[str, object]:
    """Read the parameters of the normalize step of the active Geometry recipe.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: The project.
    :type project: Project
    :returns: The parameters of the step.
    :rtype: dict[str, object]
    """
    recipe = await kit.service().recipe(actor, project.id, Stage.GEOMETRY)
    [step] = [step for step in recipe.steps if step.processor_key == NORMALIZE_KEY]
    return dict(step.params)


async def set_normalize_params(kit: ProcessingKit, actor: Actor, project: Project, changes: dict[str, object]) -> None:
    """Save the active Geometry recipe with some parameters of its normalize step changed, as the form does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: The project.
    :type project: Project
    :param changes: The parameters to set.
    :type changes: dict[str, object]
    """
    recipe = await kit.service().recipe(actor, project.id, Stage.GEOMETRY)
    steps = [
        evolve(step, params={**step.params, **changes}) if step.processor_key == NORMALIZE_KEY else step
        for step in recipe.steps
    ]
    await kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, RecipeDraft(name=recipe.name, steps=steps))


class TestMeasureBook:
    """Tests for the ``measure-book`` job."""

    async def test_the_line_height_and_the_page_of_the_pages_are_written_into_the_normalize_step(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the median line height and a page of the largest block with its margins reach the parameters.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        for index, (width, height, line_height) in enumerate(BLOCKS):
            await placed_page(fx_cv_kit, project, recipe, f'a{index}', block_data(width, height, line_height))
        before = await normalize_params(fx_cv_kit, actor, project)
        job = await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        expect((job.kind, job.state) == (JobKind.MEASURE_BOOK, JobState.SUCCEEDED))
        expect(job.progress.done == len(BLOCKS))
        expect(after[NormalizeParam.LINE_HEIGHT] == pytest.approx(MEDIAN_LINE_HEIGHT_PX))
        expect({name: after[name] for name in EXPECTED_PAGE} == EXPECTED_PAGE)
        # What the measure does not know is left as the user set it
        measured = {*EXPECTED_PAGE, NormalizeParam.LINE_HEIGHT}
        expect(
            {n: v for n, v in after.items() if n not in measured}
            == {n: v for n, v in before.items() if n not in measured}
        )
        assert_expectations()

    async def test_a_book_with_a_resolution_gets_its_margins_in_the_millimetres_of_that_resolution(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify scans at 300 and 360 dpi of one paper measure to one block in millimetres, and the page holds them.

        The blocks are the same paper at two resolutions, so once their lines are brought to the median one both are
        2200 by 3300 pixels of 330 dpi, and the margins, which are shares of that block, are the same in millimetres.
        Both line heights are within the quarter the step scales a page by.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        for index, (width, height, line_height, dpi) in enumerate(((2000, 3000, 30, 300), (2400, 3600, 36, 360))):
            data = block_data(width, height, line_height, dpi)
            await placed_page(fx_cv_kit, project, recipe, f'a{index}', data)
        await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        pixels_per_mm = 330 / MM_PER_INCH
        # 10 percent of the width, 8 percent of the width, 8 percent of the height and 10 percent of the height
        expect(after[NormalizeParam.MARGIN_INNER] == pytest.approx(220 / pixels_per_mm, abs=0.05))
        expect(after[NormalizeParam.MARGIN_OUTER] == pytest.approx(176 / pixels_per_mm, abs=0.05))
        expect(after[NormalizeParam.MARGIN_TOP] == pytest.approx(264 / pixels_per_mm, abs=0.05))
        expect(after[NormalizeParam.MARGIN_BOTTOM] == pytest.approx(330 / pixels_per_mm, abs=0.05))
        # The margins are rounded to a tenth of a millimetre, which is half a pixel at this resolution
        expect(after[NormalizeParam.PAGE_WIDTH] == pytest.approx(2200 + 220 + 176, abs=2))
        assert_expectations()

    async def test_the_page_holds_the_largest_block_of_the_book_in_each_direction(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the page is as wide as the widest block and as high as the highest, which are of two pages.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        for index, (width, height) in enumerate(((300, 500), (260, 640), (280, 600))):
            await placed_page(fx_cv_kit, project, recipe, f'a{index}', block_data(width, height, MEDIAN_LINE_HEIGHT_PX))
        await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        # The margins are 8, 10, 10 and 8 percent of the largest block, which is 300 pixels wide and 3 of them a millimetre:
        # 10 and 8 mm at the sides, and 17.1 and 21.3 mm at the top and the bottom
        expect(after[NormalizeParam.PAGE_WIDTH] == 300 + (10 + 8) * PIXELS_PER_MM)
        expect(after[NormalizeParam.PAGE_HEIGHT] == math.ceil(640 + (17.1 + 21.3) * PIXELS_PER_MM))
        assert_expectations()

    @pytest.mark.parametrize(
        ('limit', 'width'),
        [(None, 366.67 * MARGINS_SHARE), (50.0, 450.0 * MARGINS_SHARE)],
        ids=['the-limit-the-step-starts-with', 'a-limit-the-user-set'],
    )
    async def test_a_page_the_step_leaves_unscaled_is_measured_at_its_own_size(
        self, fx_cv_kit: ProcessingKit, limit: float | None, width: float
    ) -> None:
        """Verify the page is sized by the boxes as the step places them, which leaves a far page at its own size.

        Three pages are on the target of 30 pixels, one is a tenth below it and is scaled, and one is at 20, a third
        below it, which the default limit of 25 percent leaves at 300 by 600; a limit of 50 percent scales it to 450.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        :param limit: The largest change of size the step allows, or None to keep the one the step starts with.
        :type limit: float | None
        :param width: The width the page of the book comes out at, in pixels.
        :type width: float
        """
        actor, project = await fx_cv_kit.seed_project()
        if limit is not None:
            await set_normalize_params(fx_cv_kit, actor, project, {NormalizeParam.MAX_SCALE_CHANGE: limit})
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        for index, (box_width, box_height, line_height) in enumerate(FAR_PAGE_BLOCKS):
            await placed_page(fx_cv_kit, project, recipe, f'a{index}', block_data(box_width, box_height, line_height))
        await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        expect(after[NormalizeParam.PAGE_WIDTH] == pytest.approx(width, abs=SIZE_TOLERANCE_PX))
        expect(after[NormalizeParam.PAGE_HEIGHT] == pytest.approx(width * 2, abs=SIZE_TOLERANCE_PX))
        assert_expectations()

    async def test_the_pages_the_recipe_processed_go_out_of_date_and_are_announced(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a change of the parameters marks the Geometry stage of the pages stale, as every change of a recipe does.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        pages = [
            await placed_page(fx_cv_kit, project, recipe, f'a{index}', block_data(width, height, line_height))
            for index, (width, height, line_height) in enumerate(BLOCKS)
        ]
        await measure(fx_cv_kit, actor, project)
        records = [await fx_cv_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY)) for page in pages]
        announced = [event for event in fx_cv_kit.events.published if isinstance(event, PageStageChanged)]
        expect(all(record.state is StageState.STALE for record in records))
        expect({event.stage.page_id for event in announced} == {page.id for page in pages})
        assert_expectations()

    async def test_measuring_again_changes_nothing_and_marks_nothing(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a measure that finds the numbers the step has leaves the recipe and the pages as they are.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        page = await placed_page(fx_cv_kit, project, recipe, FIRST_KEY, block_data(300, 600, 30))
        await measure(fx_cv_kit, actor, project)
        written = await fx_cv_kit.uow().recipes.get(recipe.id)
        uow = fx_cv_kit.uow()
        await uow.page_stages.save(
            evolve(await uow.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY)), state=StageState.FRESH)
        )
        await uow.commit()
        job = await measure(fx_cv_kit, actor, project)
        again = await fx_cv_kit.uow().recipes.get(recipe.id)
        record = await fx_cv_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(job.state is JobState.SUCCEEDED)
        expect(again.steps == written.steps)
        expect(again.updated_at == written.updated_at)
        expect(record.state is StageState.FRESH)
        assert_expectations()

    async def test_a_page_margins_found_no_box_for_or_that_has_no_line_height_is_handled(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a page with no content does not count, and a page with no line height counts by its block alone.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        await placed_page(fx_cv_kit, project, recipe, FIRST_KEY, block_data(300, 600, 30))
        await placed_page(fx_cv_kit, project, recipe, 'a1', block_data(300, 600, None))
        await placed_page(fx_cv_kit, project, recipe, 'a2', {VersionData.SKIPPED: True, VersionData.CONFIDENCE: 0.0})
        job = await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        expect(job.progress.done == 2)
        expect(after[NormalizeParam.LINE_HEIGHT] == pytest.approx(MEDIAN_LINE_HEIGHT_PX))
        expect(after[NormalizeParam.PAGE_WIDTH] == EXPECTED_PAGE[NormalizeParam.PAGE_WIDTH])
        assert_expectations()

    async def test_a_book_none_of_whose_pages_margins_placed_has_nothing_to_measure(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the job fails with the reason, and the recipe is left as it was.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        before = await normalize_params(fx_cv_kit, actor, project)
        job = await measure(fx_cv_kit, actor, project)
        expect(job.state is JobState.FAILED)
        expect('nothing to measure' in job.error)
        expect(await normalize_params(fx_cv_kit, actor, project) == before)
        assert_expectations()

    async def test_a_recipe_without_a_normalize_step_has_nowhere_to_write_the_measures(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the job fails with the reason, and no page is marked stale.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        recipe = await fx_cv_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name=recipe.name, steps=[Step(processor_key=CROP_KEY)])
        )
        page = await placed_page(fx_cv_kit, project, recipe, FIRST_KEY, block_data(300, 600, 30))
        job = await measure(fx_cv_kit, actor, project)
        record = await fx_cv_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(job.state is JobState.FAILED)
        expect(NORMALIZE_KEY in job.error)
        expect(record.state is StageState.FRESH)
        assert_expectations()

    async def test_a_page_that_does_not_fit_the_bounds_of_the_step_fails_the_job_and_changes_nothing(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a measure that comes out larger than the step allows is refused, and the recipe is left as it was.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        await placed_page(fx_cv_kit, project, recipe, FIRST_KEY, block_data(TOO_WIDE_PX, 600, 30))
        before = await normalize_params(fx_cv_kit, actor, project)
        job = await measure(fx_cv_kit, actor, project)
        expect(job.state is JobState.FAILED)
        expect(await normalize_params(fx_cv_kit, actor, project) == before)
        assert_expectations()

    async def test_a_book_of_an_account_is_not_measured_by_another(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the request is refused for a project that is not the actor's, as every request of a project is.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        _, project = await fx_cv_kit.seed_project()
        stranger, _ = await fx_cv_kit.seed_project()
        with pytest.raises(NotFoundError):
            await fx_cv_kit.service().start_measure(stranger, project.id)


class TestMarginsSource:
    """Tests for the margins that the user set, which a measure leaves as they are."""

    async def test_a_book_starts_with_measured_margins(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the normalize step of a new book lets the measure set the margins.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        params = await normalize_params(fx_cv_kit, actor, project)
        expect(params[NormalizeParam.MARGINS_SOURCE] == MarginsSource.MEASURED)

    async def test_manual_margins_survive_a_measure_while_the_line_height_and_the_page_follow(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a measure keeps the four margins, and makes the page the largest block plus those margins.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        for index, (width, height, line_height) in enumerate(BLOCKS):
            await placed_page(fx_cv_kit, project, recipe, f'a{index}', block_data(width, height, line_height))
        margins: dict[str, object] = {
            NormalizeParam.MARGIN_TOP: 11.0,
            NormalizeParam.MARGIN_BOTTOM: 22.0,
            NormalizeParam.MARGIN_INNER: 33.0,
            NormalizeParam.MARGIN_OUTER: 44.0,
        }
        await set_normalize_params(
            fx_cv_kit, actor, project, {NormalizeParam.MARGINS_SOURCE: MarginsSource.MANUAL, **margins}
        )
        job = await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        expect(job.state is JobState.SUCCEEDED)
        expect({name: after[name] for name in margins} == margins)
        expect(after[NormalizeParam.MARGINS_SOURCE] == MarginsSource.MANUAL)
        expect(after[NormalizeParam.LINE_HEIGHT] == pytest.approx(MEDIAN_LINE_HEIGHT_PX))
        # The block is 300 by 600, and a millimetre of it is 3 pixels
        expect(after[NormalizeParam.PAGE_WIDTH] == 300 + (33 + 44) * PIXELS_PER_MM)
        expect(after[NormalizeParam.PAGE_HEIGHT] == 600 + (11 + 22) * PIXELS_PER_MM)
        assert_expectations()

    async def test_measured_margins_are_written_again_once_the_source_is_switched_back(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the measure sets the margins of a step that was manual and is measured again.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        for index, (width, height, line_height) in enumerate(BLOCKS):
            await placed_page(fx_cv_kit, project, recipe, f'a{index}', block_data(width, height, line_height))
        await set_normalize_params(
            fx_cv_kit,
            actor,
            project,
            {NormalizeParam.MARGINS_SOURCE: MarginsSource.MANUAL, NormalizeParam.MARGIN_TOP: 11},
        )
        await measure(fx_cv_kit, actor, project)
        await set_normalize_params(fx_cv_kit, actor, project, {NormalizeParam.MARGINS_SOURCE: MarginsSource.MEASURED})
        await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        expect({name: after[name] for name in EXPECTED_PAGE} == EXPECTED_PAGE)
        expect(after[NormalizeParam.MARGINS_SOURCE] == MarginsSource.MEASURED)
        assert_expectations()


class TestMeasureIsProcessing:
    """Tests for the measure of the book being one of the jobs a project runs one at a time."""

    async def test_a_run_is_refused_while_a_measure_is_queued(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a run cannot start while a measure is queued, since the measure rewrites the parameters it reads.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        await fx_cv_kit.service().start_measure(actor, project.id)
        with pytest.raises(ConflictError, match='measure of the book'):
            await fx_cv_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))

    async def test_a_measure_is_refused_while_a_run_is_queued(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify a measure cannot start while a run is queued, since the run writes the versions that are measured.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        await fx_cv_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        with pytest.raises(ConflictError, match=BUSY_MESSAGE):
            await fx_cv_kit.service().start_measure(actor, project.id)

    async def test_a_second_measure_is_refused_while_one_is_queued(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify two measures never overlap.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        await fx_cv_kit.service().start_measure(actor, project.id)
        with pytest.raises(ConflictError, match=BUSY_MESSAGE):
            await fx_cv_kit.service().start_measure(actor, project.id)
