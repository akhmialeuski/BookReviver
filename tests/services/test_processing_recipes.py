"""Tests for the recipes, variants and the active recipe of a stage, which the processing service keeps."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobKind, JobState, Stage, StageState
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.ids import PageId, RecipeId
from bookreviver.domain.values import PageStageKey, RecipeKey, SliceRequest, StageRun, Step, StepPreview
from tests.helpers.builders import make_page_stage
from tests.helpers.fake_processing import RefusingJobQueue
from tests.helpers.processing import ProcessingKit
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.adapters.storage import LocalAssetStore

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
GEOMETRY_STEPS: tuple[str, ...] = ('geometry.perspective', 'geometry.deskew', 'geometry.crop', 'geometry.normalize')
EVERYTHING: SliceRequest = SliceRequest(limit=100)


class TestRecipe:
    """Tests for ProcessingService.recipe."""

    async def test_default_recipe_is_created_the_first_time_a_stage_is_asked_for(self, fx_kit: ProcessingKit) -> None:
        """Verify the active recipe of a stage is the first template, with the default parameters filled in.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        recipe = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        expect(recipe.active)
        expect(
            [(step.processor_key, dict(step.params)) for step in recipe.steps]
            == [(FAKE_KEY, {'strength': 1, 'fail': False})]
        )
        assert_expectations()

    async def test_default_geometry_recipe_finds_the_sheet_levels_the_lines_cuts_the_frame_and_normalizes(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a new book is straightened in four steps, the sheet first and the page of the book last.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = await fx_cv_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        expect(recipe.active)
        expect([step.processor_key for step in recipe.steps] == list(GEOMETRY_STEPS))
        expect(all(step.enabled for step in recipe.steps))
        assert_expectations()

    async def test_asking_again_returns_the_recipe_that_was_created(self, fx_kit: ProcessingKit) -> None:
        """Verify the default recipes are made once, so a second request finds the same recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        first = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        again = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        assert first.id == again.id

    async def test_stage_without_a_recipe_by_default_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a stage no template exists for, such as the import.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        with pytest.raises(NotFoundError):
            await fx_kit.service().recipe(actor, project.id, Stage.IMPORT)

    async def test_project_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a project of another account is reported like a missing one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        stranger = Actor(account_id=(await fx_kit.seed_project())[0].account_id)
        with pytest.raises(NotFoundError):
            await fx_kit.service().recipe(stranger, project.id, Stage.GEOMETRY)


class TestSaveRecipe:
    """Tests for ProcessingService.save_recipe."""

    async def test_steps_are_stored_with_their_defaults_filled_in(self, fx_kit: ProcessingKit) -> None:
        """Verify the parameters a recipe stores are the checked ones, so equal recipes are equal.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        saved = await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, 'Strong', [Step(processor_key=FAKE_KEY, params={'strength': 5})]
        )
        expect(saved.name == 'Strong')
        expect(dict(saved.steps[0].params) == {'strength': 5, 'fail': False})
        assert_expectations()

    @pytest.mark.parametrize(
        ('steps', 'match'),
        [
            ([], 'at least one step'),
            ([Step(processor_key=FAKE_KEY, enabled=False)], 'switched on'),
            ([Step(processor_key='geometry.missing')], 'no processor'),
            ([Step(processor_key='cleanup.fake')], 'not to the Geometry stage'),
            ([Step(processor_key=FAKE_KEY, params={'unknown': 1})], 'Unknown parameters'),
        ],
        ids=['no-steps', 'all-steps-off', 'unknown-processor', 'wrong-stage', 'bad-parameters'],
    )
    async def test_steps_that_do_not_fit_their_processors_are_rejected(
        self, fx_kit: ProcessingKit, steps: list[Step], match: str
    ) -> None:
        """Reject a recipe with no step, none switched on, an unknown or foreign processor, or bad parameters.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param steps: Steps of the recipe under test.
        :type steps: list[Step]
        :param match: Text the error says.
        :type match: str
        """
        actor, project = await fx_kit.seed_project()
        with pytest.raises(InvalidParametersError, match=match):
            await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, 'Broken', steps)

    async def test_pages_the_recipe_processed_are_marked_stale_and_nothing_is_run(self, fx_kit: ProcessingKit) -> None:
        """Verify editing the active recipe marks its pages stale, announces them and queues no job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        await fx_kit.seed_base_version(page)
        recipe = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=recipe.id))
        await uow.commit()
        await fx_kit.service().save_recipe(
            actor, project.id, Stage.GEOMETRY, 'Strong', [Step(processor_key=FAKE_KEY, params={'strength': 2})]
        )
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(record.state is StageState.STALE)
        expect(any(isinstance(event, PageStageChanged) for event in fx_kit.events.published))
        expect(fx_kit.recording.enqueued == [])
        assert_expectations()

    async def test_switching_a_step_off_keeps_its_parameters_and_marks_the_pages_stale(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a step that is switched off stays in the recipe with its checked parameters, and changes the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        await fx_kit.seed_base_version(page)
        recipe = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=recipe.id))
        await uow.commit()
        steps = [Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY, params={'strength': 9}, enabled=False)]
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, 'One of two', steps)
        stored = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect([(step.enabled, step.params['strength']) for step in stored.steps] == [(True, 1), (False, 9)])
        expect(record.state is StageState.STALE)
        assert_expectations()


class TestVariants:
    """Tests for the variants of a stage, and the switch of the active recipe."""

    async def test_variant_is_listed_after_the_active_recipe(self, fx_kit: ProcessingKit) -> None:
        """Verify the active recipe comes first, and a variant that is added is not active.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        variant = await fx_kit.service().add_variant(
            actor, project.id, Stage.GEOMETRY, 'Strong', [Step(processor_key=FAKE_KEY, params={'strength': 3})]
        )
        listed = await fx_kit.service().variants(actor, project.id, Stage.GEOMETRY, EVERYTHING)
        expect([recipe.name for recipe in listed.items] == ['Fake', 'Strong'])
        expect((variant.active, listed.total) == (False, 2))
        assert_expectations()

    async def test_activating_a_variant_leaves_exactly_one_active_recipe(self, fx_kit: ProcessingKit) -> None:
        """Verify the switch deactivates the old recipe and activates the new one in one transaction.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        variant = await fx_kit.service().add_variant(
            actor, project.id, Stage.GEOMETRY, 'Strong', [Step(processor_key=FAKE_KEY)]
        )
        activated = await fx_kit.service().activate(actor, project.id, Stage.GEOMETRY, variant.id)
        recipes = await fx_kit.uow().recipes.list_for_stage(project.id, Stage.GEOMETRY)
        expect(activated.active)
        expect([(recipe.name, recipe.active) for recipe in recipes] == [('Strong', True), ('Fake', False)])
        assert_expectations()

    async def test_activating_marks_the_pages_of_the_old_recipe_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a page processed by the old active recipe is stale after the switch, and one processed by a variant is not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        first, _ = await fx_kit.seed_scan_page(project, order_key='a0')
        second, _ = await fx_kit.seed_scan_page(project, order_key='a1')
        old = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        variant = await fx_kit.service().add_variant(
            actor, project.id, Stage.GEOMETRY, 'Strong', [Step(processor_key=FAKE_KEY)]
        )
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=first.id, recipe_id=old.id))
        await uow.page_stages.save(make_page_stage(page_id=second.id, recipe_id=variant.id))
        await uow.commit()
        await fx_kit.service().activate(actor, project.id, Stage.GEOMETRY, variant.id)
        reader = fx_kit.uow()
        states = [
            (await reader.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).state for page in (first, second)
        ]
        assert states == [StageState.STALE, StageState.FRESH]

    async def test_activating_the_active_recipe_changes_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify activating the recipe that is active already returns it as it is.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        assert await fx_kit.service().activate(actor, project.id, Stage.GEOMETRY, active.id) == active

    async def test_recipe_of_another_stage_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject activating a recipe by the name of a stage it does not process.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        recipe = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        with pytest.raises(NotFoundError):
            await fx_kit.service().activate(actor, project.id, Stage.CLEANUP, recipe.id)

    async def test_save_variant_marks_the_pages_it_processed_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify editing a variant marks the pages it processed stale.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        variant = await fx_kit.service().add_variant(
            actor, project.id, Stage.GEOMETRY, 'Strong', [Step(processor_key=FAKE_KEY)]
        )
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=variant.id))
        await uow.commit()
        await fx_kit.service().save_variant(
            actor,
            project.id,
            RecipeKey(Stage.GEOMETRY, variant.id),
            'Stronger',
            [Step(processor_key=FAKE_KEY, params={'strength': 9})],
        )
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        assert record.state is StageState.STALE

    async def test_variant_of_another_stage_is_not_found_and_stays_as_it_was(self, fx_kit: ProcessingKit) -> None:
        """Reject a save of a recipe through the address of another stage, and rewrite nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        variant = await fx_kit.service().add_variant(
            actor, project.id, Stage.GEOMETRY, 'Strong', [Step(processor_key=FAKE_KEY)]
        )
        with pytest.raises(NotFoundError):
            await fx_kit.service().save_variant(
                actor, project.id, RecipeKey(Stage.CLEANUP, variant.id), 'Hijacked', [Step(processor_key=FAKE_KEY)]
            )
        assert (await fx_kit.uow().recipes.get(variant.id)).name == 'Strong'


