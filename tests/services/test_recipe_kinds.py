"""Tests for the recipe each page is processed by: the recipe of its kind, in every stage that has recipes."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.changes import PageChanges
from bookreviver.domain.enums import ContentType, PageKind, RecipeKind, Stage, StageState
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.values import PageStageKey, ProfileDraft, RecipeDraft, RecipeKey, SliceRequest, StageRun, Step
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from tests.helpers.page_services import make_page_service
from tests.helpers.processing import ProcessingKit
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, PageStage, Project, Recipe
    from bookreviver.domain.ids import PageId

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
STRENGTH_PARAM: str = 'strength'
# The strength the step of the recipe of each kind runs with, by which the versions of one kind are told from another's
STRENGTHS: dict[RecipeKind, int] = {
    RecipeKind.TEXT: 2,
    RecipeKind.COLOR_PICTURE: 3,
    RecipeKind.BW_PICTURE: 4,
    RecipeKind.BLANK: 5,
}
RECIPE_STAGES: tuple[Stage, ...] = (Stage.PAGE_SPLIT, Stage.GEOMETRY, Stage.CLEANUP)
TEMPLATE_STRENGTH: int = 7


async def seed_book(kit: ProcessingKit, kinds: list[PageKind]) -> tuple[Actor, Project, list[PageId]]:
    """Seed a project whose pages have the given kinds, each a scan page with the base version the stage reads.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param kinds: Kind of each page in book order.
    :type kinds: list[PageKind]
    :returns: The actor, the project and the identifiers of the pages in book order.
    :rtype: tuple[Actor, Project, list[PageId]]
    """
    actor, project = await kit.seed_project()
    page_ids: list[PageId] = []
    for index, kind in enumerate(kinds):
        page_ids.append(await add_page(kit, project, kind, order_key=f'a{index}'))
    return actor, project, page_ids


async def add_page(kit: ProcessingKit, project: Project, kind: PageKind, *, order_key: str) -> PageId:
    """Add a scan page of a kind to a project, with the base version the stage reads.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: Project owning the page.
    :type project: Project
    :param kind: Role of the page in the book.
    :type kind: PageKind
    :param order_key: Order key of the page.
    :type order_key: str
    :returns: The identifier of the page.
    :rtype: PageId
    """
    page, _ = await kit.seed_scan_page(project, order_key=order_key)
    await kit.seed_base_version(page)
    uow = kit.uow()
    await uow.pages.update(evolve(page, kind=kind))
    await uow.commit()
    return page.id


async def tune_recipes(kit: ProcessingKit, actor: Actor, project: Project) -> dict[RecipeKind, Recipe]:
    """Give the step of the recipe of each kind of the geometry stage a strength of its own.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project whose recipes are changed.
    :type project: Project
    :returns: The recipes as stored, by kind.
    :rtype: dict[RecipeKind, Recipe]
    """
    service = kit.service()
    tuned: dict[RecipeKind, Recipe] = {}
    for recipe in (await service.recipes(actor, project.id, Stage.GEOMETRY, SliceRequest(limit=10))).items:
        draft = RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAM: STRENGTHS[recipe.kind]})])
        tuned[recipe.kind] = await service.save_recipe(actor, project.id, RecipeKey(Stage.GEOMETRY, recipe.id), draft)
    return tuned


async def run_all(kit: ProcessingKit, actor: Actor, project: Project, run: StageRun | None = None) -> None:
    """Run the geometry stage over the pages of the book, and let the worker do it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to run in.
    :type project: Project
    :param run: What to run, or None for the whole book.
    :type run: StageRun | None
    """
    chosen = StageRun(stage=Stage.GEOMETRY) if run is None else run
    job = await kit.service().start_run(actor, project.id, Stage.GEOMETRY, chosen)
    await kit.jobs().run_stage(job.id)
    await kit.work_queue()


async def record_of(kit: ProcessingKit, page_id: PageId) -> PageStage:
    """Read the record of the geometry stage of a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page_id: The page.
    :type page_id: PageId
    :returns: The record as committed.
    :rtype: PageStage
    """
    return await kit.uow().page_stages.get(PageStageKey(page_id, Stage.GEOMETRY))


class TestRecipesOfAStage:
    """Tests for the recipes a stage starts with."""

    @pytest.mark.parametrize('stage', RECIPE_STAGES)
    async def test_every_stage_has_one_recipe_for_each_kind(self, fx_kit: ProcessingKit, stage: Stage) -> None:
        """Verify the first time a stage is asked for it gets the four recipes, each with steps of its own.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param stage: Stage under test.
        :type stage: Stage
        """
        actor, project = await fx_kit.seed_project()
        found = (await fx_kit.service().recipes(actor, project.id, stage, SliceRequest(limit=10))).items
        step_ids = [step.step_id for recipe in found for step in recipe.steps]
        expect([recipe.kind for recipe in found] == list(RecipeKind))
        expect(len({recipe.id for recipe in found}) == len(RecipeKind))
        expect(len(set(step_ids)) == len(step_ids))
        assert_expectations()

    async def test_a_kind_without_a_template_starts_with_a_copy_of_the_recipe_of_text(
        self, fx_asset_store: LocalAssetStore
    ) -> None:
        """Verify a template that is given for text pages alone leaves the other kinds the same steps, identifiers too.

        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        """
        template = RecipeTemplate(processor_keys=(FAKE_KEY,), params={FAKE_KEY: {STRENGTH_PARAM: TEMPLATE_STRENGTH}})
        kit = ProcessingKit(fx_asset_store, defaults=DefaultRecipes({Stage.GEOMETRY: {RecipeKind.TEXT: template}}))
        actor, project = await kit.seed_project()
        found = (await kit.service().recipes(actor, project.id, Stage.GEOMETRY, SliceRequest(limit=10))).items
        expect([recipe.kind for recipe in found] == list(RecipeKind))
        expect({step.params[STRENGTH_PARAM] for recipe in found for step in recipe.steps} == {TEMPLATE_STRENGTH})
        expect(len({step.step_id for recipe in found for step in recipe.steps}) == 1)
        assert_expectations()

    async def test_the_default_profile_makes_the_recipe_of_text_pages_only(self, fx_kit: ProcessingKit) -> None:
        """Verify the steps of the default profile go to the recipe of text pages, and the other kinds keep the template.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        profile = await fx_kit.profiles().save(
            actor,
            Stage.GEOMETRY,
            ProfileDraft(name='Own', steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAM: TEMPLATE_STRENGTH})]),
        )
        await fx_kit.profiles().set_default(actor, profile.id, is_default=True)
        found = (await fx_kit.service().recipes(actor, project.id, Stage.GEOMETRY, SliceRequest(limit=10))).items
        assert {recipe.kind: (recipe.steps[0].params[STRENGTH_PARAM], recipe.profile_id) for recipe in found} == {
            RecipeKind.TEXT: (TEMPLATE_STRENGTH, profile.id),
            RecipeKind.COLOR_PICTURE: (1, None),
            RecipeKind.BW_PICTURE: (1, None),
            RecipeKind.BLANK: (1, None),
        }


