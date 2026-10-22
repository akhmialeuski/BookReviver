"""Tests for the jobs that run a stage and preview a step: the cache of versions, the current version and staleness."""

from datetime import timedelta
from functools import partial
from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    ImagePolicy,
    JobKind,
    JobState,
    RecipeKind,
    Rendition,
    ResultMark,
    Stage,
    StageState,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.events import PageStageChanged, PageVersionReady
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import PageVersionId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import (
    NewPageEdit,
    PageStageKey,
    PageStepKey,
    RecipeDraft,
    RecipeKey,
    SliceRequest,
    StageRun,
    Step,
    StepPreview,
)
from tests.helpers.builders import EPOCH, make_job, make_page
from tests.helpers.counting import CountingUnitOfWork
from tests.helpers.fake_processing import PREVIEW_TOKEN
from tests.helpers.held_steps import steps_held
from tests.helpers.meanwhile import change_while_writing
from tests.helpers.page_batches import PageValues
from tests.helpers.processing import (
    IMAGE_CONTENT,
    ProcessingKit,
    change_versions,
    found_stage,
    stage_versions,
    stored_job,
)
from tests.helpers.processors import FAILING_PARAMETER, RAN_KEY, STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import head_of, run_stage, stage_of
from tests.helpers.stage_heads import seed_head

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, Job, Page, Project
    from bookreviver.ports.persistence import BookRepositories
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
EVERYTHING: SliceRequest = SliceRequest(limit=100)
# What another request sets while a job runs: a strength, the identifier of a version, and a comment
STRENGTH_BY_HAND: int = 7
OTHER_INPUT_ID: str = '00112233445566ff'
COMMENT_BY_HAND: str = 'Check the margin.'
# The order key of the second page of a book
SECOND_PAGE_KEY: str = 'a1'
# The pages of a book a run is interrupted in, the last of which is the one whose step is under way
INTERRUPTED_BOOK_PAGES: int = 3


async def prepared_page(kit: ProcessingKit, **project_options: ImagePolicy) -> tuple[Actor, Project, Page]:
    """Seed a project with one scan page whose base version is stored, the input of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_options: Options of the project, such as its image policy.
    :type project_options: ImagePolicy
    :returns: The actor, the project and the page.
    :rtype: tuple[Actor, Project, Page]
    """
    actor, project = await kit.seed_project(**project_options)
    page, _ = await kit.seed_scan_page(project)
    await kit.seed_base_version(project.id, page)
    return actor, project, page


