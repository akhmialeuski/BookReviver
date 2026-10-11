"""Tests for the recipe profiles: saving, listing, renaming, the default, applying to a book, and the first opening."""

from datetime import timedelta
from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import RecipeKind, Stage, StageState
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.values import PageStageKey, ProfileDraft, SliceRequest, Step
from tests.helpers.builders import make_page_stage, make_project, make_recipe_profile, new_account_id
from tests.helpers.processors import FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Project, RecipeProfile
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
MISSING_KEY: str = 'geometry.gone'
STRENGTH: str = 'strength'
PROFILE_NAME: str = 'Photographed book'
OTHER_NAME: str = 'Clean flatbed scan'
BUILT_IN_STRENGTH: int = 1
DESKEW_KEY: str = 'geometry.deskew'
EVERYTHING: SliceRequest = SliceRequest(limit=100)
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
    uow = kit.uow()
    async with uow.change():
        return await uow.recipe_profiles.add(profile)


async def second_project(kit: ProcessingKit, actor: Actor) -> Project:
    """Commit another project of the same account.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: Account owning the project.
    :type actor: Actor
    :returns: The project.
    :rtype: Project
    """
    uow = kit.uow()
    async with uow.change():
        return await uow.projects.add(make_project(owner_id=actor.account_id, title='Another book'))


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
    # The next profile is saved a minute later, so the order of creation does not fall to the random identifiers
    kit.clock.moment += timedelta(minutes=1)
    return profile


class TestSaveProfile:
    """Tests for saving the steps of a recipe as a profile."""

    async def test_the_steps_keep_their_order_parameters_and_switch(self, fx_kit: ProcessingKit) -> None:
        """Verify a profile holds the steps as given, with the defaults of each processor filled in.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        profile = await saved(fx_kit, actor)
        listed = await fx_kit.profiles().profiles(actor, None, EVERYTHING)
        assert [(step.params[STRENGTH], step.enabled) for step in listed.items[0].steps] == [(3, False), (2, True)]
        assert (profile.stage, profile.name, profile.is_default) == (Stage.GEOMETRY, PROFILE_NAME, False)

    async def test_a_step_of_an_unknown_processor_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject steps that name a processor the application does not have.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        missing = ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=MISSING_KEY)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().save(actor, Stage.GEOMETRY, missing)

    async def test_steps_that_are_all_switched_off_are_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a profile that could never run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        off = ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=FAKE_KEY, enabled=False)])
        with pytest.raises(InvalidParametersError):
            await fx_kit.profiles().save(actor, Stage.GEOMETRY, off)


class TestChangeProfile:
    """Tests for listing, renaming, choosing the default and deleting."""

    async def test_profiles_are_listed_by_stage_for_the_account_only(self, fx_kit: ProcessingKit) -> None:
        """Verify a listing leaves out the profiles of another account and, when asked, of other stages.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        other, _ = await fx_kit.seed_project()
        mine = await saved(fx_kit, actor)
        await saved(fx_kit, other, OTHER_NAME)
        cleaning = ProfileDraft(name=OTHER_NAME, steps=[Step(processor_key=fx_kit.cleanup.spec.key)])
        cleanup = await fx_kit.profiles().save(actor, Stage.CLEANUP, cleaning)
        everything = await fx_kit.profiles().profiles(actor, None, EVERYTHING)
        geometry = await fx_kit.profiles().profiles(actor, Stage.GEOMETRY, EVERYTHING)
        assert ([profile.id for profile in everything.items], everything.total) == ([mine.id, cleanup.id], 2)
        assert [profile.id for profile in geometry.items] == [mine.id]

    async def test_rename_changes_only_the_name(self, fx_kit: ProcessingKit) -> None:
        """Verify a renamed profile keeps its steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        profile = await saved(fx_kit, actor)
        renamed = await fx_kit.profiles().rename(actor, profile.id, OTHER_NAME)
        assert (renamed.name, renamed.steps) == (OTHER_NAME, profile.steps)
        assert await fx_kit.uow().recipe_profiles.get(profile.id) == renamed

    async def test_choosing_a_default_demotes_the_previous_one(self, fx_kit: ProcessingKit) -> None:
        """Verify a stage never has two defaults, and the second choice takes the place of the first.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        first = await saved(fx_kit, actor)
        second = await saved(fx_kit, actor, OTHER_NAME)
        await fx_kit.profiles().set_default(actor, first.id, is_default=True)
        await fx_kit.profiles().set_default(actor, second.id, is_default=True)
        listed = await fx_kit.profiles().profiles(actor, Stage.GEOMETRY, EVERYTHING)
        assert [(profile.id, profile.is_default) for profile in listed.items] == [(first.id, False), (second.id, True)]

    async def test_a_default_can_be_given_up(self, fx_kit: ProcessingKit) -> None:
        """Verify a profile that stops being the default leaves the stage without one.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        profile = await saved(fx_kit, actor)
        await fx_kit.profiles().set_default(actor, profile.id, is_default=True)
        await fx_kit.profiles().set_default(actor, profile.id, is_default=False)
        assert await fx_kit.uow().recipe_profiles.find_default(actor.account_id, Stage.GEOMETRY) is None

    async def test_delete_removes_the_profile_and_no_recipe(self, fx_kit: ProcessingKit) -> None:
        """Verify a recipe made from a profile outlives it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        profile = await saved(fx_kit, actor)
        applied = await fx_kit.profiles().apply(actor, project.id, profile.id, kind=RecipeKind.TEXT)
        await fx_kit.profiles().remove(actor, profile.id)
        assert (await fx_kit.uow().recipes.get(applied.recipe.id)).steps == profile.steps
        assert (await fx_kit.profiles().profiles(actor, None, EVERYTHING)).total == 0

    async def test_a_profile_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject every change of a profile by an account that does not own it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        owner, _ = await fx_kit.seed_project()
        stranger, stranger_project = await fx_kit.seed_project()
        profile = await saved(fx_kit, owner)
        service = fx_kit.profiles()
        with pytest.raises(NotFoundError):
            await service.rename(stranger, profile.id, OTHER_NAME)
        with pytest.raises(NotFoundError):
            await service.set_default(stranger, profile.id, is_default=True)
        with pytest.raises(NotFoundError):
            await service.remove(stranger, profile.id)
        with pytest.raises(NotFoundError):
            await service.apply(stranger, stranger_project.id, profile.id, kind=RecipeKind.TEXT)
        assert (await fx_kit.uow().recipe_profiles.get(profile.id)) == profile


