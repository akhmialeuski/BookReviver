"""Tests for the picture of a row of a stage: the one version the strip and the canvas of a step both draw."""

from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve, frozen

from bookreviver.domain.enums import PageKind, RecipeKind, Stage
from bookreviver.domain.values import RecipeDraft, SliceRequest, StageRun, Step
from tests.helpers.processors import CleanupProcessor, FakeProcessor
from tests.helpers.spreads import head_of, run_stage
from tests.helpers.stage_heads import seed_head

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, PageVersion, Project
    from bookreviver.domain.ids import PageId, PageVersionId, StepId
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

CASE_ARG: str = 'case'
STEP_ARG: str = 'step_index'
PAGE_KEYS: tuple[str, str, str, str] = ('a0', 'a1', 'a2', 'a3')
STEP_COUNT: int = 3
ONE_STEP_NAME: str = 'One step'
# A strength the one-step recipe runs with, which tells its versions from the ones of the recipe of three steps
OTHER_STRENGTH: int = 2


class StageCase(NamedTuple):
    """A stage the rows are asked for, with the stage its pages read and one after it.

    :ivar stage: The stage whose rows are asked for.
    :ivar processor_key: Key of the processor every step of its recipe of three steps runs.
    :ivar earlier: The stage the pages of it read, whose current version is what the stage reads.
    :ivar later: A stage after it, whose current version a row never shows.
    """

    stage: Stage
    processor_key: str
    earlier: Stage
    later: Stage


CASES: tuple[StageCase, ...] = (
    StageCase(Stage.GEOMETRY, FakeProcessor.spec.key, earlier=Stage.PAGE_SPLIT, later=Stage.CLEANUP),
    StageCase(Stage.CLEANUP, CleanupProcessor.spec.key, earlier=Stage.GEOMETRY, later=Stage.LAYOUT),
)
CASE_IDS: tuple[str, ...] = tuple(str(case.stage) for case in CASES)
STEP_INDEXES: tuple[int, ...] = tuple(range(STEP_COUNT))


@frozen(kw_only=True)
class StagedBook:
    """A book whose four pages stand differently in a stage with a recipe of three steps.

    :ivar project: The project.
    :ivar step_ids: The steps of the active recipe of the stage, in order.
    :ivar full: Page run through every step.
    :ivar partial: Page run through the first step only.
    :ivar idle: Page the stage has not run on.
    :ivar other: Page processed by a recipe that has none of the steps of the active one.
    :ivar reads: What the stage reads on each page, which the stage before it made.
    :ivar chains: The versions the stage made on each page, the first step first, none for a page it has not run on.
    """

    project: Project
    step_ids: list[StepId]
    full: Page
    partial: Page
    idle: Page
    other: Page
    reads: dict[PageId, PageVersion]
    chains: dict[PageId, list[PageVersion]]

    @property
    def pages(self) -> list[Page]:
        """The pages in book order."""
        return [self.full, self.partial, self.idle, self.other]


async def chain_of(kit: ProcessingKit, page: Page, stage: Stage) -> list[PageVersion]:
    """Read the versions a stage made on a page by walking back from its current version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :returns: The versions of the stage that made its current version, the first step first.
    :rtype: list[PageVersion]
    """
    uow = kit.uow()
    version = await head_of(kit, page, stage)
    chain = [version]
    while version.input_id is not None:
        version = await uow.page_versions.get(version.input_id)
        if version.stage is not stage:
            break
        chain.insert(0, version)
    return chain


