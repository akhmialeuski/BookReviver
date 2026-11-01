"""Tests for the recipe profile endpoints of a book, on the SQLite files of the test with the fake processors."""

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.api.schemas.profiles import AppliedProfileSchema, ProfileFileSchema, RecipeProfileSchema
from bookreviver.domain.pages import RecipeKind
from bookreviver.domain.recipes import Step
from bookreviver.domain.stages import Stage
from tests.harness.builders.domain import make_recipe_profile, make_record
from tests.harness.builders.processing import ProcessingFakesProvider
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import Provider

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.recipes import RecipeProfile
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
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
FILE_VERSION: int = 2
FIRST_FILE_VERSION: int = 1
APPLIES_TO_FIELD: str = 'applies_to'
COPY_NAME: str = 'Photographed book (copy)'
EXPORT_SUFFIX: str = 'export'
MISSING_PROFILE: UUID = UUID(int=1)


async def _text_recipe(client: httpx.AsyncClient, book_id: BookId) -> RecipeSchema:
    """Read the recipe of text pages of the geometry stage of a book, which a book creates when it is first asked.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book_id: The book.
    :type book_id: BookId
    :returns: The recipe.
    :rtype: RecipeSchema
    """
    listed = await client.get(f'{BOOKS_PATH}/{book_id}/stages/geometry/recipes')
    return next(
        recipe
        for recipe in (RecipeSchema.model_validate(item) for item in listed.json()[ITEMS])
        if recipe.kind is RecipeKind.TEXT
    )


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


def _link_path(book_id: BookId, recipe_id: object) -> str:
    """Return the path that records the profile a recipe of the geometry stage was made from.

    :param book_id: The book.
    :type book_id: BookId
    :param recipe_id: Identifier of the recipe.
    :type recipe_id: object
    :returns: The path of the request.
    :rtype: str
    """
    return f'{BOOKS_PATH}/{book_id}/stages/geometry/recipes/{recipe_id}/profile'


def _apply_path(book_id: BookId, profile_id: object) -> str:
    """Return the path that applies a profile to a book.

    :param book_id: The book.
    :type book_id: BookId
    :param profile_id: Identifier of the profile.
    :type profile_id: object
    :returns: The path of the request.
    :rtype: str
    """
    return f'{BOOKS_PATH}/{book_id}/recipe-profiles/{profile_id}/apply'


@pytest.fixture
def fx_extra_providers() -> Sequence[Provider]:
    """Replace the catalogue of processors with the fakes of the tests.

    :returns: The provider of the fake catalogue and recipes.
    :rtype: Sequence[Provider]
    """
    return [ProcessingFakesProvider()]


@pytest.fixture
async def fx_book(
    fx_catalogue: CatalogueDatabaseFactory, fx_actor: Actor, fx_databases: BookDatabasesFactory
) -> BookId:
    """Commit a book of the signed-in account.

    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The stored book.
    :rtype: BookId
    """
    book_id = await store_book(fx_catalogue(), make_record(), owner=fx_actor.account_id, databases=fx_databases())
    return book_id


@pytest.fixture
def fx_profiles_path(fx_book: BookId) -> str:
    """Return the path of the recipe profiles of the book of the signed-in account.

    :param fx_book: A book of the signed-in account.
    :type fx_book: BookId
    :returns: The path of the profiles of the book.
    :rtype: str
    """
    return f'{BOOKS_PATH}/{fx_book}/recipe-profiles'


async def _store(databases: BookDatabasesFactory, book_id: BookId, profile: RecipeProfile) -> RecipeProfile:
    """Commit a profile in the database of a book as an earlier request would have.

    :param databases: Function opening a new opener of the databases of the books of the test.
    :type databases: BookDatabasesFactory
    :param book_id: The book.
    :type book_id: BookId
    :param profile: Profile to store.
    :type profile: RecipeProfile
    :returns: The profile as stored.
    :rtype: RecipeProfile
    """
    return await databases().open(book_id).change(lambda repos, _book_record: repos.recipe_profiles.add(profile))