class TestStartRun:
    """Tests for ProcessingService.start_run."""

    async def test_job_is_recorded_with_its_parameters_and_queued(self, fx_kit: ProcessingKit) -> None:
        """Verify a run is a stored job of kind run-stage that a worker reads its parameters from.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        run = StageRun(stage=Stage.GEOMETRY, page_ids=(page.id,))
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)
        stored = await fx_kit.uow().jobs.get(job.id)
        expect((stored.kind, stored.state) == (JobKind.RUN_STAGE, JobState.QUEUED))
        expect(StageRun.from_map(stored.params) == run)
        expect(fx_kit.recording.enqueued == [job])
        assert_expectations()

    async def test_second_run_while_one_is_active_is_a_conflict(self, fx_kit: ProcessingKit) -> None:
        """Reject a run while another job that processes the project is queued or running.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        with pytest.raises(ConflictError, match='processing something'):
            await fx_kit.service().start_run(actor, project.id, Stage.CLEANUP, StageRun(stage=Stage.CLEANUP))

    async def test_preview_and_collection_are_refused_while_a_run_is_active(self, fx_kit: ProcessingKit) -> None:
        """Reject a preview and a collection while a run of the project is queued, since the project processes one thing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        preview = StepPreview(
            page_id=page.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=FAKE_KEY),), step_index=0
        )
        with pytest.raises(ConflictError, match='processing something'):
            await fx_kit.service().start_preview(actor, project.id, preview)
        with pytest.raises(ConflictError, match='processing something'):
            await fx_kit.service().start_collection(actor, project.id)

    async def test_run_is_refused_while_a_collection_is_active(self, fx_kit: ProcessingKit) -> None:
        """Reject a run while a collection is queued, which may delete the versions the run would reuse.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        await fx_kit.service().start_collection(actor, project.id)
        with pytest.raises(ConflictError, match='processing something'):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))

    async def test_collection_that_a_run_queues_is_left_out_while_another_job_processes_the_project(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the collection of a finished run is not queued behind a job that is active, which is no error.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        starter = fx_kit.parts(fx_kit.uow()).starter
        expect(await starter.enqueue_collection(project.id) is None)
        expect([job.kind for job in fx_kit.recording.enqueued] == [JobKind.RUN_STAGE])
        assert_expectations()

    async def test_page_of_another_project_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a run that names a page the project does not have.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        run = StageRun(stage=Stage.GEOMETRY, page_ids=(PageId(RecipeId(project.id)),))
        with pytest.raises(NotFoundError):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)

    async def test_recipe_of_another_stage_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a run that names a recipe of another stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        recipe = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        with pytest.raises(NotFoundError):
            await fx_kit.service().start_run(
                actor, project.id, Stage.CLEANUP, StageRun(stage=Stage.CLEANUP, recipe_id=recipe.id)
            )

    async def test_job_the_queue_refuses_is_stored_as_failed(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify a queue that fails leaves the job failed and announced, and the request still answers.

        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        """
        kit = ProcessingKit(fx_asset_store, queue=RefusingJobQueue())
        actor, project = await kit.seed_project()
        job = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        assert (await kit.uow().jobs.get(job.id)).state is JobState.FAILED
