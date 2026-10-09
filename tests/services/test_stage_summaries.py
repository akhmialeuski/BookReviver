"""Tests for the sums of the stages of books: the summary of a book, the rows of a stage and the progress in a list."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    EditorKind,
    FigureState,
    PageKind,
    PageOrigin,
    PageStageStatus,
    RecipeKind,
    ResultMark,
    ReviewReason,
    Stage,
    StageState,
    StageStatus,
    StepFlag,
    VersionState,
)
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import StepId
from bookreviver.domain.values import (
    NewPageEdit,
    PageStepKey,
    ProcessorRef,
    RecipeDraft,
    RecipeKey,
    SliceRequest,
    StageRun,
    Step,
)
from tests.helpers.builders import make_page, make_page_stage, make_page_version
from tests.helpers.page_batches import PageValues
from tests.helpers.processors import MEASURING_KEY, STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import run_stage

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project
    from bookreviver.domain.stage_summaries import StageSummary
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# The stages the fake catalogue of the kit has a processor for, which are available with the ones done by hand
STAGES_WITH_A_PROCESSOR: frozenset[Stage] = frozenset({Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP})
PAGE_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')
FAKE_KEY: str = FakeProcessor.spec.key
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))
# A strength a page sets for its step, and a far one that makes the angle of the page depart from the book
STRONGER: int = 2
FAR_STRENGTH: int = 5


async def seed_book(kit: ProcessingKit) -> tuple[Actor, Project, list[Page]]:
    """Seed a project of three pages, each with its ready base version and its record of the page split.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project and the pages in book order.
    :rtype: tuple[Actor, Project, list[Page]]
    """
    actor, project = await kit.seed_project()
    pages = []
    for key in PAGE_KEYS:
        page, _ = await kit.seed_scan_page(project, order_key=key)
        await kit.seed_base_version(page)
        pages.append(page)
    return actor, project, pages


async def seed_geometry(
    kit: ProcessingKit,
    page: Page,
    state: StageState,
    review: ReviewReason | None = None,
    through_step: int | None = None,
) -> None:
    """Commit a record of the geometry stage of a page with a ready current version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param state: State of the record.
    :type state: StageState
    :param review: Review mark of the current version, or None.
    :type review: ReviewReason | None
    :param through_step: Step the page was run through when that is short of the last step, or None.
    :type through_step: int | None
    """
    base = (await kit.uow().page_versions.list_for_page(page.id))[0]
    version = evolve(
        make_page_version(page_id=page.id, minutes=1),
        stage=Stage.GEOMETRY,
        input_id=base.id,
        review=review,
        state=VersionState.READY,
    )
    uow = kit.uow()
    await uow.page_versions.add(version)
    record = make_page_stage(page_id=page.id, stage=Stage.GEOMETRY, head_version_id=version.id, state=state)
    await uow.page_stages.save(evolve(record, through_step=through_step))
    await uow.commit()


async def mark_version(kit: ProcessingKit, version: PageVersion, mark: ResultMark) -> None:
    """Commit the mark of a stored version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param version: The version.
    :type version: PageVersion
    :param mark: The mark to set.
    :type mark: ResultMark
    """
    uow = kit.uow()
    await uow.page_versions.update(evolve(version, mark=mark))
    await uow.commit()


async def seed_marked_chain(kit: ProcessingKit, page: Page, *, steps: int, marked_at: int) -> None:
    """Commit a geometry record whose current version is the last of a chain of steps, the marks starting at one.

    A step keeps the mark of the step before it, as the processors do, so every version from ``marked_at`` on has one.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param steps: Number of versions in the chain.
    :type steps: int
    :param marked_at: Index of the first version that carries the mark.
    :type marked_at: int
    """
    uow = kit.uow()
    previous = (await uow.page_versions.list_for_page(page.id))[0]
    for index in range(steps):
        version = evolve(
            make_page_version(page_id=page.id, minutes=index + 1),
            stage=Stage.GEOMETRY,
            processor=ProcessorRef(key=f'geometry.step{index}', version='1'),
            input_id=previous.id,
            review=ReviewReason.NOT_APPLIED if index >= marked_at else None,
            state=VersionState.READY,
        )
        await uow.page_versions.add(version)
        previous = version
    await uow.page_stages.save(
        make_page_stage(page_id=page.id, stage=Stage.GEOMETRY, head_version_id=previous.id, state=StageState.FRESH)
    )
    await uow.commit()


async def summaries_of(kit: ProcessingKit, project: Project) -> dict[Stage, StageSummary]:
    """Sum the stages of a project, by stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The project.
    :type project: Project
    :returns: The summary of each stage.
    :rtype: dict[Stage, StageSummary]
    """
    overview = await kit.uow().projects.overview(project)
    return {summary.stage: summary for summary in await kit.stages().of_book(overview)}


class TestOfBook:
    """Tests for StageSummaries.of_book."""

    async def test_every_stage_is_summed_in_the_order_of_the_pipeline(self, fx_kit: ProcessingKit) -> None:
        """Verify there are ten summaries in pipeline order, and a stage with no processor installed is not available.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await seed_book(fx_kit)
        overview = await fx_kit.uow().projects.overview(project)
        summaries = await fx_kit.stages().of_book(overview)
        expect([summary.stage for summary in summaries] == list(Stage))
        expect(
            {summary.stage for summary in summaries if summary.available}
            == STAGES_WITH_A_PROCESSOR | {Stage.IMPORT, Stage.PAGE_ORDER}
        )
        assert_expectations()

    async def test_the_pages_are_counted_by_the_state_of_their_record(self, fx_kit: ProcessingKit) -> None:
        """Verify a fresh page with a review mark, a stale one and a page with no record of the stage are told apart.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        await seed_geometry(fx_kit, pages[0], StageState.FRESH, ReviewReason.LOW_CONFIDENCE)
        await seed_geometry(fx_kit, pages[1], StageState.STALE)
        geometry = (await summaries_of(fx_kit, project))[Stage.GEOMETRY]
        expect((geometry.pages, geometry.fresh, geometry.stale, geometry.failed) == (3, 1, 1, 0))
        expect((geometry.not_run, geometry.review) == (1, 1))
        expect(geometry.status(running=False) is StageStatus.ATTENTION)
        assert_expectations()

    async def test_a_page_that_is_stale_and_marked_is_checked_once(self, fx_kit: ProcessingKit) -> None:
        """Verify the check count takes a page both out of date and marked once, and leaves a clean page out.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        await seed_geometry(fx_kit, pages[0], StageState.STALE, ReviewReason.LOW_CONFIDENCE)
        await seed_geometry(fx_kit, pages[1], StageState.FRESH)
        geometry = (await summaries_of(fx_kit, project))[Stage.GEOMETRY]
        expect((geometry.stale, geometry.review, geometry.check) == (1, 1, 1))
        assert_expectations()

    async def test_a_stage_that_ran_on_every_page_is_done(self, fx_kit: ProcessingKit) -> None:
        """Verify the page split the base versions were recorded by is counted as fresh on every page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await seed_book(fx_kit)
        split = (await summaries_of(fx_kit, project))[Stage.PAGE_SPLIT]
        expect((split.fresh, split.not_run) == (3, 0))
        expect(split.status(running=False) is StageStatus.DONE)
        assert_expectations()

    async def test_pages_stopped_before_the_last_step_are_counted_by_the_step_and_keep_the_stage_waiting(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify two pages stopped at steps one and two are told apart from the one run through, and are not done.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        await seed_geometry(fx_kit, pages[0], StageState.FRESH, through_step=0)
        await seed_geometry(fx_kit, pages[1], StageState.FRESH, through_step=1)
        await seed_geometry(fx_kit, pages[2], StageState.FRESH)
        geometry = (await summaries_of(fx_kit, project))[Stage.GEOMETRY]
        expect((geometry.fresh, geometry.partial, geometry.not_run) == (3, 2, 0))
        expect(
            [(one.stage, one.through_step, one.pages) for one in geometry.stopped]
            == [(Stage.GEOMETRY, 0, 1), (Stage.GEOMETRY, 1, 1)]
        )
        expect(geometry.status(running=False) is StageStatus.WAITING)
        assert_expectations()

    async def test_a_page_that_failed_is_not_counted_as_stopped(self, fx_kit: ProcessingKit) -> None:
        """Verify the step a failed record keeps is left out of the counts, since that run did not end.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        await seed_geometry(fx_kit, pages[0], StageState.FAILED, through_step=0)
        geometry = (await summaries_of(fx_kit, project))[Stage.GEOMETRY]
        assert (geometry.partial, geometry.stopped) == (0, ())

    async def test_the_recipe_of_each_kind_is_named_once_the_stage_has_been_asked_for(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a stage has no recipe to name before it is used, and after it the four recipes with their pages.

        The book has two text pages, a plate, a blank page and a placeholder, which has no image and is not counted.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages = await seed_book(fx_kit)
        uow = fx_kit.uow()
        await uow.pages.update(evolve(pages[1], kind=PageKind.PLATE))
        await uow.pages.update(evolve(pages[2], kind=PageKind.BLANK))
        await uow.commit()
        await fx_kit.seed_scan_page(project, order_key='a3', kind=PageKind.TEXT)
        placeholder = make_page(project_id=project.id, order_key='a4')
        await uow.pages.add(placeholder)
        await uow.commit()
        before = (await summaries_of(fx_kit, project))[Stage.GEOMETRY].recipes
        recipes = {
            recipe.kind: recipe
            for recipe in (
                await fx_kit.service().recipes(actor, project.id, Stage.GEOMETRY, SliceRequest(limit=10))
            ).items
        }
        after = (await summaries_of(fx_kit, project))[Stage.GEOMETRY].recipes
        expect(before == ())
        expect(
            [(one.kind, one.recipe_id, one.pages) for one in after]
            == [
                (RecipeKind.TEXT, recipes[RecipeKind.TEXT].id, 2),
                (RecipeKind.COLOR_PICTURE, recipes[RecipeKind.COLOR_PICTURE].id, 1),
                (RecipeKind.BW_PICTURE, recipes[RecipeKind.BW_PICTURE].id, 0),
                (RecipeKind.BLANK, recipes[RecipeKind.BLANK].id, 1),
            ]
        )
        assert_expectations()


class TestRows:
    """Tests for StageSummaries.rows."""

    async def test_a_row_for_each_page_in_book_order_with_where_it_stands(self, fx_kit: ProcessingKit) -> None:
        """Verify the status, the mark and the current version of each page, and none for a page not run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        await seed_geometry(fx_kit, pages[0], StageState.FRESH, ReviewReason.NOT_APPLIED)
        await seed_geometry(fx_kit, pages[2], StageState.FAILED)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        expect([row.page_id for row in rows.items] == [page.id for page in pages])
        expect(
            [row.status for row in rows.items]
            == [PageStageStatus.FRESH, PageStageStatus.NOT_RUN, PageStageStatus.FAILED]
        )
        expect([row.review for row in rows.items] == [ReviewReason.NOT_APPLIED, None, None])
        expect([row.head_version is not None for row in rows.items] == [True, False, True])
        expect(rows.total == len(pages))
        assert_expectations()

    async def test_a_row_names_the_step_it_stopped_at(self, fx_kit: ProcessingKit) -> None:
        """Verify the step a page was run through is on its row, and none for a page run through every step.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        await seed_geometry(fx_kit, pages[0], StageState.FRESH, through_step=1)
        await seed_geometry(fx_kit, pages[1], StageState.FRESH)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        assert [row.through_step for row in rows.items] == [1, None, None]

    async def test_a_marked_row_names_the_processor_of_the_step_that_marked_it(self, fx_kit: ProcessingKit) -> None:
        """Verify the mark a step passed on is traced back to the first step that made it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        await seed_marked_chain(fx_kit, pages[0], steps=3, marked_at=1)
        await seed_marked_chain(fx_kit, pages[1], steps=3, marked_at=3)
        await seed_geometry(fx_kit, pages[2], StageState.FRESH, ReviewReason.LOW_CONFIDENCE)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        expect([row.review is not None for row in rows.items] == [True, False, True])
        expect(rows.items[0].review_processor == 'geometry.step1')
        expect(rows.items[1].review_processor is None)
        expect(rows.items[2].review_processor == make_page_version(page_id=pages[2].id).processor.key)
        assert_expectations()

    async def test_a_row_is_marked_bad_when_its_current_version_is(self, fx_kit: ProcessingKit) -> None:
        """Verify only the page whose current version carries the bad mark is flagged, and a good mark is not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        for page in pages:
            await seed_geometry(fx_kit, page, StageState.FRESH)
        heads = [
            next(
                version
                for version in await fx_kit.uow().page_versions.list_for_page(page.id)
                if version.stage is Stage.GEOMETRY
            )
            for page in pages
        ]
        await mark_version(fx_kit, heads[0], ResultMark.BAD)
        await mark_version(fx_kit, heads[1], ResultMark.GOOD)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        assert [row.marked_bad for row in rows.items] == [True, False, False]

    async def test_a_mark_an_earlier_stage_made_names_no_step(self, fx_kit: ProcessingKit) -> None:
        """Verify a mark that leads back out of the stage is not put on a step of it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        marked_base = evolve(
            (await fx_kit.uow().page_versions.list_for_page(pages[0].id))[0], review=ReviewReason.UNSURE_GUTTER
        )
        uow = fx_kit.uow()
        await uow.page_versions.update(marked_base)
        await uow.commit()
        await seed_marked_chain(fx_kit, pages[0], steps=2, marked_at=0)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        assert (rows.items[0].review is not None, rows.items[0].review_processor) == (True, None)

    async def test_only_the_records_of_the_asked_stage_count(self, fx_kit: ProcessingKit) -> None:
        """Verify the page split the pages all have is not shown as the state of the geometry.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await seed_book(fx_kit)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        assert {row.status for row in rows.items} == {PageStageStatus.NOT_RUN}

    async def test_a_window_holds_its_pages_and_the_total_is_the_whole_book(self, fx_kit: ProcessingKit) -> None:
        """Verify the rows of a window are those of its pages, and the total counts every page of the book.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages = await seed_book(fx_kit)
        window = await fx_kit.stages().rows(project, Stage.PAGE_SPLIT, SliceRequest(offset=1, limit=1))
        expect([row.page_id for row in window.items] == [pages[1].id])
        expect(window.total == len(pages))
        assert_expectations()


class TestWithProgress:
    """Tests for StageSummaries.with_progress."""

    async def test_the_progress_of_a_book_follows_its_stages(self, fx_kit: ProcessingKit) -> None:
        """Verify an imported book with a finished split has the geometry as its next stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await seed_book(fx_kit)
        overview = await fx_kit.uow().projects.overview(project)
        [with_progress] = await fx_kit.stages().with_progress([overview])
        assert with_progress.progress is not None
        statuses = {one.stage: one.status for one in with_progress.progress.stages}
        expect(with_progress.progress.next_stage is Stage.GEOMETRY)
        expect((statuses[Stage.IMPORT], statuses[Stage.PAGE_SPLIT]) == (StageStatus.DONE, StageStatus.DONE))
        expect(
            (statuses[Stage.GEOMETRY], statuses[Stage.TYPESETTING]) == (StageStatus.WAITING, StageStatus.UNAVAILABLE)
        )
        assert_expectations()

    async def test_a_stage_a_queued_run_will_work_in_is_running(self, fx_kit: ProcessingKit) -> None:
        """Verify a run-stage job that has not finished marks its stage as running, in its own book alone.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await seed_book(fx_kit)
        _, other = await fx_kit.seed_project()
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        overviews = [await fx_kit.uow().projects.overview(owned) for owned in (project, other)]
        mine, theirs = await fx_kit.stages().with_progress(overviews)
        assert mine.progress is not None
        assert theirs.progress is not None
        expect({one.stage: one.status for one in mine.progress.stages}[Stage.GEOMETRY] is StageStatus.RUNNING)
        expect(StageStatus.RUNNING not in {one.status for one in theirs.progress.stages})
        assert_expectations()

    async def test_no_books_need_no_reading(self, fx_kit: ProcessingKit) -> None:
        """Verify an empty window of books gives an empty list.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        assert await fx_kit.stages().with_progress([]) == []


async def seed_text_and_leaf(kit: ProcessingKit) -> tuple[Actor, Project, Page, Page]:
    """Seed a project of a text page and a blank leaf the program drew, each with its base version, and save a recipe.

    The recipe of text pages has two steps, which the text page passes through and the leaf passes unchanged.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the text page and the leaf.
    :rtype: tuple[Actor, Project, Page, Page]
    """
    actor, project = await kit.seed_project()
    text, _ = await kit.seed_scan_page(project, order_key='a0')
    scanned, _ = await kit.seed_scan_page(project, order_key='a1')
    leaf = evolve(scanned, origin=PageOrigin.BLANK, scan_id=None)
    uow = kit.uow()
    await uow.pages.update(leaf)
    await uow.commit()
    for page in (text, leaf):
        await kit.seed_base_version(page)
    steps = [Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY)]
    recipe = await kit.recipe_of(actor, project, Stage.GEOMETRY)
    await kit.service().save_recipe(actor, project.id, RecipeKey(Stage.GEOMETRY, recipe.id), RecipeDraft(steps=steps))
    return actor, project, text, leaf


