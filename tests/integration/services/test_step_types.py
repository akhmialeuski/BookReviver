"""Tests for the replacement of the type of a step: the parameters, the page state and the history of each type."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.core.book import Book
from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError, StepTypeChangedError
from bookreviver.domain.geometry import EditorKind, Rotation
from bookreviver.domain.history import TYPE_FIELD
from bookreviver.domain.jobs import StageRun
from bookreviver.domain.page_steps import NewPageEdit, PageStepKey, StepLayer
from bookreviver.domain.pages import RecipeKind
from bookreviver.domain.recipes import OrderMode, RecipeDraft, RecipeStepKey, Step
from bookreviver.domain.stages import PageStageKey, Stage, StageState
from bookreviver.domain.step_values import ValueField, ValueScope, ValueTarget
from bookreviver.plugins.split.split_none import SplitNone
from bookreviver.services.step_params import PageLayers
from tests.harness.builders.domain import directory_of, open_for
from tests.harness.builders.page_batches import PageValues
from tests.harness.builders.processing import ProcessingKit
from tests.harness.builders.spreads import run_stage
from tests.harness.fakes.processors import (
    MEASURING_KEY,
    SECOND_KEY,
    STRENGTH_PARAMETER,
    THIRD_KEY,
    CleanupProcessor,
    FakeProcessor,
    MeasuringProcessor,
    SecondProcessor,
    ThirdProcessor,
)

if TYPE_CHECKING:
    from typing import Any

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.history import PageHistoryEntry
    from bookreviver.domain.ids import BookId, StepId
    from bookreviver.domain.page_steps import PageEdit
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Recipe
    from bookreviver.domain.step_values import PageStepSettings
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.builders.domain import BookOpener

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
# The strengths the steps of the tests are given, none of which is the default of 1
STRONG: int = 3
STRONGER: int = 4
STRONGEST: int = 5
# The group the page of a test is put in, and the order key of the second and third pages
GROUP: str = 'Plates'
SECOND_ORDER_KEY: str = 'a1'
THIRD_ORDER_KEY: str = 'a2'
ROTATION: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=1.5))


@pytest.fixture
def fx_types_kit(fx_database: SqlDatabase, fx_open_book: BookOpener) -> ProcessingKit:
    """Build the processing kit over two processors of the geometry that can replace each other, and two that ask for a place.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: The kit whose catalogue holds the fakes of the tests, ``geometry.measuring``, ``geometry.second`` and
              ``geometry.third``.
    :rtype: ProcessingKit
    """
    processors = [
        SplitNone(),
        FakeProcessor(),
        CleanupProcessor(),
        MeasuringProcessor(),
        SecondProcessor(),
        ThirdProcessor(),
    ]
    return ProcessingKit(fx_database, fx_open_book, processors=processors)


def strong(key: str, strength: int, step_id: StepId | None = None) -> Step:
    """Build a step of a processor of the fakes with a strength of its own.

    :param key: Key of the processor.
    :type key: str
    :param strength: Its strength.
    :type strength: int
    :param step_id: Identifier of a step the book has, or None for a new step.
    :type step_id: StepId | None
    :returns: The step.
    :rtype: Step
    """
    step = Step(processor_key=key, params={STRENGTH_PARAMETER: strength})
    return step if step_id is None else evolve(step, step_id=step_id)


def at(recipe: Recipe, step_id: StepId) -> RecipeStepKey:
    """Name a step of a recipe of the geometry, as the address of the route does.

    :param recipe: A recipe that holds the step.
    :type recipe: Recipe
    :param step_id: The step.
    :type step_id: StepId
    :returns: The address of the step.
    :rtype: RecipeStepKey
    """
    return RecipeStepKey(Stage.GEOMETRY, recipe.id, step_id)


async def replace(
    kit: ProcessingKit, book_id: BookId, address: RecipeStepKey, key: str, order: OrderMode = OrderMode.USUAL
) -> Recipe:
    """Replace the type of a step of a recipe, as the route does.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: The book.
    :type book_id: BookId
    :param address: The stage, the recipe and the step.
    :type address: RecipeStepKey
    :param key: Key of the processor of the new type.
    :type key: str
    :param order: Whether a place the order refuses is refused or only warned of.
    :type order: OrderMode
    :returns: The recipe as stored.
    :rtype: Recipe
    """
    return await kit.service().replace_step_type(Book(directory_of(book_id)), address, key, order)


async def seeded_page(kit: ProcessingKit, book_id: BookId, order_key: str) -> Page:
    """Seed a page with an image the geometry can run on.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: The book.
    :type book_id: BookId
    :param order_key: Order key of the page.
    :type order_key: str
    :returns: The page.
    :rtype: Page
    """
    page, _ = await kit.seed_scan_page(book_id, order_key=order_key)
    await kit.seed_base_version(book_id, page)
    return page


async def ran_book(kit: ProcessingKit) -> tuple[Actor, BookId, Page, Recipe]:
    """Seed a book of one page whose geometry recipe has one fake step of strength 3, and run the geometry on it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the book, the page and the recipe of text pages.
    :rtype: tuple[Actor, BookId, Page, Recipe]
    """
    actor, book_id = await kit.seed_book()
    page = await seeded_page(kit, book_id, 'a0')
    first = (await kit.recipe_of(actor, book_id, Stage.GEOMETRY)).steps[0]
    draft = RecipeDraft(steps=[strong(FAKE_KEY, STRONG, first.step_id)])
    recipe = await kit.edit_recipe(actor, book_id, Stage.GEOMETRY, draft)
    await run_stage(kit, actor, book_id, StageRun(stage=Stage.GEOMETRY))
    return actor, book_id, page, recipe


async def kept_on_page(kit: ProcessingKit, actor: Actor, book_id: BookId, key: PageStepKey) -> tuple[Any, ...]:
    """Read what a step runs with on a page and what the page shows of it, through the services and ``PageLayers``.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: The signed-in account.
    :type actor: Actor
    :param book_id: The book.
    :type book_id: BookId
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The parameters a run lays for the step on the page, the settings the page lists and its edits.
    :rtype: tuple[Any, ...]
    """
    book = open_for(actor, book_id)

    def laid(repos: BookRepositories) -> dict[str, Any]:
        """Work out the parameters a run gives the step on the page.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The parameters.
        :rtype: dict[str, Any]
        """
        page = repos.pages.get(key.page_id)
        recipe = next(recipe for recipe in repos.recipes.list_for_stage(key.stage) if recipe.kind is page.recipe_kind)
        step = next(step for step in recipe.steps if step.step_id == key.step_id)
        state = repos.page_step_states.find(key)
        return PageLayers(page).lay(repos, key.step_id, step.params, None if state is None else state.params)

    settings: list[PageStepSettings] = list(await kit.page_settings().list(book, key.page_id, key.stage))
    edits: list[PageEdit] = list(await kit.edits().list(book, key.page_id, key.stage))
    return await book.read(laid), [(listed.params, list(listed.parts)) for listed in settings], edits


async def history_of(kit: ProcessingKit, actor: Actor, book_id: BookId, key: PageStepKey) -> list[PageHistoryEntry]:
    """Read the changes of a step on a page, oldest first.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: The signed-in account.
    :type actor: Actor
    :param book_id: The book.
    :type book_id: BookId
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The changes.
    :rtype: list[PageHistoryEntry]
    """
    return list((await kit.page_history().list(open_for(actor, book_id), key)).changes)


async def state_of_stage(kit: ProcessingKit, book_id: BookId, page: Page) -> StageState:
    """Read the state of the record of the geometry of a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: The book.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :returns: Whether the record is fresh or stale.
    :rtype: StageState
    """
    key = PageStageKey(page.id, Stage.GEOMETRY)
    return (await kit.databases.open(book_id).read(lambda repos: repos.page_stages.get(key))).state


async def make_fresh(kit: ProcessingKit, book_id: BookId, page: Page) -> None:
    """Mark the record of the geometry of a page fresh, as a run leaves it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: The book.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    """

    def work(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Write the record fresh.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the work does not need.
        :type _book_record: BookRecord
        """
        record = repos.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        repos.page_stages.save(evolve(record, state=StageState.FRESH))

    await kit.databases.open(book_id).change(work)


