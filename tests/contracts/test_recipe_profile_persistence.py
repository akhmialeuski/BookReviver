"""Contract of the persistence port of the recipe profiles of the accounts.

Every test runs against each adapter registered in the conftest, the in-memory one and the SQL one, so both keep the
promises of the port: the steps come back in their order with their parameters and their switch, a profile keeps the
order it was saved in, a listing holds the profiles of one account in the order of the stages, an account has one
default profile for each stage, and a recipe that refers to a profile loses the reference when the profile is deleted.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import OrderMode, RecipeKind, Stage
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.values import Step
from tests.helpers.builders import make_project, make_recipe, make_recipe_profile

if TYPE_CHECKING:
    from bookreviver.domain.entities import RecipeProfile
    from bookreviver.ports.persistence import UnitOfWork
    from tests.contracts.conftest import OwnerFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

CLEAN_SCAN_NAME: str = 'Clean flatbed scan'
FIRST_MINUTE: int = 1
SECOND_MINUTE: int = 2
THIRD_MINUTE: int = 3
BOOKS_OF_A_PROFILE: int = 2
# The profiles of the account in the test of the defaults: two defaults of two stages and two that are not
ACCOUNT_PROFILE_COUNT: int = 4
STRENGTH: str = 'strength'
STEPS: tuple[Step, ...] = (
    Step(processor_key='geometry.crop', params={}),
    Step(processor_key='geometry.deskew', params={'max_angle_deg': 7.5}, enabled=False),
    Step(processor_key='geometry.perspective', params={STRENGTH: 3}),
)


async def _store(uow_factory: UnitOfWorkFactory, *profiles: RecipeProfile) -> UnitOfWork:
    """Store profiles in one block, and open a new unit of work to read them back through.

    :param uow_factory: Function opening a new unit of work of the backend under test.
    :type uow_factory: UnitOfWorkFactory
    :param profiles: The profiles to store.
    :type profiles: RecipeProfile
    :returns: A new unit of work with no block open.
    :rtype: UnitOfWork
    """
    uow = await uow_factory()
    async with uow.change():
        await uow.recipe_profiles.add_many(profiles)
    return await uow_factory()


class TestRecipeProfileRepository:
    """Tests for the recipe profiles of the accounts."""

    async def test_a_profile_reads_back_with_its_steps_in_order(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the order, the parameters and the switch of every step survive a round trip.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        profile = make_recipe_profile(account_id=await fx_new_owner(), steps=STEPS)
        uow = await _store(fx_uow_factory, profile)
        assert await uow.recipe_profiles.get(profile.id) == profile

    async def test_a_listing_holds_the_profiles_of_one_account_in_the_order_of_the_stages(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the stages come in pipeline order, the profiles of a stage by creation, and no other account's.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        late_geometry = make_recipe_profile(account_id=account_id, minutes=THIRD_MINUTE)
        early_geometry = make_recipe_profile(account_id=account_id, minutes=FIRST_MINUTE)
        page_split = make_recipe_profile(
            account_id=account_id,
            stage=Stage.PAGE_SPLIT,
            steps=(Step(processor_key='split.none'),),
            minutes=SECOND_MINUTE,
        )
        foreign = make_recipe_profile(account_id=await fx_new_owner())
        uow = await _store(fx_uow_factory, late_geometry, foreign, early_geometry, page_split)
        assert await uow.recipe_profiles.list_for_account(account_id) == [page_split, early_geometry, late_geometry]

    async def test_a_listing_may_be_limited_to_one_stage(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a listing of one stage leaves out the profiles of the others.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        geometry = make_recipe_profile(account_id=account_id)
        cleanup = make_recipe_profile(account_id=account_id, stage=Stage.CLEANUP)
        uow = await _store(fx_uow_factory, geometry, cleanup)
        assert await uow.recipe_profiles.list_for_account(account_id, Stage.CLEANUP) == [cleanup]

    async def test_an_account_has_one_default_profile_for_each_stage(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a second default profile of a stage, which two requests choosing at once could otherwise create.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        uow = await _store(fx_uow_factory, make_recipe_profile(account_id=account_id, is_default=True))
        with pytest.raises(ConflictError):
            async with uow.change():
                await uow.recipe_profiles.add(make_recipe_profile(account_id=account_id, is_default=True))

    async def test_defaults_of_other_stages_and_accounts_do_not_clash(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify each stage of each account has its own default, and any number of profiles is not the default.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        other_id = await fx_new_owner()
        uow = await _store(
            fx_uow_factory,
            make_recipe_profile(account_id=account_id, is_default=True),
            make_recipe_profile(account_id=account_id, stage=Stage.CLEANUP, is_default=True),
            make_recipe_profile(account_id=other_id, is_default=True),
            make_recipe_profile(account_id=account_id),
            make_recipe_profile(account_id=account_id),
        )
        assert len(await uow.recipe_profiles.list_for_account(account_id)) == ACCOUNT_PROFILE_COUNT

    async def test_find_default_returns_the_default_of_the_stage_of_the_account(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the default is found for its own account and stage, and for no other.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        other_id = await fx_new_owner()
        chosen = make_recipe_profile(account_id=account_id, is_default=True)
        uow = await _store(fx_uow_factory, chosen, make_recipe_profile(account_id=account_id))
        assert await uow.recipe_profiles.find_default(account_id, Stage.GEOMETRY) == chosen
        assert await uow.recipe_profiles.find_default(account_id, Stage.CLEANUP) is None
        assert await uow.recipe_profiles.find_default(other_id, Stage.GEOMETRY) is None

    async def test_the_default_moves_when_the_old_one_is_demoted_first(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a demotion followed by a promotion in one transaction moves the default to another profile.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        old = make_recipe_profile(account_id=account_id, is_default=True)
        new = make_recipe_profile(account_id=account_id, minutes=FIRST_MINUTE)
        uow = await _store(fx_uow_factory, old, new)
        async with uow.change():
            await uow.recipe_profiles.update(evolve(old, is_default=False))
            promoted = await uow.recipe_profiles.update(evolve(new, is_default=True))
        assert await (await fx_uow_factory()).recipe_profiles.find_default(account_id, Stage.GEOMETRY) == promoted

    async def test_update_replaces_the_stored_state(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a renamed profile reads back under its new name.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        profile = make_recipe_profile(account_id=await fx_new_owner())
        uow = await _store(fx_uow_factory, profile)
        renamed = evolve(profile, name=CLEAN_SCAN_NAME)
        async with uow.change():
            await uow.recipe_profiles.update(renamed)
        assert await (await fx_uow_factory()).recipe_profiles.get(profile.id) == renamed

    async def test_delete_removes_only_that_profile(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a deleted profile is gone and the account's other profiles stay.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        gone = make_recipe_profile(account_id=account_id)
        kept = make_recipe_profile(account_id=account_id, minutes=FIRST_MINUTE)
        uow = await _store(fx_uow_factory, gone, kept)
        async with uow.change():
            await uow.recipe_profiles.delete(gone.id)
        reading = await fx_uow_factory()
        assert await reading.recipe_profiles.list_for_account(account_id) == [kept]
        with pytest.raises(NotFoundError):
            await reading.recipe_profiles.get(gone.id)

    async def test_a_profile_reads_back_in_the_order_it_was_saved_in(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the free order survives a round trip, and the usual order is what a profile has by default.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        free = evolve(make_recipe_profile(account_id=account_id), order=OrderMode.FREE)
        usual = make_recipe_profile(account_id=account_id, minutes=FIRST_MINUTE)
        uow = await _store(fx_uow_factory, free, usual)
        assert [(await uow.recipe_profiles.get(profile.id)).order for profile in (free, usual)] == [
            OrderMode.FREE,
            OrderMode.USUAL,
        ]

    async def test_a_recipe_reads_back_with_the_profile_it_was_made_from(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the reference of a recipe to a profile is stored, and a recipe made from none reads back with none.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        profile = make_recipe_profile(account_id=owner_id)
        project = make_project(owner_id=owner_id)
        linked = evolve(make_recipe(project_id=project.id), profile_id=profile.id)
        unlinked = make_recipe(project_id=project.id, stage=Stage.CLEANUP)
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.recipe_profiles.add(profile)
            await uow.projects.add(project)
        async with uow.change_book(project.id):
            await uow.recipes.add_many([linked, unlinked])
        reading = await fx_uow_factory()
        assert [(await reading.recipes.get(recipe.id)).profile_id for recipe in (linked, unlinked)] == [
            profile.id,
            None,
        ]

    async def test_a_recipe_cannot_refer_to_a_profile_that_is_not_stored(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a recipe whose profile does not exist, as a foreign key does.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        project = make_project(owner_id=await fx_new_owner())
        stray = evolve(
            make_recipe(project_id=project.id),
            profile_id=make_recipe_profile(account_id=project.owner_id).id,
        )
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.projects.add(project)
        with pytest.raises(NotFoundError):
            async with uow.change_book(project.id):
                await uow.recipes.add(stray)

    async def test_deleting_a_profile_empties_the_reference_of_its_recipes_and_keeps_them(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a recipe made from a profile outlives it, and no longer names it.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        profile = make_recipe_profile(account_id=owner_id)
        project = make_project(owner_id=owner_id)
        recipe = evolve(make_recipe(project_id=project.id), profile_id=profile.id)
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.recipe_profiles.add(profile)
            await uow.projects.add(project)
        async with uow.change_book(project.id):
            await uow.recipes.add(recipe)
        deleting = await fx_uow_factory()
        async with deleting.change():
            await deleting.recipe_profiles.delete(profile.id)
        assert (await (await fx_uow_factory()).recipes.get(recipe.id)) == evolve(recipe, profile_id=None)

    async def test_the_books_of_a_profile_are_counted_once_each(
        self, fx_uow_factory: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a book with two recipes of a profile counts once, an unused profile has no entry, others are left out.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the backend accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        owner_id = await fx_new_owner()
        used = make_recipe_profile(account_id=owner_id)
        unused = make_recipe_profile(account_id=owner_id, minutes=FIRST_MINUTE)
        other = make_recipe_profile(account_id=owner_id, minutes=SECOND_MINUTE)
        first_book = make_project(owner_id=owner_id)
        second_book = make_project(owner_id=owner_id, title='Second')
        first_recipes = [
            evolve(make_recipe(project_id=first_book.id), profile_id=used.id),
            evolve(make_recipe(project_id=first_book.id, kind=RecipeKind.BLANK), profile_id=used.id),
        ]
        second_recipes = [
            evolve(make_recipe(project_id=second_book.id), profile_id=used.id),
            evolve(make_recipe(project_id=second_book.id, stage=Stage.CLEANUP), profile_id=other.id),
            make_recipe(project_id=second_book.id, stage=Stage.PAGE_SPLIT),
        ]
        uow = await fx_uow_factory()
        async with uow.change():
            await uow.recipe_profiles.add_many([used, unused, other])
            await uow.projects.add_many([first_book, second_book])
        async with uow.change_book(first_book.id):
            await uow.recipes.add_many(first_recipes)
        async with uow.change_book(second_book.id):
            await uow.recipes.add_many(second_recipes)
        counted = await (await fx_uow_factory()).recipe_profiles.count_books([used.id, unused.id])
        assert dict(counted) == {used.id: BOOKS_OF_A_PROFILE}

    async def test_counting_the_books_of_no_profile_gives_nothing(self, fx_uow_factory: UnitOfWorkFactory) -> None:
        """Verify an empty request is answered with an empty count rather than an error.

        :param fx_uow_factory: Function opening a new unit of work of the backend under test.
        :type fx_uow_factory: UnitOfWorkFactory
        """
        assert dict(await (await fx_uow_factory()).recipe_profiles.count_books([])) == {}
