"""Tests for the recipe each page of a run is processed by: the pin, the rules of the stage and the active recipe."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import ContentType, PageKind, RuleCondition, Stage, StageState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.values import PageStageKey, RecipeDraft, RecipeKey, StageRun, Step
from bookreviver.services.recipe_picks import RecipePicker
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from tests.helpers.processing import ProcessingKit
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.adapters.storage import LocalAssetStore
    from bookreviver.domain.entities import Actor, PageStage, Project, Recipe
    from bookreviver.domain.ids import PageId

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
PLATES_NAME: str = 'Plates'
SOFT_NAME: str = 'Soft'
GROUP: str = 'Engravings'
PLATES_STRENGTH: int = 5
SOFT_STRENGTH: int = 7


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


async def add_page(kit: ProcessingKit, project: Project, kind: PageKind, *, order_key: str, group: str = '') -> PageId:
    """Add a scan page of a kind to a project, with the base version the stage reads.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: Project owning the page.
    :type project: Project
    :param kind: Kind of the page.
    :type kind: PageKind
    :param order_key: Order key of the page.
    :type order_key: str
    :param group: Label of the group the page is in, or empty.
    :type group: str
    :returns: The identifier of the page.
    :rtype: PageId
    """
    page, _ = await kit.seed_scan_page(project, order_key=order_key)
    await kit.seed_base_version(page)
    uow = kit.uow()
    await uow.pages.update(evolve(page, kind=kind, group_label=group))
    await uow.commit()
    return page.id


async def add_variant(kit: ProcessingKit, actor: Actor, project: Project, name: str, strength: int) -> Recipe:
    """Add a variant of the geometry stage whose one step has a strength of its own.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to add the variant to.
    :type project: Project
    :param name: Name of the variant.
    :type name: str
    :param strength: Strength the step of the variant runs with, by which its versions are told from another's.
    :type strength: int
    :returns: The variant.
    :rtype: Recipe
    """
    draft = RecipeDraft(name=name, steps=[Step(processor_key=FAKE_KEY, params={'strength': strength})])
    return await kit.service().add_variant(actor, project.id, Stage.GEOMETRY, draft)


async def run_all(kit: ProcessingKit, actor: Actor, project: Project, run: StageRun | None = None) -> None:
    """Run the geometry stage over the pages of the book, and let the worker do it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :param project: Project to run in.
    :type project: Project
    :param run: What to run, or None for the whole book by the recipes the pages choose.
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