class TestRecipeOfAPage:
    """Tests for a run, which processes each page by the recipe of its kind."""

    async def test_a_page_is_processed_by_the_recipe_of_its_kind(self, fx_kit: ProcessingKit) -> None:
        """Verify text, a plate, a blank page and a picture found on a text page each go to the recipe of their kind.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (text, plate, blank, found) = await seed_book(
            fx_kit, [PageKind.TEXT, PageKind.PLATE, PageKind.BLANK, PageKind.TEXT]
        )
        uow = fx_kit.uow()
        await uow.pages.update(evolve(await uow.pages.get(found), content_type=ContentType.BW_PICTURE))
        await uow.commit()
        recipes = await tune_recipes(fx_kit, actor, project)

        await run_all(fx_kit, actor, project)

        chosen = [(await record_of(fx_kit, page_id)).recipe_id for page_id in (text, plate, blank, found)]
        assert chosen == [
            recipes[RecipeKind.TEXT].id,
            recipes[RecipeKind.COLOR_PICTURE].id,
            recipes[RecipeKind.BLANK].id,
            recipes[RecipeKind.BW_PICTURE].id,
        ]

    async def test_what_the_user_set_decides_the_kind_of_a_page(self, fx_kit: ProcessingKit) -> None:
        """Verify a text page the user made a picture goes to the picture recipe and a plate made text to the text one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (by_hand, plate_as_text) = await seed_book(fx_kit, [PageKind.TEXT, PageKind.PLATE])
        uow = fx_kit.uow()
        for page_id, content in ((by_hand, ContentType.COLOR_PICTURE), (plate_as_text, ContentType.TEXT)):
            await uow.pages.update(evolve(await uow.pages.get(page_id), content_type=content, content_by_hand=True))
        await uow.commit()
        recipes = await tune_recipes(fx_kit, actor, project)

        await run_all(fx_kit, actor, project)

        chosen = [(await record_of(fx_kit, page_id)).recipe_id for page_id in (by_hand, plate_as_text)]
        assert chosen == [recipes[RecipeKind.COLOR_PICTURE].id, recipes[RecipeKind.TEXT].id]

    async def test_a_run_of_some_pages_chooses_the_recipe_of_each(self, fx_kit: ProcessingKit) -> None:
        """Verify naming the pages of a run does not change the recipe each of them goes to.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (_, plate) = await seed_book(fx_kit, [PageKind.TEXT, PageKind.PLATE])
        recipes = await tune_recipes(fx_kit, actor, project)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, page_ids=(plate,)))
        assert (await record_of(fx_kit, plate)).recipe_id == recipes[RecipeKind.COLOR_PICTURE].id

    async def test_a_page_added_later_goes_to_the_recipe_of_its_kind(self, fx_kit: ProcessingKit) -> None:
        """Verify a plate that joins the book after the first run is processed by the recipe of plates on the next.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await seed_book(fx_kit, [PageKind.TEXT])
        recipes = await tune_recipes(fx_kit, actor, project)
        await run_all(fx_kit, actor, project)
        late_plate = await add_page(fx_kit, project, PageKind.PLATE, order_key='z0')
        await run_all(fx_kit, actor, project)
        assert (await record_of(fx_kit, late_plate)).recipe_id == recipes[RecipeKind.COLOR_PICTURE].id

    async def test_changing_what_a_page_shows_moves_it_to_the_recipe_of_its_new_kind_and_marks_it_stale(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a page the user makes a black-and-white picture is stale, and the next run uses the picture recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page_id,) = await seed_book(fx_kit, [PageKind.TEXT])
        recipes = await tune_recipes(fx_kit, actor, project)
        await run_all(fx_kit, actor, project)
        before = await record_of(fx_kit, page_id)
        pages = make_page_service(fx_kit.uow(), fx_kit.assets, (fx_kit.events, fx_kit.clock, fx_kit.recording))

        await pages.update(actor, project.id, page_id, PageChanges(content_type=ContentType.BW_PICTURE))
        stale = await record_of(fx_kit, page_id)
        await run_all(fx_kit, actor, project)
        after = await record_of(fx_kit, page_id)

        expect((before.recipe_id, before.state) == (recipes[RecipeKind.TEXT].id, StageState.FRESH))
        expect((stale.recipe_id, stale.state) == (recipes[RecipeKind.TEXT].id, StageState.STALE))
        expect((after.recipe_id, after.state) == (recipes[RecipeKind.BW_PICTURE].id, StageState.FRESH))
        assert_expectations()

    async def test_marking_a_page_blank_moves_it_to_the_recipe_of_blank_pages_and_marks_it_stale(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the role of a page decides its kind too, so a page made blank is stale and runs by the blank recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page_id,) = await seed_book(fx_kit, [PageKind.TEXT])
        recipes = await tune_recipes(fx_kit, actor, project)
        await run_all(fx_kit, actor, project)
        pages = make_page_service(fx_kit.uow(), fx_kit.assets, (fx_kit.events, fx_kit.clock, fx_kit.recording))

        await pages.update(actor, project.id, page_id, PageChanges(kind=PageKind.BLANK))
        stale = await record_of(fx_kit, page_id)
        await run_all(fx_kit, actor, project)

        expect(stale.state is StageState.STALE)
        expect((await record_of(fx_kit, page_id)).recipe_id == recipes[RecipeKind.BLANK].id)
        assert_expectations()