async def seed_staged_book(kit: ProcessingKit, case: StageCase) -> StagedBook:
    """Seed four pages and run the stage of the case on three of them, each in another way.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param case: The stage of the rows.
    :type case: StageCase
    :returns: The book with what each page reads and the versions the stage made on it.
    :rtype: StagedBook
    """
    actor, project = await kit.seed_project()
    pages: list[Page] = []
    for key in PAGE_KEYS:
        page, _ = await kit.seed_scan_page(project, order_key=key)
        await kit.seed_base_version(page)
        pages.append(page)
    full, partial, idle, other = pages
    uow = kit.uow()
    other = evolve(other, kind=PageKind.PLATE)
    await uow.pages.update(other)
    await uow.commit()
    if case.stage is Stage.CLEANUP:
        await run_stage(kit, actor, project, StageRun(stage=Stage.GEOMETRY))
    steps = [Step(processor_key=case.processor_key) for _ in range(STEP_COUNT)]
    await kit.edit_recipe(actor, project, case.stage, RecipeDraft(steps=steps))
    one_step = RecipeDraft(steps=[Step(processor_key=case.processor_key, params={'strength': OTHER_STRENGTH})])
    await kit.edit_recipe(actor, project, case.stage, one_step, RecipeKind.COLOR_PICTURE)
    await run_stage(kit, actor, project, StageRun(stage=case.stage, page_ids=(full.id,)))
    await run_stage(kit, actor, project, StageRun(stage=case.stage, page_ids=(partial.id,), through_step=0))
    await run_stage(kit, actor, project, StageRun(stage=case.stage, page_ids=(other.id,)))
    recipe = await kit.recipe_of(actor, project, case.stage)
    reads = {page.id: await head_of(kit, page, case.earlier) for page in pages}
    chains = {page.id: await chain_of(kit, page, case.stage) for page in (full, partial, other)}
    return StagedBook(
        project=project,
        step_ids=[step.step_id for step in recipe.steps],
        full=full,
        partial=partial,
        idle=idle,
        other=other,
        reads=reads,
        chains={**chains, idle.id: []},
    )


async def pictures_of(
    kit: ProcessingKit, book: StagedBook, case: StageCase, step_index: int | None
) -> dict[PageId, PageVersionId | None]:
    """Ask for the rows of the stage, at a step or for the stage alone, and take the picture of each.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book: The book.
    :type book: StagedBook
    :param case: The stage of the rows.
    :type case: StageCase
    :param step_index: Index of the step in the active recipe to place the pages at, or None for the stage alone.
    :type step_index: int | None
    :returns: The identifier of the picture of each page, or None for a page that has none.
    :rtype: dict[PageId, PageVersionId | None]
    """
    step_id = None if step_index is None else book.step_ids[step_index]
    rows = await kit.stages().rows(book.project, case.stage, SliceRequest(), step_id)
    return {row.page_id: None if row.picture is None else row.picture.id for row in rows.items}


