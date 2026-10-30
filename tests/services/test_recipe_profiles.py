"""Tests for the recipe profiles of a book: saving, listing, renaming, deleting and applying to a recipe of the book."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.pages import RecipeKind
from bookreviver.domain.recipes import ProfileDraft, Step
from bookreviver.domain.slices import SliceRequest
from bookreviver.domain.stages import PageStageKey, Stage, StageState
from tests.helpers.builders import make_page_stage, make_recipe_profile
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.recipes import RecipeProfile
    from bookreviver.services.book import Book
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


async def store_profile(book: Book, profile: RecipeProfile) -> RecipeProfile:
    """Commit a profile the way the database of a book holds it, whatever its steps name.

    :param book: The open book.
    :type book: Book
    :param profile: Profile to store.
    :type profile: RecipeProfile
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    return await book.change(lambda repos, _book_record: repos.recipe_profiles.add(profile))


async def saved(kit: ProcessingKit, book: Book, name: str = PROFILE_NAME) -> RecipeProfile:
    """Save the reordered steps as a profile of the geometry stage.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book: The open book.
    :type book: Book
    :param name: Name of the profile.
    :type name: str
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    return await kit.profiles().save(book, Stage.GEOMETRY, ProfileDraft(name=name, steps=REORDERED))


class TestSaveProfile:
    """Tests for saving the steps of a recipe as a profile."""

    async def test_the_steps_keep_their_order_parameters_and_switch(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a profile holds the steps as given, with the defaults of each processor filled in.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        book = fx_open_book(actor, book_id)
        profile = await saved(fx_kit, book)
        listed = await fx_kit.profiles().library(book, None, SliceRequest())
        assert [(step.params[STRENGTH], step.enabled) for step in listed.items[0].steps] == [(3, False), (2, True)]
        assert (profile.stage, profile.name) == (Stage.GEOMETRY, PROFILE_NAME)

    async def test_a_step_of_an_unknown_processor_is_refused(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Reject steps that name a processor the application does not have.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        missing = ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=MISSING_KEY)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().save(fx_open_book(actor, book_id), Stage.GEOMETRY, missing)

    async def test_steps_that_are_all_switched_off_are_refused(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Reject a profile that could never run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        off = ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=FAKE_KEY, enabled=False)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().save(fx_open_book(actor, book_id), Stage.GEOMETRY, off)


class TestChangeProfile:
    """Tests for listing, renaming and deleting."""

    async def test_rename_changes_only_the_name(self, fx_kit: ProcessingKit, fx_open_book: BookOpener) -> None:
        """Verify a renamed profile keeps its steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        book = fx_open_book(actor, book_id)
        profile = await saved(fx_kit, book)
        renamed = await fx_kit.profiles().rename(book, profile.id, OTHER_NAME)
        assert (renamed.name, renamed.steps) == (OTHER_NAME, profile.steps)
        assert await book.read(lambda repos: repos.recipe_profiles.get(profile.id)) == renamed

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
        book = fx_open_book(actor, book_id)
        profile = await saved(fx_kit, book)
        applied = await fx_kit.profiles().apply(book, profile.id, kind=RecipeKind.TEXT)
        await fx_kit.profiles().remove(book, profile.id)
        kept = await book.read(lambda repos: repos.recipes.get(applied.recipe.id))
        assert kept.steps == profile.steps
        assert (await fx_kit.profiles().library(book, None, SliceRequest())).total == 0


class TestApplyProfile:
    """Tests for applying a profile to a recipe of its book."""

    async def test_the_profile_is_reproduced_in_the_recipe_of_a_kind(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the steps arrive in their order, with their parameters and their switch, in the recipe asked for only.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        book = fx_open_book(actor, book_id)
        profile = await saved(fx_kit, book)
        applied = await fx_kit.profiles().apply(book, profile.id, kind=RecipeKind.BW_PICTURE)
        picture = await fx_kit.recipe_of(actor, book_id, Stage.GEOMETRY, RecipeKind.BW_PICTURE)
        text = await fx_kit.recipe_of(actor, book_id, Stage.GEOMETRY)
        expect(applied.recipe.steps == profile.steps)
        expect(applied.recipe.kind is RecipeKind.BW_PICTURE)
        expect(applied.missing_processors == ())
        expect((picture.id, picture.profile_id) == (applied.recipe.id, profile.id))
        expect(text.steps != profile.steps)
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
        book = fx_open_book(actor, book_id)
        profile = await saved(fx_kit, book)
        await fx_kit.profiles().apply(book, profile.id, kind=RecipeKind.TEXT)
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
        book = fx_open_book(actor, book_id)
        profile = await store_profile(
            book,
            make_recipe_profile(
                steps=(
                    Step(processor_key=MISSING_KEY),
                    Step(processor_key=FAKE_KEY, params={STRENGTH: 2}),
                    Step(processor_key=MISSING_KEY),
                ),
            ),
        )
        applied = await fx_kit.profiles().apply(book, profile.id, kind=RecipeKind.TEXT)
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
        book = fx_open_book(actor, book_id)
        profile = await store_profile(book, make_recipe_profile(steps=(Step(processor_key=MISSING_KEY),)))
        with pytest.raises(InvalidParametersError, match=MISSING_KEY):
            await fx_kit.profiles().apply(book, profile.id, kind=RecipeKind.TEXT)
