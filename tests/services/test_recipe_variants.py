"""Tests for the recipe each page of a run is processed by: the pin, the rules of the stage and the active recipe."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import PageKind, RuleCondition, Stage, StageState
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.values import PageStageKey, RecipeDraft, StageRun, Step
from bookreviver.services.recipe_picks import RecipePicker
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, PageStage, Project, Recipe
    from bookreviver.domain.ids import PageId
    from tests.helpers.processing import ProcessingKit

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
