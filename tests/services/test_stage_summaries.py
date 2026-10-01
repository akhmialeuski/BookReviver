"""Tests for the sums of the stages of books: the summary of a book, the rows of a stage and the progress in a list."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    PageStageStatus,
    ReviewReason,
    Stage,
    StageState,
    StageStatus,
    VersionState,
)
from bookreviver.domain.values import SliceRequest, StageRun
from tests.helpers.builders import make_page_stage, make_page_version

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, Project
    from bookreviver.domain.stage_summaries import StageSummary
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

# The stages the fake catalogue of the kit has a processor for, which are available with the ones done by hand
STAGES_WITH_A_PROCESSOR: frozenset[Stage] = frozenset({Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP})
PAGE_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')


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


async def seed_geometry(kit: ProcessingKit, page: Page, state: StageState, review: ReviewReason | None = None) -> None:
    """Commit a record of the geometry stage of a page with a ready current version.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param state: State of the record.
    :type state: StageState
    :param review: Review mark of the current version, or None.
    :type review: ReviewReason | None
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
    await uow.page_stages.save(
        make_page_stage(page_id=page.id, stage=Stage.GEOMETRY, head_version_id=version.id, state=state)
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
