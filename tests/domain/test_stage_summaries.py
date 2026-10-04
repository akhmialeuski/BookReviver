"""Tests for the summary of a stage of a book, its status, and the progress of a book along the pipeline."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Recipe
from bookreviver.domain.enums import (
    FigureState,
    PageStageStatus,
    ResultMark,
    ReviewReason,
    Stage,
    StageState,
    StageStatus,
    VersionData,
)
from bookreviver.domain.ids import PageId, ProjectId, RecipeId
from bookreviver.domain.stage_summaries import BookProgress, StageRow, StageSummary, StageTally, StepRow, StepTally
from bookreviver.domain.values import ProcessorRef, Step
from tests.helpers.builders import make_page_version

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion

PROJECT_ID: ProjectId = ProjectId(uuid4())
PAGES: int = 5
# The processors of the steps of the recipe the tests of the rows of a step place a page in
PERSPECTIVE_KEY: str = 'geometry.perspective'
DESKEW_KEY: str = 'geometry.deskew'
CROP_KEY: str = 'geometry.crop'


class Counts(NamedTuple):
    """How many pages of a stage are in each state, as the records of the stage say.

    :ivar fresh: Pages that are up to date.
    :ivar stale: Pages that are out of date.
    :ivar failed: Pages the stage failed on.
    :ivar review: Pages marked for review.
    :ivar check: Pages that are stale, failed or marked, each once.
    :ivar partial: Pages run through some of the steps of their recipe only.
    """

    fresh: int = 0
    stale: int = 0
    failed: int = 0
    review: int = 0
    check: int = 0
    partial: int = 0


class StatusCase(NamedTuple):
    """One stage of a book and the status the rule gives it.

    :ivar stage: The stage.
    :ivar available: Whether the stage can be worked in.
    :ivar pages: Number of pages with an image.
    :ivar counts: How many pages are in each state.
    :ivar running: Whether a job runs the stage.
    :ivar expected: The status the rule gives.
    """

    stage: Stage
    available: bool
    pages: int
    counts: Counts
    running: bool
    expected: StageStatus


STATUS_CASES: dict[str, StatusCase] = {
    'unavailable': StatusCase(
        stage=Stage.CLEANUP,
        available=False,
        pages=PAGES,
        counts=Counts(),
        running=False,
        expected=StageStatus.UNAVAILABLE,
    ),
    'unavailable-beats-running': StatusCase(
        stage=Stage.CLEANUP,
        available=False,
        pages=PAGES,
        counts=Counts(),
        running=True,
        expected=StageStatus.UNAVAILABLE,
    ),
    'no-pages': StatusCase(
        stage=Stage.GEOMETRY, available=True, pages=0, counts=Counts(), running=False, expected=StageStatus.WAITING
    ),
    'never-run': StatusCase(
        stage=Stage.GEOMETRY, available=True, pages=PAGES, counts=Counts(), running=False, expected=StageStatus.WAITING
    ),
    'partly-run': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(fresh=3),
        running=False,
        expected=StageStatus.WAITING,
    ),
    'every-page-up-to-date-but-stopped-at-a-step': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(fresh=PAGES, partial=2),
        running=False,
        expected=StageStatus.WAITING,
    ),
    'running': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(fresh=2),
        running=True,
        expected=StageStatus.RUNNING,
    ),
    'running-beats-attention': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(failed=1),
        running=True,
        expected=StageStatus.RUNNING,
    ),
    'stale': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(fresh=4, stale=1),
        running=False,
        expected=StageStatus.ATTENTION,
    ),
    'failed': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(fresh=4, failed=1),
        running=False,
        expected=StageStatus.ATTENTION,
    ),
    'review': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(fresh=PAGES, review=1),
        running=False,
        expected=StageStatus.ATTENTION,
    ),
    'done': StatusCase(
        stage=Stage.GEOMETRY,
        available=True,
        pages=PAGES,
        counts=Counts(fresh=PAGES),
        running=False,
        expected=StageStatus.DONE,
    ),
    'manual-with-pages': StatusCase(
        stage=Stage.IMPORT, available=True, pages=PAGES, counts=Counts(), running=False, expected=StageStatus.DONE
    ),
    'manual-without-pages': StatusCase(
        stage=Stage.PAGE_ORDER, available=True, pages=0, counts=Counts(), running=False, expected=StageStatus.WAITING
    ),
}


def summary_of(
    stage: Stage, *, available: bool = True, pages: int = PAGES, counts: Counts | None = None
) -> StageSummary:
    """Sum a stage from counts of its pages.

    :param stage: The stage.
    :type stage: Stage
    :param available: Whether the stage can be worked in.
    :type available: bool
    :param pages: Number of pages with an image.
    :type pages: int
    :param counts: How many pages are in each state, or None when no page has a record.
    :type counts: Counts | None
    :returns: The summary.
    :rtype: StageSummary
    """
    tally = None if counts is None else StageTally(project_id=PROJECT_ID, stage=stage, **counts._asdict())
    return StageSummary.of(stage, available=available, pages=pages, tally=tally, active_recipe_id=None)


def book_progress(
    available: set[Stage],
    pages: int = PAGES,
    counts: dict[Stage, Counts] | None = None,
    running: set[Stage] | None = None,
) -> BookProgress:
    """Sum a whole book from the stages that are available and the counts of the ones that ran.

    :param available: The stages that can be worked in, besides the ones done by hand.
    :type available: set[Stage]
    :param pages: Number of pages with an image.
    :type pages: int
    :param counts: The counts of the stages that have records.
    :type counts: dict[Stage, Counts] | None
    :param running: The stages a job runs.
    :type running: set[Stage] | None
    :returns: The progress of the book.
    :rtype: BookProgress
    """
    counted = counts or {}
    summaries = [
        summary_of(stage, available=stage.manual or stage in available, pages=pages, counts=counted.get(stage))
        for stage in Stage
    ]
    return BookProgress.of(summaries, running or set())


class TestStageSummary:
    """Tests for StageSummary."""

    @pytest.mark.parametrize('case', STATUS_CASES.values(), ids=STATUS_CASES.keys())
    def test_status_follows_the_one_rule(self, case: StatusCase) -> None:
        """Verify an unavailable stage, a running one, one with something to look at, a finished one and one waiting.

        :param case: The stage, its counts and the status expected.
        :type case: StatusCase
        """
        summary = summary_of(case.stage, available=case.available, pages=case.pages, counts=case.counts)
        assert summary.status(running=case.running) is case.expected

    def test_pages_without_a_record_are_counted_as_not_run(self) -> None:
        """Verify the stage that ran on three of five pages has two that it has not run on."""
        summary = summary_of(Stage.GEOMETRY, counts=Counts(fresh=2, stale=1))
        expect((summary.fresh, summary.stale, summary.failed, summary.not_run) == (2, 1, 0, PAGES - 3))
        expect(summary.manual is False)
        assert_expectations()

    def test_the_check_count_is_the_tally_and_not_the_sum_of_stale_and_review(self) -> None:
        """Verify a page both stale and marked is counted once, since the count comes from the tally as it is."""
        summary = summary_of(Stage.GEOMETRY, counts=Counts(fresh=2, stale=2, review=2, check=3))
        assert summary.check == 3

    def test_the_steps_the_pages_stopped_at_come_in_the_order_of_the_steps(self) -> None:
        """Verify the counts of the steps are sorted, and a stage done by hand takes none."""
        stopped = [StepTally(stage=Stage.GEOMETRY, through_step=step, pages=1) for step in (2, 0, 1)]
        summary = summary_of(Stage.GEOMETRY, counts=Counts(fresh=3, partial=3)).with_stopped(stopped)
        manual = summary_of(Stage.PAGE_ORDER).with_stopped(stopped)
        expect([one.through_step for one in summary.stopped] == [0, 1, 2])
        expect((summary.partial, manual.stopped) == (3, ()))
        assert_expectations()

    def test_a_stage_that_never_ran_has_every_page_not_run(self) -> None:
        """Verify a stage no page has a record of counts all its pages as not run."""
        summary = summary_of(Stage.GEOMETRY)
        assert (summary.fresh, summary.not_run, summary.review, summary.check) == (0, PAGES, 0, 0)

    def test_more_records_than_pages_never_make_a_negative_count(self) -> None:
        """Verify a page that turned into a placeholder after a run cannot take the count of the others below zero."""
        assert summary_of(Stage.GEOMETRY, pages=2, counts=Counts(fresh=3)).not_run == 0

    def test_a_stage_done_by_hand_has_no_counts_whatever_the_tally_says(self) -> None:
        """Verify the import and the page order are summed by their pages alone."""
        summary = summary_of(Stage.PAGE_ORDER, counts=Counts(fresh=PAGES, review=2, check=2))
        expect(summary.manual is True)
        expect((summary.fresh, summary.not_run, summary.review, summary.check, summary.pages) == (0, 0, 0, 0, PAGES))
        assert_expectations()


class TestBookProgress:
    """Tests for BookProgress."""

    def test_the_stages_come_in_the_order_of_the_pipeline(self) -> None:
        """Verify there is a status for every one of the ten stages, in pipeline order."""
        progress = book_progress({Stage.GEOMETRY})
        assert [one.stage for one in progress.stages] == list(Stage)

    def test_an_empty_book_waits_at_the_import(self) -> None:
        """Verify the first stage with work for a book without pages is the import, which waits for its first files."""
        progress = book_progress({Stage.PAGE_SPLIT, Stage.GEOMETRY}, pages=0)
        expect(progress.next_stage is Stage.IMPORT)
        expect(progress.stages[0].status is StageStatus.WAITING)
        assert_expectations()

    def test_an_imported_book_goes_on_to_the_first_available_stage_of_processing(self) -> None:
        """Verify the import and the order are done once there are pages, and stages with no plugin are not offered."""
        progress = book_progress({Stage.PAGE_SPLIT, Stage.GEOMETRY})
        statuses = {one.stage: one.status for one in progress.stages}
        expect(progress.next_stage is Stage.PAGE_SPLIT)
        expect(statuses[Stage.IMPORT] is StageStatus.DONE)
        expect(statuses[Stage.PAGE_ORDER] is StageStatus.DONE)
        expect(statuses[Stage.CLEANUP] is StageStatus.UNAVAILABLE)
        assert_expectations()

    def test_a_stage_that_is_done_is_passed_over(self) -> None:
        """Verify the next stage is the first that is not done, here the geometry after a finished split."""
        progress = book_progress({Stage.PAGE_SPLIT, Stage.GEOMETRY}, counts={Stage.PAGE_SPLIT: Counts(fresh=PAGES)})
        assert progress.next_stage is Stage.GEOMETRY

    def test_a_stage_that_needs_a_look_is_the_next_one(self) -> None:
        """Verify a stage with a stale page comes back as the next one although a later stage waits."""
        progress = book_progress(
            {Stage.PAGE_SPLIT, Stage.GEOMETRY}, counts={Stage.PAGE_SPLIT: Counts(fresh=PAGES - 1, stale=1)}
        )
        expect(progress.next_stage is Stage.PAGE_SPLIT)
        expect(progress.stages[1].status is StageStatus.ATTENTION)
        assert_expectations()

    def test_a_running_stage_is_marked_and_still_counts_as_work(self) -> None:
        """Verify a stage a job runs has the status running, and is the next stage while it is the first with work."""
        progress = book_progress(
            {Stage.PAGE_SPLIT, Stage.GEOMETRY},
            counts={Stage.PAGE_SPLIT: Counts(fresh=PAGES)},
            running={Stage.GEOMETRY},
        )
        expect({one.stage: one.status for one in progress.stages}[Stage.GEOMETRY] is StageStatus.RUNNING)
        expect(progress.next_stage is Stage.GEOMETRY)
        assert_expectations()

    def test_a_book_with_nothing_left_to_do_has_no_next_stage(self) -> None:
        """Verify the next stage is None when every stage that can be worked in is done."""
        done = {Stage.PAGE_SPLIT: Counts(fresh=PAGES), Stage.GEOMETRY: Counts(fresh=PAGES)}
        assert book_progress({Stage.PAGE_SPLIT, Stage.GEOMETRY}, counts=done).next_stage is None


class TestPageStageStatus:
    """Tests for PageStageStatus."""

    @pytest.mark.parametrize(
        ('state', 'expected'),
        [
            (None, PageStageStatus.NOT_RUN),
            (StageState.FRESH, PageStageStatus.FRESH),
            (StageState.STALE, PageStageStatus.STALE),
            (StageState.FAILED, PageStageStatus.FAILED),
        ],
        ids=['no-record', 'fresh', 'stale', 'failed'],
    )
    def test_status_is_the_state_of_the_record_or_not_run(
        self, state: StageState | None, expected: PageStageStatus
    ) -> None:
        """Verify a page with no record is not run, and a record gives the status of the same name.

        :param state: State of the record, or None.
        :type state: StageState | None
        :param expected: The status.
        :type expected: PageStageStatus
        """
        assert PageStageStatus.of(state) is expected

    def test_every_state_of_a_record_has_a_status_of_its_own(self) -> None:
        """Verify no state of a record is left without a status, which a new state would break."""
        assert {PageStageStatus.of(state).value for state in StageState} == {state.value for state in StageState}


class TestStageRow:
    """Tests for StageRow."""

    def test_a_row_with_no_version_has_no_review_mark(self) -> None:
        """Verify a page the stage has not run on asks for no second look."""
        assert StageRow(page_id=PageId(uuid4())).review is None

    def test_the_review_mark_of_a_row_is_the_mark_of_its_current_version(self) -> None:
        """Verify the row shows what the version it holds says, so the strip and the version never disagree."""
        page_id = PageId(uuid4())
        version = evolve(make_page_version(page_id=page_id), review=ReviewReason.NOT_APPLIED)
        assert StageRow(page_id=page_id, head_version=version).review is ReviewReason.NOT_APPLIED


def recipe_of(*steps: Step) -> Recipe:
    """Build a recipe of the geometry stage with the steps, in the order given.

    :param steps: The steps of the recipe.
    :type steps: Step
    :returns: The recipe.
    :rtype: Recipe
    """
    moment = datetime(2026, 1, 1, tzinfo=UTC)
    return Recipe(
        id=RecipeId(uuid4()),
        project_id=PROJECT_ID,
        stage=Stage.GEOMETRY,
        name='Book',
        steps=steps,
        active=True,
        created_at=moment,
        updated_at=moment,
    )


def made_by(page_id: PageId, processor_key: str, *, skipped: bool = False) -> PageVersion:
    """Build a version of the geometry stage that a step of a processor made.

    :param page_id: The page.
    :type page_id: PageId
    :param processor_key: Key of the processor of the step.
    :type processor_key: str
    :param skipped: Whether the page did not meet the condition of the step, so the step passed it unchanged.
    :type skipped: bool
    :returns: The version.
    :rtype: PageVersion
    """
    return evolve(
        make_page_version(page_id=page_id),
        stage=Stage.GEOMETRY,
        processor=ProcessorRef(key=processor_key, version='1'),
        data={VersionData.SKIPPED_BY_CONDITION: True} if skipped else {},
    )


class TestStepRow:
    """Tests for StepRow.of, the place of one step on one page."""

    def test_a_page_the_stage_has_not_run_on_holds_the_default_shape_and_no_version(self) -> None:
        """Verify a page without a recipe has nothing of the step but the default shape."""
        step = Step(processor_key=DESKEW_KEY)
        row = StepRow.of(step.step_id, None, [], None, edited=False)
        assert (row.state, row.input_version, row.version) == (FigureState.DEFAULT, None, None)

    def test_a_step_that_ran_has_found_a_shape_and_reads_the_version_before_it(self) -> None:
        """Verify the version of a step is the one at its place in the chain, and its input the one before."""
        page_id = PageId(uuid4())
        first, second = Step(processor_key=PERSPECTIVE_KEY), Step(processor_key=DESKEW_KEY)
        chain = [made_by(page_id, PERSPECTIVE_KEY), made_by(page_id, DESKEW_KEY)]
        row = StepRow.of(second.step_id, recipe_of(first, second), chain, None, edited=False)
        expect(row.state is FigureState.FOUND)
        expect(row.version is chain[1])
        expect(row.input_version is chain[0])
        assert_expectations()

    def test_the_first_step_reads_the_version_the_stage_before_made(self) -> None:
        """Verify the input of the first step of the chain is the version of the earlier stage it read."""
        page_id = PageId(uuid4())
        step = Step(processor_key=PERSPECTIVE_KEY)
        before = make_page_version(page_id=page_id)
        chain = [made_by(page_id, PERSPECTIVE_KEY)]
        row = StepRow.of(step.step_id, recipe_of(step), chain, before, edited=False)
        assert row.input_version is before

    def test_a_step_a_run_stopped_before_has_no_version_but_reads_what_the_step_before_made(self) -> None:
        """Verify a page run through the first step only offers the next step the picture it would read."""
        page_id = PageId(uuid4())
        first, second = Step(processor_key=PERSPECTIVE_KEY), Step(processor_key=DESKEW_KEY)
        chain = [made_by(page_id, PERSPECTIVE_KEY)]
        row = StepRow.of(second.step_id, recipe_of(first, second), chain, None, edited=False)
        expect((row.state, row.version) == (FigureState.DEFAULT, None))
        expect(row.input_version is chain[0])
        assert_expectations()

    def test_a_step_that_is_switched_off_leaves_the_places_of_the_others(self) -> None:
        """Verify the place in the chain counts the steps that are on, as the run makes the versions."""
        page_id = PageId(uuid4())
        off = Step(processor_key=PERSPECTIVE_KEY, enabled=False)
        first, second = Step(processor_key=DESKEW_KEY), Step(processor_key=CROP_KEY)
        chain = [made_by(page_id, DESKEW_KEY), made_by(page_id, CROP_KEY)]
        recipe = recipe_of(off, first, second)
        expect(StepRow.of(off.step_id, recipe, chain, None, edited=False).version is None)
        expect(StepRow.of(second.step_id, recipe, chain, None, edited=False).version is chain[1])
        assert_expectations()

    def test_a_version_of_another_processor_is_the_version_of_a_step_the_recipe_changed(self) -> None:
        """Verify a step does not take for its own the version of another processor that stands at its place."""
        page_id = PageId(uuid4())
        step = Step(processor_key=DESKEW_KEY)
        row = StepRow.of(step.step_id, recipe_of(step), [made_by(page_id, CROP_KEY)], None, edited=False)
        assert row.version is None

    def test_a_page_that_did_not_meet_the_condition_is_skipped_whatever_the_edit_is(self) -> None:
        """Verify the skip wins over an edit, which a skipped step does not read."""
        page_id = PageId(uuid4())
        step = Step(processor_key=DESKEW_KEY)
        row = StepRow.of(step.step_id, recipe_of(step), [made_by(page_id, DESKEW_KEY, skipped=True)], None, edited=True)
        assert row.state is FigureState.SKIPPED

    def test_an_edit_makes_the_shape_set_by_hand_on_a_page_that_ran_and_on_one_that_did_not(self) -> None:
        """Verify an edit outlives the run, and is there before the first run."""
        page_id = PageId(uuid4())
        step = Step(processor_key=DESKEW_KEY)
        ran = StepRow.of(step.step_id, recipe_of(step), [made_by(page_id, DESKEW_KEY)], None, edited=True)
        not_run = StepRow.of(step.step_id, None, [], None, edited=True)
        assert (ran.state, not_run.state) == (FigureState.BY_HAND, FigureState.BY_HAND)

    def test_two_steps_of_one_processor_are_told_apart_by_their_identifiers(self) -> None:
        """Verify the second deskew of a recipe gets the second version and not the first."""
        page_id = PageId(uuid4())
        first, second = Step(processor_key=DESKEW_KEY), Step(processor_key=DESKEW_KEY)
        chain = [made_by(page_id, DESKEW_KEY, skipped=True), made_by(page_id, DESKEW_KEY)]
        recipe = recipe_of(first, second)
        expect(StepRow.of(first.step_id, recipe, chain, None, edited=False).state is FigureState.SKIPPED)
        expect(StepRow.of(second.step_id, recipe, chain, None, edited=False).state is FigureState.FOUND)
        assert_expectations()


class TestStageManual:
    """Tests for Stage.manual."""

    def test_only_the_import_and_the_page_order_are_done_by_hand(self) -> None:
        """Verify the stages that need no processor are the two the interface lets the user do alone."""
        assert {stage for stage in Stage if stage.manual} == {Stage.IMPORT, Stage.PAGE_ORDER}


class TestStageRowMarkedBad:
    """Tests for StageRow.marked_bad."""

    BAD: PageVersion = evolve(make_page_version(page_id=PageId(uuid4())), mark=ResultMark.BAD)
    GOOD: PageVersion = evolve(make_page_version(page_id=PageId(uuid4())), mark=ResultMark.GOOD)

    def test_a_row_of_the_stage_stands_on_the_current_version(self) -> None:
        """Verify the flag is set by a bad current version only, not by a good one or by none."""
        page_id = PageId(uuid4())
        expect(StageRow(page_id=page_id, head_version=self.BAD).marked_bad)
        expect(not StageRow(page_id=page_id, head_version=self.GOOD).marked_bad)
        expect(not StageRow(page_id=page_id).marked_bad)
        assert_expectations()

    def test_a_row_at_a_step_stands_on_the_version_of_the_step(self) -> None:
        """Verify the version at the step decides, whatever the current version of the stage carries."""
        page_id = PageId(uuid4())
        step_id = Step(processor_key=DESKEW_KEY).step_id
        marked_at_step = StageRow(
            page_id=page_id, head_version=self.GOOD, step=StepRow(step_id=step_id, version=self.BAD)
        )
        marked_at_head = StageRow(
            page_id=page_id, head_version=self.BAD, step=StepRow(step_id=step_id, version=self.GOOD)
        )
        not_reached = StageRow(page_id=page_id, head_version=self.BAD, step=StepRow(step_id=step_id))
        expect(marked_at_step.marked_bad)
        expect(not marked_at_head.marked_bad)
        expect(not not_reached.marked_bad)
        assert_expectations()
