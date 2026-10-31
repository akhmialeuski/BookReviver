"""Tests for the library of profiles of a book: listing, deleting, copying and importing."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.errors import InvalidParametersError
from bookreviver.domain.pages import RecipeKind
from bookreviver.domain.recipes import OrderMode, ProfileDraft, Step
from bookreviver.domain.slices import SliceRequest
from bookreviver.domain.stages import Stage
from bookreviver.services.recipe_profiles import COPY_NAME
from tests.harness.fakes.processors import FakeProcessor, SecondProcessor, ThirdProcessor

if TYPE_CHECKING:
    from bookreviver.domain.recipes import RecipeProfile
    from bookreviver.services.book import Book
    from tests.harness.builders.domain import BookOpener
    from tests.harness.builders.processing import ProcessingKit

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
FILE_NAME: str = 'From a file'
CLEANUP_KEY: str = 'cleanup.fake'


async def save_profile(kit: ProcessingKit, book: Book, *, stage: Stage = Stage.GEOMETRY) -> RecipeProfile:
    """Save the steps of the tests as a profile of the book.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book: The open book.
    :type book: Book
    :param stage: Stage of the profile.
    :type stage: Stage
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    steps = STEPS if stage is Stage.GEOMETRY else (Step(processor_key=CLEANUP_KEY),)
    return await kit.profiles().save(book, stage, ProfileDraft(name=PROFILE_NAME, steps=steps))


class TestLibraryOfABook:
    """Tests for the profiles a book lists."""

    async def test_deleting_a_profile_removes_it_from_the_library(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify a deleted profile is no longer listed, and the recipe that was made from it keeps its steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        book = fx_open_book(actor, book_id)
        profile = await save_profile(fx_kit, book)
        applied = await fx_kit.profiles().apply(book, profile.id, kind=RecipeKind.TEXT)
        await fx_kit.profiles().remove(book, profile.id)
        listed = await fx_kit.profiles().library(book, None, EVERYTHING)
        kept = await book.read(lambda repos: repos.recipes.get(applied.recipe.id))
        assert (listed.total, kept.profile_id) == (0, None)


class TestDuplicateProfile:
    """Tests for the copy of a profile."""

    async def test_the_copy_has_the_steps_of_the_profile_and_a_name_of_its_own(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the steps, the order and the stage are copied, and the name says it is a copy.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        book = fx_open_book(actor, book_id)
        profile = await save_profile(fx_kit, book)
        copy = await fx_kit.profiles().duplicate(book, profile.id)
        stored = await book.read(lambda repos: repos.recipe_profiles.get(copy.id))
        expect(stored.id != profile.id)
        expect(stored.name == COPY_NAME.format(name=PROFILE_NAME))
        expect(stored.steps == profile.steps)
        expect((stored.stage, stored.order) == (Stage.GEOMETRY, OrderMode.USUAL))
        assert_expectations()


class TestImportProfile:
    """Tests for saving the profile a file holds."""

    async def test_an_imported_profile_keeps_its_steps_and_gets_new_identifiers(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the file's steps are stored in their order with their parameters, and the file's order is kept.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        book = fx_open_book(actor, book_id)
        source = await save_profile(fx_kit, book)
        draft = ProfileDraft(
            name=FILE_NAME,
            steps=[Step(processor_key=FAKE_KEY, params={STRENGTH: 3}, enabled=False), Step(processor_key=FAKE_KEY)],
        )
        imported = await fx_kit.profiles().import_profile(book, Stage.GEOMETRY, draft)
        stored = await book.read(lambda repos: repos.recipe_profiles.get(imported.id))
        expect(stored.name == FILE_NAME)
        expect([(step.params[STRENGTH], step.enabled) for step in stored.steps] == [(3, False), (1, True)])
        expect(stored.id != source.id)
        assert_expectations()

    async def test_a_file_that_needs_a_missing_processor_is_refused_and_names_every_one(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Reject a file one of whose processors is not installed, saving nothing, and name the missing ones.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        book = fx_open_book(actor, book_id)
        draft = ProfileDraft(
            name='Foreign',
            steps=[Step(processor_key=FAKE_KEY), Step(processor_key=MISSING_KEY), Step(processor_key='geometry.also')],
        )
        with pytest.raises(InvalidParametersError, match=f'{MISSING_KEY}, geometry.also'):
            await fx_kit.profiles().import_profile(book, Stage.GEOMETRY, draft)
        assert (await fx_kit.profiles().library(book, None, EVERYTHING)).total == 0

    async def test_a_file_whose_steps_do_not_fit_their_processor_is_refused(
        self, fx_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Reject a file with a parameter the processor does not know, as saving a profile rejects it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        draft = ProfileDraft(name='Odd', steps=[Step(processor_key=FAKE_KEY, params={'unknown': 1})])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(fx_open_book(actor, book_id), Stage.GEOMETRY, draft)

    async def test_a_file_of_the_wrong_stage_is_refused(self, fx_kit: ProcessingKit, fx_open_book: BookOpener) -> None:
        """Reject a file that puts a processor of the geometry stage into the cleanup stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        draft = ProfileDraft(name='Misfiled', steps=[Step(processor_key=FAKE_KEY)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(fx_open_book(actor, book_id), Stage.CLEANUP, draft)

    async def test_a_file_with_all_steps_off_is_refused(self, fx_kit: ProcessingKit, fx_open_book: BookOpener) -> None:
        """Reject a file none of whose steps is on, since nothing in it could run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_kit.seed_book()
        draft = ProfileDraft(name='Idle', steps=[Step(processor_key=FAKE_KEY, enabled=False)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(fx_open_book(actor, book_id), Stage.GEOMETRY, draft)

    async def test_a_file_off_a_required_place_is_refused_in_the_usual_order_only(
        self, fx_ordered_kit: ProcessingKit, fx_open_book: BookOpener
    ) -> None:
        """Verify the order of a file is checked as a saved profile's is: refused when usual, kept when free.

        :param fx_ordered_kit: Kit over processors that declare a place.
        :type fx_ordered_kit: ProcessingKit
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        actor, book_id = await fx_ordered_kit.seed_book()
        book = fx_open_book(actor, book_id)
        usual = ProfileDraft(name='Usual', steps=BROKEN_ORDER)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().import_profile(book, Stage.GEOMETRY, usual)
        free = ProfileDraft(name='Free', steps=BROKEN_ORDER, order=OrderMode.FREE)
        imported = await fx_ordered_kit.profiles().import_profile(book, Stage.GEOMETRY, free)
        stored = await book.read(lambda repos: repos.recipe_profiles.get(imported.id))
        assert ([step.processor_key for step in stored.steps], stored.order) == (
            [THIRD_KEY, SECOND_KEY],
            OrderMode.FREE,
        )