class TestProfiles:
    """Tests for saving, listing, renaming and deleting."""

    async def test_profile_is_created_listed_renamed_and_deleted(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify a profile is answered 201, listed with its steps in order, renamed by PATCH and deleted with 204.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        created = await fx_client.post(fx_profiles_path, json=_body())
        profile = RecipeProfileSchema.model_validate_json(created.content)
        renamed = await fx_client.patch(f'{fx_profiles_path}/{profile.id}', json={NAME_FIELD: OTHER_NAME})
        listed = await fx_client.get(fx_profiles_path)
        deleted = await fx_client.delete(f'{fx_profiles_path}/{profile.id}')
        after = await fx_client.get(fx_profiles_path)
        expect(created.status_code == status.HTTP_201_CREATED)
        expect((profile.stage, profile.name) == (Stage.GEOMETRY, PROFILE_NAME))
        expect([(step.params[STRENGTH], step.enabled) for step in profile.steps] == [(3, False), (2, True)])
        expect(RecipeProfileSchema.model_validate_json(renamed.content).name == OTHER_NAME)
        expect([item[NAME_FIELD] for item in listed.json()[ITEMS]] == [OTHER_NAME])
        expect(deleted.status_code == status.HTTP_204_NO_CONTENT)
        expect(after.json()[TOTAL_FIELD] == 0)
        assert_expectations()

    async def test_a_listing_may_name_a_stage(self, fx_client: httpx.AsyncClient, fx_profiles_path: str) -> None:
        """Verify the stage in the query limits the listing to the profiles of that stage.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        await fx_client.post(fx_profiles_path, json=_body())
        listed = await fx_client.get(fx_profiles_path, params={STAGE_FIELD: Stage.CLEANUP})
        expect(listed.status_code == status.HTTP_200_OK)
        expect(listed.json()[TOTAL_FIELD] == 0)
        assert_expectations()

    async def test_steps_that_do_not_fit_are_a_422_problem(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify a parameter the processor does not know answers 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        body = _body() | {STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {'unknown': 1}}]}
        response = await fx_client.post(fx_profiles_path, json=body)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_profile_the_book_does_not_have_is_a_404_problem(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str, fx_book: BookId
    ) -> None:
        """Verify a profile that is not in the book cannot be changed, deleted or applied.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        path = f'{fx_profiles_path}/{MISSING_PROFILE}'
        recipe = await _text_recipe(fx_client, fx_book)
        responses = [
            await fx_client.patch(path, json={NAME_FIELD: OTHER_NAME}),
            await fx_client.put(
                path, json={key: value for key, value in _body(OTHER_NAME).items() if key != STAGE_FIELD}
            ),
            await fx_client.put(_link_path(fx_book, recipe.id), json={PROFILE_ID_FIELD: str(MISSING_PROFILE)}),
            await fx_client.delete(path),
            await fx_client.post(_apply_path(fx_book, MISSING_PROFILE), json={'kind': 'text'}),
        ]
        expect([response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * len(responses))
        assert_expectations()


class TestApplyProfile:
    """Tests for applying a profile to a book."""

    async def test_apply_puts_the_steps_of_the_profile_into_the_recipe_of_the_kind(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str, fx_book: BookId
    ) -> None:
        """Verify the answer is 201 with the recipe of the kind, whose steps are the profile's, and no other changes.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        before = await _text_recipe(fx_client, fx_book)
        response = await fx_client.post(_apply_path(fx_book, profile.id), json={'kind': 'blank'})
        applied = AppliedProfileSchema.model_validate_json(response.content)
        after = await _text_recipe(fx_client, fx_book)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect((applied.recipe.kind, applied.recipe.profile_id) == (RecipeKind.BLANK, profile.id))
        expect(applied.recipe.steps == profile.steps)
        expect(applied.missing_processors == [])
        expect(after == before)
        assert_expectations()

    async def test_apply_needs_the_kind_and_knows_no_activation_or_pages(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str, fx_book: BookId
    ) -> None:
        """Verify a body without the kind of page, with one that is not a kind, or with the fields of the old body is refused.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        bodies = [{}, {'kind': 'plates'}, {'kind': 'text', 'activate': True, 'page_ids': []}]
        responses = [await fx_client.post(_apply_path(fx_book, profile.id), json=body) for body in bodies]
        assert [response.status_code for response in responses] == [
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
        ]

    async def test_a_missing_processor_is_named_in_the_answer(
        self, fx_client: httpx.AsyncClient, fx_databases: BookDatabasesFactory, fx_book: BookId
    ) -> None:
        """Verify the steps that can run are applied and the processor that cannot is listed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        profile = await _store(
            fx_databases,
            fx_book,
            make_recipe_profile(steps=(_step(MISSING_KEY), _step(FAKE_KEY, strength=2))),
        )
        response = await fx_client.post(_apply_path(fx_book, profile.id), json={'kind': 'text'})
        applied = AppliedProfileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect([step.processor_key for step in applied.recipe.steps] == [FAKE_KEY])
        expect(applied.missing_processors == [MISSING_KEY])
        assert_expectations()


class TestOrderOfProfile:
    """Tests for the order a profile is saved in and replaced under."""

    async def test_a_profile_is_answered_with_the_order_it_was_saved_in(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify the usual order is the default, and the free order the body asks for is stored and listed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        usual = RecipeProfileSchema.model_validate_json((await fx_client.post(fx_profiles_path, json=_body())).content)
        free = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body(OTHER_NAME) | {ORDER_FIELD: FREE_ORDER})).content
        )
        listed = await fx_client.get(fx_profiles_path)
        expect((usual.order, free.order) == ('usual', FREE_ORDER))
        expect([item[ORDER_FIELD] for item in listed.json()[ITEMS]] == ['usual', FREE_ORDER])
        assert_expectations()

    async def test_put_replaces_the_name_the_steps_and_the_order_and_keeps_the_stage(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify PUT answers the profile as replaced, and a listing reads the same.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        body = {
            NAME_FIELD: OTHER_NAME,
            ORDER_FIELD: FREE_ORDER,
            STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 5}}],
        }
        response = await fx_client.put(f'{fx_profiles_path}/{profile.id}', json=body)
        replaced = RecipeProfileSchema.model_validate_json(response.content)
        listed = await fx_client.get(fx_profiles_path)
        expect(response.status_code == status.HTTP_200_OK)
        expect(
            (replaced.id, replaced.stage, replaced.name, replaced.order)
            == (profile.id, Stage.GEOMETRY, OTHER_NAME, FREE_ORDER)
        )
        expect([step.params[STRENGTH] for step in replaced.steps] == [5])
        expect(listed.json()[ITEMS][0][STEPS_FIELD] == response.json()[STEPS_FIELD])
        assert_expectations()

    async def test_put_with_steps_that_do_not_fit_is_a_422_problem(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify a parameter the processor does not know answers 422 and leaves the profile as it was.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        body = _body() | {STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {'unknown': 1}}]}
        response = await fx_client.put(f'{fx_profiles_path}/{profile.id}', json=body)
        listed = await fx_client.get(fx_profiles_path)
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(len(listed.json()[ITEMS][0][STEPS_FIELD]) == len(profile.steps))
        assert_expectations()


class TestLinkRecipeToProfile:
    """Tests for the profile a recipe of a book is made from."""

    async def test_an_applied_profile_is_the_profile_of_the_recipe_it_made(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str, fx_book: BookId
    ) -> None:
        """Verify the answer of applying, and the recipe read after it, name the profile.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        applied = AppliedProfileSchema.model_validate_json(
            (await fx_client.post(_apply_path(fx_book, profile.id), json={'kind': 'text'})).content
        )
        active = await _text_recipe(fx_client, fx_book)
        expect(applied.recipe.profile_id == profile.id)
        expect((active.id, active.profile_id) == (applied.recipe.id, profile.id))
        assert_expectations()

    async def test_a_recipe_made_from_no_profile_answers_null(
        self, fx_client: httpx.AsyncClient, fx_book: BookId
    ) -> None:
        """Verify the built-in recipe of a book names no profile.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        assert (await _text_recipe(fx_client, fx_book)).profile_id is None

    async def test_put_links_a_recipe_to_a_profile_and_null_unlinks_it(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str, fx_book: BookId
    ) -> None:
        """Verify PUT records the profile without changing the steps, and a null profile clears it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        before = await _text_recipe(fx_client, fx_book)
        path = _link_path(fx_book, before.id)
        linked = await fx_client.put(path, json={PROFILE_ID_FIELD: str(profile.id)})
        read = await _text_recipe(fx_client, fx_book)
        unlinked = await fx_client.put(path, json={PROFILE_ID_FIELD: None})
        expect(linked.status_code == status.HTTP_200_OK)
        expect(RecipeSchema.model_validate_json(linked.content).profile_id == profile.id)
        expect((read.profile_id, read.steps, read.updated_at) == (profile.id, before.steps, before.updated_at))
        expect(RecipeSchema.model_validate_json(unlinked.content).profile_id is None)
        assert_expectations()

    async def test_a_profile_of_another_stage_is_a_422_problem(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str, fx_book: BookId
    ) -> None:
        """Verify a recipe cannot be linked to a profile of another stage.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        cleanup = {
            NAME_FIELD: OTHER_NAME,
            STAGE_FIELD: Stage.CLEANUP,
            STEPS_FIELD: [{PROCESSOR_FIELD: 'cleanup.fake', PARAMS_FIELD: {}}],
        }
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=cleanup)).content
        )
        recipe = await _text_recipe(fx_client, fx_book)
        response = await fx_client.put(_link_path(fx_book, recipe.id), json={PROFILE_ID_FIELD: str(profile.id)})
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_deleting_a_profile_clears_the_link_of_its_recipes(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str, fx_book: BookId
    ) -> None:
        """Verify a recipe made from a profile is still answered after the profile is deleted, with no profile.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        :param fx_book: A book of the signed-in account.
        :type fx_book: BookId
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        await fx_client.post(_apply_path(fx_book, profile.id), json={'kind': 'text'})
        await fx_client.delete(f'{fx_profiles_path}/{profile.id}')
        assert (await _text_recipe(fx_client, fx_book)).profile_id is None


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
    """Tests for the copy of a profile and the file a profile is exchanged by."""

    async def test_a_profile_is_duplicated_with_a_201_and_a_copy_name(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify the copy has the steps of the profile and is listed beside it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        response = await fx_client.post(f'{fx_profiles_path}/{profile.id}/duplicate')
        copy = RecipeProfileSchema.model_validate_json(response.content)
        listed = RecipeProfileSchema.model_validate((await fx_client.get(fx_profiles_path)).json()[ITEMS][1])
        expect(response.status_code == status.HTTP_201_CREATED)
        expect((copy.name, copy.stage) == (COPY_NAME, Stage.GEOMETRY))
        expect(
            [(step.params, step.enabled) for step in copy.steps]
            == [(step.params, step.enabled) for step in profile.steps]
        )
        expect(listed.id == copy.id)
        assert_expectations()

    async def test_a_profile_is_exported_as_a_versioned_file_without_identifiers(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify the file carries the version, the stage, the name, the order and the steps, and no identifier.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        response = await fx_client.get(f'{fx_profiles_path}/{profile.id}/{EXPORT_SUFFIX}')
        exported = ProfileFileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect(response.json()[VERSION_FIELD] == FILE_VERSION)
        expect((exported.name, exported.stage, exported.order) == (PROFILE_NAME, Stage.GEOMETRY, 'usual'))
        # Saving fills in the defaults of the processor, so the file holds more parameters than the body gave
        expect([(step.params[STRENGTH], step.enabled) for step in exported.steps] == [(3, False), (2, True)])
        expect(all('step_id' not in step for step in response.json()[STEPS_FIELD]))
        assert_expectations()

    async def test_an_exported_file_imports_as_a_profile_with_the_same_steps(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify the export of a profile is accepted by the import, which makes a profile of its own.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        profile = RecipeProfileSchema.model_validate_json(
            (await fx_client.post(fx_profiles_path, json=_body())).content
        )
        exported = await fx_client.get(f'{fx_profiles_path}/{profile.id}/{EXPORT_SUFFIX}')
        response = await fx_client.post(f'{fx_profiles_path}/import', json=exported.json())
        imported = RecipeProfileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect(imported.id != profile.id)
        expect(
            [(step.params, step.enabled) for step in imported.steps]
            == [(step.params, step.enabled) for step in profile.steps]
        )
        expect({step.step_id for step in imported.steps}.isdisjoint({step.step_id for step in profile.steps}))
        assert_expectations()

    async def test_a_file_of_the_first_version_imports_and_its_conditions_are_dropped(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify a file exported before the conditions went, whose steps say which pages they process, is accepted.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        steps = [
            {PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 3}, 'enabled': False, APPLIES_TO_FIELD: 'all'},
            {PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {STRENGTH: 2}, APPLIES_TO_FIELD: 'plates'},
        ]
        response = await fx_client.post(
            f'{fx_profiles_path}/import', json=_file(**{VERSION_FIELD: FIRST_FILE_VERSION, STEPS_FIELD: steps})
        )
        imported = RecipeProfileSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect([(step.params[STRENGTH], step.enabled) for step in imported.steps] == [(3, False), (2, True)])
        assert_expectations()

    async def test_a_file_of_the_current_version_that_names_a_condition_is_a_422_problem(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify the condition is a field of the first version only, so the current one refuses it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        steps = [{PROCESSOR_FIELD: FAKE_KEY, APPLIES_TO_FIELD: 'all'}]
        response = await fx_client.post(f'{fx_profiles_path}/import', json=_file(**{STEPS_FIELD: steps}))
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_file_that_does_not_validate_is_a_422_problem(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify a wrong version, an unknown field, a missing processor and bad parameters all answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        steps_of = {STEPS_FIELD: [{PROCESSOR_FIELD: FAKE_KEY, PARAMS_FIELD: {'unknown': 1}}]}
        missing = {STEPS_FIELD: [{PROCESSOR_FIELD: MISSING_KEY}]}
        responses = [
            await fx_client.post(f'{fx_profiles_path}/import', json=_file(**{VERSION_FIELD: FILE_VERSION + 1})),
            await fx_client.post(f'{fx_profiles_path}/import', json=_file(extra=1)),
            await fx_client.post(f'{fx_profiles_path}/import', json=_file(**missing)),
            await fx_client.post(f'{fx_profiles_path}/import', json=_file(**steps_of)),
            await fx_client.post(f'{fx_profiles_path}/import', json=_file(**{STEPS_FIELD: []})),
        ]
        listed = await fx_client.get(fx_profiles_path)
        expect(
            [response.status_code for response in responses] == [status.HTTP_422_UNPROCESSABLE_CONTENT] * len(responses)
        )
        expect(MISSING_KEY in responses[2].text)
        expect(listed.json()[TOTAL_FIELD] == 0)
        assert_expectations()

    async def test_a_profile_the_book_does_not_have_cannot_be_exported_or_copied(
        self, fx_client: httpx.AsyncClient, fx_profiles_path: str
    ) -> None:
        """Verify a profile that is not in the book answers 404 to the export and to the copy.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_profiles_path: Path of the recipe profiles of the book of the signed-in account.
        :type fx_profiles_path: str
        """
        responses = [
            await fx_client.get(f'{fx_profiles_path}/{MISSING_PROFILE}/{EXPORT_SUFFIX}'),
            await fx_client.post(f'{fx_profiles_path}/{MISSING_PROFILE}/duplicate'),
        ]
        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * len(responses)