class TestPicture:
    """Tests for StageRow.picture, as StageSummaries.rows builds it."""

    @pytest.mark.parametrize(CASE_ARG, CASES, ids=CASE_IDS)
    @pytest.mark.parametrize(STEP_ARG, STEP_INDEXES)
    async def test_a_page_run_through_the_stage_shows_what_the_step_reads(
        self, fx_kit: ProcessingKit, case: StageCase, step_index: int
    ) -> None:
        """Verify the picture is the input of the step: what the stage reads for the first, the step before for others.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param case: The stage of the rows.
        :type case: StageCase
        :param step_index: Index of the step the rows are placed at.
        :type step_index: int
        """
        book = await seed_staged_book(fx_kit, case)
        chain = book.chains[book.full.id]
        before_step = [book.reads[book.full.id], chain[0], chain[1]]
        pictures = await pictures_of(fx_kit, book, case, step_index)
        assert pictures[book.full.id] == before_step[step_index].id

    @pytest.mark.parametrize(CASE_ARG, CASES, ids=CASE_IDS)
    @pytest.mark.parametrize(STEP_ARG, STEP_INDEXES)
    async def test_a_page_that_stopped_earlier_shows_the_last_version_before_the_step(
        self, fx_kit: ProcessingKit, case: StageCase, step_index: int
    ) -> None:
        """Verify a page run through the first step shows its result at every later step, never a version after it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param case: The stage of the rows.
        :type case: StageCase
        :param step_index: Index of the step the rows are placed at.
        :type step_index: int
        """
        book = await seed_staged_book(fx_kit, case)
        [first] = book.chains[book.partial.id]
        last_before_step = [book.reads[book.partial.id], first, first]
        pictures = await pictures_of(fx_kit, book, case, step_index)
        assert pictures[book.partial.id] == last_before_step[step_index].id

    @pytest.mark.parametrize(CASE_ARG, CASES, ids=CASE_IDS)
    @pytest.mark.parametrize(STEP_ARG, STEP_INDEXES)
    async def test_a_page_the_stage_has_not_run_on_shows_what_the_stage_reads(
        self, fx_kit: ProcessingKit, case: StageCase, step_index: int
    ) -> None:
        """Verify a page without a result of the stage is drawn from the version a run of the stage would read.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param case: The stage of the rows.
        :type case: StageCase
        :param step_index: Index of the step the rows are placed at.
        :type step_index: int
        """
        book = await seed_staged_book(fx_kit, case)
        pictures = await pictures_of(fx_kit, book, case, step_index)
        assert pictures[book.idle.id] == book.reads[book.idle.id].id

    @pytest.mark.parametrize(CASE_ARG, CASES, ids=CASE_IDS)
    @pytest.mark.parametrize(STEP_ARG, STEP_INDEXES)
    async def test_a_page_whose_recipe_lacks_the_step_shows_what_the_stage_reads(
        self, fx_kit: ProcessingKit, case: StageCase, step_index: int
    ) -> None:
        """Verify a page another recipe processed is not drawn from its head, which the asked step did not make.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param case: The stage of the rows.
        :type case: StageCase
        :param step_index: Index of the step the rows are placed at.
        :type step_index: int
        """
        book = await seed_staged_book(fx_kit, case)
        pictures = await pictures_of(fx_kit, book, case, step_index)
        assert pictures[book.other.id] == book.reads[book.other.id].id

    @pytest.mark.parametrize(CASE_ARG, CASES, ids=CASE_IDS)
    async def test_a_row_without_a_step_shows_the_current_version_of_the_stage(
        self, fx_kit: ProcessingKit, case: StageCase
    ) -> None:
        """Verify the rows of the stage alone are drawn from the current version of every page that has one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param case: The stage of the rows.
        :type case: StageCase
        """
        book = await seed_staged_book(fx_kit, case)
        pictures = await pictures_of(fx_kit, book, case, None)
        heads = {page.id: book.chains[page.id][-1].id for page in (book.full, book.partial, book.other)}
        assert {page_id: pictures[page_id] for page_id in heads} == heads

    @pytest.mark.parametrize(CASE_ARG, CASES, ids=CASE_IDS)
    async def test_a_row_without_a_step_and_without_a_head_shows_what_the_stage_reads(
        self, fx_kit: ProcessingKit, case: StageCase
    ) -> None:
        """Verify a page the stage has not run on is drawn from what the stage reads in a list of the stage alone.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param case: The stage of the rows.
        :type case: StageCase
        """
        book = await seed_staged_book(fx_kit, case)
        pictures = await pictures_of(fx_kit, book, case, None)
        assert pictures[book.idle.id] == book.reads[book.idle.id].id

    @pytest.mark.parametrize(CASE_ARG, CASES, ids=CASE_IDS)
    @pytest.mark.parametrize(STEP_ARG, [None, *STEP_INDEXES])
    async def test_a_version_of_a_later_stage_is_never_the_picture(
        self, fx_kit: ProcessingKit, case: StageCase, step_index: int | None
    ) -> None:
        """Verify a stage after the one asked for, which holds a current version on every page, never shows in a row.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param case: The stage of the rows.
        :type case: StageCase
        :param step_index: Index of the step the rows are placed at, or None for the stage alone.
        :type step_index: int | None
        """
        book = await seed_staged_book(fx_kit, case)
        later = [await seed_head(fx_kit, page, case.later, after=book.reads[page.id]) for page in book.pages]
        step_id = None if step_index is None else book.step_ids[step_index]
        rows = await fx_kit.stages().rows(book.project, case.stage, SliceRequest(), step_id)
        later_ids = {version.id for version in later}
        assert all(
            row.picture is not None
            and row.picture.id not in later_ids
            and row.picture.stage.position <= case.stage.position
            for row in rows.items
        )

    async def test_a_page_with_no_image_has_no_picture(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that has no version at all, such as a placeholder, has none to draw, with or without a step.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        await fx_kit.seed_scan_page(project, order_key=PAGE_KEYS[0])
        one_step = RecipeDraft(steps=[Step(processor_key=FakeProcessor.spec.key)])
        recipe = await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, one_step)
        step_id = recipe.steps[0].step_id
        alone = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        at_step = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), step_id)
        assert [row.picture for row in (*alone.items, *at_step.items)] == [None, None]
