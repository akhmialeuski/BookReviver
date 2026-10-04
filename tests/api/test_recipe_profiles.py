"""Tests for the recipe profile endpoints, on in-memory persistence with the fake processors of the tests."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.api.schemas.profiles import (
    AppliedProfileSchema,
    LibraryProfileSchema,
    ProfileFileSchema,
    RecipeProfileSchema,
)
from bookreviver.domain.enums import JobKind, JobState, Stage
from bookreviver.domain.values import Step
from tests.helpers.builders import make_page, make_project, make_recipe_profile, new_account_id
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
ORDER_FIELD: str = 'order'
PROFILE_ID_FIELD: str = 'profile_id'
FREE_ORDER: str = 'free'
VERSION_FIELD: str = 'version'
BOOKS_FIELD: str = 'books'
FILE_VERSION: int = 1
COPY_NAME: str = 'Photographed book (copy)'
MANY_BOOKS: int = 2
EXPORT_SUFFIX: str = 'export'
IMPORT_PATH: str = f'{PROFILES_PATH}/import'


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


def _link_path(project: Project, recipe_id: object) -> str:
    """Return the path that records the profile a recipe of the geometry stage was made from.

    :param project: The project.
    :type project: Project
    :param recipe_id: Identifier of the recipe.
    :type recipe_id: object
    :returns: The path of the request.
    :rtype: str
    """
    return f'{PROJECTS_PATH}/{project.id}/stages/geometry/variants/{recipe_id}/profile'


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
        recipe = RecipeSchema.model_validate_json((await fx_client.get(_recipe_path(fx_project))).content)
        responses = [
            await fx_client.patch(path, json={NAME_FIELD: OTHER_NAME}),
            await fx_client.put(
                path, json={key: value for key, value in _body(OTHER_NAME).items() if key != STAGE_FIELD}
            ),
            await fx_client.put(_link_path(fx_project, recipe.id), json={PROFILE_ID_FIELD: str(foreign.id)}),
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


class TestOrderOfProfile:
    """Tests for the order a profile is saved in and replaced under."""

    async def test_a_profile_is_answered_with_the_order_it_was_saved_in(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the usual order is the default, and the free order the body asks for is stored and listed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        usual = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        free = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(PROFILES_PATH, json=_body(OTHER_NAME) | {ORDER_FIELD: FREE_ORDER})).content
        )
        listed = await fx_client.get(PROFILES_PATH)
        expect((usual.order, free.order) == ('usual', FREE_ORDER))
        expect([item[ORDER_FIELD] for item in listed.json()[ITEMS]] == ['usual', FREE_ORDER])
        assert_expectations()

    async def test_put_replaces_the_name_the_steps_and_the_order_and_keeps_the_stage(
        self, fx_client: httpx.AsyncClient
    ) -> None:
        """Verify PUT answers the profile as replaced, and a listing reads the same.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        body = {
            NAME_FIELD: OTHER_NAME,
            ORDER_FIELD: FREE_ORDER,
            STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 5}}],
        }
        response = await fx_client.put(f'{PROFILES_PATH}/{profile.id}', json=body)
        replaced = RecipeProfileSchema.model_validate_json(response.content)
        listed = await fx_client.get(PROFILES_PATH)
        expect(response.status_code == status.HTTP_200_OK)
        expect(
            (replaced.id, replaced.stage, replaced.name, replaced.order)
            == (profile.id, Stage.GEOMETRY, OTHER_NAME, FREE_ORDER)
        )
        expect([step.params[STRENGTH] for step in replaced.steps] == [5])
        expect(listed.json()[ITEMS][0][STEPS_FIELD] == response.json()[STEPS_FIELD])
        assert_expectations()

    async def test_put_with_steps_that_do_not_fit_is_a_422_problem(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a parameter the processor does not know answers 422 and leaves the profile as it was.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        body = _body() | {STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {'unknown': 1}}]}
        response = await fx_client.put(f'{PROFILES_PATH}/{profile.id}', json=body)
        listed = await fx_client.get(PROFILES_PATH)
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(len(listed.json()[ITEMS][0][STEPS_FIELD]) == len(profile.steps))
        assert_expectations()