class TestResetToTheDefaultSteps:
    """Tests for putting back the steps a stage starts with, which are the profile of the account or the template."""

    async def test_the_template_steps_come_back_and_the_recipe_keeps_what_is_its_own(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the steps are new ones of the template while the identifier and the kind of the recipe stay.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (plate,) = await seed_book(fx_kit, [PageKind.PLATE])
        recipes = await tune_recipes(fx_kit, actor, project)
        soft = recipes[RecipeKind.COLOR_PICTURE]
        await run_all(fx_kit, actor, project)
        reset = await fx_kit.profiles().reset(actor, project.id, RecipeKey(Stage.GEOMETRY, soft.id))
        record = await record_of(fx_kit, plate)
        expect((reset.id, reset.kind) == (soft.id, RecipeKind.COLOR_PICTURE))
        expect(
            [(step.processor_key, step.params) for step in reset.steps] == [(FAKE_KEY, {'strength': 1, 'fail': False})]
        )
        expect(reset.steps[0].step_id != soft.steps[0].step_id)
        expect(reset.profile_id is None)
        expect((record.recipe_id, record.state) == (soft.id, StageState.STALE))
        assert_expectations()

    async def test_the_default_profile_of_the_account_comes_before_the_template_for_text_pages(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify an account with a default profile for the stage gets the steps of that profile in the recipe of text.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        profile = await fx_kit.profiles().save(
            actor,
            Stage.GEOMETRY,
            ProfileDraft(name='Own', steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAM: TEMPLATE_STRENGTH})]),
        )
        await fx_kit.profiles().set_default(actor, profile.id, is_default=True)
        found = (await fx_kit.service().recipes(actor, project.id, Stage.GEOMETRY, SliceRequest(limit=10))).items
        picture = next(recipe for recipe in found if recipe.kind is RecipeKind.BW_PICTURE)
        text = next(recipe for recipe in found if recipe.kind is RecipeKind.TEXT)
        reset_text = await fx_kit.profiles().reset(actor, project.id, RecipeKey(Stage.GEOMETRY, text.id))
        reset_picture = await fx_kit.profiles().reset(actor, project.id, RecipeKey(Stage.GEOMETRY, picture.id))
        expect([step.params[STRENGTH_PARAM] for step in reset_text.steps] == [TEMPLATE_STRENGTH])
        expect(reset_text.profile_id == profile.id)
        expect([step.params[STRENGTH_PARAM] for step in reset_picture.steps] == [1])
        expect(reset_picture.profile_id is None)
        assert_expectations()

    async def test_a_recipe_takes_the_template_of_its_own_kind(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify each recipe gets the template of its kind.

        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        """
        templates = DefaultRecipes(
            {
                Stage.GEOMETRY: {
                    kind: RecipeTemplate(processor_keys=(FAKE_KEY,), params={FAKE_KEY: {STRENGTH_PARAM: strength}})
                    for kind, strength in STRENGTHS.items()
                }
            }
        )
        kit = ProcessingKit(fx_asset_store, defaults=templates)
        actor, project = await kit.seed_project()
        found = (await kit.service().recipes(actor, project.id, Stage.GEOMETRY, SliceRequest(limit=10))).items
        strengths = {}
        for recipe in found:
            await kit.service().save_recipe(
                actor,
                project.id,
                RecipeKey(Stage.GEOMETRY, recipe.id),
                RecipeDraft(steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAM: TEMPLATE_STRENGTH})]),
            )
            reset = await kit.profiles().reset(actor, project.id, RecipeKey(Stage.GEOMETRY, recipe.id))
            strengths[recipe.kind] = reset.steps[0].params[STRENGTH_PARAM]
        assert strengths == STRENGTHS

    async def test_a_recipe_of_another_book_or_stage_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the recipe of another account's book and the recipe named under another stage are refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        stranger, _ = await fx_kit.seed_project()
        recipe = (await fx_kit.service().recipes(actor, project.id, Stage.GEOMETRY, SliceRequest(limit=10))).items[0]
        with pytest.raises(NotFoundError):
            await fx_kit.profiles().reset(stranger, project.id, RecipeKey(Stage.GEOMETRY, recipe.id))
        with pytest.raises(NotFoundError):
            await fx_kit.profiles().reset(actor, project.id, RecipeKey(Stage.CLEANUP, recipe.id))