async def step_ids_of(kit: ProcessingKit, project: Project) -> list[StepId]:
    """Read the identifiers of the steps of the recipe of text pages of the geometry stage, in order.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The project.
    :type project: Project
    :returns: The identifiers of the steps.
    :rtype: list[StepId]
    """
    recipe = next(
        recipe
        for recipe in await kit.uow().recipes.list_for_stage(project.id, Stage.GEOMETRY)
        if recipe.kind is RecipeKind.TEXT
    )
    return [step.step_id for step in recipe.steps]


class TestStepRows:
    """Tests for StageSummaries.rows asked for a step."""

    async def test_a_page_the_stage_has_not_run_on_holds_the_default_shape_at_every_step(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the rows of a step before any run say nothing of the step but its default shape.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _, _ = await seed_text_and_leaf(fx_kit)
        first, _ = await step_ids_of(fx_kit, project)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        assert [(row.step.state, row.step.version) for row in rows.items if row.step is not None] == [
            (FigureState.DEFAULT, None),
            (FigureState.DEFAULT, None),
        ]

    async def test_a_row_without_a_step_has_no_step(self, fx_kit: ProcessingKit) -> None:
        """Verify the rows of the stage alone are the rows they were, so a strip that asks for no step pays nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _, _ = await seed_text_and_leaf(fx_kit)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        assert [row.step for row in rows.items] == [None, None]

    async def test_a_page_has_found_the_shape_of_the_step_that_ran_on_it_and_a_leaf_skipped_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a step that ran on a text page is found, and the leaf the program drew passes every step skipped.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await seed_text_and_leaf(fx_kit)
        first, second = await step_ids_of(fx_kit, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        by_first = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        by_second = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), second)
        expect([row.step.state for row in by_first.items if row.step] == [FigureState.FOUND, FigureState.SKIPPED])
        expect([row.step.state for row in by_second.items if row.step] == [FigureState.FOUND, FigureState.SKIPPED])
        assert_expectations()

    async def test_the_second_step_reads_what_the_first_made_and_the_first_reads_the_stage_before(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the input of a step is the version of the step before it, and for the first step the earlier stage's.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await seed_text_and_leaf(fx_kit)
        first, second = await step_ids_of(fx_kit, project)
        base = (await fx_kit.uow().page_versions.list_for_page(text.id))[0]
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        by_first = (await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)).items[0].step
        by_second = (await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), second)).items[0].step
        assert by_first is not None
        assert by_second is not None
        assert by_first.input_version is not None
        expect(by_first.input_version.id == base.id)
        expect(by_second.input_version == by_first.version)
        assert_expectations()

    async def test_an_edit_of_the_step_makes_its_shape_set_by_hand_on_that_page_only(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify an edit belongs to its step: the other step of the same processor still has the default shape.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await seed_text_and_leaf(fx_kit)
        first, second = await step_ids_of(fx_kit, project)
        await fx_kit.edits().save(actor, project.id, PageStepKey(text.id, Stage.GEOMETRY, first), ROTATION, None)
        by_first = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        by_second = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), second)
        expect([row.step.state for row in by_first.items if row.step] == [FigureState.BY_HAND, FigureState.DEFAULT])
        expect([row.step.state for row in by_second.items if row.step] == [FigureState.DEFAULT, FigureState.DEFAULT])
        assert_expectations()

    async def test_a_row_at_a_step_is_marked_bad_by_the_version_of_that_step(self, fx_kit: ProcessingKit) -> None:
        """Verify the flag follows the version at the step asked for and not the current version of the stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await seed_text_and_leaf(fx_kit)
        first, second = await step_ids_of(fx_kit, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        at_first = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        made = at_first.items[0].step
        assert made is not None
        assert made.version is not None
        await mark_version(fx_kit, made.version, ResultMark.BAD)
        by_first = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        by_second = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), second)
        of_stage = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        expect([row.marked_bad for row in by_first.items] == [True, False])
        expect([row.marked_bad for row in by_second.items] == [False, False])
        expect([row.marked_bad for row in of_stage.items] == [False, False])
        assert_expectations()

    async def test_a_step_no_recipe_of_the_stage_has_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify an identifier that is the step of no recipe is refused, not answered with default shapes.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _, _ = await seed_text_and_leaf(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), StepId(uuid4()))

    async def test_a_page_with_a_setting_of_its_own_for_the_step_is_set_by_hand_and_keeps_its_shape(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a setting of the page raises the flag without changing where the shape comes from.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await seed_text_and_leaf(fx_kit)
        first, _ = await step_ids_of(fx_kit, project)
        key = PageStepKey(text.id, Stage.GEOMETRY, first)
        await PageValues(fx_kit, actor, project.id).set(key, STRENGTH_PARAMETER, STRONGER)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        steps = [row.step for row in rows.items]
        assert [(step.state, step.flags) for step in steps if step] == [
            (FigureState.DEFAULT, (StepFlag.BY_HAND,)),
            (FigureState.DEFAULT, ()),
        ]

    async def test_a_leaf_that_passed_the_step_unchanged_is_flagged_as_skipped(self, fx_kit: ProcessingKit) -> None:
        """Verify the skipped flag follows the leaf the program drew.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await seed_text_and_leaf(fx_kit)
        first, _ = await step_ids_of(fx_kit, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        assert [row.step.flags for row in rows.items if row.step] == [(), (StepFlag.SKIPPED,)]


async def seed_measured_book(kit: ProcessingKit) -> tuple[Project, StepId]:
    """Seed a book of four pages run through a step that finds an angle, and one page that sets a far strength.

    The processor records its strength as the angle, so three pages find 1 and the page that set a strength of its own
    finds 5, which is far from the median of the book.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The project and the step.
    :rtype: tuple[Project, StepId]
    """
    actor, project = await kit.seed_project()
    pages: list[Page] = []
    for key in (*PAGE_KEYS, 'a3'):
        page, _ = await kit.seed_scan_page(project, order_key=key)
        await kit.seed_base_version(page)
        pages.append(page)
    recipe = await kit.recipe_of(actor, project, Stage.GEOMETRY)
    await kit.service().save_recipe(
        actor,
        project.id,
        RecipeKey(Stage.GEOMETRY, recipe.id),
        RecipeDraft(steps=[Step(processor_key=MEASURING_KEY)]),
    )
    [step_id] = await step_ids_of(kit, project)
    await PageValues(kit, actor, project.id).set(
        PageStepKey(pages[-1].id, Stage.GEOMETRY, step_id), STRENGTH_PARAMETER, FAR_STRENGTH
    )
    await run_stage(kit, actor, project, StageRun(stage=Stage.GEOMETRY))
    return project, step_id


class TestUnusualPages:
    """Tests for the pages whose value at a step departs from the book."""

    async def test_the_page_whose_angle_is_far_from_the_median_of_the_book_is_unusual(
        self, fx_measuring_kit: ProcessingKit
    ) -> None:
        """Verify one page of four, the one that found 5 where the others found 1, is the unusual one.

        :param fx_measuring_kit: The kit whose catalogue has a processor that finds an angle.
        :type fx_measuring_kit: ProcessingKit
        """
        project, step_id = await seed_measured_book(fx_measuring_kit)
        rows = await fx_measuring_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), step_id)
        assert [row.step.flags for row in rows.items if row.step] == [
            (),
            (),
            (),
            (StepFlag.UNUSUAL, StepFlag.BY_HAND),
        ]

    async def test_a_window_of_the_book_is_compared_with_the_whole_book(self, fx_measuring_kit: ProcessingKit) -> None:
        """Verify the last page is unusual in a window of its own, where its median alone would be 5.

        :param fx_measuring_kit: The kit whose catalogue has a processor that finds an angle.
        :type fx_measuring_kit: ProcessingKit
        """
        project, step_id = await seed_measured_book(fx_measuring_kit)
        window = await fx_measuring_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(offset=3, limit=1), step_id)
        assert [row.step.flags for row in window.items if row.step] == [(StepFlag.UNUSUAL, StepFlag.BY_HAND)]

    async def test_a_step_with_nothing_to_compare_marks_no_page_unusual(self, fx_kit: ProcessingKit) -> None:
        """Verify a step whose processor declares no measure never raises the flag.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await seed_text_and_leaf(fx_kit)
        first, _ = await step_ids_of(fx_kit, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        assert all(StepFlag.UNUSUAL not in row.step.flags for row in rows.items if row.step)