class TestLinkRecipeToProfile:
    """Tests for the profile a recipe of a book is made from."""

    async def test_an_applied_profile_is_the_profile_of_the_recipe_it_made(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the answer of applying, and the active recipe once it is activated, name the profile.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        applied = AppliedProfileSchema.model_validate_json(
            (await fx_client.post(_apply_path(fx_project, profile.id), json={'activate': True})).content
        )
        active = RecipeSchema.model_validate_json((await fx_client.get(_recipe_path(fx_project))).content)
        expect(applied.recipe.profile_id == profile.id)
        expect((active.id, active.profile_id) == (applied.recipe.id, profile.id))
        assert_expectations()

    async def test_a_recipe_made_from_no_profile_answers_null(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the built-in recipe of a book names no profile.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.get(_recipe_path(fx_project))
        assert response.json()[PROFILE_ID_FIELD] is None

    async def test_put_links_a_recipe_to_a_profile_and_null_unlinks_it(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify PUT records the profile without changing the steps, and a null profile clears it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        before = RecipeSchema.model_validate_json((await fx_client.get(_recipe_path(fx_project))).content)
        path = _link_path(fx_project, before.id)
        linked = await fx_client.put(path, json={PROFILE_ID_FIELD: str(profile.id)})
        read = RecipeSchema.model_validate_json((await fx_client.get(_recipe_path(fx_project))).content)
        unlinked = await fx_client.put(path, json={PROFILE_ID_FIELD: None})
        expect(linked.status_code == status.HTTP_200_OK)
        expect(RecipeSchema.model_validate_json(linked.content).profile_id == profile.id)
        expect((read.profile_id, read.steps, read.updated_at) == (profile.id, before.steps, before.updated_at))
        expect(RecipeSchema.model_validate_json(unlinked.content).profile_id is None)
        assert_expectations()

    async def test_a_profile_of_another_stage_is_a_422_problem(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify a recipe cannot be linked to a profile of another stage.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        cleanup = {
            NAME_FIELD: OTHER_NAME,
            STAGE_FIELD: Stage.CLEANUP,
            STEPS_FIELD: [{PROCESSOR_FIELD: 'cleanup.fake', PARAMS_FIELD: {}}],
        }
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=cleanup)).content)
        recipe = RecipeSchema.model_validate_json((await fx_client.get(_recipe_path(fx_project))).content)
        response = await fx_client.put(_link_path(fx_project, recipe.id), json={PROFILE_ID_FIELD: str(profile.id)})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_deleting_a_profile_clears_the_link_of_its_recipes(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify a recipe made from a profile is still answered after the profile is deleted, with no profile.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        await fx_client.post(_apply_path(fx_project, profile.id), json={'activate': True})
        await fx_client.delete(f'{PROFILES_PATH}/{profile.id}')
        response = await fx_client.get(_recipe_path(fx_project))
        assert (response.status_code, response.json()[PROFILE_ID_FIELD]) == (status.HTTP_200_OK, None)


def _file(**overrides: object) -> dict[str, object]:
    """Build the content of a profile file of the geometry stage, with two steps, the first of them switched off.

    :param overrides: Fields of the file to replace.
    :type overrides: object
    :returns: The JSON body of an import.
    :rtype: dict[str, object]
    """
    return {
        VERSION_FIELD: FILE_VERSION,
        STAGE_FIELD: Stage.GEOMETRY,
        NAME_FIELD: PROFILE_NAME,
        STEPS_FIELD: [
            {PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 3}, 'enabled': False},
            {PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 2}},
        ],
    } | overrides


