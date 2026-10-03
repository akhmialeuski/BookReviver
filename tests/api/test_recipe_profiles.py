"""Tests for the recipe profile endpoints, on in-memory persistence with the fake processors of the tests."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.api.schemas.profiles import AppliedProfileSchema, RecipeProfileSchema
from bookreviver.domain.enums import Stage
from bookreviver.domain.values import Step
from tests.helpers.builders import make_project, make_recipe_profile, new_account_id
from tests.helpers.processing import ProcessingFakesProvider
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import Provider

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project, RecipeProfile

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROFILES_PATH: str = '/api/v1/recipe-profiles'
FAKE_KEY: str = 'geometry.fake'
MISSING_KEY: str = 'geometry.gone'
PROFILE_NAME: str = 'Photographed book'
OTHER_NAME: str = 'Clean flatbed scan'
ITEMS: str = 'items'
STEPS_FIELD: str = 'steps'
NAME_FIELD: str = 'name'
STRENGTH: str = 'strength'
STAGE_FIELD: str = 'stage'
TOTAL_FIELD: str = 'total'
PROCESSOR_FIELD: str = 'processor_key'
PARAMS_FIELD: str = 'params'


def _default_path(profile_id: object) -> str:
    """Return the path that chooses a profile as the default of its stage, or gives it up.

    :param profile_id: Identifier of the profile.
    :type profile_id: object
    :returns: The path of the request.
    :rtype: str
    """
    return f'{PROFILES_PATH}/{profile_id}/default'


def _recipe_path(project: Project) -> str:
    """Return the path of the active recipe of the geometry stage of a project.

    :param project: The project.
    :type project: Project
    :returns: The path of the request.
    :rtype: str
    """
    return f'{PROJECTS_PATH}/{project.id}/stages/geometry/recipe'


def _step(key: str, **params: object) -> Step:
    """Build a step of a processor.

    :param key: Key of the processor.
    :type key: str
    :param params: Parameters of the step.
    :type params: object
    :returns: The step, switched on.
    :rtype: Step
    """
    return Step(processor_key=key, params=params)


def _body(name: str = PROFILE_NAME) -> dict[str, object]:
    """Build the body that saves two steps of the geometry stage, the first of them switched off.

    :param name: Name of the profile.
    :type name: str
    :returns: The JSON body of a request.
    :rtype: dict[str, object]
    """
    return {
        NAME_FIELD: name,
        STAGE_FIELD: Stage.GEOMETRY,
        STEPS_FIELD: [
            {PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 3}, 'enabled': False},
            {PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 2}},
        ],
    }


def _apply_path(project: Project, profile_id: object) -> str:
    """Return the path that applies a profile to a project.

    :param project: The project.
    :type project: Project
    :param profile_id: Identifier of the profile.
    :type profile_id: object
    :returns: The path of the request.
    :rtype: str
    """
    return f'{PROJECTS_PATH}/{project.id}/recipe-profiles/{profile_id}/apply'


@pytest.fixture
def fx_extra_providers() -> Sequence[Provider]:
    """Replace the catalogue of processors with the fakes of the tests.

    :returns: The provider of the fake catalogue and recipes.
    :rtype: Sequence[Provider]
    """
    return [ProcessingFakesProvider()]


@pytest.fixture
async def fx_project(fx_database: InMemoryDatabase, fx_actor: Actor) -> Project:
    """Commit a book of the signed-in account.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored project.
    :rtype: Project
    """
    project = make_project(owner_id=fx_actor.account_id)
    await commit_project(fx_database, project)
    return project


async def _store(database: InMemoryDatabase, profile: RecipeProfile) -> RecipeProfile:
    """Commit a profile as an earlier request would have.

    :param database: In-memory database of the application.
    :type database: InMemoryDatabase
    :param profile: Profile to store.
    :type profile: RecipeProfile
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    uow = InMemoryUnitOfWork(database)
    stored = await uow.recipe_profiles.add(profile)
    await uow.commit()
    return stored


