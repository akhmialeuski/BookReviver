"""Tests for the recipe profiles: saving, listing, renaming, the default, applying to a book, and the first opening."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import RecipeKind, Stage, StageState
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.values import PageStageKey, ProfileDraft, Step
from tests.helpers.builders import make_page_stage, make_recipe_profile, make_record
from tests.helpers.processing import account_books, account_profiles
from tests.helpers.processors import FakeProcessor
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, RecipeProfile
    from bookreviver.domain.ids import BookId
    from tests.helpers.builders import BookOpener
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
MISSING_KEY: str = 'geometry.gone'
STRENGTH: str = 'strength'
PROFILE_NAME: str = 'Photographed book'
OTHER_NAME: str = 'Clean flatbed scan'
BUILT_IN_STRENGTH: int = 1
# Two steps of one processor that tell their place by their strength, the first of them switched off
REORDERED: tuple[Step, ...] = (
    Step(processor_key=FAKE_KEY, params={STRENGTH: 3}, enabled=False),
    Step(processor_key=FAKE_KEY, params={STRENGTH: 2}),
)


async def store_profile(kit: ProcessingKit, profile: RecipeProfile) -> RecipeProfile:
    """Commit a profile the way a database holds it, whatever its steps name.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param profile: Profile to store.
    :type profile: RecipeProfile
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    return await kit.catalogue().change(lambda repos: repos.recipe_profiles.add(profile))


async def second_record(kit: ProcessingKit, actor: Actor) -> BookId:
    """Commit another book of the same account.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the book.
    :type actor: Actor
    :returns: The book.
    :rtype: BookId
    """
    book_id = await store_book(
        kit.catalogue(), make_record(title='Another book'), owner=actor.account_id, databases=kit.databases
    )
    return book_id


async def saved(kit: ProcessingKit, actor: Actor, name: str = PROFILE_NAME) -> RecipeProfile:
    """Save the reordered steps as a profile of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account saving the profile.
    :type actor: Actor
    :param name: Name of the profile.
    :type name: str
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    profile = await kit.profiles().save(actor, Stage.GEOMETRY, ProfileDraft(name=name, steps=REORDERED))
    return profile


class TestSaveProfile:
    """Tests for saving the steps of a recipe as a profile."""

    async def test_the_steps_keep_their_order_parameters_and_switch(self, fx_kit: ProcessingKit) -> None:
        """Verify a profile holds the steps as given, with the defaults of each processor filled in.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_book()
        profile = await saved(fx_kit, actor)
        listed = await account_profiles(fx_kit, actor, None)
        assert [(step.params[STRENGTH], step.enabled) for step in listed.items[0].steps] == [(3, False), (2, True)]
        assert (profile.stage, profile.name) == (Stage.GEOMETRY, PROFILE_NAME)

    async def test_a_step_of_an_unknown_processor_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject steps that name a processor the application does not have.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_book()
        missing = ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=MISSING_KEY)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().save(actor, Stage.GEOMETRY, missing)

    async def test_steps_that_are_all_switched_off_are_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a profile that could never run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_book()
        off = ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=FAKE_KEY, enabled=False)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().save(actor, Stage.GEOMETRY, off)


class TestChangeProfile:
    """Tests for listing, renaming, choosing the default and deleting."""

    async def test_rename_changes_only_the_name(self, fx_kit: ProcessingKit) -> None:
        """Verify a renamed profile keeps its steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_book()
        profile = await saved(fx_kit, actor)
        renamed = await fx_kit.profiles().rename(profile, OTHER_NAME)
        assert (renamed.name, renamed.steps) == (OTHER_NAME, profile.steps)
        assert await fx_kit.catalogue().read(lambda repos: repos.recipe_profiles.get(profile.id)) == renamed

    async def test_delete_removes_the_profile_and_no_recipe(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a recipe made from a profile outlives it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        profile = await saved(fx_kit, actor)
        applied = await fx_kit.profiles().apply(fx_open_book(actor, book_id), profile, kind=RecipeKind.TEXT)
        await fx_kit.profiles().remove(profile, await account_books(fx_kit, actor))
        kept = await fx_open_book.databases.open(book_id).read(lambda repos: repos.recipes.get(applied.recipe.id))
        assert kept.steps == profile.steps
        assert (await account_profiles(fx_kit, actor, None)).total == 0


class TestApplyProfile:
    """Tests for applying a profile to a book."""

    async def test_the_profile_is_reproduced_in_the_recipe_of_a_kind_of_another_book(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the steps arrive in their order, with their parameters and their switch, in the recipe asked for only.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        profile = await saved(fx_kit, actor)
        other = await second_record(fx_kit, actor)
        applied = await fx_kit.profiles().apply(fx_open_book(actor, other), profile, kind=RecipeKind.BW_PICTURE)
        picture = await fx_kit.recipe_of(actor, other, Stage.GEOMETRY, RecipeKind.BW_PICTURE)
        text = await fx_kit.recipe_of(actor, other, Stage.GEOMETRY)
        expect(applied.recipe.steps == profile.steps)
        expect(applied.recipe.kind is RecipeKind.BW_PICTURE)
        expect(applied.missing_processors == ())
        expect((picture.id, picture.profile_id) == (applied.recipe.id, profile.id))
        expect(text.steps != profile.steps)
        expect((await fx_kit.recipe_of(actor, book_id, Stage.GEOMETRY)).steps != profile.steps)
        assert_expectations()

    async def test_applying_a_profile_marks_the_pages_of_the_recipe_stale(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a page the recipe processed is stale after the profile took its steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        page, _ = await fx_kit.seed_scan_page(book_id)
        recipe = await fx_kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        seeded = make_page_stage(page_id=page.id, recipe_id=recipe.id)
        await fx_open_book.databases.open(book_id).change(lambda repos, _book_record: repos.page_stages.save(seeded))
        profile = await saved(fx_kit, actor)
        await fx_kit.profiles().apply(fx_open_book(actor, book_id), profile, kind=RecipeKind.TEXT)
        record = await fx_open_book.databases.open(book_id).read(
            lambda repos: repos.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        )
        assert record.state is StageState.STALE

    async def test_a_step_of_a_missing_processor_is_left_out_and_named(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the steps that can run are applied and the processor that cannot is reported.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        profile = await store_profile(
            fx_kit,
            make_recipe_profile(
                account_id=actor.account_id,
                steps=(
                    Step(processor_key=MISSING_KEY),
                    Step(processor_key=FAKE_KEY, params={STRENGTH: 2}),
                    Step(processor_key=MISSING_KEY),
                ),
            ),
        )
        applied = await fx_kit.profiles().apply(fx_open_book(actor, book_id), profile, kind=RecipeKind.TEXT)
        assert [step.processor_key for step in applied.recipe.steps] == [FAKE_KEY]
        assert applied.missing_processors == (MISSING_KEY,)

    async def test_a_profile_with_nothing_that_can_run_is_refused(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Reject a profile all of whose processors are missing, and name them.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        profile = await store_profile(
            fx_kit,
            make_recipe_profile(account_id=actor.account_id, steps=(Step(processor_key=MISSING_KEY),)),
        )
        with pytest.raises(InvalidParametersError, match=MISSING_KEY):
            await fx_kit.profiles().apply(fx_open_book(actor, book_id), profile, kind=RecipeKind.TEXT)