class TestLibrary:
    """Tests for the books of a profile, the copy of a profile and the file a profile is exchanged by."""

    async def test_the_listing_counts_the_books_that_use_each_profile(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor, fx_project: Project
    ) -> None:
        """Verify applying a profile to two books makes the listing say two, and a profile never applied says none.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        used = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        await fx_client.post(PROFILES_PATH, json=_body(OTHER_NAME))
        other = make_project(owner_id=fx_actor.account_id, title='Another book')
        await commit_project(fx_database, other)
        for book in (fx_project, other, fx_project):
            await fx_client.post(_apply_path(book, used.id), json={})
        listed = await fx_client.get(PROFILES_PATH)
        assert [(item[NAME_FIELD], item[BOOKS_FIELD]) for item in listed.json()[ITEMS]] == [
            (PROFILE_NAME, MANY_BOOKS),
            (OTHER_NAME, 0),
        ]

    async def test_a_profile_is_duplicated_with_a_201_and_a_copy_name(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the copy has the steps of the profile, is not the default and is listed beside it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        await fx_client.put(_default_path(profile.id))
        response = await fx_client.post(f'{PROFILES_PATH}/{profile.id}/duplicate')
        copy = RecipeProfileSchema.model_validate_json(response.content)
        listed = LibraryProfileSchema.model_validate((await fx_client.get(PROFILES_PATH)).json()[ITEMS][1])
        expect(response.status_code == status.HTTP_201_CREATED)
        expect((copy.name, copy.is_default, copy.stage) == (COPY_NAME, False, Stage.GEOMETRY))
        expect(
            [(step.params, step.enabled) for step in copy.steps]
            == [(step.params, step.enabled) for step in profile.steps]
        )
        expect(listed.id == copy.id)
        assert_expectations()

    async def test_a_profile_is_exported_as_a_versioned_file_without_identifiers(
        self, fx_client: httpx.AsyncClient
    ) -> None:
        """Verify the file carries the version, the stage, the name, the order and the steps, and no identifier.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        response = await fx_client.get(f'{PROFILES_PATH}/{profile.id}/{EXPORT_SUFFIX}')
        exported = ProfileFileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect(response.json()[VERSION_FIELD] == FILE_VERSION)
        expect((exported.name, exported.stage, exported.order) == (PROFILE_NAME, Stage.GEOMETRY, 'usual'))
        expect(
            [(step.params, step.enabled) for step in exported.steps] == [({STRENGTH: 3}, False), ({STRENGTH: 2}, True)]
        )
        expect(all('step_id' not in step for step in response.json()[STEPS_FIELD]))
        assert_expectations()

    async def test_an_exported_file_imports_as_a_profile_with_the_same_steps(
        self, fx_client: httpx.AsyncClient
    ) -> None:
        """Verify the export of a profile is accepted by the import, which makes a profile of its own.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        exported = await fx_client.get(f'{PROFILES_PATH}/{profile.id}/{EXPORT_SUFFIX}')
        response = await fx_client.post(IMPORT_PATH, json=exported.json())
        imported = RecipeProfileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect(imported.id != profile.id)
        expect(
            [(step.params, step.enabled) for step in imported.steps]
            == [(step.params, step.enabled) for step in profile.steps]
        )
        expect({step.step_id for step in imported.steps}.isdisjoint({step.step_id for step in profile.steps}))
        assert_expectations()

    async def test_a_file_that_does_not_validate_is_a_422_problem(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a wrong version, an unknown field, a missing processor and bad parameters all answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        steps_of = {STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {'unknown': 1}}]}
        missing = {STEPS_FIELD: [{PROCESSOR_FIELD: MISSING_KEY}]}
        responses = [
            await fx_client.post(IMPORT_PATH, json=_file(**{VERSION_FIELD: FILE_VERSION + 1})),
            await fx_client.post(IMPORT_PATH, json=_file(extra=1)),
            await fx_client.post(IMPORT_PATH, json=_file(**missing)),
            await fx_client.post(IMPORT_PATH, json=_file(**steps_of)),
            await fx_client.post(IMPORT_PATH, json=_file(**{STEPS_FIELD: []})),
        ]
        listed = await fx_client.get(PROFILES_PATH)
        expect(
            [response.status_code for response in responses] == [status.HTTP_422_UNPROCESSABLE_CONTENT] * len(responses)
        )
        expect(MISSING_KEY in responses[2].text)
        expect(listed.json()[TOTAL_FIELD] == 0)
        assert_expectations()

    async def test_a_profile_of_another_account_cannot_be_exported_or_copied(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify another account's profile answers 404 to the export and to the copy.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        foreign = await _store(fx_database, make_recipe_profile(account_id=new_account_id(), steps=(_step(FAKE_KEY),)))
        responses = [
            await fx_client.get(f'{PROFILES_PATH}/{foreign.id}/{EXPORT_SUFFIX}'),
            await fx_client.post(f'{PROFILES_PATH}/{foreign.id}/duplicate'),
        ]
        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * len(responses)


class TestApplyProfileToPages:
    """Tests for applying a profile to some pages of a book through the apply route."""

    async def test_apply_to_pages_answers_the_queued_run(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the answer holds the variant, which stays inactive, and the queued run of the stage on the pages.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id, title='With pages')
        page = make_page(project_id=project.id)
        await commit_project(fx_database, project, page)
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        response = await fx_client.post(_apply_path(project, profile.id), json={'page_ids': [str(page.id)]})
        applied = AppliedProfileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect(applied.recipe.active is False)
        assert applied.job is not None
        expect((applied.job.kind, applied.job.state) == (JobKind.RUN_STAGE, JobState.QUEUED))
        expect(applied.job.stage is Stage.GEOMETRY)
        assert_expectations()

    async def test_apply_without_pages_answers_no_job(self, fx_client: httpx.AsyncClient, fx_project: Project) -> None:
        """Verify applying to the book alone answers a null job.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        response = await fx_client.post(_apply_path(fx_project, profile.id), json={})
        assert response.json()['job'] is None

    async def test_a_page_that_is_not_in_the_book_is_a_404_problem(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify pages of no book of the account answer 404, and an empty list of pages answers 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: A book of the signed-in account.
        :type fx_project: Project
        """
        profile = RecipeProfileSchema.model_validate_json((await fx_client.post(PROFILES_PATH, json=_body())).content)
        stray = make_page(project_id=make_project(owner_id=new_account_id()).id)
        missing = await fx_client.post(_apply_path(fx_project, profile.id), json={'page_ids': [str(stray.id)]})
        empty = await fx_client.post(_apply_path(fx_project, profile.id), json={'page_ids': []})
        assert (missing.status_code, empty.status_code) == (
            status.HTTP_404_NOT_FOUND,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
