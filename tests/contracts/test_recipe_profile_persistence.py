"""Contract of the persistence port of the recipe profiles of the accounts.

Every test runs against the SQL adapter, so it keeps the promises of the port: the steps come back in their order with
their parameters and their switch, a profile keeps the order it was saved in, a listing holds the profiles of one
account in the order of the stages, an account has one default profile for each stage, and a recipe of a book keeps
the identifier of the profile it was made from.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.domain.enums import OrderMode, Stage
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.values import Step
from tests.helpers.builders import make_recipe, make_recipe_profile, make_record
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import RecipeProfile
    from bookreviver.ports.persistence import BookRepositories, CatalogueRepositories, UnitOfWork
    from tests.contracts.conftest import OwnerFactory
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

CLEAN_SCAN_NAME: str = 'Clean flatbed scan'
FIRST_MINUTE: int = 1
SECOND_MINUTE: int = 2
THIRD_MINUTE: int = 3
# The profiles of the account in the test of the defaults: two defaults of two stages and two that are not
ACCOUNT_PROFILE_COUNT: int = 4
STRENGTH: str = 'strength'
STEPS: tuple[Step, ...] = (
    Step(processor_key='geometry.crop', params={}),
    Step(processor_key='geometry.deskew', params={'max_angle_deg': 7.5}, enabled=False),
    Step(processor_key='geometry.perspective', params={STRENGTH: 3}),
)


async def _store(uow_factory: UnitOfWorkFactory, *profiles: RecipeProfile) -> UnitOfWork:
    """Store profiles in one change, and open a new unit of work to read them back through.

    :param uow_factory: Function opening a new unit of work over the catalogue and the books of the test.
    :type uow_factory: UnitOfWorkFactory
    :param profiles: The profiles to store.
    :type profiles: RecipeProfile
    :returns: A new unit of work.
    :rtype: UnitOfWork
    """
    await uow_factory().change(lambda repos: repos.recipe_profiles.add_many(profiles))
    return uow_factory()


class TestRecipeProfileRepository:
    """Tests for the recipe profiles of the accounts."""

    async def test_a_profile_reads_back_with_its_steps_in_order(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the order, the parameters and the switch of every step survive a round trip.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        profile = make_recipe_profile(account_id=await fx_new_owner(), steps=STEPS)
        uow = await _store(fx_uow, profile)
        assert await uow.read(lambda repos: repos.recipe_profiles.get(profile.id)) == profile

    async def test_a_listing_holds_the_profiles_of_one_account_in_the_order_of_the_stages(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the stages come in pipeline order, the profiles of a stage by creation, and no other account's.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
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
        uow = await _store(fx_uow, late_geometry, foreign, early_geometry, page_split)
        listed = await uow.read(lambda repos: repos.recipe_profiles.list_for_account(account_id))
        assert listed == [page_split, early_geometry, late_geometry]

    async def test_a_listing_may_be_limited_to_one_stage(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a listing of one stage leaves out the profiles of the others.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        geometry = make_recipe_profile(account_id=account_id)
        cleanup = make_recipe_profile(account_id=account_id, stage=Stage.CLEANUP)
        uow = await _store(fx_uow, geometry, cleanup)
        listed = await uow.read(lambda repos: repos.recipe_profiles.list_for_account(account_id, Stage.CLEANUP))
        assert listed == [cleanup]

    async def test_an_account_has_one_default_profile_for_each_stage(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Reject a second default profile of a stage, which two requests choosing at once could otherwise create.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        uow = await _store(fx_uow, make_recipe_profile(account_id=account_id, is_default=True))
        second_default = make_recipe_profile(account_id=account_id, is_default=True)
        with pytest.raises(ConflictError):
            await uow.change(lambda repos: repos.recipe_profiles.add(second_default))

    async def test_defaults_of_other_stages_and_accounts_do_not_clash(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify each stage of each account has its own default, and any number of profiles is not the default.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        other_id = await fx_new_owner()
        uow = await _store(
            fx_uow,
            make_recipe_profile(account_id=account_id, is_default=True),
            make_recipe_profile(account_id=account_id, stage=Stage.CLEANUP, is_default=True),
            make_recipe_profile(account_id=other_id, is_default=True),
            make_recipe_profile(account_id=account_id),
            make_recipe_profile(account_id=account_id),
        )
        listed = await uow.read(lambda repos: repos.recipe_profiles.list_for_account(account_id))
        assert len(listed) == ACCOUNT_PROFILE_COUNT

    async def test_find_default_returns_the_default_of_the_stage_of_the_account(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the default is found for its own account and stage, and for no other.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        other_id = await fx_new_owner()
        chosen = make_recipe_profile(account_id=account_id, is_default=True)
        uow = await _store(fx_uow, chosen, make_recipe_profile(account_id=account_id))

        def find_defaults(
            repos: CatalogueRepositories,
        ) -> tuple[RecipeProfile | None, RecipeProfile | None, RecipeProfile | None]:
            """Look up the default of the account in its stage, of the account in another stage, and of another account.

            :param repos: CatalogueRepositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The three profiles that were found, or None for each that was not.
            :rtype: tuple[RecipeProfile | None, RecipeProfile | None, RecipeProfile | None]
            """
            profiles = repos.recipe_profiles
            return (
                profiles.find_default(account_id, Stage.GEOMETRY),
                profiles.find_default(account_id, Stage.CLEANUP),
                profiles.find_default(other_id, Stage.GEOMETRY),
            )

        assert await uow.read(find_defaults) == (chosen, None, None)

    async def test_the_default_moves_when_the_old_one_is_demoted_first(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a demotion followed by a promotion in one transaction moves the default to another profile.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        old = make_recipe_profile(account_id=account_id, is_default=True)
        new = make_recipe_profile(account_id=account_id, minutes=FIRST_MINUTE)
        uow = await _store(fx_uow, old, new)

        def demote_and_promote(repos: CatalogueRepositories) -> RecipeProfile:
            """Demote the old default, then promote the other profile.

            :param repos: CatalogueRepositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The promoted profile as stored.
            :rtype: RecipeProfile
            """
            repos.recipe_profiles.update(evolve(old, is_default=False))
            return repos.recipe_profiles.update(evolve(new, is_default=True))

        promoted = await uow.change(demote_and_promote)
        found = await fx_uow().read(lambda repos: repos.recipe_profiles.find_default(account_id, Stage.GEOMETRY))
        assert found == promoted

    async def test_update_replaces_the_stored_state(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a renamed profile reads back under its new name.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        profile = make_recipe_profile(account_id=await fx_new_owner())
        uow = await _store(fx_uow, profile)
        renamed = evolve(profile, name=CLEAN_SCAN_NAME)
        await uow.change(lambda repos: repos.recipe_profiles.update(renamed))
        assert await fx_uow().read(lambda repos: repos.recipe_profiles.get(profile.id)) == renamed

    async def test_delete_removes_only_that_profile(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify a deleted profile is gone and the account's other profiles stay.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        gone = make_recipe_profile(account_id=account_id)
        kept = make_recipe_profile(account_id=account_id, minutes=FIRST_MINUTE)
        uow = await _store(fx_uow, gone, kept)
        await uow.change(lambda repos: repos.recipe_profiles.delete(gone.id))
        reading = fx_uow()
        assert await reading.read(lambda repos: repos.recipe_profiles.list_for_account(account_id)) == [kept]
        with pytest.raises(NotFoundError):
            await reading.read(lambda repos: repos.recipe_profiles.get(gone.id))

    async def test_a_profile_reads_back_in_the_order_it_was_saved_in(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory
    ) -> None:
        """Verify the free order survives a round trip, and the usual order is what a profile has by default.

        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the database accepts as an owner.
        :type fx_new_owner: OwnerFactory
        """
        account_id = await fx_new_owner()
        free = evolve(make_recipe_profile(account_id=account_id), order=OrderMode.FREE)
        usual = make_recipe_profile(account_id=account_id, minutes=FIRST_MINUTE)
        uow = await _store(fx_uow, free, usual)

        def orders(repos: CatalogueRepositories) -> list[OrderMode]:
            """Read the order of the two profiles.

            :param repos: CatalogueRepositories of the transaction.
            :type repos: CatalogueRepositories
            :returns: The order of the free profile and of the usual one.
            :rtype: list[OrderMode]
            """
            return [repos.recipe_profiles.get(profile.id).order for profile in (free, usual)]

        assert await uow.read(orders) == [OrderMode.FREE, OrderMode.USUAL]

    async def test_a_recipe_reads_back_with_the_profile_it_was_made_from(
        self, fx_uow: UnitOfWorkFactory, fx_new_owner: OwnerFactory, fx_databases: BookDatabasesFactory
    ) -> None:
        """Verify the profile a recipe of a book names is stored, and a recipe made from none reads back with none.

        The profile lives in the catalogue and the recipe in the file of the book, so the recipe keeps the identifier
        of the profile and no key between the two files checks it.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_new_owner: Function creating an account the catalogue accepts as an owner.
        :type fx_new_owner: OwnerFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        owner_id = await fx_new_owner()
        profile = make_recipe_profile(account_id=owner_id)
        linked = evolve(make_recipe(), profile_id=profile.id)
        unlinked = make_recipe(stage=Stage.CLEANUP)

        def profile_ids(repos: BookRepositories) -> list[object]:
            """Read the profile each of the two recipes names.

            :param repos: CatalogueRepositories of the transaction.
            :type repos: BookRepositories
            :returns: The profile identifier of the linked recipe and of the unlinked one.
            :rtype: list[object]
            """
            return [repos.recipes.get(recipe.id).profile_id for recipe in (linked, unlinked)]

        uow = await _store(fx_uow, profile)
        book_id = await store_book(uow, make_record(), owner=owner_id, databases=fx_databases())
        await (
            fx_databases().open(book_id).change(lambda repos, _book_record: repos.recipes.add_many([linked, unlinked]))
        )
        assert await fx_databases().open(book_id).read(profile_ids) == [profile.id, None]
