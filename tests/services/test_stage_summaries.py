"""Tests for the sums of the stages of books: the summary of a book, the rows of a stage and the progress in a list."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    AppliesTo,
    EditorKind,
    FigureState,
    PageKind,
    PageStageStatus,
    ReviewReason,
    Stage,
    StageState,
    StageStatus,
    VersionState,
)
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import StepId
from bookreviver.domain.values import NewPageEdit, PageStepKey, ProcessorRef, SliceRequest, StageRun, Step
from tests.helpers.builders import make_page_stage, make_page_version
from tests.helpers.processors import FakeProcessor
from tests.helpers.spreads import run_stage

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, Project
    from bookreviver.domain.stage_summaries import StageSummary
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# The stages the fake catalogue of the kit has a processor for, which are available with the ones done by hand
STAGES_WITH_A_PROCESSOR: frozenset[Stage] = frozenset({Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP})
PAGE_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')
FAKE_KEY: str = FakeProcessor.spec.key
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))


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

    async def test_the_active_recipe_is_named_once_the_stage_has_been_asked_for(self, fx_kit: ProcessingKit) -> None:
        """Verify a stage has no recipe to name before it is used, and the recipe the default made after.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await seed_book(fx_kit)
        before = (await summaries_of(fx_kit, project))[Stage.GEOMETRY].active_recipe_id
        recipe = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        after = (await summaries_of(fx_kit, project))[Stage.GEOMETRY].active_recipe_id
        expect(before is None)
        expect(after == recipe.id)
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


async def seed_text_and_plate(kit: ProcessingKit) -> tuple[Actor, Project, Page, Page]:
    """Seed a project of a text page and a plate, each with its base version, and save a recipe of two steps for them.

    The first step processes the pages of text and the second the pictures, so each page passes one of them unchanged.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the text page and the plate.
    :rtype: tuple[Actor, Project, Page, Page]
    """
    actor, project = await kit.seed_project()
    text, _ = await kit.seed_scan_page(project, order_key='a0')
    plate, _ = await kit.seed_scan_page(project, order_key='a1', kind=PageKind.PLATE)
    for page in (text, plate):
        await kit.seed_base_version(page)
    steps = [
        Step(processor_key=FAKE_KEY, applies_to=AppliesTo.TEXT),
        Step(processor_key=FAKE_KEY, applies_to=AppliesTo.PICTURES),
    ]
    await kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, 'Two', steps)
    return actor, project, text, plate


async def step_ids_of(kit: ProcessingKit, project: Project) -> list[StepId]:
    """Read the identifiers of the steps of the active recipe of the geometry stage, in order.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The project.
    :type project: Project
    :returns: The identifiers of the steps.
    :rtype: list[StepId]
    """
    recipe = await kit.uow().recipes.find_active(project.id, Stage.GEOMETRY)
    assert recipe is not None
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
        _, project, _, _ = await seed_text_and_plate(fx_kit)
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
        _, project, _, _ = await seed_text_and_plate(fx_kit)
        rows = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest())
        assert [row.step for row in rows.items] == [None, None]

    async def test_each_page_has_found_the_shape_of_the_step_that_ran_on_it_and_skipped_the_other(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a step that ran is found, a step that passed the page by its condition is skipped, per step.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, _ = await seed_text_and_plate(fx_kit)
        first, second = await step_ids_of(fx_kit, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        by_first = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        by_second = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), second)
        expect([row.step.state for row in by_first.items if row.step] == [FigureState.FOUND, FigureState.SKIPPED])
        expect([row.step.state for row in by_second.items if row.step] == [FigureState.SKIPPED, FigureState.FOUND])
        assert_expectations()

    async def test_the_second_step_reads_what_the_first_made_and_the_first_reads_the_stage_before(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the input of a step is the version of the step before it, and for the first step the earlier stage's.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, text, _ = await seed_text_and_plate(fx_kit)
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
        actor, project, text, _ = await seed_text_and_plate(fx_kit)
        first, second = await step_ids_of(fx_kit, project)
        await fx_kit.edits().save(actor, project.id, PageStepKey(text.id, Stage.GEOMETRY, first), ROTATION, None)
        by_first = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), first)
        by_second = await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), second)
        expect([row.step.state for row in by_first.items if row.step] == [FigureState.BY_HAND, FigureState.DEFAULT])
        expect([row.step.state for row in by_second.items if row.step] == [FigureState.DEFAULT, FigureState.DEFAULT])
        assert_expectations()

    async def test_a_step_no_recipe_of_the_stage_has_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify an identifier that is the step of no recipe is refused, not answered with default shapes.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _, _ = await seed_text_and_plate(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.stages().rows(project, Stage.GEOMETRY, SliceRequest(), StepId(uuid4()))