class TestRulesOfAStage:
    """Tests for a run that names no recipe and the rules of the stage."""

    async def test_plates_get_the_variant_of_the_rule_and_text_pages_the_active_recipe(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the rule on plates sends them to its variant, and every other page is processed by the active one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (text, plate, frontispiece) = await seed_book(
            fx_kit, [PageKind.TEXT, PageKind.PLATE, PageKind.FRONTISPIECE]
        )
        variant = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)
        await run_all(fx_kit, actor, project)
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        chosen = [(await record_of(fx_kit, page_id)).recipe_id for page_id in (text, plate, frontispiece)]
        assert chosen == [active.id, variant.id, variant.id]

    async def test_the_rule_on_plates_follows_what_a_page_shows_and_not_only_its_kind(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a text page the detection found a picture on and one the user made a picture go to the plates variant.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (found, by_hand, text, plate_as_text) = await seed_book(
            fx_kit, [PageKind.TEXT, PageKind.TEXT, PageKind.TEXT, PageKind.PLATE]
        )
        uow = fx_kit.uow()
        for page_id, fields in (
            (found, {'content_type': ContentType.BW_PICTURE}),
            (by_hand, {'content_type': ContentType.COLOR_PICTURE, 'content_by_hand': True}),
            (plate_as_text, {'content_type': ContentType.TEXT, 'content_by_hand': True}),
        ):
            await uow.pages.update(evolve(await uow.pages.get(page_id), **fields))
        await uow.commit()
        variant = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)

        await run_all(fx_kit, actor, project)

        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        chosen = [(await record_of(fx_kit, page_id)).recipe_id for page_id in (found, by_hand, text, plate_as_text)]
        assert chosen == [variant.id, variant.id, active.id, active.id]

    async def test_a_run_without_rules_uses_the_active_recipe_for_every_page(self, fx_kit: ProcessingKit) -> None:
        """Verify a stage without a rule is processed as before, by its active recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page_ids = await seed_book(fx_kit, [PageKind.TEXT, PageKind.PLATE])
        await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        await run_all(fx_kit, actor, project)
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        assert [(await record_of(fx_kit, page_id)).recipe_id for page_id in page_ids] == [active.id, active.id]

    async def test_the_first_matching_rule_wins(self, fx_kit: ProcessingKit) -> None:
        """Verify a plate on an odd page goes to the variant of the rule added first, not to the later rule on parity.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (first_plate, _, odd_text) = await seed_book(
            fx_kit, [PageKind.PLATE, PageKind.TEXT, PageKind.TEXT]
        )
        plates = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        soft = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await fx_kit.add_rule(actor, plates, RuleCondition.PLATES)
        await fx_kit.add_rule(actor, soft, RuleCondition.ODD)
        await run_all(fx_kit, actor, project)
        assert [(await record_of(fx_kit, page_id)).recipe_id for page_id in (first_plate, odd_text)] == [
            plates.id,
            soft.id,
        ]

    async def test_parity_is_counted_over_the_whole_book_even_when_the_run_names_some_pages(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a run of the second page alone still sees it as an even page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (_, second) = await seed_book(fx_kit, [PageKind.TEXT, PageKind.TEXT])
        variant = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await fx_kit.add_rule(actor, variant, RuleCondition.EVEN)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, page_ids=(second,)))
        assert (await record_of(fx_kit, second)).recipe_id == variant.id

    async def test_a_manual_group_is_sent_to_its_variant(self, fx_kit: ProcessingKit) -> None:
        """Verify the pages with the label of the group get the variant, and the others do not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (loose,) = await seed_book(fx_kit, [PageKind.TEXT])
        grouped = await add_page(fx_kit, project, PageKind.TEXT, order_key='b0', group=GROUP)
        variant = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await fx_kit.add_rule(actor, variant, RuleCondition.GROUP, GROUP)
        await run_all(fx_kit, actor, project)
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        assert [(await record_of(fx_kit, page_id)).recipe_id for page_id in (loose, grouped)] == [
            active.id,
            variant.id,
        ]

    async def test_a_page_added_after_the_rule_gets_the_variant_of_the_rule(self, fx_kit: ProcessingKit) -> None:
        """Verify the rule is kept, so a plate that joins the book later is processed by it on the next run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _ = await seed_book(fx_kit, [PageKind.TEXT])
        variant = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)
        await run_all(fx_kit, actor, project)
        late_plate = await add_page(fx_kit, project, PageKind.PLATE, order_key='z0')
        await run_all(fx_kit, actor, project)
        assert (await record_of(fx_kit, late_plate)).recipe_id == variant.id

    async def test_the_condition_on_illustrations_changes_nothing_yet(self, fx_kit: ProcessingKit) -> None:
        """Verify a rule on illustrations is stored and matches no page, so every page keeps the active recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page_ids = await seed_book(fx_kit, [PageKind.PLATE, PageKind.TEXT])
        variant = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        await fx_kit.add_rule(actor, variant, RuleCondition.ILLUSTRATED)
        await run_all(fx_kit, actor, project)
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        assert {(await record_of(fx_kit, page_id)).recipe_id for page_id in page_ids} == {active.id}


class TestPinnedVariant:
    """Tests for the variant pinned to a page, and for the runs that name a recipe."""

    async def test_a_pinned_variant_beats_the_rule_and_survives_a_run_on_all_pages(self, fx_kit: ProcessingKit) -> None:
        """Verify a plate pinned to another variant keeps it through a run of the whole book, twice.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (plate,) = await seed_book(fx_kit, [PageKind.PLATE])
        plates = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        soft = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await fx_kit.add_rule(actor, plates, RuleCondition.PLATES)
        pinning = StageRun(stage=Stage.GEOMETRY, recipe_id=soft.id, page_ids=(plate,), pin=True)
        await run_all(fx_kit, actor, project, pinning)
        await run_all(fx_kit, actor, project)
        await run_all(fx_kit, actor, project)
        record = await record_of(fx_kit, plate)
        assert (record.recipe_id, record.pinned, record.state) == (soft.id, True, StageState.FRESH)

    async def test_a_run_by_a_recipe_pins_nothing_unless_it_says_so(self, fx_kit: ProcessingKit) -> None:
        """Verify a run of a variant on a page leaves the page unpinned, so the next run chooses by the rules again.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page,) = await seed_book(fx_kit, [PageKind.TEXT])
        variant = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=variant.id))
        tried = await record_of(fx_kit, page)
        await run_all(fx_kit, actor, project)
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        assert [tried.recipe_id, tried.pinned, (await record_of(fx_kit, page)).recipe_id] == [
            variant.id,
            False,
            active.id,
        ]

    async def test_a_trial_of_another_variant_replaces_the_pin(self, fx_kit: ProcessingKit) -> None:
        """Verify running another variant on a pinned page, without pinning it, leaves the page unpinned.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page,) = await seed_book(fx_kit, [PageKind.TEXT])
        plates = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        soft = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=plates.id, pin=True))
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=soft.id))
        record = await record_of(fx_kit, page)
        assert (record.recipe_id, record.pinned) == (soft.id, False)

    async def test_running_the_pinned_variant_again_keeps_the_pin(self, fx_kit: ProcessingKit) -> None:
        """Verify a run of the very variant a page is pinned to, with no pin in the request, leaves the pin.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page,) = await seed_book(fx_kit, [PageKind.TEXT])
        plates = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=plates.id, pin=True))
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=plates.id))
        assert (await record_of(fx_kit, page)).pinned

    async def test_unpinning_hands_the_page_back_to_the_rules(self, fx_kit: ProcessingKit) -> None:
        """Verify an unpinned page is marked stale and the next run chooses its recipe by the rules.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (plate,) = await seed_book(fx_kit, [PageKind.PLATE])
        plates = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        soft = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await fx_kit.add_rule(actor, plates, RuleCondition.PLATES)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=soft.id, pin=True))
        unpinned = await fx_kit.service().unpin(actor, project.id, plate, Stage.GEOMETRY)
        await run_all(fx_kit, actor, project)
        record = await record_of(fx_kit, plate)
        assert [unpinned.pinned, unpinned.state, record.recipe_id, record.pinned] == [
            False,
            StageState.STALE,
            plates.id,
            False,
        ]

    async def test_unpinning_a_stage_that_has_not_run_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject unpinning a stage no record of which exists.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page,) = await seed_book(fx_kit, [PageKind.TEXT])
        with pytest.raises(NotFoundError):
            await fx_kit.service().unpin(actor, project.id, page, Stage.GEOMETRY)

    async def test_unpinning_waits_while_the_project_is_processing(self, fx_kit: ProcessingKit) -> None:
        """Reject unpinning while a run is queued, since the run may be writing the record.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page,) = await seed_book(fx_kit, [PageKind.TEXT])
        await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        with pytest.raises(ConflictError):
            await fx_kit.service().unpin(actor, project.id, page, Stage.GEOMETRY)

    async def test_a_deleted_variant_leaves_the_page_to_the_rules(self, fx_kit: ProcessingKit) -> None:
        """Verify a pin whose variant is gone holds nothing, so the page falls to the active recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (page_id,) = await seed_book(fx_kit, [PageKind.TEXT])
        plates = await add_variant(fx_kit, actor, project, PLATES_NAME, PLATES_STRENGTH)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=plates.id, pin=True))
        uow = fx_kit.uow()
        await uow.recipes.delete(plates.id)
        await uow.commit()
        reader = fx_kit.uow()
        picker = RecipePicker(uow=reader, recipes=fx_kit.parts(reader).recipes)
        picked = await picker.pick(project.id, Stage.GEOMETRY, [await reader.pages.get(page_id)])
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        assert picked[page_id].id == active.id


