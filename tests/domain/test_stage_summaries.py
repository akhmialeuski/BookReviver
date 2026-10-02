"""Tests for the summary of a stage of a book, its status, and the progress of a book along the pipeline."""

from typing import NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import PageStageStatus, ReviewReason, Stage, StageState, StageStatus
from bookreviver.domain.ids import PageId, ProjectId
from bookreviver.domain.stage_summaries import BookProgress, StageRow, StageSummary, StageTally
from tests.helpers.builders import make_page_version

PROJECT_ID: ProjectId = ProjectId(uuid4())
PAGES: int = 5


class Counts(NamedTuple):
    """How many pages of a stage are in each state, as the records of the stage say.

    :ivar fresh: Pages that are up to date.
    :ivar stale: Pages that are out of date.
    :ivar failed: Pages the stage failed on.
    :ivar review: Pages marked for review.
    :ivar check: Pages that are stale, failed or marked, each once.
    """

    fresh: int = 0
    stale: int = 0
    failed: int = 0
    review: int = 0
    check: int = 0


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


class TestStageManual:
    """Tests for Stage.manual."""

    def test_only_the_import_and_the_page_order_are_done_by_hand(self) -> None:
        """Verify the stages that need no processor are the two the interface lets the user do alone."""
        assert {stage for stage in Stage if stage.manual} == {Stage.IMPORT, Stage.PAGE_ORDER}