class TestProfiles:
    """Tests for saving, listing, renaming, choosing the default and deleting."""

    async def test_profile_is_created_listed_renamed_and_deleted(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a profile is answered 201, listed with its steps in order, renamed by PATCH and deleted with 204.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        created = await fx_client.post(PROFILES_PATH, json=_body())
        profile = RecipeProfileSchema.model_validate_json(created.content)
        renamed = await fx_client.patch(f'{PROFILES_PATH}/{profile.id}', json={NAME_FIELD: OTHER_NAME})
        listed = await fx_client.get(PROFILES_PATH)
        deleted = await fx_client.delete(f'{PROFILES_PATH}/{profile.id}')
        after = await fx_client.get(PROFILES_PATH)
        expect(created.status_code == status.HTTP_201_CREATED)
        expect((profile.stage, profile.name, profile.is_default) == (Stage.GEOMETRY, PROFILE_NAME, False))
        expect([(step.params[STRENGTH], step.enabled) for step in profile.steps] == [(3, False), (2, True)])
        expect(RecipeProfileSchema.model_validate_json(renamed.content).name == OTHER_NAME)
        expect([item[NAME_FIELD] for item in listed.json()[ITEMS]] == [OTHER_NAME])
        expect(deleted.status_code == status.HTTP_204_NO_CONTENT)
        expect(after.json()[TOTAL_FIELD] == 0)
        assert_expectations()

    async def test_a_listing_may_name_a_stage(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the stage in the query limits the listing to the profiles of that stage.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        await fx_client.post(PROFILES_PATH, json=_body())
        listed = await fx_client.get(PROFILES_PATH, params={STAGE_FIELD: Stage.CLEANUP})
        expect(listed.status_code == status.HTTP_200_OK)
        expect(listed.json()[TOTAL_FIELD] == 0)
        assert_expectations()

    async def test_steps_that_do_not_fit_are_a_422_problem(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a parameter the processor does not know answers 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        body = _body() | {STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {'unknown': 1}}]}
        response = await fx_client.post(PROFILES_PATH, json=body)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_the_default_is_chosen_and_given_up(self, fx_client: httpx.AsyncClient) -> None:
        """Verify PUT makes a profile the default, a second one takes the place, and DELETE gives it up.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        first = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        second = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(PROFILES_PATH, json=_body(OTHER_NAME))).content
        )
        chosen = await fx_client.put(_default_path(first.id))
        replaced = await fx_client.put(_default_path(second.id))
        listed = await fx_client.get(PROFILES_PATH)
        given_up = await fx_client.delete(_default_path(second.id))
        expect(RecipeProfileSchema.model_validate_json(chosen.content).is_default)
        expect(RecipeProfileSchema.model_validate_json(replaced.content).is_default)
        expect([item['is_default'] for item in listed.json()[ITEMS]] == [False, True])
        expect(RecipeProfileSchema.model_validate_json(given_up.content).is_default is False)
        assert_expectations()

    async def test_a_profile_of_another_account_is_a_404_problem(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_project: Project
    ) -> None:
        """Verify another account's profile is not listed and cannot be changed or applied.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        foreign = await _store(fx_database, make_recipe_profile(account_id=new_account_id(), steps=(_step(FAKE_KEY),)))
        path = f'{PROFILES_PATH}/{foreign.id}'
        listed = await fx_client.get(PROFILES_PATH)
        responses = [
            await fx_client.patch(path, json={NAME_FIELD: OTHER_NAME}),
            await fx_client.put(_default_path(foreign.id)),
            await fx_client.delete(_default_path(foreign.id)),
            await fx_client.delete(path),
            await fx_client.post(_apply_path(fx_project, foreign.id), json={}),
        ]
        expect(listed.json()[TOTAL_FIELD] == 0)
        expect([response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * len(responses))
        assert_expectations()


class TestApplyProfile:
    """Tests for applying a profile to a book."""

    async def test_apply_adds_a_variant_with_the_steps_of_the_profile(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the answer is 201 with the variant, whose steps are the profile's, and the active recipe stays.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        response = await fx_client.post(_apply_path(fx_project, profile.id), json={})
        applied = AppliedProfileSchema.model_validate_json(response.content)
        active = await fx_client.get(_recipe_path(fx_project))
        expect(response.status_code == status.HTTP_201_CREATED)
        expect((applied.recipe.name, applied.recipe.active) == (PROFILE_NAME, False))
        expect(applied.recipe.steps == profile.steps)
        expect(applied.missing_processors == [])
        expect(RecipeSchema.model_validate_json(active.content).id != applied.recipe.id)
        assert_expectations()

    async def test_apply_may_activate_the_variant(self, fx_client: httpx.AsyncClient, fx_project: Project) -> None:
        """Verify ``activate`` makes the new recipe the active one.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        response = await fx_client.post(_apply_path(fx_project, profile.id), json={'activate': True})
        applied = AppliedProfileSchema.model_validate_json(response.content)
        active = await fx_client.get(_recipe_path(fx_project))
        expect(applied.recipe.active)
        expect(RecipeSchema.model_validate_json(active.content).id == applied.recipe.id)
        assert_expectations()

    async def test_a_missing_processor_is_named_in_the_answer(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor, fx_project: Project
    ) -> None:
        """Verify the steps that can run are applied and the processor that cannot is listed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = await _store(
            fx_database,
            make_recipe_profile(
                account_id=fx_actor.account_id, steps=(_step(MISSING_KEY), _step(FAKE_KEY, strength=2))
            ),
        )
        response = await fx_client.post(_apply_path(fx_project, profile.id), json={})
        applied = AppliedProfileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect([step.processor_key for step in applied.recipe.steps] == [FAKE_KEY])
        expect(applied.missing_processors == [MISSING_KEY])
        assert_expectations()

    async def test_a_new_book_starts_with_the_default_profile(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the first request of a stage in a new book answers with the default profile.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        await fx_client.put(_default_path(profile.id))
        fresh = make_project(owner_id=fx_actor.account_id, title='Fresh')
        await commit_project(fx_database, fresh)
        response = await fx_client.get(_recipe_path(fresh))
        recipe = RecipeSchema.model_validate_json(response.content)
        expect((recipe.name, recipe.active, recipe.steps) == (PROFILE_NAME, True, profile.steps))
        assert_expectations()