class TestResetToTheDefaultSteps:
    """Tests for putting back the steps a stage starts with, which are the profile of the account or the template."""

    async def test_the_template_steps_come_back_and_the_recipe_keeps_what_is_its_own(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the steps are new ones of the template while the identifier, the name and the pin of the recipe stay.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, (plate,) = await seed_book(fx_kit, [PageKind.PLATE])
        soft = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        await run_all(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY, recipe_id=soft.id, pin=True))
        reset = await fx_kit.profiles().reset(actor, project.id, RecipeKey(Stage.GEOMETRY, soft.id))
        record = await record_of(fx_kit, plate)
        expect((reset.id, reset.name, reset.active) == (soft.id, SOFT_NAME, False))
        expect(
            [(step.processor_key, step.params) for step in reset.steps] == [(FAKE_KEY, {'strength': 1, 'fail': False})]
        )
        expect(reset.steps[0].step_id != soft.steps[0].step_id)
        expect(reset.profile_id is None)
        expect((record.recipe_id, record.pinned, record.state) == (soft.id, True, StageState.STALE))
        assert_expectations()

    async def test_the_default_profile_of_the_account_comes_before_the_template(self, fx_kit: ProcessingKit) -> None:
        """Verify an account with a default profile for the stage gets the steps of that profile.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        profile = await fx_kit.profiles().save(
            actor,
            Stage.GEOMETRY,
            RecipeDraft(name='Own', steps=[Step(processor_key=FAKE_KEY, params={'strength': PLATES_STRENGTH})]),
        )
        await fx_kit.profiles().set_default(actor, profile.id, is_default=True)
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        reset = await fx_kit.profiles().reset(actor, project.id, RecipeKey(Stage.GEOMETRY, active.id))
        expect([step.params['strength'] for step in reset.steps] == [PLATES_STRENGTH])
        expect(reset.profile_id == profile.id)
        assert_expectations()

    async def test_a_recipe_takes_the_template_of_its_own_name(self, fx_asset_store: LocalAssetStore) -> None:
        """Verify a variant named like a built-in template gets that template, and any other name the first one.

        :param fx_asset_store: Local asset store over the test's storage root.
        :type fx_asset_store: LocalAssetStore
        """
        templates = DefaultRecipes(
            {
                Stage.GEOMETRY: (
                    RecipeTemplate(name='Text', processor_keys=(FAKE_KEY,), params={FAKE_KEY: {'strength': 2}}),
                    RecipeTemplate(name=PLATES_NAME, processor_keys=(FAKE_KEY,), params={FAKE_KEY: {'strength': 3}}),
                )
            }
        )
        kit = ProcessingKit(fx_asset_store, defaults=templates)
        actor, project = await kit.seed_project()
        strengths = []
        for name in (PLATES_NAME, SOFT_NAME):
            variant = await add_variant(kit, actor, project, name, SOFT_STRENGTH)
            reset = await kit.profiles().reset(actor, project.id, RecipeKey(Stage.GEOMETRY, variant.id))
            strengths.append(reset.steps[0].params['strength'])
        assert strengths == [3, 2]

    async def test_a_recipe_of_another_book_or_stage_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the recipe of another account's book and the recipe named under another stage are refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        stranger, _ = await fx_kit.seed_project()
        soft = await add_variant(fx_kit, actor, project, SOFT_NAME, SOFT_STRENGTH)
        with pytest.raises(NotFoundError):
            await fx_kit.profiles().reset(stranger, project.id, RecipeKey(Stage.GEOMETRY, soft.id))
        with pytest.raises(NotFoundError):
            await fx_kit.profiles().reset(actor, project.id, RecipeKey(Stage.CLEANUP, soft.id))