class TestParameters:
    """Tests for the parameters a step runs with under each type it has had."""

    async def test_replacing_the_type_of_a_step_keeps_the_parameters_of_the_earlier_type_and_returning_restores_them(
        self, fx_types_kit: ProcessingKit
    ) -> None:
        """Verify a new type starts from its defaults, and each type comes back with the parameters it had.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id, _, recipe = await ran_book(kit)
        step_id = recipe.steps[0].step_id
        measuring = await replace(kit, book_id, at(recipe, step_id), MEASURING_KEY)
        draft = RecipeDraft(steps=[strong(MEASURING_KEY, STRONGER, step_id)])
        await kit.edit_recipe(actor, book_id, Stage.GEOMETRY, draft)
        back = await replace(kit, book_id, at(recipe, step_id), FAKE_KEY)
        again = await replace(kit, book_id, at(recipe, step_id), MEASURING_KEY)
        expect([(step.step_id, step.processor_key) for step in measuring.steps] == [(step_id, MEASURING_KEY)])
        expect(measuring.steps[0].params == MeasuringProcessor().validate_params({}))
        expect(back.steps[0] == recipe.steps[0])
        expect(again.steps[0].params[STRENGTH_PARAMETER] == STRONGER)
        assert_expectations()

    async def test_a_step_two_recipes_hold_takes_the_type_in_both_and_each_keeps_its_parameters(
        self, fx_types_kit: ProcessingKit
    ) -> None:
        """Verify the recipe of blank pages that holds the step of the recipe of text pages gets the type too.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id, _, recipe = await ran_book(kit)
        step_id = recipe.steps[0].step_id
        blank_draft = RecipeDraft(steps=[strong(FAKE_KEY, STRONGEST, step_id)])
        blank = await kit.edit_recipe(actor, book_id, Stage.GEOMETRY, blank_draft, RecipeKind.BLANK)
        await replace(kit, book_id, at(recipe, step_id), MEASURING_KEY)
        both = [
            await kit.recipe_of(actor, book_id, Stage.GEOMETRY, kind) for kind in (RecipeKind.TEXT, RecipeKind.BLANK)
        ]
        await replace(kit, book_id, at(blank, step_id), FAKE_KEY)
        back = [
            await kit.recipe_of(actor, book_id, Stage.GEOMETRY, kind) for kind in (RecipeKind.TEXT, RecipeKind.BLANK)
        ]
        expect([held.steps[0].processor_key for held in both] == [MEASURING_KEY, MEASURING_KEY])
        expect([held.steps[0].params[STRENGTH_PARAMETER] for held in back] == [STRONG, STRONGEST])
        assert_expectations()


