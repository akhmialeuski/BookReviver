"""Tests for the library of profiles: the books that use a profile, copying, importing, and importing."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import OrderMode, RecipeKind, Stage
from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.values import ProfileDraft, SliceRequest, Step
from bookreviver.services.recipe_profiles import COPY_NAME
from tests.helpers.builders import make_record, open_for
from tests.helpers.processing import account_books, account_profiles
from tests.helpers.processors import FakeProcessor, SecondProcessor, ThirdProcessor
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, BookRecord, Recipe, RecipeProfile
    from bookreviver.domain.ids import RecipeId
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
SECOND_KEY: str = SecondProcessor.spec.key
THIRD_KEY: str = ThirdProcessor.spec.key
MISSING_KEY: str = 'geometry.gone'
STRENGTH: str = 'strength'
PROFILE_NAME: str = 'Photographed book'
EVERYTHING: SliceRequest = SliceRequest(limit=100)
STEPS: tuple[Step, ...] = (
    Step(processor_key=FAKE_KEY, params={STRENGTH: 3}, enabled=False),
    Step(processor_key=FAKE_KEY, params={STRENGTH: 2}),
)
# The third processor has to stand after the second, so these steps break a required place
BROKEN_ORDER: tuple[Step, ...] = (Step(processor_key=THIRD_KEY), Step(processor_key=SECOND_KEY))
BOOKS_OF_ONE_PROFILE: int = 2
FILE_NAME: str = 'From a file'
CLEANUP_KEY: str = 'cleanup.fake'


async def save_profile(kit: ProcessingKit, actor: Actor, *, stage: Stage = Stage.GEOMETRY) -> RecipeProfile:
    """Save the steps of the tests as a profile of the account.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account saving the profile.
    :type actor: Actor
    :param stage: Stage of the profile.
    :type stage: Stage
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    steps = STEPS if stage is Stage.GEOMETRY else (Step(processor_key=CLEANUP_KEY),)
    return await kit.profiles().save(actor, stage, ProfileDraft(name=PROFILE_NAME, steps=steps))


async def another_book(kit: ProcessingKit, actor: Actor) -> BookRecord:
    """Commit another book of the same account.

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


class TestBooksOfAProfile:
    """Tests for the number of books that use each profile of the library."""

    async def test_a_book_counts_once_however_many_recipes_it_made_from_the_profile(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify two books count two, and a second recipe of the same profile in one book adds none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        other = await another_book(fx_kit, actor)
        profile = await save_profile(fx_kit, actor)
        for target in (book_record, other, book_record):
            await fx_kit.profiles().apply(open_for(actor, target.id), profile, kind=RecipeKind.TEXT)
        listed = await fx_kit.profiles().library(
            await account_profiles(fx_kit, actor, None), await account_books(fx_kit, actor)
        )
        assert [(item.profile.id, item.books) for item in listed.items] == [(profile.id, BOOKS_OF_ONE_PROFILE)]

    async def test_a_profile_that_no_book_uses_has_none(self, fx_kit: ProcessingKit) -> None:
        """Verify a profile that was never applied is listed with no books, beside one that was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        used = await save_profile(fx_kit, actor)
        unused = await fx_kit.profiles().duplicate(used)
        await fx_kit.profiles().apply(open_for(actor, book_record.id), used, kind=RecipeKind.TEXT)
        listed = await fx_kit.profiles().library(
            await account_profiles(fx_kit, actor, None), await account_books(fx_kit, actor)
        )
        assert {item.profile.id: item.books for item in listed.items} == {used.id: 1, unused.id: 0}

    async def test_the_books_of_another_account_do_not_count(self, fx_kit: ProcessingKit) -> None:
        """Verify the listing holds the profiles of the account only, and the total is the number of them.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_record()
        stranger, _ = await fx_kit.seed_record()
        await save_profile(fx_kit, stranger)
        mine = await save_profile(fx_kit, actor)
        listed = await fx_kit.profiles().library(
            await account_profiles(fx_kit, actor, Stage.GEOMETRY), await account_books(fx_kit, actor)
        )
        assert ([item.profile.id for item in listed.items], listed.total) == ([mine.id], 1)

    async def test_deleting_a_profile_removes_it_from_the_library(self, fx_kit: ProcessingKit) -> None:
        """Verify a deleted profile is no longer listed, and the book that used it keeps its recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        profile = await save_profile(fx_kit, actor)
        applied = await fx_kit.profiles().apply(open_for(actor, book_record.id), profile, kind=RecipeKind.TEXT)
        await fx_kit.profiles().remove(profile, await account_books(fx_kit, actor))
        listed = await fx_kit.profiles().library(
            await account_profiles(fx_kit, actor, None), await account_books(fx_kit, actor)
        )
        kept = await fx_kit.uow().read_book(book_record.id, lambda repos: repos.recipes.get(applied.recipe.id))
        assert (listed.total, kept.profile_id) == (0, None)

    async def test_deleting_a_profile_clears_the_link_in_every_book_of_the_owner(self, fx_kit: ProcessingKit) -> None:
        """Verify the recipes of both books that were made from the profile lose the link, and keep their steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        other = await another_book(fx_kit, actor)
        profile = await save_profile(fx_kit, actor)
        applied = [
            await fx_kit.profiles().apply(open_for(actor, target.id), profile, kind=RecipeKind.TEXT)
            for target in (book_record, other)
        ]

        await fx_kit.profiles().remove(profile, await account_books(fx_kit, actor))

        async def recipe_of(target: BookRecord, recipe_id: RecipeId) -> Recipe:
            """Read a recipe of a book as committed.

            :param target: The book.
            :type target: BookRecord
            :param recipe_id: The recipe.
            :type recipe_id: RecipeId
            :returns: The recipe.
            :rtype: Recipe
            """
            return await fx_kit.uow().read_book(target.id, lambda repos: repos.recipes.get(recipe_id))

        kept = [
            await recipe_of(target, done.recipe.id) for target, done in zip((book_record, other), applied, strict=True)
        ]
        expect([recipe.profile_id for recipe in kept] == [None, None])
        expect([recipe.steps for recipe in kept] == [done.recipe.steps for done in applied])
        assert_expectations()


class TestDuplicateProfile:
    """Tests for the copy of a profile."""

    async def test_the_copy_has_the_steps_of_the_profile_and_a_name_of_its_own(self, fx_kit: ProcessingKit) -> None:
        """Verify the steps, the order and the stage are copied, the name says it is a copy, and the default is not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_record()
        profile = await save_profile(fx_kit, actor)
        await fx_kit.profiles().set_default(profile, is_default=True)
        copy = await fx_kit.profiles().duplicate(profile)
        stored, original = await fx_kit.uow().read(
            lambda repos: (repos.recipe_profiles.get(copy.id), repos.recipe_profiles.get(profile.id))
        )
        expect(stored.id != profile.id)
        expect(stored.name == COPY_NAME.format(name=PROFILE_NAME))
        expect(stored.steps == profile.steps)
        expect((stored.stage, stored.order, stored.is_default) == (Stage.GEOMETRY, OrderMode.USUAL, False))
        expect(original.is_default)
        assert_expectations()


