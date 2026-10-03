"""Tests for the job that measures the book: the medians of the crop of every page, written into the normalize step."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import JobKind, JobState, NormalizeParam, Stage, StageState, VersionData, VersionState
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.geometry import Rect
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.values import PageStageKey, ProcessorRef, Step
from tests.helpers.builders import make_page_stage, make_page_version

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Job, Page, Project, Recipe
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

CROP_KEY: str = 'geometry.crop'
NORMALIZE_KEY: str = 'geometry.normalize'
VERSION_ID_DIGITS: int = 16
FIRST_KEY: str = 'a0'
# The pages of the first test: a block and a line height each, the block being the same size when the lines are brought
# to the median one, which is 30 pixels, so the median block is 300 by 600
BLOCKS: tuple[tuple[float, float, float], ...] = ((200, 400, 20), (300, 600, 30), (400, 800, 40))
MEDIAN_LINE_HEIGHT_PX: float = 30.0
# The median block 300 by 600 plus the margins, which are 8, 10, 10 and 8 percent of the block, rounded up
EXPECTED_PAGE: dict[str, int] = {
    NormalizeParam.PAGE_WIDTH: 354,
    NormalizeParam.PAGE_HEIGHT: 708,
    NormalizeParam.MARGIN_TOP: 48,
    NormalizeParam.MARGIN_BOTTOM: 60,
    NormalizeParam.MARGIN_INNER: 30,
    NormalizeParam.MARGIN_OUTER: 24,
}
TOO_WIDE_PX: float = 30_000


def version_id() -> PageVersionId:
    """Make the identifier of a version.

    :returns: Sixteen hexadecimal digits that are not any other version's.
    :rtype: PageVersionId
    """
    return PageVersionId(uuid4().hex[:VERSION_ID_DIGITS])


async def crop_page(
    kit: ProcessingKit,
    project: Project,
    recipe: Recipe,
    order_key: str,
    data: dict[str, object],
) -> Page:
    """Commit a page whose Geometry stage stands on a normalize version that read a crop version with the given data.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: Project owning the page.
    :type project: Project
    :param recipe: Active recipe of the Geometry stage, which the page was processed by.
    :type recipe: Recipe
    :param order_key: Order key of the page.
    :type order_key: str
    :param data: The data the crop version recorded.
    :type data: dict[str, object]
    :returns: The page.
    :rtype: Page
    """
    page, _ = await kit.seed_scan_page(project, order_key=order_key)
    crop = evolve(
        make_page_version(page_id=page.id),
        id=version_id(),
        stage=Stage.GEOMETRY,
        processor=ProcessorRef(key=CROP_KEY, version='1'),
        state=VersionState.READY,
        data=data,
    )
    normalize = evolve(
        crop,
        id=version_id(),
        processor=ProcessorRef(key=NORMALIZE_KEY, version='1'),
        input_id=crop.id,
        data={},
    )
    uow = kit.uow()
    await uow.page_versions.add(crop)
    await uow.page_versions.add(normalize)
    await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=recipe.id, head_version_id=normalize.id))
    await uow.commit()
    return page


def crop_data(width: float, height: float, line_height: float | None) -> dict[str, object]:
    """Build the data a crop records for a page.

    :param width: Width of the frame in pixels.
    :type width: float
    :param height: Height of the frame in pixels.
    :type height: float
    :param line_height: Distance between the lines in pixels, or None when the crop found none.
    :type line_height: float | None
    :returns: The frame, the confidence, that the page was not skipped, and the line height when there is one.
    :rtype: dict[str, object]
    """
    data: dict[str, object] = {
        VersionData.FRAME: Rect(left=10, top=20, width=width, height=height).to_data(),
        VersionData.CONFIDENCE: 1.0,
        VersionData.SKIPPED: False,
    }
    if line_height is not None:
        data[VersionData.LINE_HEIGHT_PX] = line_height
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


class TestMeasureBook:
    """Tests for the ``measure-book`` job."""

    async def test_the_medians_of_the_pages_are_written_into_the_normalize_step(self, fx_cv_kit: ProcessingKit) -> None:
        """Verify the median line height and a page of the median block with its margins reach the parameters.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        for index, (width, height, line_height) in enumerate(BLOCKS):
            await crop_page(fx_cv_kit, project, recipe, f'a{index}', crop_data(width, height, line_height))
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
            await crop_page(fx_cv_kit, project, recipe, f'a{index}', crop_data(width, height, line_height))
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
        page = await crop_page(fx_cv_kit, project, recipe, FIRST_KEY, crop_data(300, 600, 30))
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

    async def test_a_page_the_crop_left_as_it_was_or_that_has_no_line_height_is_handled(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a page with no content does not count, and a page with no line height counts by its block alone.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        await crop_page(fx_cv_kit, project, recipe, FIRST_KEY, crop_data(300, 600, 30))
        await crop_page(fx_cv_kit, project, recipe, 'a1', crop_data(300, 600, None))
        await crop_page(fx_cv_kit, project, recipe, 'a2', {VersionData.SKIPPED: True, VersionData.CONFIDENCE: 0.0})
        job = await measure(fx_cv_kit, actor, project)
        after = await normalize_params(fx_cv_kit, actor, project)
        expect(job.progress.done == 2)
        expect(after[NormalizeParam.LINE_HEIGHT] == pytest.approx(MEDIAN_LINE_HEIGHT_PX))
        expect(after[NormalizeParam.PAGE_WIDTH] == EXPECTED_PAGE[NormalizeParam.PAGE_WIDTH])
        assert_expectations()

    async def test_a_book_none_of_whose_pages_was_cropped_has_nothing_to_measure(
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
            actor, project.id, Stage.GEOMETRY, recipe.name, [Step(processor_key=CROP_KEY)]
        )
        page = await crop_page(fx_cv_kit, project, recipe, FIRST_KEY, crop_data(300, 600, 30))
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
        await crop_page(fx_cv_kit, project, recipe, FIRST_KEY, crop_data(TOO_WIDE_PX, 600, 30))
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