class TestRunStage:
    """Tests for the ``run-stage`` job."""

    async def test_page_gets_a_ready_version_that_is_the_current_one_of_the_stage(self, fx_kit: ProcessingKit) -> None:
        """Verify a run makes the version, stores its files and pyramid, and records it as current and fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        version = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        record = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        keys = ProjectKeys(project.id)
        async with fx_kit.assets.readable(keys.version_rendition(version, Rendition.FULL_JPEG)) as full:
            content = full.read_bytes()
        expect((version.state, version.scale) == (VersionState.READY, VersionScale.FULL))
        expect((version.renditions is not None and version.renditions.ready, version.tiles_ready) == (True, True))
        expect(content == IMAGE_CONTENT)
        expect(record.state is StageState.FRESH)
        expect(version.data == {RAN_KEY: 1})
        assert_expectations()

    async def test_job_succeeds_and_counts_its_pages(self, fx_kit: ProcessingKit) -> None:
        """Verify the job ends succeeded with one step of progress for each page, and queues a collection after it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        expect((stored.state, stored.progress.done, stored.progress.total) == (JobState.SUCCEEDED, 1, 1))
        expect([queued.kind.value for queued in fx_kit.board.put_jobs] == ['run-stage', 'collect-versions'])
        assert_expectations()

    async def test_progress_counts_a_task_for_each_step_of_each_page(self, fx_kit: ProcessingKit) -> None:
        """Verify a run of two pages by a recipe of three steps plans six tasks and has done all six when it ends.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        second, _ = await fx_kit.seed_scan_page(project, order_key=SECOND_PAGE_KEY)
        await fx_kit.seed_base_version(project.id, second)
        await save_three_steps(fx_kit, actor, project)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        expect((stored.state, stored.progress.done, stored.progress.total) == (JobState.SUCCEEDED, 6, 6))
        assert_expectations()

    async def test_rerun_with_the_same_parameters_finds_the_version_it_made(self, fx_kit: ProcessingKit) -> None:
        """Verify a repeated run of the page is a lookup: the processor does not run again and no version is added.

        The run names the page, which a run of the whole book would leave out as fresh, so the versions that were made
        are what answers the steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        run = StageRun(stage=Stage.GEOMETRY, page_ids=(page.id,))
        await run_stage(fx_kit, actor, project, run)
        await run_stage(fx_kit, actor, project, run)
        versions = await stage_versions(fx_kit, project.id, page, Stage.GEOMETRY)
        expect((fx_kit.fake.runs, versions.total) == (1, 1))
        assert_expectations()

    async def test_run_of_the_whole_book_leaves_out_the_page_whose_result_is_fresh(self, fx_kit: ProcessingKit) -> None:
        """Verify a second run of the whole book plans no task for a page that is fresh, and still ends as succeeded.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        again = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, again.id)
        expect((stored.state, stored.progress.done, stored.progress.total) == (JobState.SUCCEEDED, 0, 0))
        expect(fx_kit.fake.runs == 1)
        assert_expectations()

    async def test_rerun_with_other_parameters_makes_a_new_version_and_going_back_makes_the_first_again(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify other parameters give another identifier, and going back makes the first version again.

        The run of the second version deletes the first, since it is no longer current and nothing reads it, so going
        back runs the processor once more and makes the version under the same identifier. The run that goes back
        deletes the second version in its turn.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        first = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        strong = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, strong)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        second = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        replaced = await fx_kit.uow().read_book(project.id, lambda repos: repos.page_versions.find(first.id))
        back_draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY)])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, back_draft)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        back = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        total = (await stage_versions(fx_kit, project.id, page, Stage.GEOMETRY)).total
        expect((first.id != second.id, back.id == first.id, total) == (True, True, 1))
        expect(replaced is None)
        expect((fx_kit.fake.runs, second.data) == (3, {RAN_KEY: 2}))
        assert_expectations()

    async def test_events_announce_the_version_and_the_stage(self, fx_kit: ProcessingKit) -> None:
        """Verify the viewer is told of the new version and of the changed stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        head = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        ready = [event.version.id for event in fx_kit.events.published if isinstance(event, PageVersionReady)]
        changed = [
            (event.stage.stage, event.stage.head_version_id)
            for event in fx_kit.events.published
            if isinstance(event, PageStageChanged)
        ]
        expect(ready == [head.id])
        expect(changed == [(Stage.GEOMETRY, head.id)])
        assert_expectations()

    async def test_new_current_version_marks_the_later_stages_stale_and_keeps_their_versions(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a changed geometry makes the cleanup stale without deleting its versions, and a rerun gives new ones.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        cleanup_before = await head_of(fx_kit, project.id, page, Stage.CLEANUP)
        strong = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, strong)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        stale = await stage_of(fx_kit, project.id, page, Stage.CLEANUP)
        expect((stale.state, stale.head_version_id) == (StageState.STALE, cleanup_before.id))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        cleanup_after = await head_of(fx_kit, project.id, page, Stage.CLEANUP)
        fresh = await stage_of(fx_kit, project.id, page, Stage.CLEANUP)
        expect((cleanup_after.id != cleanup_before.id, fresh.state) == (True, StageState.FRESH))
        expect(cleanup_after.input_id == (await head_of(fx_kit, project.id, page, Stage.GEOMETRY)).id)
        assert_expectations()

    async def test_stale_earlier_stage_is_run_again_before_the_stage_after_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a run of the cleanup brings a stale geometry up to date first, and reads its new version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        strong = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, strong)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        geometry = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        cleanup = await head_of(fx_kit, project.id, page, Stage.CLEANUP)
        expect(geometry.state is StageState.FRESH)
        expect(cleanup.input_id == geometry.head_version_id)
        assert_expectations()

    async def test_stale_earlier_stage_that_cannot_be_run_again_fails_the_stage_after_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a stage is not made from a stale input it could not bring up to date, and it is not marked fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        cleanup_before = await head_of(fx_kit, project.id, page, Stage.CLEANUP)
        failing = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={FAILING_PARAMETER: True})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, failing)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        geometry = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        cleanup = await stage_of(fx_kit, project.id, page, Stage.CLEANUP)
        versions = await stage_versions(fx_kit, project.id, page, Stage.CLEANUP)
        expect((geometry.state, cleanup.state) == (StageState.FAILED, StageState.FAILED))
        expect((cleanup.head_version_id, versions.total) == (cleanup_before.id, 1))
        assert_expectations()

    async def test_run_skips_an_earlier_stage_whose_current_version_has_no_image_and_reads_the_one_before(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a run does not read a current version that has no published image, but the version a stage back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        base = await fx_kit.seed_base_version(project.id, page)
        await seed_head(fx_kit, project.id, PageStageKey(page.id, Stage.GEOMETRY), after=base, ready=False)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        assert (await head_of(fx_kit, project.id, page, Stage.CLEANUP)).input_id == base.id

    async def test_failing_step_fails_its_version_and_stage_and_the_job_goes_on(self, fx_kit: ProcessingKit) -> None:
        """Verify a step that fails is stored as failed with its reason, and the next page is still processed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, first = await prepared_page(fx_kit)
        second, _ = await fx_kit.seed_scan_page(project, order_key=SECOND_PAGE_KEY)
        await fx_kit.seed_base_version(project.id, second)
        failing = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={FAILING_PARAMETER: True})])
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, failing)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.work_queue()
        versions = await stage_versions(fx_kit, project.id, first, Stage.GEOMETRY)
        stage = await stage_of(fx_kit, project.id, first, Stage.GEOMETRY)
        other = await stage_of(fx_kit, project.id, second, Stage.GEOMETRY)
        stored = stored_job(fx_kit, job.id)
        expect(
            [(version.state, version.data) for version in versions.items]
            == [(VersionState.FAILED, {VersionData.ERROR: 'The fake step was told to fail.'})]
        )
        expect((stage.state, other.state) == (StageState.FAILED, StageState.FAILED))
        expect((stored.state, stored.progress.total) == (JobState.FAILED, 2))
        assert_expectations()

    async def test_failed_version_is_made_again_by_the_next_run(self, fx_kit: ProcessingKit) -> None:
        """Verify a version that failed is computed again under the same identifier when its cause is gone.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        good = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        await fx_kit.uow().change_book(
            project.id,
            lambda repos, _project: repos.page_versions.update(
                evolve(good, state=VersionState.FAILED, data={VersionData.ERROR: 'x'})
            ),
        )
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        again = await fx_kit.stored_version(project.id, good.id)
        expect((again.state, again.data) == (VersionState.READY, {RAN_KEY: 1}))
        assert_expectations()

    async def test_placeholder_is_skipped(self, fx_kit: ProcessingKit) -> None:
        """Verify a page without an image is not processed, and does not fail the job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        placeholder = make_page()
        await fx_kit.uow().change_book(project.id, lambda repos, _project: repos.pages.add(placeholder))
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        assert (stored.state, stored.progress.total, fx_kit.fake.runs) == (JobState.SUCCEEDED, 0, 0)

    async def test_page_split_runs_the_split_none_plugin_on_the_scan(self, fx_kit: ProcessingKit) -> None:
        """Verify the page split reads the scan, stores a copy as the base version and records its size.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, scan = await fx_kit.seed_scan_page(project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        version = await head_of(fx_kit, project.id, page, Stage.PAGE_SPLIT)
        expect((version.processor.key, version.input_id) == ('split.none', None))
        expect(version.data == {'width_px': scan.facts.width_px, 'height_px': scan.facts.height_px})
        assert_expectations()

    @pytest.mark.parametrize(
        ('policy', 'expected'),
        [(ImagePolicy.COMPACT, Rendition.FULL_JPEG), (ImagePolicy.LOSSLESS, Rendition.FULL_PNG)],
        ids=['compact', 'lossless'],
    )
    async def test_format_of_full_follows_the_image_policy_of_the_project(
        self, fx_kit: ProcessingKit, policy: ImagePolicy, expected: Rendition
    ) -> None:
        """Verify the gray result of a step is stored as a JPEG under ``compact`` and as a PNG under ``lossless``.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param policy: Image policy of the project.
        :type policy: ImagePolicy
        :param expected: Format of ``full`` the policy asks for a gray page.
        :type expected: Rendition
        """
        actor, project, page = await prepared_page(fx_kit, image_policy=policy)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        version = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        expect(fx_kit.writer.calls == [(expected, ColorMode.GRAY)])
        expect(version.renditions is not None and version.renditions.full is expected)
        assert_expectations()

    async def test_only_the_last_version_of_a_recipe_gets_its_pyramid(self, fx_kit: ProcessingKit) -> None:
        """Verify the version before the last is not tiled, since a viewer opens the current version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        twice = RecipeDraft(
            steps=[Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2})]
        )
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, twice)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        last = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        assert last.input_id is not None
        first = await fx_kit.stored_version(project.id, last.input_id)
        assert (first.tiles_ready, last.tiles_ready) == (False, True)

    async def test_step_switched_off_is_not_run(self, fx_kit: ProcessingKit) -> None:
        """Verify a run makes the versions of the steps that are on alone, as if the other were not in the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = [
            Step(processor_key=FAKE_KEY),
            Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 7}, enabled=False),
        ]
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, RecipeDraft(steps=steps))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        versions = await stage_versions(fx_kit, project.id, page, Stage.GEOMETRY)
        head = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        expect((fx_kit.fake.runs, versions.total) == (1, 1))
        expect(head.params[STRENGTH_PARAMETER] == 1)
        assert_expectations()

    async def test_job_cancelled_while_queued_is_dropped_and_runs_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify a job cancelled before the board takes it never runs, and no page is processed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.board.cancel(job.id)
        await fx_kit.work_queue()
        expect(stored_job(fx_kit, job.id).state is JobState.CANCELLED)
        expect((fx_kit.fake.runs, await found_stage(fx_kit, project.id, page, Stage.GEOMETRY)) == (0, None))
        assert_expectations()

    async def test_job_with_parameters_it_cannot_read_fails_with_the_reason(self, fx_kit: ProcessingKit) -> None:
        """Verify a job whose parameters are not those of a run fails and says so.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, _ = await prepared_page(fx_kit)
        job = fx_kit.board.put(make_job(project_id=project.id, kind=JobKind.RUN_STAGE, params={'stage': 'nowhere'}))
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        assert (stored.state, 'not valid' in stored.error) == (JobState.FAILED, True)


async def save_three_steps(kit: ProcessingKit, actor: Actor, project: Project) -> None:
    """Save a geometry recipe of three steps of the fake processor, which differ by their strength.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to save the recipe in.
    :type project: Project
    """
    steps = [Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: strength}) for strength in range(1, 4)]
    await kit.edit_recipe(actor, project, Stage.GEOMETRY, RecipeDraft(steps=steps))


class TestRunThroughStep:
    """Tests for a run of a stage that stops at one of the steps of its recipe."""

    async def test_run_makes_the_steps_up_to_the_one_asked_and_leaves_the_rest(self, fx_kit: ProcessingKit) -> None:
        """Verify a run through the second step makes two versions, and the stage head is the result of the second.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await save_three_steps(fx_kit, actor, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=1))
        head = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        record = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        versions = await stage_versions(fx_kit, project.id, page, Stage.GEOMETRY)
        expect((fx_kit.fake.runs, versions.total) == (2, 2))
        expect(head.params[STRENGTH_PARAMETER] == 2)
        expect((record.through_step, record.state) == (1, StageState.FRESH))
        expect(head.tiles_ready)
        assert_expectations()

    async def test_run_through_the_next_step_finds_the_steps_before_it_in_the_cache(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify going on from step one to step two and then to the last step runs each processor once.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await save_three_steps(fx_kit, actor, project)
        calls = []
        for through_step in (0, 1, None):
            await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=through_step))
            calls.append(fx_kit.fake.runs)
        record = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        versions = await stage_versions(fx_kit, project.id, page, Stage.GEOMETRY)
        expect(calls == [1, 2, 3])
        expect(versions.total == 3)
        expect(record.through_step is None)
        assert_expectations()

    async def test_run_through_the_last_step_that_is_on_is_a_complete_run(self, fx_kit: ProcessingKit) -> None:
        """Verify a run through a step that is the last one on leaves no step to finish, though a step is off after it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = [
            Step(processor_key=FAKE_KEY),
            Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2}, enabled=False),
        ]
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, RecipeDraft(steps=steps))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=0))
        record = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        assert record.through_step is None

    async def test_page_stopped_before_the_last_step_is_run_through_the_rest_before_the_next_stage(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the cleanup does not read a geometry that stopped at step one, and finishes it first from its cache.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await save_three_steps(fx_kit, actor, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=0))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        geometry = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        cleanup = await head_of(fx_kit, project.id, page, Stage.CLEANUP)
        expect((geometry.through_step, geometry.state) == (None, StageState.FRESH))
        expect(cleanup.input_id == geometry.head_version_id)
        expect((await head_of(fx_kit, project.id, page, Stage.GEOMETRY)).params[STRENGTH_PARAMETER] == 3)
        # The first geometry step is not computed twice, and the cleanup has a processor of its own
        expect(fx_kit.fake.runs == 3)
        assert_expectations()

    async def test_edit_made_after_the_first_step_reaches_the_steps_run_after_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a manual edit saved while the page is stopped at step one is read by the steps made on the next run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await save_three_steps(fx_kit, actor, project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=0))
        first = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        edit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))
        recipe = await fx_kit.parts(fx_kit.uow()).recipes.of_kind(project.id, Stage.GEOMETRY, RecipeKind.TEXT)
        key = PageStepKey(page.id, Stage.GEOMETRY, recipe.steps[-1].step_id)
        await fx_kit.edits().save(actor, project.id, key, edit, None)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        last = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        expect(last.edit_hash != '')
        expect(first.id not in {last.id, last.input_id})
        assert_expectations()

    async def test_failing_step_fails_the_stage_and_keeps_the_page_where_it_was(self, fx_kit: ProcessingKit) -> None:
        """Verify a step that fails on a run through it marks the stage failed and keeps the earlier stop.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = [Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY, params={FAILING_PARAMETER: True})]
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, RecipeDraft(steps=steps))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=0))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=1))
        record = await stage_of(fx_kit, project.id, page, Stage.GEOMETRY)
        expect((record.state, record.through_step) == (StageState.FAILED, 0))
        assert_expectations()

    @pytest.mark.parametrize('through_step', [3, 7])
    async def test_run_through_a_step_the_recipe_does_not_have_is_refused(
        self, fx_kit: ProcessingKit, through_step: int
    ) -> None:
        """Verify the request is refused at once, and no job is queued.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param through_step: Index past the last of the three steps.
        :type through_step: int
        """
        actor, project, _ = await prepared_page(fx_kit)
        await save_three_steps(fx_kit, actor, project)
        run = StageRun(stage=Stage.GEOMETRY, through_step=through_step)
        with pytest.raises(ConflictError):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)
        assert fx_kit.board.put_jobs == []

    async def test_run_through_a_step_with_every_step_up_to_it_off_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Verify there is nothing to run when the steps up to the one asked are all switched off.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        steps = [Step(processor_key=FAKE_KEY, enabled=False), Step(processor_key=FAKE_KEY)]
        await fx_kit.edit_recipe(actor, project, Stage.GEOMETRY, RecipeDraft(steps=steps))
        with pytest.raises(ConflictError):
            await fx_kit.service().start_run(
                actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY, through_step=0)
            )

    async def test_run_through_a_step_is_stored_with_the_job_and_read_back(self, fx_kit: ProcessingKit) -> None:
        """Verify the step travels in the parameters of the job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        await save_three_steps(fx_kit, actor, project)
        run = StageRun(stage=Stage.GEOMETRY, through_step=1)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)
        assert StageRun.from_map(job.params) == run


class TestPreviewStep:
    """Tests for the ``preview-step`` job."""

    async def test_preview_is_a_version_with_a_preview_file_that_never_becomes_current(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a preview makes a preview-scale version with its preview file only, and records no stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        preview = StepPreview(
            page_id=page.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=FAKE_KEY),), step_index=0
        )
        await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.work_queue()
        versions = await fx_kit.uow().read_book(
            project.id,
            lambda repos: repos.page_versions.list_for_stage(page.id, Stage.GEOMETRY, VersionScale.PREVIEW, EVERYTHING),
        )
        [version] = versions.items
        async with fx_kit.assets.readable(
            ProjectKeys(project.id).version_rendition(version, Rendition.PREVIEW)
        ) as file:
            content = file.read_bytes()
        record = await found_stage(fx_kit, project.id, page, Stage.GEOMETRY)
        expect((version.state, fx_kit.fake.previews, fx_kit.fake.runs) == (VersionState.READY, 1, 0))
        expect(content == PREVIEW_TOKEN)
        expect(record is None)
        assert_expectations()

    async def test_preview_of_the_same_steps_is_found_in_the_cache(self, fx_kit: ProcessingKit) -> None:
        """Verify moving a parameter back finds the preview that was made, so only changed steps are computed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        preview = StepPreview(
            page_id=page.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=FAKE_KEY),), step_index=0
        )
        for _ in range(2):
            await fx_kit.service().start_preview(actor, project.id, preview)
            await fx_kit.work_queue()
        assert fx_kit.fake.previews == 1

    async def test_only_the_steps_after_a_changed_one_are_computed_again(self, fx_kit: ProcessingKit) -> None:
        """Verify changing the second step of a form leaves the preview of the first in the cache.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        first = Step(processor_key=FAKE_KEY)
        for strength in (1, 2):
            steps = (first, Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: strength + 1}))
            preview = StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=steps, step_index=1)
            await fx_kit.service().start_preview(actor, project.id, preview)
            await fx_kit.work_queue()
        assert fx_kit.fake.previews == 3

    async def test_step_switched_off_is_left_out_of_the_preview(self, fx_kit: ProcessingKit) -> None:
        """Verify a preview up to the last step runs the steps that are on alone, which here is the first.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = (
            Step(processor_key=FAKE_KEY),
            Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2}, enabled=False),
        )
        preview = StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=steps, step_index=1)
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        expect((stored.state, fx_kit.fake.previews) == (JobState.SUCCEEDED, 1))
        assert_expectations()

    async def test_preview_with_every_step_up_to_the_index_off_fails_its_job(self, fx_kit: ProcessingKit) -> None:
        """Verify there is nothing to show when the steps up to the one asked for are all off, and the job says so.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = (
            Step(processor_key=FAKE_KEY, enabled=False),
            Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2}),
        )
        preview = StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=steps, step_index=0)
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        expect((stored.state, fx_kit.fake.previews) == (JobState.FAILED, 0))
        expect('switched off' in stored.error)
        assert_expectations()

    async def test_step_of_a_form_that_does_not_fit_its_processor_is_rejected_at_the_request(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Reject a preview whose parameters are wrong before any job is recorded.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        preview = StepPreview(
            page_id=page.id,
            stage=Stage.GEOMETRY,
            steps=(Step(processor_key=FAKE_KEY, params={'unknown': 1}),),
            step_index=0,
        )
        with pytest.raises(InvalidParametersError):
            await fx_kit.service().start_preview(actor, project.id, preview)

    async def test_preview_of_a_page_without_an_image_fails_its_job(self, fx_kit: ProcessingKit) -> None:
        """Verify a preview on a placeholder ends the job failed with the reason.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        placeholder = make_page()
        await fx_kit.uow().change_book(project.id, lambda repos, _project: repos.pages.add(placeholder))
        preview = StepPreview(
            page_id=placeholder.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=FAKE_KEY),), step_index=0
        )
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        assert (stored.state, stored.error) == (JobState.FAILED, 'The page has no image to preview a step on.')