class TestImportProfile:
    """Tests for saving the profile a file holds."""

    async def test_an_imported_profile_keeps_its_steps_and_gets_new_identifiers(self, fx_kit: ProcessingKit) -> None:
        """Verify the file's steps are stored in their order with their parameters, and the file's order is kept.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_record()
        source = await save_profile(fx_kit, actor)
        draft = ProfileDraft(
            name=FILE_NAME,
            steps=[Step(processor_key=FAKE_KEY, params={STRENGTH: 3}, enabled=False), Step(processor_key=FAKE_KEY)],
        )
        imported = await fx_kit.profiles().import_profile(actor, Stage.GEOMETRY, draft)
        stored = await fx_kit.uow().read(lambda repos: repos.recipe_profiles.get(imported.id))
        expect(stored.name == FILE_NAME)
        expect([(step.params[STRENGTH], step.enabled) for step in stored.steps] == [(3, False), (1, True)])
        expect(stored.is_default is False)
        expect(stored.id != source.id)
        assert_expectations()

    async def test_a_file_that_needs_a_missing_processor_is_refused_and_names_every_one(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Reject a file one of whose processors is not installed, saving nothing, and name the missing ones.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_record()
        draft = ProfileDraft(
            name='Foreign',
            steps=[Step(processor_key=FAKE_KEY), Step(processor_key=MISSING_KEY), Step(processor_key='geometry.also')],
        )
        with pytest.raises(InvalidParametersError, match=f'{MISSING_KEY}, geometry.also'):
            await fx_kit.profiles().import_profile(actor, Stage.GEOMETRY, draft)
        assert (await account_profiles(fx_kit, actor, None)).total == 0

    async def test_a_file_whose_steps_do_not_fit_their_processor_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a file with a parameter the processor does not know, as saving a profile rejects it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_record()
        draft = ProfileDraft(name='Odd', steps=[Step(processor_key=FAKE_KEY, params={'unknown': 1})])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(actor, Stage.GEOMETRY, draft)

    async def test_a_file_of_the_wrong_stage_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a file that puts a processor of the geometry stage into the cleanup stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_record()
        draft = ProfileDraft(name='Misfiled', steps=[Step(processor_key=FAKE_KEY)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(actor, Stage.CLEANUP, draft)

    async def test_a_file_with_all_steps_off_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a file none of whose steps is on, since nothing in it could run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_record()
        draft = ProfileDraft(name='Idle', steps=[Step(processor_key=FAKE_KEY, enabled=False)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(actor, Stage.GEOMETRY, draft)

    async def test_a_file_off_a_required_place_is_refused_in_the_usual_order_only(
        self, fx_ordered_kit: ProcessingKit
    ) -> None:
        """Verify the order of a file is checked as a saved profile's is: refused when usual, kept when free.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        """
        actor, _ = await fx_ordered_kit.seed_record()
        usual = ProfileDraft(name='Usual', steps=BROKEN_ORDER)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().import_profile(actor, Stage.GEOMETRY, usual)
        free = ProfileDraft(name='Free', steps=BROKEN_ORDER, order=OrderMode.FREE)
        imported = await fx_ordered_kit.profiles().import_profile(actor, Stage.GEOMETRY, free)
        stored = await fx_ordered_kit.uow().read(lambda repos: repos.recipe_profiles.get(imported.id))
        assert ([step.processor_key for step in stored.steps], stored.order) == (
            [THIRD_KEY, SECOND_KEY],
            OrderMode.FREE,
        )