class TestRefusals:
    """Tests for the types a step is not given, and for the one way a type changes."""

    async def test_a_type_the_order_refuses_at_the_place_of_the_step_is_refused_unless_the_order_is_free(
        self, fx_types_kit: ProcessingKit
    ) -> None:
        """Verify a step that must stand after another is refused before it, and given in the free order.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id = await kit.seed_book()
        draft = RecipeDraft(steps=[strong(FAKE_KEY, STRONG), strong(SECOND_KEY, STRONG)])
        recipe = await kit.edit_recipe(actor, book_id, Stage.GEOMETRY, draft)
        step_id = recipe.steps[0].step_id
        with pytest.raises(InvalidParametersError):
            await replace(kit, book_id, at(recipe, step_id), THIRD_KEY)
        kept = await kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        free = await replace(kit, book_id, at(recipe, step_id), THIRD_KEY, OrderMode.FREE)
        expect(kept == recipe)
        expect(free.steps[0].processor_key == THIRD_KEY)
        assert_expectations()

    @pytest.mark.parametrize('key', [CleanupProcessor.spec.key, 'geometry.gone'], ids=['other stage', 'unknown'])
    async def test_a_processor_of_another_stage_or_none_is_refused(self, fx_types_kit: ProcessingKit, key: str) -> None:
        """Verify only a processor of the catalogue of the stage of the recipe becomes the type of a step.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        :param key: Key of the processor asked for.
        :type key: str
        """
        kit = fx_types_kit
        actor, book_id = await kit.seed_book()
        recipe = await kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        with pytest.raises(InvalidParametersError):
            await replace(kit, book_id, at(recipe, recipe.steps[0].step_id), key)

    async def test_a_step_the_recipe_does_not_hold_is_not_found(self, fx_types_kit: ProcessingKit) -> None:
        """Verify the step is named through a recipe that holds it.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id = await kit.seed_book()
        text = await kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        blank = await kit.recipe_of(actor, book_id, Stage.GEOMETRY, RecipeKind.BLANK)
        with pytest.raises(NotFoundError):
            await replace(kit, book_id, at(text, blank.steps[0].step_id), MEASURING_KEY)

    async def test_saving_a_draft_that_changes_the_type_of_a_step_is_refused(self, fx_types_kit: ProcessingKit) -> None:
        """Verify a recipe saved with another processor for a step it keeps is refused, and stays as it was.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id = await kit.seed_book()
        recipe = await kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        draft = RecipeDraft(steps=[evolve(recipe.steps[0], processor_key=MEASURING_KEY)])
        with pytest.raises(StepTypeChangedError):
            await kit.edit_recipe(actor, book_id, Stage.GEOMETRY, draft)
        assert await kit.recipe_of(actor, book_id, Stage.GEOMETRY) == recipe


class TestPageState:
    """Tests for what the pages keep for the step under each type, and the stale marks of a replacement."""

    async def test_page_edits_and_group_values_of_the_earlier_type_are_inactive_after_the_replacement_and_active_after(
        self, fx_types_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a run lays and the page lists nothing of the earlier type under the new one, and all of it once back.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        kit = fx_types_kit
        actor, book_id, page, recipe = await ran_book(kit)
        key = PageStepKey(page.id, Stage.GEOMETRY, recipe.steps[0].step_id)
        await fx_open_book.databases.open(book_id).change(
            lambda repos, _book_record: repos.pages.update(evolve(page, group_label=GROUP))
        )
        target = ValueTarget(scope=ValueScope.GROUP, group_label=GROUP)
        field = ValueField(stage=Stage.GEOMETRY, step_id=key.step_id, name=STRENGTH_PARAMETER, target=target)
        await kit.page_settings().change(open_for(actor, book_id), field, STRONGER)
        await kit.edits().save(open_for(actor, book_id), key, ROTATION, None)
        before = await kept_on_page(kit, actor, book_id, key)
        await replace(kit, book_id, at(recipe, key.step_id), MEASURING_KEY)
        under_measuring = await kept_on_page(kit, actor, book_id, key)
        await replace(kit, book_id, at(recipe, key.step_id), FAKE_KEY)
        back = await kept_on_page(kit, actor, book_id, key)
        expect(before[0][STRENGTH_PARAMETER] == STRONGER and len(before[1]) == 1 and len(before[2]) == 1)
        expect(under_measuring == (MeasuringProcessor().validate_params({}), [], []))
        expect(back == before)
        assert_expectations()

    async def test_the_heads_of_the_stage_on_the_pages_where_the_step_is_enabled_are_marked_stale(
        self, fx_types_kit: ProcessingKit
    ) -> None:
        """Verify the page a recipe runs the step on goes stale, and none goes stale for a step that is switched off.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id, page, recipe = await ran_book(kit)
        await make_fresh(kit, book_id, page)
        await replace(kit, book_id, at(recipe, recipe.steps[0].step_id), MEASURING_KEY)
        enabled = await state_of_stage(kit, book_id, page)
        off = evolve(strong(FAKE_KEY, STRONG), enabled=False)
        draft = RecipeDraft(steps=[off, strong(MEASURING_KEY, STRONG, recipe.steps[0].step_id)])
        two = await kit.edit_recipe(actor, book_id, Stage.GEOMETRY, draft)
        await make_fresh(kit, book_id, page)
        await replace(kit, book_id, at(two, two.steps[0].step_id), SECOND_KEY)
        expect(enabled is StageState.STALE)
        expect(await state_of_stage(kit, book_id, page) is StageState.FRESH)
        assert_expectations()