class TestApplyProfile:
    """Tests for applying a profile to a book."""

    async def test_the_profile_is_reproduced_in_the_recipe_of_a_kind_of_another_book(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the steps arrive in their order, with their parameters and their switch, in the recipe asked for only.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        profile = await saved(fx_kit, actor)
        other = await second_project(fx_kit, actor)
        applied = await fx_kit.profiles().apply(actor, other.id, profile.id, kind=RecipeKind.BW_PICTURE)
        picture = await fx_kit.recipe_of(actor, other, Stage.GEOMETRY, RecipeKind.BW_PICTURE)
        text = await fx_kit.recipe_of(actor, other, Stage.GEOMETRY)
        expect(applied.recipe.steps == profile.steps)
        expect((applied.recipe.kind, applied.recipe.project_id) == (RecipeKind.BW_PICTURE, other.id))
        expect(applied.missing_processors == ())
        expect((picture.id, picture.profile_id) == (applied.recipe.id, profile.id))
        expect(text.steps != profile.steps)
        expect((await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)).steps != profile.steps)
        assert_expectations()

    async def test_applying_a_profile_marks_the_pages_of_the_recipe_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a page the recipe processed is stale after the profile took its steps.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        recipe = await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)
        uow = fx_kit.uow()
        async with uow.change_book(project.id):
            await uow.page_stages.save(make_page_stage(page_id=page.id, recipe_id=recipe.id))
        profile = await saved(fx_kit, actor)
        await fx_kit.profiles().apply(actor, project.id, profile.id, kind=RecipeKind.TEXT)
        record = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        assert record.state is StageState.STALE

    async def test_a_step_of_a_missing_processor_is_left_out_and_named(self, fx_kit: ProcessingKit) -> None:
        """Verify the steps that can run are applied and the processor that cannot is reported.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
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
        applied = await fx_kit.profiles().apply(actor, project.id, profile.id, kind=RecipeKind.TEXT)
        assert [step.processor_key for step in applied.recipe.steps] == [FAKE_KEY]
        assert applied.missing_processors == (MISSING_KEY,)

    async def test_a_profile_with_nothing_that_can_run_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Reject a profile all of whose processors are missing, and name them.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        profile = await store_profile(
            fx_kit,
            make_recipe_profile(account_id=actor.account_id, steps=(Step(processor_key=MISSING_KEY),)),
        )
        with pytest.raises(InvalidParametersError, match=MISSING_KEY):
            await fx_kit.profiles().apply(actor, project.id, profile.id, kind=RecipeKind.TEXT)

    async def test_a_book_of_another_account_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Reject applying a profile to a book the account does not own.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        _, foreign_project = await fx_kit.seed_project()
        profile = await saved(fx_kit, actor)
        with pytest.raises(NotFoundError):
            await fx_kit.profiles().apply(actor, foreign_project.id, profile.id, kind=RecipeKind.TEXT)


class TestDefaultProfileOfANewBook:
    """Tests for the profile a stage starts with when a book opens it the first time."""

    async def test_the_default_profile_makes_the_recipe_of_text_pages_and_the_templates_the_others(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the profile replaces the built-in recipe of text pages and keeps its steps, and links to it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        profile = await saved(fx_kit, actor)
        await fx_kit.profiles().set_default(actor, profile.id, is_default=True)
        fresh = await second_project(fx_kit, actor)
        listed = await fx_kit.service().recipes(actor, fresh.id, Stage.GEOMETRY, EVERYTHING)
        assert [(recipe.kind, recipe.steps == profile.steps, recipe.profile_id) for recipe in listed.items] == [
            (RecipeKind.TEXT, True, profile.id),
            (RecipeKind.COLOR_PICTURE, False, None),
            (RecipeKind.BW_PICTURE, False, None),
            (RecipeKind.BLANK, False, None),
        ]

    async def test_the_picture_recipes_of_a_book_still_follow_the_templates_after_a_default_profile(
        self, fx_cv_kit: ProcessingKit
    ) -> None:
        """Verify the recipes of pictures keep their own methods when the default profile makes the text recipe.

        :param fx_cv_kit: The processing kit with the real OpenCV plugins.
        :type fx_cv_kit: ProcessingKit
        """
        actor, _ = await fx_cv_kit.seed_project()
        deskew = ProfileDraft(name=PROFILE_NAME, steps=[Step(processor_key=DESKEW_KEY)])
        profile = await fx_cv_kit.profiles().save(actor, Stage.GEOMETRY, deskew)
        await fx_cv_kit.profiles().set_default(actor, profile.id, is_default=True)
        fresh = await second_project(fx_cv_kit, actor)
        listed = await fx_cv_kit.service().recipes(actor, fresh.id, Stage.GEOMETRY, EVERYTHING)
        picture = next(recipe for recipe in listed.items if recipe.kind is RecipeKind.COLOR_PICTURE)
        expect([step.processor_key for step in listed.items[0].steps] == [DESKEW_KEY])
        expect(len(picture.steps) > 1)
        expect({step.processor_key: step.params.get('method') for step in picture.steps}['geometry.deskew'] == 'hough')
        assert_expectations()

    async def test_without_a_default_the_built_in_recipe_is_used(self, fx_kit: ProcessingKit) -> None:
        """Verify a profile that is not the default changes nothing for a new book.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, _ = await fx_kit.seed_project()
        await saved(fx_kit, actor)
        fresh = await second_project(fx_kit, actor)
        assert (await fx_kit.recipe_of(actor, fresh, Stage.GEOMETRY)).profile_id is None

    async def test_the_default_of_another_account_is_not_used(self, fx_kit: ProcessingKit) -> None:
        """Verify a book starts with the built-in recipe when only a stranger has a default.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        stranger = Actor(account_id=new_account_id())
        profile = await saved(fx_kit, stranger)
        await fx_kit.profiles().set_default(stranger, profile.id, is_default=True)
        actor, project = await fx_kit.seed_project()
        assert (await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)).profile_id is None

    async def test_a_book_that_has_opened_the_stage_keeps_its_recipe(self, fx_kit: ProcessingKit) -> None:
        """Verify choosing a default later does not change the recipes a book has.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)
        profile = await saved(fx_kit, actor)
        await fx_kit.profiles().set_default(actor, profile.id, is_default=True)
        assert (await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)).profile_id is None

    async def test_a_default_with_nothing_that_can_run_falls_back_to_the_built_in_recipe(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a default whose processors are all missing does not leave the stage without a recipe.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        await store_profile(
            fx_kit,
            make_recipe_profile(account_id=actor.account_id, steps=(Step(processor_key=MISSING_KEY),), is_default=True),
        )
        assert (await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)).profile_id is None

    async def test_a_missing_processor_is_left_out_of_the_default(self, fx_kit: ProcessingKit) -> None:
        """Verify the steps of a default that can run still start the stage.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        await store_profile(
            fx_kit,
            make_recipe_profile(
                account_id=actor.account_id,
                steps=(Step(processor_key=MISSING_KEY), Step(processor_key=FAKE_KEY)),
                is_default=True,
            ),
        )
        text = await fx_kit.recipe_of(actor, project, Stage.GEOMETRY)
        assert [step.processor_key for step in text.steps] == [FAKE_KEY]
        assert text.profile_id is not None
