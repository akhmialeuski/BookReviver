"""Tests for the library of profiles: the books that use a profile, copying, importing, and importing."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import OrderMode, RecipeKind, Stage
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.values import ProfileDraft, SliceRequest, Step
from bookreviver.services.recipe_profiles import COPY_NAME
from tests.helpers.builders import make_project
from tests.helpers.processors import FakeProcessor, SecondProcessor, ThirdProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Actor, Project, RecipeProfile
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


async def another_book(kit: ProcessingKit, actor: Actor) -> Project:
    """Commit another project of the same account.

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


class TestBooksOfAProfile:
    """Tests for the number of books that use each profile of the library."""

    async def test_a_book_counts_once_however_many_recipes_it_made_from_the_profile(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify two books count two, and a second recipe of the same profile in one book adds none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        other = await another_book(fx_kit, actor)
        profile = await save_profile(fx_kit, actor)
        for target in (project, other, project):
            await fx_kit.profiles().apply(actor, target.id, profile.id, kind=RecipeKind.TEXT)
        listed = await fx_kit.profiles().library(actor, None, EVERYTHING)
        assert [(item.profile.id, item.books) for item in listed.items] == [(profile.id, BOOKS_OF_ONE_PROFILE)]

    async def test_a_profile_that_no_book_uses_has_none(self, fx_kit: ProcessingKit) -> None:
        """Verify a profile that was never applied is listed with no books, beside one that was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        used = await save_profile(fx_kit, actor)
        unused = await fx_kit.profiles().duplicate(actor, used.id)
        await fx_kit.profiles().apply(actor, project.id, used.id, kind=RecipeKind.TEXT)
        listed = await fx_kit.profiles().library(actor, None, EVERYTHING)
        assert {item.profile.id: item.books for item in listed.items} == {used.id: 1, unused.id: 0}

    async def test_the_books_of_another_account_do_not_count(self, fx_kit: ProcessingKit) -> None:
        """Verify the listing holds the profiles of the account only, and the total is the number of them.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        stranger, _ = await fx_kit.seed_project()
        await save_profile(fx_kit, stranger)
        mine = await save_profile(fx_kit, actor)
        listed = await fx_kit.profiles().library(actor, Stage.GEOMETRY, EVERYTHING)
        assert ([item.profile.id for item in listed.items], listed.total) == ([mine.id], 1)

    async def test_deleting_a_profile_removes_it_from_the_library(self, fx_kit: ProcessingKit) -> None:
        """Verify a deleted profile is no longer listed, and the book that used it keeps its recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        profile = await save_profile(fx_kit, actor)
        applied = await fx_kit.profiles().apply(actor, project.id, profile.id, kind=RecipeKind.TEXT)
        await fx_kit.profiles().remove(actor, profile.id)
        listed = await fx_kit.profiles().library(actor, None, EVERYTHING)
        kept = await fx_kit.uow().recipes.get(applied.recipe.id)
        assert (listed.total, kept.profile_id) == (0, None)


class TestDuplicateProfile:
    """Tests for the copy of a profile."""

    async def test_the_copy_has_the_steps_of_the_profile_and_a_name_of_its_own(self, fx_kit: ProcessingKit) -> None:
        """Verify the steps, the order and the stage are copied, the name says it is a copy, and the default is not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        profile = await save_profile(fx_kit, actor)
        await fx_kit.profiles().set_default(actor, profile.id, is_default=True)
        copy = await fx_kit.profiles().duplicate(actor, profile.id)
        stored = await fx_kit.uow().recipe_profiles.get(copy.id)
        expect(stored.id != profile.id)
        expect(stored.name == COPY_NAME.format(name=PROFILE_NAME))
        expect(stored.steps == profile.steps)
        expect((stored.stage, stored.order, stored.is_default) == (Stage.GEOMETRY, OrderMode.USUAL, False))
        expect((await fx_kit.uow().recipe_profiles.get(profile.id)).is_default)
        assert_expectations()

    async def test_the_profile_of_another_account_cannot_be_copied(self, fx_kit: ProcessingKit) -> None:
        """Reject a copy of a profile the account does not own, as a missing one is rejected.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        stranger, _ = await fx_kit.seed_project()
        foreign = await save_profile(fx_kit, stranger)
        with pytest.raises(NotFoundError):
            await fx_kit.profiles().duplicate(actor, foreign.id)


class TestImportProfile:
    """Tests for saving the profile a file holds."""

    async def test_an_imported_profile_keeps_its_steps_and_gets_new_identifiers(self, fx_kit: ProcessingKit) -> None:
        """Verify the file's steps are stored in their order with their parameters, and the file's order is kept.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        source = await save_profile(fx_kit, actor)
        draft = ProfileDraft(
            name=FILE_NAME,
            steps=[Step(processor_key=FAKE_KEY, params={STRENGTH: 3}, enabled=False), Step(processor_key=FAKE_KEY)],
        )
        imported = await fx_kit.profiles().import_profile(actor, Stage.GEOMETRY, draft)
        stored = await fx_kit.uow().recipe_profiles.get(imported.id)
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
        actor, _ = await fx_kit.seed_project()
        draft = ProfileDraft(
            name='Foreign',
            steps=[Step(processor_key=FAKE_KEY), Step(processor_key=MISSING_KEY), Step(processor_key='geometry.also')],
        )
        with pytest.raises(InvalidParametersError, match=f'{MISSING_KEY}, geometry.also'):
            await fx_kit.profiles().import_profile(actor, Stage.GEOMETRY, draft)
        assert (await fx_kit.profiles().profiles(actor, None, EVERYTHING)).total == 0

    async def test_a_file_whose_steps_do_not_fit_their_processor_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a file with a parameter the processor does not know, as saving a profile rejects it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        draft = ProfileDraft(name='Odd', steps=[Step(processor_key=FAKE_KEY, params={'unknown': 1})])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(actor, Stage.GEOMETRY, draft)

    async def test_a_file_of_the_wrong_stage_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a file that puts a processor of the geometry stage into the cleanup stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        draft = ProfileDraft(name='Misfiled', steps=[Step(processor_key=FAKE_KEY)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().import_profile(actor, Stage.CLEANUP, draft)

    async def test_a_file_with_all_steps_off_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a file none of whose steps is on, since nothing in it could run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
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
        actor, _ = await fx_ordered_kit.seed_project()
        usual = ProfileDraft(name='Usual', steps=BROKEN_ORDER)
        with pytest.raises(InvalidParametersError):
            await fx_ordered_kit.profiles().import_profile(actor, Stage.GEOMETRY, usual)
        free = ProfileDraft(name='Free', steps=BROKEN_ORDER, order=OrderMode.FREE)
        imported = await fx_ordered_kit.profiles().import_profile(actor, Stage.GEOMETRY, free)
        stored = await fx_ordered_kit.uow().recipe_profiles.get(imported.id)
        assert ([step.processor_key for step in stored.steps], stored.order) == (
            [THIRD_KEY, SECOND_KEY],
            OrderMode.FREE,
        )