class TestHistory:
    """Tests for the replacement in the histories of the pages, and its undo."""

    async def test_the_history_gets_one_entry_per_page_in_one_batch_and_the_undo_of_the_batch_brings_the_type_back(
        self, fx_types_kit: ProcessingKit
    ) -> None:
        """Verify the pages that keep a setting or an edit get the one change, and its undo from one page restores all.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id, page, recipe = await ran_book(kit)
        keys = [PageStepKey(page.id, Stage.GEOMETRY, recipe.steps[0].step_id)]
        for order_key in (SECOND_ORDER_KEY, THIRD_ORDER_KEY):
            keys.append(evolve(keys[0], page_id=(await seeded_page(kit, book_id, order_key)).id))
        await PageValues(kit, actor, book_id).set(keys[0], STRENGTH_PARAMETER, STRONGER)
        await kit.edits().save(open_for(actor, book_id), keys[1], ROTATION, None)
        kept = await kept_on_page(kit, actor, book_id, keys[1])
        await replace(kit, book_id, at(recipe, keys[0].step_id), MEASURING_KEY)
        replaced = [(await history_of(kit, actor, book_id, key))[-1:] for key in keys]
        undone = await kit.page_history().undo(open_for(actor, book_id), keys[0], None)
        restored = await kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        changes = [entries[0] for entries in replaced[:2]]
        expect(replaced[2] == [])
        expect(
            {(change.layer, change.id, change.batch_id) for change in changes}
            == {(changes[0].layer, changes[0].id, changes[0].batch_id)}
        )
        expect(changes[0].layer is StepLayer.TYPE and changes[0].batch_id is not None)
        expect((changes[0].before, changes[0].after) == ({TYPE_FIELD: FAKE_KEY}, {TYPE_FIELD: MEASURING_KEY}))
        expect(sorted(undo.page_id for undo in undone) == sorted(key.page_id for key in keys[:2]))
        expect(restored == evolve(recipe, updated_at=restored.updated_at))
        expect(await kept_on_page(kit, actor, book_id, keys[1]) == kept)
        assert_expectations()

    async def test_a_page_that_kept_a_setting_under_the_type_that_comes_back_gets_the_change_too(
        self, fx_types_kit: ProcessingKit
    ) -> None:
        """Verify the return of a type reaches the history of a page whose setting it brings back.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id, page, recipe = await ran_book(kit)
        key = PageStepKey(page.id, Stage.GEOMETRY, recipe.steps[0].step_id)
        await PageValues(kit, actor, book_id).set(key, STRENGTH_PARAMETER, STRONGER)
        await replace(kit, book_id, at(recipe, key.step_id), MEASURING_KEY)
        await replace(kit, book_id, at(recipe, key.step_id), FAKE_KEY)
        history = await history_of(kit, actor, book_id, key)
        assert [(change.layer, change.after) for change in history] == [
            (StepLayer.SETTINGS, {STRENGTH_PARAMETER: STRONGER}),
            (StepLayer.TYPE, {TYPE_FIELD: MEASURING_KEY}),
            (StepLayer.TYPE, {TYPE_FIELD: FAKE_KEY}),
        ]

    async def test_the_type_the_step_has_changes_nothing(self, fx_types_kit: ProcessingKit) -> None:
        """Verify asking for the type the step has writes no history and leaves the recipe as it was.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id, page, recipe = await ran_book(kit)
        key = PageStepKey(page.id, Stage.GEOMETRY, recipe.steps[0].step_id)
        await PageValues(kit, actor, book_id).set(key, STRENGTH_PARAMETER, STRONGER)
        same = await replace(kit, book_id, at(recipe, key.step_id), FAKE_KEY)
        expect(same == recipe)
        expect([change.layer for change in await history_of(kit, actor, book_id, key)] == [StepLayer.SETTINGS])
        assert_expectations()

    async def test_the_undo_of_a_replacement_the_step_has_left_since_is_refused(
        self, fx_types_kit: ProcessingKit
    ) -> None:
        """Verify a replacement is taken back only while the step has the type it gave, which a later one changed.

        The page keeps nothing under the second and the third type, so the later replacement is not in its history.

        :param fx_types_kit: What the processing services of the test share.
        :type fx_types_kit: ProcessingKit
        """
        kit = fx_types_kit
        actor, book_id, page, recipe = await ran_book(kit)
        key = PageStepKey(page.id, Stage.GEOMETRY, recipe.steps[0].step_id)
        await PageValues(kit, actor, book_id).set(key, STRENGTH_PARAMETER, STRONGER)
        await replace(kit, book_id, at(recipe, key.step_id), MEASURING_KEY)
        await replace(kit, book_id, at(recipe, key.step_id), SECOND_KEY)
        with pytest.raises(ConflictError):
            await kit.page_history().undo(open_for(actor, book_id), key, None)
        kept = await kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        assert kept.steps[0].processor_key == SECOND_KEY
