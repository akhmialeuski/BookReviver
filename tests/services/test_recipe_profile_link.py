"""Tests for the link of a recipe to its profile, and for the order a profile is saved in and replaced under."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import OrderMode, Stage
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.values import RecipeDraft, RecipeKey, SliceRequest, Step
from tests.helpers.builders import make_project
from tests.helpers.processors import SECOND_KEY, THIRD_KEY

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Project, Recipe, RecipeProfile
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

PROFILE_NAME: str = 'Photographed book'
RENAMED: str = 'Clean flatbed scan'
CLEANUP_KEY: str = 'cleanup.fake'
EVERYTHING: SliceRequest = SliceRequest(limit=100)
# The third processor has to stand after the second, so these steps break a required place
BROKEN_ORDER: tuple[Step, ...] = (Step(processor_key=THIRD_KEY), Step(processor_key=SECOND_KEY))
KEPT_ORDER: tuple[Step, ...] = (Step(processor_key=SECOND_KEY), Step(processor_key=THIRD_KEY))


async def save_profile(
    kit: ProcessingKit,
    actor: Actor,
    steps: tuple[Step, ...] = KEPT_ORDER,
    *,
    stage: Stage = Stage.GEOMETRY,
    order: OrderMode = OrderMode.USUAL,
) -> RecipeProfile:
    """Save steps as a profile of the account.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account saving the profile.
    :type actor: Actor
    :param steps: The steps of the profile.
    :type steps: tuple[Step, ...]
    :param stage: Stage of the profile.
    :type stage: Stage
    :param order: The order to save the profile in.
    :type order: OrderMode
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    draft = RecipeDraft(name=PROFILE_NAME, steps=steps, order=order)
    return await kit.profiles().save(actor, stage, draft)


async def another_book(kit: ProcessingKit, actor: Actor) -> Project:
    """Commit another project of the same account, which has opened no stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :returns: The project.
    :rtype: Project
    """
    uow = kit.uow()
    project = await uow.projects.add(make_project(owner_id=actor.account_id, title='Another book'))
    await uow.commit()
    return project


async def stored_recipe(kit: ProcessingKit, recipe: Recipe) -> Recipe:
    """Read a recipe back through a new unit of work.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param recipe: The recipe to read.
    :type recipe: Recipe
    :returns: The recipe as committed.
    :rtype: Recipe
    """
    return await kit.uow().recipes.get(recipe.id)