class SeededPage(NamedTuple):
    """The account, the project and the page a change made while a job runs is made on.

    :ivar actor: Account owning the project.
    :ivar project: The project.
    :ivar page: The page the job runs.
    """

    actor: Actor
    project: Project
    page: Page


async def delete_the_page(kit: ProcessingKit, seeded: SeededPage) -> None:
    """Delete the page, as a request of the user does while a job of the stage runs.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param seeded: The project and the page.
    :type seeded: SeededPage
    """
    await kit.uow().change_book(seeded.project.id, lambda repos, _project: repos.pages.delete(seeded.page.id))


async def set_a_setting_by_hand(kit: ProcessingKit, seeded: SeededPage) -> None:
    """Give the page its own strength for the geometry step, through the service the interface uses.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param seeded: The account, the project and the page.
    :type seeded: SeededPage
    """
    key = await kit.edit_key(seeded.project.id, seeded.page, Stage.GEOMETRY, FAKE_KEY)
    await PageValues(kit, seeded.actor, seeded.project.id).set(key, STRENGTH_PARAMETER, STRENGTH_BY_HAND)


async def replace_the_input(kit: ProcessingKit, seeded: SeededPage) -> None:
    """Make another ready version the current one of the page split, which the geometry stage reads.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param seeded: The project and the page.
    :type seeded: SeededPage
    """

    def replace(repos: BookRepositories, _project: Project) -> None:
        """Add a copy of the base version under another identifier, and make it the head of the page split.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _project: Project of the book, which the work does not need.
        :type _project: Project
        """
        [base] = repos.page_versions.list_base_versions([seeded.page.id])
        other = evolve(base, id=PageVersionId(OTHER_INPUT_ID))
        record = repos.page_stages.get(PageStageKey(seeded.page.id, Stage.PAGE_SPLIT))
        repos.page_versions.add(other)
        repos.page_stages.save(evolve(record, head_version_id=other.id))

    await kit.uow().change_book(seeded.project.id, replace)


