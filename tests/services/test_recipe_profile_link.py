"""Tests for the link of a recipe to its profile, and for the order a profile is saved in and replaced under."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import OrderMode, RecipeKind, Stage
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.values import ProfileDraft, RecipeDraft, RecipeKey, SliceRequest, Step
from tests.helpers.builders import make_record, open_for
from tests.helpers.processing import account_books
from tests.helpers.processors import SECOND_KEY, THIRD_KEY
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, BookRecord, Recipe, RecipeProfile
    from bookreviver.domain.ids import BookId
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
    draft = ProfileDraft(name=PROFILE_NAME, steps=steps, order=order)
    return await kit.profiles().save(actor, stage, draft)


async def another_book(kit: ProcessingKit, actor: Actor) -> BookRecord:
    """Commit another book of the same account, which has opened no stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :returns: The book.
    :rtype: BookRecord
    """
    book_record = make_record(title='Another book')
    await store_book(kit.uow(), book_record, owner=actor.account_id)
    return book_record


async def stored_recipe(kit: ProcessingKit, book_id: BookId, recipe: Recipe) -> Recipe:
    """Read a recipe back through a new unit of work.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :param recipe: The recipe to read.
    :type recipe: Recipe
    :returns: The recipe as committed.
    :rtype: Recipe
    """
    return await kit.uow().read_book(book_id, lambda repos: repos.recipes.get(recipe.id))


class TestProfileOrder:
    """Tests for the order a profile is saved in."""

    async def test_a_profile_is_saved_in_the_usual_order_unless_asked_otherwise(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify the default order of a saved profile is the usual one, and the stored profile says so.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        stored = await fx_ordered_kit.uow().read(lambda repos: repos.recipe_profiles.get(profile.id))
        assert (profile.order, stored.order) == (OrderMode.USUAL, OrderMode.USUAL)

    async def test_a_profile_saved_in_the_free_order_keeps_a_step_off_a_required_place(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify the free order lets the steps stand and the profile remembers that it was saved in it.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor, BROKEN_ORDER, order=OrderMode.FREE)
        stored = await fx_ordered_kit.uow().read(lambda repos: repos.recipe_profiles.get(profile.id))
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
        actor, _ = await fx_ordered_kit.seed_record()
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
        actor, _ = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        await fx_ordered_kit.profiles().set_default(profile, is_default=True)
        draft = ProfileDraft(name=RENAMED, steps=BROKEN_ORDER, order=OrderMode.FREE)
        replaced = await fx_ordered_kit.profiles().replace(profile, draft)
        stored = await fx_ordered_kit.uow().read(lambda repos: repos.recipe_profiles.get(profile.id))
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
        actor, _ = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().replace(profile, ProfileDraft(name=PROFILE_NAME, steps=BROKEN_ORDER))
        stored = await fx_ordered_kit.uow().read(lambda repos: repos.recipe_profiles.get(profile.id))
        assert stored == profile

    async def test_a_step_of_another_stage_is_refused(self, fx_ordered_kit: ProcessingKit) -> None:
        """Reject a replacement whose steps belong to another stage than the profile's.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().replace(
                profile, ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=CLEANUP_KEY)])
            )

    async def test_the_recipes_made_from_the_profile_are_not_changed(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify a book that applied the profile keeps its steps when the profile is replaced.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, book_record = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        applied = await fx_ordered_kit.profiles().apply(open_for(actor, book_record.id), profile, kind=RecipeKind.TEXT)
        await fx_ordered_kit.profiles().replace(
            profile, ProfileDraft(name=RENAMED, steps=[Step(processor_key=SECOND_KEY)])
        )
        assert await stored_recipe(fx_ordered_kit, book_record.id, applied.recipe) == applied.recipe


class TestRecipeLink:
    """Tests for the profile a recipe of a book remembers it was made from."""

    async def test_an_applied_profile_makes_a_recipe_linked_to_it(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify the recipe a profile is applied to names the profile.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, book_record = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        applied = (
            await fx_ordered_kit.profiles().apply(open_for(actor, book_record.id), profile, kind=RecipeKind.BLANK)
        ).recipe
        assert (await stored_recipe(fx_ordered_kit, book_record.id, applied)).profile_id == profile.id

    async def test_the_default_profile_of_a_new_book_is_the_profile_of_its_first_recipe(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify the recipe a stage starts with from the default profile is linked to that profile.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        await fx_ordered_kit.profiles().set_default(profile, is_default=True)
        fresh = await another_book(fx_ordered_kit, actor)
        recipe = await fx_ordered_kit.recipe_of(actor, fresh, Stage.GEOMETRY)
        assert recipe.profile_id == profile.id

    async def test_a_recipe_can_be_linked_to_a_profile_and_unlinked_again(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify linking records the profile, changes neither the steps nor the time, and None unlinks.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, book_record = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        recipe = await fx_ordered_kit.recipe_of(actor, book_record, Stage.GEOMETRY)
        key = RecipeKey(Stage.GEOMETRY, recipe.id)
        linked = await fx_ordered_kit.profiles().link(open_for(actor, book_record.id), key, profile)
        assert linked == evolve(recipe, profile_id=profile.id)
        assert await stored_recipe(fx_ordered_kit, book_record.id, recipe) == linked
        unlinked = await fx_ordered_kit.profiles().link(open_for(actor, book_record.id), key, None)
        assert unlinked == recipe

    async def test_saving_the_steps_of_a_linked_recipe_keeps_its_link(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify a recipe that is edited is still made from its profile, which is how the book is compared with it.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, book_record = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        applied = (
            await fx_ordered_kit.profiles().apply(open_for(actor, book_record.id), profile, kind=RecipeKind.TEXT)
        ).recipe
        edited = RecipeDraft(steps=[Step(processor_key=SECOND_KEY, enabled=False), Step(processor_key=THIRD_KEY)])
        saved = await fx_ordered_kit.service().save_recipe(
            open_for(actor, book_record.id), RecipeKey(Stage.GEOMETRY, applied.id), edited
        )
        assert saved.profile_id == profile.id

    async def test_deleting_a_profile_unlinks_the_recipes_made_from_it(self, fx_ordered_kit: ProcessingKit) -> None:
        """Verify the recipes outlive their profile and no longer name it.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, book_record = await fx_ordered_kit.seed_record()
        profile = await save_profile(fx_ordered_kit, actor)
        applied = (
            await fx_ordered_kit.profiles().apply(open_for(actor, book_record.id), profile, kind=RecipeKind.TEXT)
        ).recipe
        await fx_ordered_kit.profiles().remove(profile, await account_books(fx_ordered_kit, actor))
        assert (await stored_recipe(fx_ordered_kit, book_record.id, applied)).profile_id is None

    async def test_a_profile_of_another_stage_is_refused(self, fx_ordered_kit: ProcessingKit) -> None:
        """Reject linking a recipe to a profile whose steps belong to another stage.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, book_record = await fx_ordered_kit.seed_record()
        cleanup = await save_profile(fx_ordered_kit, actor, (Step(processor_key=CLEANUP_KEY),), stage=Stage.CLEANUP)
        recipe = await fx_ordered_kit.recipe_of(actor, book_record, Stage.GEOMETRY)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().link(
                open_for(actor, book_record.id), RecipeKey(Stage.GEOMETRY, recipe.id), cleanup
            )