class TestProfileOrder:
    """Tests for the order a profile is saved in."""

    async def test_a_profile_is_saved_in_the_usual_order_unless_asked_otherwise(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify the default order of a saved profile is the usual one, and the stored profile says so.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        assert (profile.order, (await fx_ordered_kit.uow().recipe_profiles.get(profile.id)).order) == (
            OrderMode.USUAL,
            OrderMode.USUAL,
        )

    async def test_a_profile_saved_in_the_free_order_keeps_a_step_off_a_required_place(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify the free order lets the steps stand and the profile remembers that it was saved in it.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor, BROKEN_ORDER, order=OrderMode.FREE)
        stored = await fx_ordered_kit.uow().recipe_profiles.get(profile.id)
        assert ([step.processor_key for step in stored.steps], stored.order) == (
            [THIRD_KEY, SECOND_KEY],
            OrderMode.FREE,
        )

    async def test_a_profile_is_refused_a_required_place_in_the_usual_order(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Reject steps that stand where they cannot work when the profile is saved in the usual order.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_project()
        with pytest.raises(InvalidParametersError):
            await save_profile(fx_ordered_kit, actor, BROKEN_ORDER)


class TestReplaceProfile:
    """Tests for replacing the name, the steps and the order of a profile, which is how a book saves to its profile."""

    async def test_the_name_the_steps_and_the_order_are_replaced_and_the_stage_and_the_default_stay(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify what a book sends replaces what the profile had, and what it does not send stays.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        await fx_ordered_kit.profiles().set_default(actor, profile.id, is_default=True)
        draft = RecipeDraft(name=RENAMED, steps=BROKEN_ORDER, order=OrderMode.FREE)
        replaced = await fx_ordered_kit.profiles().replace(actor, profile.id, draft)
        stored = await fx_ordered_kit.uow().recipe_profiles.get(profile.id)
        assert stored == replaced
        assert (stored.name, stored.order, stored.stage, stored.is_default) == (
            RENAMED,
            OrderMode.FREE,
            Stage.GEOMETRY,
            True,
        )
        assert [step.processor_key for step in stored.steps] == [THIRD_KEY, SECOND_KEY]

    async def test_a_step_off_a_required_place_is_refused_in_the_usual_order_and_the_profile_is_unchanged(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Reject a replacement that breaks a required place, and leave the stored profile as it was.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().replace(
                actor, profile.id, RecipeDraft(name=PROFILE_NAME, steps=BROKEN_ORDER)
            )
        assert await fx_ordered_kit.uow().recipe_profiles.get(profile.id) == profile

    async def test_a_step_of_another_stage_is_refused(self, fx_ordered_kit: ProcessingKit) -> None:
        """Reject a replacement whose steps belong to another stage than the profile's.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().replace(
                actor, profile.id, RecipeDraft(name=PROFILE_NAME, steps=[Step(processor_key=CLEANUP_KEY)])
            )

    async def test_the_recipes_made_from_the_profile_are_not_changed(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify a book that applied the profile keeps its steps when the profile is replaced.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        applied = await fx_ordered_kit.profiles().apply(actor, project.id, profile.id, activate=False)
        await fx_ordered_kit.profiles().replace(
            actor, profile.id, RecipeDraft(name=RENAMED, steps=[Step(processor_key=SECOND_KEY)])
        )
        assert await stored_recipe(fx_ordered_kit, applied.recipe) == applied.recipe

    async def test_a_profile_of_another_account_is_not_found(self, fx_ordered_kit: ProcessingKit) -> None:
        """Reject replacing a profile the account does not own.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        owner, _ = await fx_ordered_kit.seed_project()
        stranger, _ = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, owner)
        with pytest.raises(NotFoundError):
            await fx_ordered_kit.profiles().replace(stranger, profile.id, RecipeDraft(name=RENAMED, steps=KEPT_ORDER))


class TestRecipeLink:
    """Tests for the profile a recipe of a book remembers it was made from."""

    async def test_an_applied_profile_makes_a_recipe_linked_to_it(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify the variant a profile makes names the profile, and keeps the link when it becomes the active recipe.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        variant = (await fx_ordered_kit.profiles().apply(actor, project.id, profile.id, activate=False)).recipe
        assert (await stored_recipe(fx_ordered_kit, variant)).profile_id == profile.id
        active = (await fx_ordered_kit.profiles().apply(actor, project.id, profile.id, activate=True)).recipe
        assert (active.active, (await stored_recipe(fx_ordered_kit, active)).profile_id) == (True, profile.id)

    async def test_the_default_profile_of_a_new_book_is_the_profile_of_its_first_recipe(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify the recipe a stage starts with from the default profile is linked to that profile.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        await fx_ordered_kit.profiles().set_default(actor, profile.id, is_default=True)
        fresh = await another_book(fx_ordered_kit, actor)
        recipe = await fx_ordered_kit.service().recipe(actor, fresh.id, Stage.GEOMETRY)
        assert recipe.profile_id == profile.id

    async def test_a_recipe_can_be_linked_to_a_profile_and_unlinked_again(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify linking records the profile, changes neither the steps nor the time, and None unlinks.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        recipe = await fx_ordered_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        key = RecipeKey(Stage.GEOMETRY, recipe.id)
        linked = await fx_ordered_kit.profiles().link(actor, project.id, key, profile.id)
        assert linked == evolve(recipe, profile_id=profile.id)
        assert await stored_recipe(fx_ordered_kit, recipe) == linked
        unlinked = await fx_ordered_kit.profiles().link(actor, project.id, key, None)
        assert unlinked == recipe

    async def test_saving_the_steps_of_a_linked_recipe_keeps_its_link(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify a recipe that is edited is still made from its profile, which is how the book is compared with it.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        applied = (await fx_ordered_kit.profiles().apply(actor, project.id, profile.id, activate=True)).recipe
        edited = RecipeDraft(
            name=applied.name, steps=[Step(processor_key=SECOND_KEY, enabled=False), Step(processor_key=THIRD_KEY)]
        )
        saved = await fx_ordered_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, edited)
        assert saved.profile_id == profile.id

    async def test_deleting_a_profile_unlinks_the_recipes_made_from_it(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify the recipes outlive their profile and no longer name it.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        profile = await save_profile(fx_ordered_kit, actor)
        applied = (await fx_ordered_kit.profiles().apply(actor, project.id, profile.id, activate=False)).recipe
        await fx_ordered_kit.profiles().remove(actor, profile.id)
        assert (await stored_recipe(fx_ordered_kit, applied)).profile_id is None

    async def test_a_profile_of_another_stage_is_refused(self, fx_ordered_kit: ProcessingKit) -> None:
        """Reject linking a recipe to a profile whose steps belong to another stage.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, project = await fx_ordered_kit.seed_project()
        cleanup = await save_profile(fx_ordered_kit, actor, (Step(processor_key=CLEANUP_KEY),), stage=Stage.CLEANUP)
        recipe = await fx_ordered_kit.service().recipe(actor, project.id, Stage.GEOMETRY)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().link(actor, project.id, RecipeKey(Stage.GEOMETRY, recipe.id), cleanup.id)

    async def test_a_profile_or_a_book_of_another_account_is_not_found(self, fx_ordered_kit: ProcessingKit) -> None:
        """Reject linking with a profile the account does not own, and linking a recipe of a book it does not own.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        owner, project = await fx_ordered_kit.seed_project()
        stranger, foreign_project = await fx_ordered_kit.seed_project()
        theirs = await save_profile(fx_ordered_kit, stranger)
        recipe = await fx_ordered_kit.service().recipe(owner, project.id, Stage.GEOMETRY)
        key = RecipeKey(Stage.GEOMETRY, recipe.id)
        with pytest.raises(NotFoundError):
            await fx_ordered_kit.profiles().link(owner, project.id, key, theirs.id)
        with pytest.raises(NotFoundError):
            await fx_ordered_kit.profiles().link(stranger, foreign_project.id, key, theirs.id)
        assert (await stored_recipe(fx_ordered_kit, recipe)).profile_id is None
