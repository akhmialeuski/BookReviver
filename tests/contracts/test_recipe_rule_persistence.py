"""Contract of the persistence ports for the rules of a stage, the pin of a page stage and the group of a page.

Every test runs against each adapter registered in the conftest, so the in-memory and the SQL adapter keep the same
promises: the order of the rules, the one rule for each condition, the actions of the foreign keys, and the counts of
the pages each recipe processed.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import PageOrigin, RuleCondition, Stage
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import ProjectId
from bookreviver.domain.stage_summaries import VariantTally
from bookreviver.domain.values import PageStageKey
from tests.helpers.builders import (
    make_page,
    make_page_stage,
    make_pinned_stage,
    make_project,
    make_recipe,
    make_recipe_rule,
    new_account_id,
)

if TYPE_CHECKING:
    from bookreviver.domain.entities import Recipe
    from bookreviver.ports.persistence import UnitOfWork
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

PLATES_NAME: str = 'Plates'
GROUP: str = 'Engravings'
OTHER_GROUP: str = 'Maps'
FIRST_ORDER: int = 0
SECOND_ORDER: int = 1
THIRD_ORDER: int = 2
TWO_PAGES: int = 2


async def _store_recipes(
    uow_factory: UnitOfWorkFactory, new_owner: OwnerFactory
) -> tuple[UnitOfWork, ProjectId, Recipe, Recipe]:
    """Store a project with the active recipe of the geometry stage and a variant of it, and commit.

    :param uow_factory: Function opening a new unit of work of the backend under test.
    :type uow_factory: UnitOfWorkFactory
    :param new_owner: Function creating an account the backend accepts as an owner.
    :type new_owner: OwnerFactory
    :returns: A new unit of work, the project, the active recipe and the variant.
    :rtype: tuple[UnitOfWork, ProjectId, Recipe, Recipe]
    """
    uow = await uow_factory()
    project = make_project(owner_id=await new_owner())
    active = make_recipe(project_id=project.id, active=True)
    variant = make_recipe(project_id=project.id, name=PLATES_NAME, minutes=1)
    await uow.projects.add(project)
    await uow.recipes.add_many([active, variant])
    await uow.commit()
    return await uow_factory(), project.id, active, variant


class TestRecipeRuleRepository:
    """Tests for the rules that send pages to recipes of a stage."""

    async def test_rules_are_listed_in_the_order_they_are_tried(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the rules of one stage come back by their order, whatever order they were stored in.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, _, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        later = make_recipe_rule(recipe=variant, condition=RuleCondition.COVERS, order=THIRD_ORDER)
        first = make_recipe_rule(recipe=variant, condition=RuleCondition.PLATES, order=FIRST_ORDER)
        second = make_recipe_rule(recipe=variant, condition=RuleCondition.ODD, order=SECOND_ORDER)
        await uow.recipe_rules.add_many([later, first, second])
        await uow.commit()
        listed = await (await fx_uow_factory()).recipe_rules.list_for_stage(project_id, Stage.GEOMETRY)
        assert listed == [first, second, later]

    async def test_rules_of_another_stage_or_project_are_left_out(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a listing holds the rules of its stage of its project only.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, _, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        other_project = make_project(owner_id=await fx_new_owner())
        foreign = make_recipe(project_id=other_project.id)
        cleanup = make_recipe(project_id=project_id, stage=Stage.CLEANUP, active=True)
        await uow.projects.add(other_project)
        await uow.recipes.add_many([foreign, cleanup])
        own = make_recipe_rule(recipe=variant)
        await uow.recipe_rules.add_many([own, make_recipe_rule(recipe=foreign), make_recipe_rule(recipe=cleanup)])
        await uow.commit()
        listed = await (await fx_uow_factory()).recipe_rules.list_for_stage(project_id, Stage.GEOMETRY)
        assert listed == [own]

    async def test_a_stage_has_one_rule_for_each_condition(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a second rule for a condition, which could never be reached behind the first.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, _, active, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        await uow.recipe_rules.add(make_recipe_rule(recipe=variant, condition=RuleCondition.PLATES))
        with pytest.raises(ConflictError):
            await uow.recipe_rules.add(
                make_recipe_rule(recipe=active, condition=RuleCondition.PLATES, order=SECOND_ORDER)
            )

    async def test_a_stage_has_one_rule_for_each_group_label(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify two groups may each have a rule, and one group may not have two.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, _, active, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        await uow.recipe_rules.add(make_recipe_rule(recipe=variant, condition=RuleCondition.GROUP, group_label=GROUP))
        await uow.recipe_rules.add(
            make_recipe_rule(recipe=variant, condition=RuleCondition.GROUP, group_label=OTHER_GROUP, order=SECOND_ORDER)
        )
        with pytest.raises(ConflictError):
            await uow.recipe_rules.add(
                make_recipe_rule(recipe=active, condition=RuleCondition.GROUP, group_label=GROUP, order=THIRD_ORDER)
            )

    async def test_a_rule_names_a_stored_recipe_of_a_stored_project(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a rule whose recipe, or whose project, is not stored.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, _, _, _ = await _store_recipes(fx_uow_factory, fx_new_owner)
        unstored = make_recipe(project_id=ProjectId(new_account_id()))
        with pytest.raises(NotFoundError):
            await uow.recipe_rules.add(make_recipe_rule(recipe=unstored))

    async def test_the_recipe_of_a_rule_can_be_replaced(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify an updated rule keeps its place and names the new recipe.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, active, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        rule = make_recipe_rule(recipe=variant, order=SECOND_ORDER)
        await uow.recipe_rules.add(rule)
        await uow.recipe_rules.update(evolve(rule, recipe_id=active.id))
        await uow.commit()
        listed = await (await fx_uow_factory()).recipe_rules.list_for_stage(project_id, Stage.GEOMETRY)
        assert listed == [evolve(rule, recipe_id=active.id)]

    async def test_deleting_a_recipe_deletes_its_rules(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the rules that name a recipe go with it, and the rules of other recipes stay.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, active, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        kept = make_recipe_rule(recipe=active, condition=RuleCondition.ODD)
        await uow.recipe_rules.add_many([make_recipe_rule(recipe=variant), kept])
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.recipes.delete(variant.id)
        await uow.commit()
        listed = await (await fx_uow_factory()).recipe_rules.list_for_stage(project_id, Stage.GEOMETRY)
        assert listed == [kept]

    async def test_deleting_the_project_deletes_its_rules(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the rules go with their project.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, _, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        rule = make_recipe_rule(recipe=variant)
        await uow.recipe_rules.add(rule)
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.projects.delete(project_id)
        await uow.commit()
        with pytest.raises(NotFoundError):
            await (await fx_uow_factory()).recipe_rules.get(rule.id)


class TestPinnedPageStage:
    """Tests for the pin of the recipe of a page stage."""

    async def test_the_pin_survives_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a pinned record reads back pinned, and an unpinned one unpinned.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, _, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        pinned, loose = (
            make_page(project_id=project_id, order_key='a0'),
            make_page(project_id=project_id, order_key='a1'),
        )
        await uow.pages.add_many([pinned, loose])
        await uow.page_stages.save(make_pinned_stage(page_id=pinned.id, recipe_id=variant.id))
        await uow.page_stages.save(make_page_stage(page_id=loose.id, recipe_id=variant.id))
        await uow.commit()
        stages = (await fx_uow_factory()).page_stages
        found = [await stages.get(PageStageKey(page.id, Stage.GEOMETRY)) for page in (pinned, loose)]
        assert [record.pinned for record in found] == [True, False]

    async def test_a_pin_holds_nothing_once_its_recipe_is_deleted(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the record loses the recipe, so the pin that is left names no recipe to run a page by.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, _, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        page = make_page(project_id=project_id)
        await uow.pages.add(page)
        await uow.page_stages.save(make_pinned_stage(page_id=page.id, recipe_id=variant.id))
        await uow.commit()
        uow = await fx_uow_factory()
        await uow.recipes.delete(variant.id)
        await uow.commit()
        kept = await (await fx_uow_factory()).page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        assert kept.pinned_recipe_id is None


class TestVariantTally:
    """Tests for the count of the pages each recipe processed."""

    async def test_pages_are_counted_for_the_recipe_that_processed_them(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify each stage and recipe gets its own count, and placeholders and recipe-less records are left out.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, active, variant = await _store_recipes(fx_uow_factory, fx_new_owner)
        pages = [
            evolve(make_page(project_id=project_id, order_key=f'a{index}'), origin=PageOrigin.BLANK)
            for index in range(TWO_PAGES + 1)
        ]
        placeholder = make_page(project_id=project_id, order_key='b0')
        await uow.pages.add_many([*pages, placeholder])
        await uow.page_stages.save(make_page_stage(page_id=pages[0].id, recipe_id=active.id))
        await uow.page_stages.save(make_pinned_stage(page_id=pages[1].id, recipe_id=variant.id))
        await uow.page_stages.save(make_page_stage(page_id=pages[2].id, recipe_id=variant.id))
        await uow.page_stages.save(make_page_stage(page_id=placeholder.id, recipe_id=variant.id))
        await uow.page_stages.save(make_page_stage(page_id=pages[0].id, stage=Stage.CLEANUP))
        await uow.commit()
        tally = await (await fx_uow_factory()).page_stages.variant_tally(project_id)
        assert sorted(tally, key=lambda one: one.pages) == sorted(
            [
                VariantTally(stage=Stage.GEOMETRY, recipe_id=active.id, pages=1),
                VariantTally(stage=Stage.GEOMETRY, recipe_id=variant.id, pages=TWO_PAGES),
            ],
            key=lambda one: one.pages,
        )

    async def test_a_project_without_records_has_no_counts(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a project no stage has run in gives an empty tally.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, _, _ = await _store_recipes(fx_uow_factory, fx_new_owner)
        assert await uow.page_stages.variant_tally(project_id) == []


class TestPageGroup:
    """Tests for the label of the group a page is in."""

    async def test_the_group_label_survives_the_store(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a page reads back with its group label, which an update can change and clear.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        uow, project_id, _, _ = await _store_recipes(fx_uow_factory, fx_new_owner)
        page = make_page(project_id=project_id, group_label=GROUP)
        await uow.pages.add(page)
        await uow.commit()
        uow = await fx_uow_factory()
        grouped = await uow.pages.get(page.id)
        assert grouped.group_label == GROUP
        await uow.pages.update(evolve(grouped, group_label=''))
        await uow.commit()
        assert (await (await fx_uow_factory()).pages.get(page.id)).group_label == ''
