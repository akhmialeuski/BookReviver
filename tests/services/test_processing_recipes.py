"""Tests for the recipes of a stage, one for each kind of page, which the processing service keeps."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobKind, JobState, PageKind, RecipeKind, Stage, StageState
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.ids import PageId
from bookreviver.domain.values import (
    PageStageKey,
    RecipeDraft,
    RecipeKey,
    SliceRequest,
    StageRun,
    Step,
    StepPreview,
)
from tests.helpers.builders import make_page_stage
from tests.helpers.fake_processing import RefusingJobQueue
from tests.helpers.processing import ProcessingKit
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Project, Recipe

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
GEOMETRY_STEPS: tuple[str, ...] = (
    'geometry.perspective',
    'geometry.deskew',
    'geometry.dewarp',
    'geometry.crop',
    'geometry.normalize',
)
# The Cleanup steps of the recipe of pictures in the order they run, which the recipe of text pages has the despeckling and
# the thickness between
CLEANUP_STEPS: tuple[str, ...] = ('cleanup.binarize', 'cleanup.despeckle', 'cleanup.eraser')
TEXT_CLEANUP_STEPS: tuple[str, ...] = (
    'cleanup.binarize',
    'cleanup.despeckle',
    'cleanup.thickness',
    'cleanup.eraser',
)
TEXT_STRENGTH: int = 2
EVERYTHING: SliceRequest = SliceRequest(limit=100)


async def recipes_of(kit: ProcessingKit, actor: Actor, project: Project, stage: Stage) -> dict[RecipeKind, Recipe]:
    """Read the recipes of a stage, by the kind of page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project whose recipes are read.
    :type project: Project
    :param stage: The stage.
    :type stage: Stage
    :returns: The recipe of each kind.
    :rtype: dict[RecipeKind, Recipe]
    """
    listed = await kit.service().recipes(actor, project.id, stage, EVERYTHING)
    return {recipe.kind: recipe for recipe in listed.items}


class TestRecipes:
    """Tests for ProcessingService.recipes."""

    async def test_default_recipes_are_created_the_first_time_a_stage_is_asked_for(self, fx_kit: ProcessingKit) -> None:
        """Verify the recipe of every kind of a stage is the template, with the default parameters filled in.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        recipes = await recipes_of(fx_kit, actor, project, Stage.GEOMETRY)
        expect(list(recipes) == list(RecipeKind))
        expect(
            all(
                [(step.processor_key, dict(step.params)) for step in recipe.steps]
                == [(FAKE_KEY, {'strength': 1, 'fail': False})]
                for recipe in recipes.values()
            )
        )
        assert_expectations()

    async def test_default_geometry_recipe_finds_the_sheet_levels_flattens_cuts_the_frame_and_normalizes(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify a new book is straightened in five steps, the sheet first and the page of the book last.

        The recipe of text pages turns the page by the projection of its ink and flattens it by the curves of its lines
        of text.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipe = (await recipes_of(fx_cv_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        methods = {step.processor_key: step.params.get('method') for step in recipe.steps}
        expect([step.processor_key for step in recipe.steps] == list(GEOMETRY_STEPS))
        expect(all(step.enabled for step in recipe.steps))
        expect(methods['geometry.deskew'] == 'projection')
        expect(methods['geometry.dewarp'] == 'text-lines')
        assert_expectations()

    async def test_default_geometry_recipes_follow_lines_of_text_or_the_edges_of_the_sheet(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the pictures are straightened by Hough lines and the edges of the sheet, and text and blanks by text.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipes = await recipes_of(fx_cv_kit, actor, project, Stage.GEOMETRY)
        methods = {
            kind: (
                {step.processor_key: step.params.get('method') for step in recipe.steps}['geometry.deskew'],
                {step.processor_key: step.params.get('method') for step in recipe.steps}['geometry.dewarp'],
            )
            for kind, recipe in recipes.items()
        }
        assert methods == {
            RecipeKind.TEXT: ('projection', 'text-lines'),
            RecipeKind.COLOR_PICTURE: ('hough', 'page-edges'),
            RecipeKind.BW_PICTURE: ('hough', 'page-edges'),
            RecipeKind.BLANK: ('projection', 'text-lines'),
        }

    async def test_default_cleanup_recipes_keep_pictures_in_tones_and_make_text_black_and_white(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the recipes of text and blank pages binarize, despeckle and thin the strokes, and the pictures do not.

        The steps of the recipe of text pages run in the order binarize, despeckle, thickness, eraser. The pictures are
        gray and not despeckled.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, project = await fx_cv_kit.seed_project()
        recipes = await recipes_of(fx_cv_kit, actor, project, Stage.CLEANUP)
        text, picture = recipes[RecipeKind.TEXT], recipes[RecipeKind.BW_PICTURE]
        expect([step.processor_key for step in text.steps] == list(TEXT_CLEANUP_STEPS))
        expect([(step.params.get('mode'), step.params.get('method')) for step in text.steps[:1]] == [('bw', 'sauvola')])
        expect(text.steps[1].params['strength'] == TEXT_STRENGTH)
        expect([step.processor_key for step in picture.steps] == [CLEANUP_STEPS[0], CLEANUP_STEPS[2]])
        expect(picture.steps[0].params['mode'] == 'gray')
        expect([step.processor_key for step in recipes[RecipeKind.BLANK].steps] == list(TEXT_CLEANUP_STEPS))
        assert_expectations()

    async def test_a_plate_and_a_frontispiece_are_cleaned_by_the_recipe_of_pictures_and_a_text_page_by_text(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the recipe of each page of a book is the one of its kind.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        _actor, project = await fx_cv_kit.seed_project()
        pages = []
        for index, kind in enumerate((PageKind.TEXT, PageKind.PLATE, PageKind.FRONTISPIECE)):
            page, _ = await fx_cv_kit.seed_scan_page(project, order_key=f'a{index}')
            uow = fx_cv_kit.uow()
            await uow.pages.update(evolve(page, kind=kind))
            await uow.commit()
            pages.append(page)
        reader = fx_cv_kit.uow()
        picked = await fx_cv_kit.parts(reader).recipes.for_pages(
            project.id, Stage.CLEANUP, [await reader.pages.get(page.id) for page in pages]
        )
        assert [picked[page.id].kind for page in pages] == [
            RecipeKind.TEXT,
            RecipeKind.COLOR_PICTURE,
            RecipeKind.COLOR_PICTURE,
        ]

    async def test_asking_again_returns_the_recipes_that_were_created(self, fx_kit: ProcessingKit) -> None:
        """Verify the default recipes are made once, so a second request finds the same recipes.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        first = await recipes_of(fx_kit, actor, project, Stage.GEOMETRY)
        again = await recipes_of(fx_kit, actor, project, Stage.GEOMETRY)
        assert first == again

    @pytest.mark.parametrize(
        ('stored', 'listed'),
        [
            pytest.param({'strength': 1}, {'strength': 1, 'fail': False}, id='a-parameter-the-processor-gained'),
            pytest.param({'strength': 1, 'removed': 5}, {'strength': 1, 'removed': 5}, id='a-parameter-it-refuses'),
        ],
    )
    async def test_steps_are_listed_in_the_form_their_processor_writes_today(
        self, fx_kit: ProcessingKit, stored: dict[str, object], listed: dict[str, object]
    ) -> None:
        """Verify a recipe stored before its processor gained a parameter lists the default, so a form opens unchanged.

        A step whose stored parameters the processor refuses is listed as stored, since the list has no right to drop
        what a run will report.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param stored: Parameters of the step as they were stored.
        :type stored: dict[str, object]
        :param listed: Parameters the list is expected to give.
        :type listed: dict[str, object]
        """
        actor, project = await fx_kit.seed_project()
        text = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        uow = fx_kit.uow()
        await uow.recipes.update(evolve(text, steps=(evolve(text.steps[0], params=stored),)))
        await uow.commit()
        again = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        assert [dict(step.params) for step in again.steps] == [listed]

    async def test_stage_without_a_recipe_by_default_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a stage no template exists for, such as the import.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        with pytest.raises(NotFoundError):
            await fx_kit.service().recipes(actor, project.id, Stage.IMPORT, EVERYTHING)

    async def test_project_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a project of another account is reported like a missing one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, project = await fx_kit.seed_project()
        stranger = Actor(account_id=(await fx_kit.seed_project())[0].account_id)
        with pytest.raises(NotFoundError):
            await fx_kit.service().recipes(stranger, project.id, Stage.GEOMETRY, EVERYTHING)


class TestSaveRecipe:
    """Tests for ProcessingService.save_recipe."""

    async def test_steps_are_stored_with_their_defaults_filled_in(self, fx_kit: ProcessingKit) -> None:
        """Verify the parameters a recipe stores are the checked ones, so equal recipes are equal.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        text = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={'strength': 5})])
        saved = await fx_kit.service().save_recipe(actor, project.id, RecipeKey(Stage.GEOMETRY, text.id), draft)
        expect((saved.id, saved.kind) == (text.id, RecipeKind.TEXT))
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
        text = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        with pytest.raises(InvalidParametersError, match=match):
            await fx_kit.service().save_recipe(
                actor, project.id, RecipeKey(Stage.GEOMETRY, text.id), RecipeDraft(steps=steps)
            )

    async def test_pages_the_recipe_processed_are_marked_stale_and_nothing_is_run(self, fx_kit: ProcessingKit) -> None:
        """Verify editing a recipe marks its pages stale, announces them and queues no job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        await fx_kit.seed_base_version(page)
        recipe = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=recipe.id))
        await uow.commit()
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={'strength': 2})])
        await fx_kit.service().save_recipe(actor, project.id, RecipeKey(Stage.GEOMETRY, recipe.id), draft)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(record.state is StageState.STALE)
        expect(any(isinstance(event, PageStageChanged) for event in fx_kit.events.published))
        expect(fx_kit.recording.enqueued == [])
        assert_expectations()

    async def test_only_the_pages_of_the_edited_recipe_are_marked_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a page processed by the recipe of another kind stays up to date.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        first, _ = await fx_kit.seed_scan_page(project, order_key='a0')
        second, _ = await fx_kit.seed_scan_page(project, order_key='a1')
        recipes = await recipes_of(fx_kit, actor, project, Stage.GEOMETRY)
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=first.id, recipe_id=recipes[RecipeKind.TEXT].id))
        await uow.page_stages.save(make_page_stage(page_id=second.id, recipe_id=recipes[RecipeKind.BW_PICTURE].id))
        await uow.commit()
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={'strength': 9})])
        await fx_kit.service().save_recipe(
            actor, project.id, RecipeKey(Stage.GEOMETRY, recipes[RecipeKind.TEXT].id), draft
        )
        reader = fx_kit.uow()
        states = [
            (await reader.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).state for page in (first, second)
        ]
        assert states == [StageState.STALE, StageState.FRESH]

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
        recipe = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        uow = fx_kit.uow()
        await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=recipe.id))
        await uow.commit()
        steps = [Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY, params={'strength': 9}, enabled=False)]
        await fx_kit.service().save_recipe(
            actor, project.id, RecipeKey(Stage.GEOMETRY, recipe.id), RecipeDraft(steps=steps)
        )
        stored = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect([(step.enabled, step.params['strength']) for step in stored.steps] == [(True, 1), (False, 9)])
        expect(record.state is StageState.STALE)
        assert_expectations()

    async def test_recipe_of_another_stage_is_not_found_and_stays_as_it_was(self, fx_kit: ProcessingKit) -> None:
        """Reject a save of a recipe through the address of another stage, and rewrite nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        recipe = (await recipes_of(fx_kit, actor, project, Stage.GEOMETRY))[RecipeKind.TEXT]
        hijacked = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={'strength': 9})])
        with pytest.raises(NotFoundError):
            await fx_kit.service().save_recipe(actor, project.id, RecipeKey(Stage.CLEANUP, recipe.id), hijacked)
        assert (await fx_kit.uow().recipes.get(recipe.id)).steps == recipe.steps


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
        with pytest.raises(ConflictError, match='project is busy'):
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
        with pytest.raises(ConflictError, match='project is busy'):
            await fx_kit.service().start_preview(actor, project.id, preview)
        with pytest.raises(ConflictError, match='project is busy'):
            await fx_kit.service().start_collection(actor, project.id)

    async def test_run_waits_while_a_collection_is_active(self, fx_kit: ProcessingKit) -> None:
        """Store a run asked for during a collection as queued without handing it to a worker, which starts it later.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        collection = await fx_kit.service().start_collection(actor, project.id)
        run = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        expect(run.state is JobState.QUEUED)
        expect(fx_kit.recording.enqueued == [collection])
        assert_expectations()

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
        run = StageRun(stage=Stage.GEOMETRY, page_ids=(PageId(project.id),))
        with pytest.raises(NotFoundError):
            await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, run)

    async def test_job_the_queue_refuses_is_stored_as_failed(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify a queue that fails leaves the job failed and announced, and the request still answers.

        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        """
        kit = ProcessingKit(fx_asset_store, queue=RefusingJobQueue())
        actor, project = await kit.seed_project()
        job = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        assert (await kit.uow().jobs.get(job.id)).state is JobState.FAILED