async def save_the_recipe(kit: ProcessingKit, seeded: SeededPage) -> None:
    """Save the geometry recipe with another strength a moment later, as the form of the recipe does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param seeded: The account and the project owning the recipe.
    :type seeded: SeededPage
    """
    kit.clock.moment = EPOCH + timedelta(hours=1)
    recipe = await kit.recipe_of(seeded.actor, seeded.project, Stage.GEOMETRY)
    draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: STRENGTH_BY_HAND})])
    await kit.service().save_recipe(seeded.actor, seeded.project.id, RecipeKey(Stage.GEOMETRY, recipe.id), draft)


class TestRunApplyStep:
    """Tests for the change that makes the result of a run current, which reads again what the run went by."""

    @pytest.mark.parametrize(
        'change',
        [
            pytest.param(delete_the_page, id='page-deleted'),
            pytest.param(set_a_setting_by_hand, id='setting-set-by-hand'),
            pytest.param(replace_the_input, id='input-replaced'),
            pytest.param(save_the_recipe, id='recipe-saved'),
        ],
    )
    async def test_a_run_whose_basis_changed_meanwhile_is_skipped_and_makes_nothing_current(
        self,
        fx_kit: ProcessingKit,
        monkeypatch: pytest.MonkeyPatch,
        change: Callable[[ProcessingKit, SeededPage], Awaitable[None]],
    ) -> None:
        """Verify a change that lands while the steps run leaves the stage without a head, and says nothing of it.

        The change lands after the step is computed and before the change that stores its version, which is where a
        request made while the job runs lands. The job ends as succeeded with no page made, since a page that was
        skipped is not a failure, and the stage keeps the mark the change gave it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the writer of renditions.
        :type monkeypatch: pytest.MonkeyPatch
        :param change: The change another request makes while the job runs.
        :type change: Callable[[ProcessingKit, SeededPage], Awaitable[None]]
        """
        seeded = SeededPage(*await prepared_page(fx_kit))
        job = await fx_kit.service().start_run(
            seeded.actor, seeded.project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY)
        )
        change_while_writing(fx_kit, monkeypatch, partial(change, fx_kit, seeded))
        await fx_kit.work_queue()
        stored = stored_job(fx_kit, job.id)
        record = await found_stage(fx_kit, seeded.project.id, seeded.page, Stage.GEOMETRY)
        announced = [
            event
            for event in fx_kit.events.published
            if isinstance(event, PageStageChanged) and event.stage.stage is Stage.GEOMETRY
        ]
        expect((stored.state, stored.progress.total, stored.error) == (JobState.SUCCEEDED, 1, ''))
        expect(record is None)
        expect(announced == [])
        assert_expectations()

    async def test_the_files_of_a_page_deleted_while_its_steps_ran_are_discarded(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the run leaves no file under the directory of the page that went away while it computed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the writer of renditions.
        :type monkeypatch: pytest.MonkeyPatch
        """
        seeded = SeededPage(*await prepared_page(fx_kit))
        await fx_kit.service().start_run(
            seeded.actor, seeded.project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY)
        )
        change_while_writing(fx_kit, monkeypatch, partial(delete_the_page, fx_kit, seeded))
        await fx_kit.work_queue()
        left = await fx_kit.assets.size_of(ProjectKeys(seeded.project.id).page(seeded.page.id))
        assert left == 0

    async def test_a_mark_and_a_comment_put_on_the_version_while_it_ran_are_kept(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the change that stores the result of a step does not write the mark of the version it read before.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the writer of renditions.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, project, page = await prepared_page(fx_kit)

        async def mark_the_running_version() -> None:
            """Mark and comment the version of the stage that is being made, through a unit of work of its own."""
            listed = await stage_versions(fx_kit, project.id, page, Stage.GEOMETRY)
            [running] = [version for version in listed.items if version.state is VersionState.RUNNING]
            await fx_kit.uow().change_book(
                project.id,
                lambda repos, _project: repos.page_versions.update(
                    evolve(running, mark=ResultMark.BAD, comment=COMMENT_BY_HAND)
                ),
            )

        change_while_writing(fx_kit, monkeypatch, mark_the_running_version)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        head = await head_of(fx_kit, project.id, page, Stage.GEOMETRY)
        expect((head.state, head.mark, head.comment) == (VersionState.READY, ResultMark.BAD, COMMENT_BY_HAND))
        assert_expectations()


async def interrupted_book(kit: ProcessingKit) -> tuple[Actor, Project, list[Page], Job]:
    """Seed a book of three pages and cancel a run of its geometry stage while the step of the third page is under way.

    The step is let go after the cancel, so it finishes and stores its version, and the job stops at the boundary of
    the task, before it makes the page current.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the pages in book order, and the job as it was cancelled.
    :rtype: tuple[Actor, Project, list[Page], Job]
    """
    actor, project = await kit.seed_project()
    pages = []
    for number in range(INTERRUPTED_BOOK_PAGES):
        page, _ = await kit.seed_scan_page(project, order_key=f'a{number}')
        await kit.seed_base_version(project.id, page)
        pages.append(page)
    job = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
    async with steps_held(kit, after=INTERRUPTED_BOOK_PAGES - 1):
        await kit.board.cancel(job.id)
    return actor, project, pages, job


class TestCancelledRun:
    """Tests for a run that is cancelled while a task is under way, which stops between two tasks."""

    async def test_cancel_between_tasks_keeps_the_finished_versions_and_fails_no_page(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the pages the run finished stay fresh, the task under way keeps its version, and no page is failed.

        The third step finishes after the cancel, so its version is stored as ready, and the cancellation raised after
        it passes the handlers that record a failed page, which leaves the third page without a record of the stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project, pages, job = await interrupted_book(fx_kit)
        records = [await found_stage(fx_kit, project.id, page, Stage.GEOMETRY) for page in pages]
        versions = [await stage_versions(fx_kit, project.id, page, Stage.GEOMETRY) for page in pages]
        expect(stored_job(fx_kit, job.id).state is JobState.CANCELLED)
        expect(
            [None if record is None else record.state for record in records]
            == [StageState.FRESH, StageState.FRESH, None]
        )
        expect(
            [[version.state for version in listed.items] for listed in versions]
            == [[VersionState.READY]] * INTERRUPTED_BOOK_PAGES
        )
        expect(fx_kit.fake.runs == INTERRUPTED_BOOK_PAGES)
        assert_expectations()


class TestRestart:
    """Tests for a run asked for after the process restarted, whose board holds none of the jobs of the one before."""

    async def test_whole_book_run_after_a_restart_goes_over_the_page_that_was_left_and_makes_its_running_version_again(
        self, fx_kit: ProcessingKit, fx_asset_store: LocalAssetStore, fx_uow: UnitOfWorkFactory
    ) -> None:
        """Verify only the third page gets tasks, and its version that a crash left running is made again.

        The run is interrupted after its second page, and the version of the third page is put back in the state a
        crash in the middle of its step leaves, which is running and without its files. The new board stands for the
        process after the restart: it holds no job, and the database and the files are the ones the first left.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_asset_store: Asset store the two kits share.
        :type fx_asset_store: LocalAssetStore
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        actor, project, pages, interrupted = await interrupted_book(fx_kit)
        [left] = (await stage_versions(fx_kit, project.id, pages[-1], Stage.GEOMETRY)).items
        await change_versions(
            fx_kit,
            project.id,
            lambda versions: versions.update(
                evolve(left, state=VersionState.RUNNING, renditions=None, tiles_ready=False)
            ),
        )
        restarted = ProcessingKit(fx_kit.database, fx_uow, fx_asset_store)
        job = await restarted.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await restarted.work_queue()
        stored = stored_job(restarted, job.id)
        made_again = await restarted.stored_version(project.id, left.id)
        record = await found_stage(restarted, project.id, pages[-1], Stage.GEOMETRY)
        with pytest.raises(NotFoundError):
            restarted.board.get(interrupted.id)
        expect((stored.state, stored.progress.done, stored.progress.total) == (JobState.SUCCEEDED, 1, 1))
        expect(restarted.fake.runs == 1)
        expect((made_again.state, made_again.renditions is not None) == (VersionState.READY, True))
        expect(record is not None and (record.state, record.head_version_id) == (StageState.FRESH, left.id))
        assert_expectations()


class TestStartRun:
    """Tests for the reads that ``ProcessingService.start_run`` makes before it queues the job."""

    async def test_run_over_chosen_pages_with_stored_recipes_takes_one_read(self, fx_kit: ProcessingKit) -> None:
        """Verify the pages and the recipes are read in one transaction of the book, not one for each.

        The recipes are stored by a first read of them, the mode is ``keep`` and no step is named, so the request needs
        no plan beyond the check. The job is stored by a change, which is no read.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)
        uow = CountingUnitOfWork(fx_kit.uow())
        run = StageRun(stage=Stage.GEOMETRY, page_ids=(page.id,))

        job = await fx_kit.service(uow).start_run(actor, project.id, Stage.GEOMETRY, run)

        assert (uow.reads, job.project_id) == (1, project.id)
