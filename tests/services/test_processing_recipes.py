"""Tests for the recipes of a stage, one for each kind of page, which the processing service keeps."""

from typing import TYPE_CHECKING

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.repositories import SqlAlchemyRecipeRepository
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobKind, JobState, PageKind, RecipeKind, Stage, StageState
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
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
from tests.helpers.processors import FAILING_PARAMETER, STRENGTH_PARAMETER, FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bookreviver.domain.entities import BookRecord, Page, PageStage, Recipe
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.persistence import BookRepositories, UnitOfWork
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
DESKEW_KEY: str = 'geometry.deskew'
DEWARP_KEY: str = 'geometry.dewarp'
# Tasks that ask for the recipes of one stage at the same time
TASKS: int = 2
GEOMETRY_STEPS: tuple[str, ...] = (
    'geometry.perspective',
    DESKEW_KEY,
    DEWARP_KEY,
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
BUSY_REASON: str = 'book is busy'
METHOD_PARAMETER: str = 'method'
MODE_PARAMETER: str = 'mode'
REMOVED_PARAMETER: str = 'removed'
# The methods of the default recipes
PROJECTION: str = 'projection'
TEXT_LINES: str = 'text-lines'
HOUGH: str = 'hough'
PAGE_EDGES: str = 'page-edges'


async def recipes_of(
    kit: ProcessingKit, actor: Actor, book_record: BookRecord, stage: Stage
) -> dict[RecipeKind, Recipe]:
    """Read the recipes of a stage, by the kind of page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :param book_record: Record of the book whose recipes are read.
    :type book_record: BookRecord
    :param stage: The stage.
    :type stage: Stage
    :returns: The recipe of each kind.
    :rtype: dict[RecipeKind, Recipe]
    """
    listed = await kit.service().recipes(actor, book_record.id, stage, EVERYTHING)
    return {recipe.kind: recipe for recipe in listed.items}


async def save_stage_records(kit: ProcessingKit, book_record: BookRecord, *records: PageStage) -> None:
    """Store records of the Geometry stage, as earlier runs of the pages would have.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_record: Record of the book owning the pages of the records.
    :type book_record: BookRecord
    :param records: The records to store.
    :type records: PageStage
    """

    def save(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Save the records.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: Record of the book, which the work does not need.
        :type _book_record: BookRecord
        """
        for record in records:
            repos.page_stages.save(record)

    await kit.uow().change_book(book_record.id, save)


async def geometry_state(kit: ProcessingKit, book_id: BookId, page: Page) -> StageState:
    """Read the state of the record of the Geometry stage of a page as committed.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :returns: The state of the record.
    :rtype: StageState
    """
    record = await kit.uow().read_book(
        book_id, lambda repos: repos.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
    )
    return record.state


class TestRecipes:
    """Tests for ProcessingService.recipes."""

    async def test_default_recipes_are_created_the_first_time_a_stage_is_asked_for(self, fx_kit: ProcessingKit) -> None:
        """Verify the recipe of every kind of a stage is the template, with the default parameters filled in.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        recipes = await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY)
        expect(list(recipes) == list(RecipeKind))
        expect(
            all(
                [(step.processor_key, dict(step.params)) for step in recipe.steps]
                == [(FAKE_KEY, {STRENGTH_PARAMETER: 1, FAILING_PARAMETER: False})]
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
        actor, book_record = await fx_cv_kit.seed_record()
        recipe = (await recipes_of(fx_cv_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        methods = {step.processor_key: step.params.get(METHOD_PARAMETER) for step in recipe.steps}
        expect([step.processor_key for step in recipe.steps] == list(GEOMETRY_STEPS))
        expect(all(step.enabled for step in recipe.steps))
        expect(methods[DESKEW_KEY] == PROJECTION)
        expect(methods[DEWARP_KEY] == TEXT_LINES)
        assert_expectations()

    async def test_default_geometry_recipes_follow_lines_of_text_or_the_edges_of_the_sheet(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the pictures are straightened by Hough lines and the edges of the sheet, and text and blanks by text.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, book_record = await fx_cv_kit.seed_record()
        recipes = await recipes_of(fx_cv_kit, actor, book_record, Stage.GEOMETRY)
        methods = {
            kind: (
                {step.processor_key: step.params.get(METHOD_PARAMETER) for step in recipe.steps}[DESKEW_KEY],
                {step.processor_key: step.params.get(METHOD_PARAMETER) for step in recipe.steps}[DEWARP_KEY],
            )
            for kind, recipe in recipes.items()
        }
        assert methods == {
            RecipeKind.TEXT: (PROJECTION, TEXT_LINES),
            RecipeKind.COLOR_PICTURE: (HOUGH, PAGE_EDGES),
            RecipeKind.BW_PICTURE: (HOUGH, PAGE_EDGES),
            RecipeKind.BLANK: (PROJECTION, TEXT_LINES),
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
        actor, book_record = await fx_cv_kit.seed_record()
        recipes = await recipes_of(fx_cv_kit, actor, book_record, Stage.CLEANUP)
        text, picture = recipes[RecipeKind.TEXT], recipes[RecipeKind.BW_PICTURE]
        expect([step.processor_key for step in text.steps] == list(TEXT_CLEANUP_STEPS))
        expect(
            [(step.params.get(MODE_PARAMETER), step.params.get(METHOD_PARAMETER)) for step in text.steps[:1]]
            == [('bw', 'sauvola')]
        )
        expect(text.steps[1].params[STRENGTH_PARAMETER] == TEXT_STRENGTH)
        expect([step.processor_key for step in picture.steps] == [CLEANUP_STEPS[0], CLEANUP_STEPS[2]])
        expect(picture.steps[0].params[MODE_PARAMETER] == 'gray')
        expect([step.processor_key for step in recipes[RecipeKind.BLANK].steps] == list(TEXT_CLEANUP_STEPS))
        assert_expectations()

    async def test_a_plate_and_a_frontispiece_are_cleaned_by_the_recipe_of_pictures_and_a_text_page_by_text(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the recipe of each page of a book is the one of its kind.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        _actor, book_record = await fx_cv_kit.seed_record()
        pages = []
        for index, kind in enumerate((PageKind.TEXT, PageKind.PLATE, PageKind.FRONTISPIECE)):
            page, _ = await fx_cv_kit.seed_scan_page(book_record, order_key=f'a{index}', kind=kind)
            pages.append(page)
        reader = fx_cv_kit.uow()
        stored_pages = await reader.read_book(
            book_record.id, lambda repos: repos.pages.list_by_ids([page.id for page in pages])
        )
        picked = await fx_cv_kit.parts(reader).recipes.for_pages(book_record.id, Stage.CLEANUP, stored_pages)
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
        actor, book_record = await fx_kit.seed_record()
        first = await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY)
        again = await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY)
        assert first == again

    @pytest.mark.parametrize(
        ('stored', 'listed'),
        [
            pytest.param(
                {STRENGTH_PARAMETER: 1},
                {STRENGTH_PARAMETER: 1, FAILING_PARAMETER: False},
                id='a-parameter-the-processor-gained',
            ),
            pytest.param(
                {STRENGTH_PARAMETER: 1, REMOVED_PARAMETER: 5},
                {STRENGTH_PARAMETER: 1, REMOVED_PARAMETER: 5},
                id='a-parameter-it-refuses',
            ),
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
        actor, book_record = await fx_kit.seed_record()
        text = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        await fx_kit.uow().change_book(
            book_record.id,
            lambda repos, _book_record: repos.recipes.update(
                evolve(text, steps=(evolve(text.steps[0], params=stored),))
            ),
        )
        again = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        assert [dict(step.params) for step in again.steps] == [listed]

    async def test_stage_without_a_recipe_by_default_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a stage no template exists for, such as the import.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        with pytest.raises(NotFoundError):
            await fx_kit.service().recipes(actor, book_record.id, Stage.IMPORT, EVERYTHING)

    async def test_book_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a book of another account is reported like a missing one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, book_record = await fx_kit.seed_record()
        stranger = Actor(account_id=(await fx_kit.seed_record())[0].account_id)
        with pytest.raises(NotFoundError):
            await fx_kit.service().recipes(stranger, book_record.id, Stage.GEOMETRY, EVERYTHING)


class TestSaveRecipe:
    """Tests for ProcessingService.save_recipe."""

    async def test_steps_are_stored_with_their_defaults_filled_in(self, fx_kit: ProcessingKit) -> None:
        """Verify the parameters a recipe stores are the checked ones, so equal recipes are equal.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        text = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 5})])
        saved = await fx_kit.service().save_recipe(actor, book_record.id, RecipeKey(Stage.GEOMETRY, text.id), draft)
        expect((saved.id, saved.kind) == (text.id, RecipeKind.TEXT))
        expect(dict(saved.steps[0].params) == {STRENGTH_PARAMETER: 5, FAILING_PARAMETER: False})
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
        actor, book_record = await fx_kit.seed_record()
        text = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        with pytest.raises(InvalidParametersError, match=match):
            await fx_kit.service().save_recipe(
                actor, book_record.id, RecipeKey(Stage.GEOMETRY, text.id), RecipeDraft(steps=steps)
            )

    async def test_pages_the_recipe_processed_are_marked_stale_and_nothing_is_run(self, fx_kit: ProcessingKit) -> None:
        """Verify editing a recipe marks its pages stale and queues no job.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        page, _ = await fx_kit.seed_scan_page(book_record)
        await fx_kit.seed_base_version(book_record.id, page)
        recipe = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        await save_stage_records(fx_kit, book_record, make_page_stage(page_id=page.id, recipe_id=recipe.id))
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 2})])
        await fx_kit.service().save_recipe(actor, book_record.id, RecipeKey(Stage.GEOMETRY, recipe.id), draft)
        expect(await geometry_state(fx_kit, book_record.id, page) is StageState.STALE)
        expect(fx_kit.board.put_jobs == [])
        assert_expectations()

    async def test_only_the_pages_of_the_edited_recipe_are_marked_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a page processed by the recipe of another kind stays up to date.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        first, _ = await fx_kit.seed_scan_page(book_record, order_key='a0')
        second, _ = await fx_kit.seed_scan_page(book_record, order_key='a1')
        recipes = await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY)
        await save_stage_records(
            fx_kit,
            book_record,
            make_page_stage(page_id=first.id, recipe_id=recipes[RecipeKind.TEXT].id),
            make_page_stage(page_id=second.id, recipe_id=recipes[RecipeKind.BW_PICTURE].id),
        )
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 9})])
        await fx_kit.service().save_recipe(
            actor, book_record.id, RecipeKey(Stage.GEOMETRY, recipes[RecipeKind.TEXT].id), draft
        )
        states = [await geometry_state(fx_kit, book_record.id, page) for page in (first, second)]
        assert states == [StageState.STALE, StageState.FRESH]

    async def test_switching_a_step_off_keeps_its_parameters_and_marks_the_pages_stale(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a step that is switched off stays in the recipe with its checked parameters, and changes the recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        page, _ = await fx_kit.seed_scan_page(book_record)
        await fx_kit.seed_base_version(book_record.id, page)
        recipe = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        await save_stage_records(fx_kit, book_record, make_page_stage(page_id=page.id, recipe_id=recipe.id))
        steps = [
            Step(processor_key=FAKE_KEY),
            Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 9}, enabled=False),
        ]
        await fx_kit.service().save_recipe(
            actor, book_record.id, RecipeKey(Stage.GEOMETRY, recipe.id), RecipeDraft(steps=steps)
        )
        stored = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        expect([(step.enabled, step.params[STRENGTH_PARAMETER]) for step in stored.steps] == [(True, 1), (False, 9)])
        expect(await geometry_state(fx_kit, book_record.id, page) is StageState.STALE)
        assert_expectations()

    async def test_recipe_of_another_stage_is_not_found_and_stays_as_it_was(self, fx_kit: ProcessingKit) -> None:
        """Reject a save of a recipe through the address of another stage, and rewrite nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        recipe = (await recipes_of(fx_kit, actor, book_record, Stage.GEOMETRY))[RecipeKind.TEXT]
        hijacked = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 9})])
        with pytest.raises(NotFoundError):
            await fx_kit.service().save_recipe(actor, book_record.id, RecipeKey(Stage.CLEANUP, recipe.id), hijacked)
        assert (
            await fx_kit.uow().read_book(book_record.id, lambda repos: repos.recipes.get(recipe.id))
        ).steps == recipe.steps


class TestStartRun:
    """Tests for ProcessingService.start_run."""

    async def test_job_is_put_on_the_board_with_its_parameters(self, fx_kit: ProcessingKit) -> None:
        """Verify a run is a job of kind run-stage that the board holds, queued, with the parameters of the run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        page, _ = await fx_kit.seed_scan_page(book_record)
        run = StageRun(stage=Stage.GEOMETRY, page_ids=(page.id,))
        job = await fx_kit.service().start_run(actor, book_record.id, Stage.GEOMETRY, run)
        held = fx_kit.board.get(job.id)
        expect((held.kind, held.state) == (JobKind.RUN_STAGE, JobState.QUEUED))
        expect(StageRun.from_map(held.params) == run)
        expect(fx_kit.board.put_jobs == [job])
        assert_expectations()

    async def test_second_run_while_one_is_active_is_a_conflict(self, fx_kit: ProcessingKit) -> None:
        """Reject a run while another job that processes the book is queued or running.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        await fx_kit.service().start_run(actor, book_record.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await fx_kit.service().start_run(actor, book_record.id, Stage.CLEANUP, StageRun(stage=Stage.CLEANUP))

    async def test_preview_and_collection_are_refused_while_a_run_is_active(self, fx_kit: ProcessingKit) -> None:
        """Reject a preview and a collection while a run of the book is queued, since the book processes one thing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        page, _ = await fx_kit.seed_scan_page(book_record)
        await fx_kit.service().start_run(actor, book_record.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        preview = StepPreview(
            page_id=page.id, stage=Stage.GEOMETRY, steps=(Step(processor_key=FAKE_KEY),), step_index=0
        )
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await fx_kit.service().start_preview(actor, book_record.id, preview)
        with pytest.raises(ConflictError, match=BUSY_REASON):
            await fx_kit.service().start_collection(actor, book_record.id)

    async def test_run_waits_while_a_collection_is_active(self, fx_kit: ProcessingKit) -> None:
        """Queue a run asked for during a collection behind it, so the board starts it when the collection has ended.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        collection = await fx_kit.service().start_collection(actor, book_record.id)
        run = await fx_kit.service().start_run(actor, book_record.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        expect(run.state is JobState.QUEUED)
        expect(fx_kit.board.put_jobs == [collection, run])
        assert_expectations()

    async def test_collection_that_a_run_queues_is_left_out_while_another_job_processes_the_book(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the collection of a finished run is not queued behind a job that is active, which is no error.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        await fx_kit.service().start_run(actor, book_record.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        starter = fx_kit.parts(fx_kit.uow()).starter
        expect(await starter.enqueue_collection(book_record.id) is None)
        expect([job.kind for job in fx_kit.board.put_jobs] == [JobKind.RUN_STAGE])
        assert_expectations()

    async def test_page_of_another_book_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a run that names a page the book does not have.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        run = StageRun(stage=Stage.GEOMETRY, page_ids=(PageId(book_record.id),))
        with pytest.raises(NotFoundError):
            await fx_kit.service().start_run(actor, book_record.id, Stage.GEOMETRY, run)


class ReadGate:
    """Holds the first read of the recipes of each unit of work until every one of them has made it.

    A request that finds no recipes stores the defaults in a ``change_book`` of its own, so two requests that both read
    none meet in that change. The gate makes them both read before either of them stores, which is the order that
    matters.

    :ivar reads: How many units of work have made their first read.
    """

    def __init__(self, parties: int) -> None:
        """Wait for as many units of work as there are parties.

        :param parties: Number of units of work that read.
        :type parties: int
        """
        self.reads = 0
        self._parties = parties
        self._all_read = anyio.Event()

    def watch(self, uow: UnitOfWork, monkeypatch: pytest.MonkeyPatch) -> None:
        """Hold the first read of the recipes of a stage that the unit of work makes.

        The recipe book reads in a transaction that only reads before it opens a change, and a unit of work makes no
        other read while it asks for the recipes of a stage, so the first read of the unit of work is the one held.

        :param uow: Unit of work of one of the tasks.
        :type uow: UnitOfWork
        :param monkeypatch: Fixture that restores the unit of work when the test ends.
        :type monkeypatch: pytest.MonkeyPatch
        """
        read = uow.read_book
        waited = False

        async def gated[T](book_id: BookId, work: Callable[[BookRepositories], T]) -> T:
            """Read as the unit of work does, and wait on the first call until every unit of work has read.

            :param book_id: Book that is read.
            :type book_id: BookId
            :param work: Function over the repositories of the transaction.
            :type work: Callable[[BookRepositories], T]
            :returns: What the work returns.
            :rtype: T
            """
            nonlocal waited
            found = await read(book_id, work)
            if not waited:
                waited = True
                self.reads += 1
                if self.reads == self._parties:
                    self._all_read.set()
                await self._all_read.wait()
            return found

        monkeypatch.setattr(uow, 'read_book', gated)


class TestDefaultRecipesInChanges:
    """Tests for the creation of the default recipes by tasks that ask for the stage at the same time."""

    async def test_two_tasks_that_both_found_no_recipes_store_one_set(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the task that enters the change second reads again, finds the recipes, and adds none.

        Both tasks have read an empty stage before either enters its change, which the gate proves, so the second one
        reaches the change with nothing in hand but a stale read. It must come out with the recipes of the first.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Fixture that restores the units of work and the repository.
        :type monkeypatch: pytest.MonkeyPatch
        """
        _, book_record = await fx_kit.seed_record()
        units = [fx_kit.uow() for _ in range(TASKS)]
        gate = ReadGate(parties=TASKS)
        for unit in units:
            gate.watch(unit, monkeypatch)
        stored_sets: list[int] = []
        add_many = SqlAlchemyRecipeRepository.add_many

        def counting(recipes: SqlAlchemyRecipeRepository, entities: Sequence[Recipe]) -> Sequence[Recipe]:
            """Count the recipes each call of ``add_many`` stores, then store them.

            :param recipes: The repository the call was made on.
            :type recipes: SqlAlchemyRecipeRepository
            :param entities: The recipes to store.
            :type entities: Sequence[Recipe]
            :returns: The recipes as stored.
            :rtype: Sequence[Recipe]
            """
            stored_sets.append(len(entities))
            return add_many(recipes, entities)

        monkeypatch.setattr(SqlAlchemyRecipeRepository, 'add_many', counting)
        answers: list[list[Recipe]] = []

        async def ask(unit: UnitOfWork) -> None:
            """Ask for the recipes of the geometry stage through the book of one task.

            :param unit: Unit of work of the task.
            :type unit: UnitOfWork
            """
            answers.append(await fx_kit.parts(unit).recipes.recipes(book_record.id, Stage.GEOMETRY))

        async with anyio.create_task_group() as tasks:
            for unit in units:
                tasks.start_soon(ask, unit)
        stored = await fx_kit.uow().read_book(
            book_record.id, lambda repos: repos.recipes.list_for_stage(Stage.GEOMETRY)
        )
        expect(gate.reads == TASKS)
        expect(stored_sets == [len(RecipeKind)])
        expect(len(stored) == len(RecipeKind))
        expect(len(answers) == TASKS)
        expect(all([recipe.id for recipe in answer] == [recipe.id for recipe in stored] for answer in answers))
        assert_expectations()
