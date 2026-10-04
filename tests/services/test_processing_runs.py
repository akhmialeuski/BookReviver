"""Tests for the jobs that run a stage and preview a step: the cache of versions, the current version and staleness."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import (
    ColorMode,
    EditorKind,
    ImagePolicy,
    JobState,
    Rendition,
    Stage,
    StageState,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, InvalidParametersError
from bookreviver.domain.events import PageStageChanged, PageVersionReady
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import (
    NewPageEdit,
    PageStageKey,
    PageStepKey,
    RecipeDraft,
    SliceRequest,
    StageRun,
    Step,
    StepPreview,
)
from tests.helpers.builders import make_page
from tests.helpers.fake_processing import PREVIEW_TOKEN
from tests.helpers.processing import IMAGE_CONTENT
from tests.helpers.processors import FAILING_PARAMETER, STRENGTH_PARAMETER, FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Page, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
EVERYTHING: SliceRequest = SliceRequest(limit=100)


async def run_stage(kit: ProcessingKit, actor: Actor, project: Project, run: StageRun) -> None:
    """Start a run of a stage and let the worker do it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to run in.
    :type project: Project
    :param run: The stage and the recipe and pages to run it on.
    :type run: StageRun
    """
    job = await kit.service().start_run(actor, project.id, run.stage, run)
    await kit.jobs().run_stage(job.id)
    await kit.work_queue()


async def head_of(kit: ProcessingKit, page: Page, stage: Stage) -> PageVersion:
    """Read the current version of a stage of a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :returns: The version the stage record names.
    :rtype: PageVersion
    """
    reader = kit.uow()
    record = await reader.page_stages.get(PageStageKey(page.id, stage))
    assert record.head_version_id is not None
    return await reader.page_versions.get(record.head_version_id)


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
    await kit.seed_base_version(page)
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
        version = await head_of(fx_kit, page, Stage.GEOMETRY)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        keys = ProjectKeys(project.id)
        async with fx_kit.assets.readable(keys.version_rendition(version, Rendition.FULL_JPEG)) as full:
            content = full.read_bytes()
        expect((version.state, version.scale) == (VersionState.READY, VersionScale.FULL))
        expect((version.renditions is not None and version.renditions.ready, version.tiles_ready) == (True, True))
        expect(content == IMAGE_CONTENT)
        expect(record.state is StageState.FRESH)
        expect(version.data == {'ran': 1})
        assert_expectations()

    async def test_job_succeeds_and_counts_its_pages(self, fx_kit: ProcessingKit) -> None:
        """Verify the job ends succeeded with one step of progress for each page, and queues a collection after it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.jobs().run_stage(job.id)
        stored = await fx_kit.uow().jobs.get(job.id)
        expect((stored.state, stored.progress.done, stored.progress.total) == (JobState.SUCCEEDED, 1, 1))
        expect([queued.kind.value for queued in fx_kit.recording.enqueued] == ['run-stage', 'collect-versions'])
        assert_expectations()

    async def test_rerun_with_the_same_parameters_finds_the_version_it_made(self, fx_kit: ProcessingKit) -> None:
        """Verify a repeated run is a lookup: the processor does not run again and no version is added.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        versions = await fx_kit.uow().page_versions.list_for_stage(page.id, Stage.GEOMETRY, None, EVERYTHING)
        expect((fx_kit.fake.runs, versions.total) == (1, 1))
        assert_expectations()

    async def test_rerun_with_other_parameters_makes_a_new_version_beside_the_old_one(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify other parameters give another identifier, and going back to the first finds the first version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        first = await head_of(fx_kit, page, Stage.GEOMETRY)
        service = fx_kit.service()
        strong = RecipeDraft(name='Strong', steps=[Step(processor_key=FAKE_KEY, params={'strength': 2})])
        await service.save_recipe(actor, project.id, Stage.GEOMETRY, strong)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        second = await head_of(fx_kit, page, Stage.GEOMETRY)
        back_draft = RecipeDraft(name='Back', steps=[Step(processor_key=FAKE_KEY)])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, back_draft)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        back = await head_of(fx_kit, page, Stage.GEOMETRY)
        total = (await fx_kit.uow().page_versions.list_for_stage(page.id, Stage.GEOMETRY, None, EVERYTHING)).total
        expect((first.id != second.id, back.id == first.id, total) == (True, True, 2))
        expect((fx_kit.fake.runs, second.data) == (2, {'ran': 2}))
        assert_expectations()

    async def test_events_announce_the_version_and_the_stage(self, fx_kit: ProcessingKit) -> None:
        """Verify the viewer is told of the new version and of the changed stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        head = await head_of(fx_kit, page, Stage.GEOMETRY)
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
        cleanup_before = await head_of(fx_kit, page, Stage.CLEANUP)
        strong = RecipeDraft(name='Strong', steps=[Step(processor_key=FAKE_KEY, params={'strength': 2})])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, strong)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        stale = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.CLEANUP))
        expect((stale.state, stale.head_version_id) == (StageState.STALE, cleanup_before.id))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        cleanup_after = await head_of(fx_kit, page, Stage.CLEANUP)
        fresh = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.CLEANUP))
        expect((cleanup_after.id != cleanup_before.id, fresh.state) == (True, StageState.FRESH))
        expect(cleanup_after.input_id == (await head_of(fx_kit, page, Stage.GEOMETRY)).id)
        assert_expectations()

    async def test_stale_earlier_stage_is_run_again_before_the_stage_after_it(self, fx_kit: ProcessingKit) -> None:
        """Verify a run of the cleanup brings a stale geometry up to date first, and reads its new version.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        strong = RecipeDraft(name='Strong', steps=[Step(processor_key=FAKE_KEY, params={'strength': 2})])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, strong)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        geometry = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        cleanup = await head_of(fx_kit, page, Stage.CLEANUP)
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
        cleanup_before = await head_of(fx_kit, page, Stage.CLEANUP)
        failing = RecipeDraft(name='Failing', steps=[Step(processor_key=FAKE_KEY, params={'fail': True})])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, failing)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        geometry = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        cleanup = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.CLEANUP))
        versions = await fx_kit.uow().page_versions.list_for_stage(page.id, Stage.CLEANUP, None, EVERYTHING)
        expect((geometry.state, cleanup.state) == (StageState.FAILED, StageState.FAILED))
        expect((cleanup.head_version_id, versions.total) == (cleanup_before.id, 1))
        assert_expectations()

    async def test_failing_step_fails_its_version_and_stage_and_the_job_goes_on(self, fx_kit: ProcessingKit) -> None:
        """Verify a step that fails is stored as failed with its reason, and the next page is still processed.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, first = await prepared_page(fx_kit)
        second, _ = await fx_kit.seed_scan_page(project, order_key='a1')
        await fx_kit.seed_base_version(second)
        failing = RecipeDraft(name='Failing', steps=[Step(processor_key=FAKE_KEY, params={'fail': True})])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, failing)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.jobs().run_stage(job.id)
        versions = await fx_kit.uow().page_versions.list_for_stage(first.id, Stage.GEOMETRY, None, EVERYTHING)
        stage = await fx_kit.uow().page_stages.get(PageStageKey(first.id, Stage.GEOMETRY))
        other = await fx_kit.uow().page_stages.get(PageStageKey(second.id, Stage.GEOMETRY))
        stored = await fx_kit.uow().jobs.get(job.id)
        expect(
            [(version.state, version.data) for version in versions.items]
            == [(VersionState.FAILED, {'error': 'The fake step was told to fail.'})]
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
        good = await head_of(fx_kit, page, Stage.GEOMETRY)
        uow = fx_kit.uow()
        await uow.page_versions.update(evolve(good, state=VersionState.FAILED, data={'error': 'x'}))
        await uow.commit()
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        again = await fx_kit.stored_version(good.id)
        expect((again.state, again.data) == (VersionState.READY, {'ran': 1}))
        assert_expectations()

    async def test_placeholder_is_skipped(self, fx_kit: ProcessingKit) -> None:
        """Verify a page without an image is not processed, and does not fail the job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        placeholder = make_page(project_id=project.id)
        uow = fx_kit.uow()
        await uow.pages.add(placeholder)
        await uow.commit()
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.jobs().run_stage(job.id)
        stored = await fx_kit.uow().jobs.get(job.id)
        assert (stored.state, stored.progress.total, fx_kit.fake.runs) == (JobState.SUCCEEDED, 0, 0)

    async def test_page_split_runs_the_split_none_plugin_on_the_scan(self, fx_kit: ProcessingKit) -> None:
        """Verify the page split reads the scan, stores a copy as the base version and records its size.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, scan = await fx_kit.seed_scan_page(project)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.PAGE_SPLIT))
        version = await head_of(fx_kit, page, Stage.PAGE_SPLIT)
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
        version = await head_of(fx_kit, page, Stage.GEOMETRY)
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
            name='Twice', steps=[Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY, params={'strength': 2})]
        )
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, twice)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        last = await head_of(fx_kit, page, Stage.GEOMETRY)
        assert last.input_id is not None
        first = await fx_kit.stored_version(last.input_id)
        assert (first.tiles_ready, last.tiles_ready) == (False, True)

    async def test_step_switched_off_is_not_run(self, fx_kit: ProcessingKit) -> None:
        """Verify a run makes the versions of the steps that are on alone, as if the other were not in the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = [Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY, params={'strength': 7}, enabled=False)]
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name='One of two', steps=steps)
        )
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        versions = await fx_kit.uow().page_versions.list_for_stage(page.id, Stage.GEOMETRY, None, EVERYTHING)
        head = await head_of(fx_kit, page, Stage.GEOMETRY)
        expect((fx_kit.fake.runs, versions.total) == (1, 1))
        expect(head.params['strength'] == 1)
        assert_expectations()

    async def test_cancelled_job_stops_before_its_next_page(self, fx_kit: ProcessingKit) -> None:
        """Verify a job cancelled while queued does nothing when a worker takes it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        uow = fx_kit.uow()
        await uow.jobs.update(evolve(job, state=JobState.CANCELLED))
        await uow.commit()
        await fx_kit.jobs().run_stage(job.id)
        assert (fx_kit.fake.runs, await fx_kit.uow().page_stages.find(PageStageKey(page.id, Stage.GEOMETRY))) == (
            0,
            None,
        )

    async def test_job_with_parameters_it_cannot_read_fails_with_the_reason(self, fx_kit: ProcessingKit) -> None:
        """Verify a job whose stored parameters are not those of a run fails and says so.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        uow = fx_kit.uow()
        await uow.jobs.update(evolve(job, params={'stage': 'nowhere'}))
        await uow.commit()
        await fx_kit.jobs().run_stage(job.id)
        stored = await fx_kit.uow().jobs.get(job.id)
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
    await kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, RecipeDraft(name='Three', steps=steps))


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
        head = await head_of(fx_kit, page, Stage.GEOMETRY)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        versions = await fx_kit.uow().page_versions.list_for_stage(page.id, Stage.GEOMETRY, None, EVERYTHING)
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
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        versions = await fx_kit.uow().page_versions.list_for_stage(page.id, Stage.GEOMETRY, None, EVERYTHING)
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
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name='Off at the end', steps=steps)
        )
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=0))
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
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
        geometry = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        cleanup = await head_of(fx_kit, page, Stage.CLEANUP)
        expect((geometry.through_step, geometry.state) == (None, StageState.FRESH))
        expect(cleanup.input_id == geometry.head_version_id)
        expect((await head_of(fx_kit, page, Stage.GEOMETRY)).params[STRENGTH_PARAMETER] == 3)
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
        first = await head_of(fx_kit, page, Stage.GEOMETRY)
        edit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))
        recipe = await fx_kit.parts(fx_kit.uow()).recipes.active(project.id, Stage.GEOMETRY)
        key = PageStepKey(page.id, Stage.GEOMETRY, recipe.steps[-1].step_id)
        await fx_kit.edits().save(actor, project.id, key, edit, None)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        last = await head_of(fx_kit, page, Stage.GEOMETRY)
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
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name='Fails second', steps=steps)
        )
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=0))
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, through_step=1))
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
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
        assert fx_kit.recording.enqueued == []

    async def test_run_through_a_step_with_every_step_up_to_it_off_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Verify there is nothing to run when the steps up to the one asked are all switched off.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await prepared_page(fx_kit)
        steps = [Step(processor_key=FAKE_KEY, enabled=False), Step(processor_key=FAKE_KEY)]
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, RecipeDraft(name='Off first', steps=steps)
        )
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
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.jobs().preview_step(job.id)
        versions = await fx_kit.uow().page_versions.list_for_stage(
            page.id, Stage.GEOMETRY, VersionScale.PREVIEW, EVERYTHING
        )
        [version] = versions.items
        async with fx_kit.assets.readable(
            ProjectKeys(project.id).version_rendition(version, Rendition.PREVIEW)
        ) as file:
            content = file.read_bytes()
        record = await fx_kit.uow().page_stages.find(PageStageKey(page.id, Stage.GEOMETRY))
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
            job = await fx_kit.service().start_preview(actor, project.id, preview)
            await fx_kit.jobs().preview_step(job.id)
        assert fx_kit.fake.previews == 1

    async def test_only_the_steps_after_a_changed_one_are_computed_again(self, fx_kit: ProcessingKit) -> None:
        """Verify changing the second step of a form leaves the preview of the first in the cache.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        first = Step(processor_key=FAKE_KEY)
        for strength in (1, 2):
            steps = (first, Step(processor_key=FAKE_KEY, params={'strength': strength + 1}))
            preview = StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=steps, step_index=1)
            job = await fx_kit.service().start_preview(actor, project.id, preview)
            await fx_kit.jobs().preview_step(job.id)
        assert fx_kit.fake.previews == 3

    async def test_step_switched_off_is_left_out_of_the_preview(self, fx_kit: ProcessingKit) -> None:
        """Verify a preview up to the last step runs the steps that are on alone, which here is the first.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = (Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY, params={'strength': 2}, enabled=False))
        preview = StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=steps, step_index=1)
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.jobs().preview_step(job.id)
        stored = await fx_kit.uow().jobs.get(job.id)
        expect((stored.state, fx_kit.fake.previews) == (JobState.SUCCEEDED, 1))
        assert_expectations()

    async def test_preview_with_every_step_up_to_the_index_off_fails_its_job(self, fx_kit: ProcessingKit) -> None:
        """Verify there is nothing to show when the steps up to the one asked for are all off, and the job says so.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page = await prepared_page(fx_kit)
        steps = (Step(processor_key=FAKE_KEY, enabled=False), Step(processor_key=FAKE_KEY, params={'strength': 2}))
        preview = StepPreview(page_id=page.id, stage=Stage.GEOMETRY, steps=steps, step_index=0)
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.jobs().preview_step(job.id)
        stored = await fx_kit.uow().jobs.get(job.id)
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
        placeholder = make_page(project_id=project.id)
        uow = fx_kit.uow()
        await uow.pages.add(placeholder)
        await uow.commit()
        preview = StepPreview(
            page_id=placeholder.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=FAKE_KEY),), step_index=0
        )
        job = await fx_kit.service().start_preview(actor, project.id, preview)
        await fx_kit.jobs().preview_step(job.id)
        stored = await fx_kit.uow().jobs.get(job.id)
        assert (stored.state, stored.error) == (JobState.FAILED, 'The page has no image to preview a step on.')
