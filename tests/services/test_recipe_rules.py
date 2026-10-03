"""Tests for the use cases that keep the rules of a stage: list, add, retarget and remove."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import RuleCondition, Stage
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import RecipeRuleId
from bookreviver.domain.values import RecipeDraft, RecipeKey, SliceRequest, Step
from tests.helpers.builders import new_account_id
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Project, Recipe
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
EVERYTHING: SliceRequest = SliceRequest(limit=100)


async def seed_with_variant(kit: ProcessingKit) -> tuple[Actor, Project, Recipe]:
    """Seed a project and a variant of its geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor owning the project, the project and the variant.
    :rtype: tuple[Actor, Project, Recipe]
    """
    actor, project = await kit.seed_project()
    draft = RecipeDraft(name='Plates', steps=[Step(processor_key=FAKE_KEY, params={})])
    variant = await kit.service().add_variant(actor, project.id, Stage.GEOMETRY, draft)
    return actor, project, variant


class TestAddRule:
    """Tests for adding a rule."""

    async def test_rules_are_added_after_the_others(self, fx_kit: ProcessingKit) -> None:
        """Verify each new rule is tried after the ones added before it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, variant = await seed_with_variant(fx_kit)
        for condition in (RuleCondition.PLATES, RuleCondition.COVERS, RuleCondition.ODD):
            await fx_kit.add_rule(actor, variant, condition)
        listed = await fx_kit.rules().rules(actor, project.id, Stage.GEOMETRY, EVERYTHING)
        assert [(rule.condition, rule.order) for rule in listed.items] == [
            (RuleCondition.PLATES, 0),
            (RuleCondition.COVERS, 1),
            (RuleCondition.ODD, 2),
        ]

    async def test_a_second_rule_for_a_condition_is_a_conflict(self, fx_kit: ProcessingKit) -> None:
        """Reject a rule for a condition the stage has a rule for already.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _, variant = await seed_with_variant(fx_kit)
        await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)
        with pytest.raises(ConflictError):
            await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)

    async def test_a_recipe_of_another_stage_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a rule of the cleanup stage that names a recipe of the geometry stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, variant = await seed_with_variant(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.rules().add(
                actor, project.id, RecipeKey(Stage.CLEANUP, variant.id), condition=RuleCondition.PLATES, group_label=''
            )

    async def test_a_project_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject a rule in a project the actor does not own.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        _, _, variant = await seed_with_variant(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.add_rule(Actor(account_id=new_account_id()), variant, RuleCondition.PLATES)


class TestChangeRule:
    """Tests for retargeting and removing a rule."""

    async def test_retarget_sends_the_pages_to_another_recipe_and_keeps_the_place(self, fx_kit: ProcessingKit) -> None:
        """Verify the rule names the new recipe, in the place it had.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, variant = await seed_with_variant(fx_kit)
        active = await fx_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        rule = await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)
        changed = await fx_kit.rules().retarget(actor, project.id, Stage.GEOMETRY, rule.id, active.id)
        assert (changed.recipe_id, changed.order) == (active.id, rule.order)

    async def test_remove_deletes_the_rule(self, fx_kit: ProcessingKit) -> None:
        """Verify a removed rule is gone from the listing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, variant = await seed_with_variant(fx_kit)
        rule = await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)
        await fx_kit.rules().remove(actor, project.id, Stage.GEOMETRY, rule.id)
        assert (await fx_kit.rules().rules(actor, project.id, Stage.GEOMETRY, EVERYTHING)).total == 0

    async def test_a_rule_is_found_in_its_own_stage_only(self, fx_kit: ProcessingKit) -> None:
        """Reject changing a rule through the address of another stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, variant = await seed_with_variant(fx_kit)
        rule = await fx_kit.add_rule(actor, variant, RuleCondition.PLATES)
        with pytest.raises(NotFoundError):
            await fx_kit.rules().remove(actor, project.id, Stage.CLEANUP, rule.id)

    async def test_a_missing_rule_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject changing a rule that is not stored.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, variant = await seed_with_variant(fx_kit)
        with pytest.raises(NotFoundError):
            await fx_kit.rules().retarget(actor, project.id, Stage.GEOMETRY, RecipeRuleId(uuid4()), variant.id)
