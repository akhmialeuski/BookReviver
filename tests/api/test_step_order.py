"""Tests for the order of steps on the recipe, variant and profile endpoints, over processors that declare a place."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from dishka import Provider, Scope, provide
from fastapi import status

from bookreviver.api.schemas.processing import ProcessorSchema, RecipeSchema
from bookreviver.api.schemas.profiles import AppliedProfileSchema
from bookreviver.domain.enums import OrderMode, OrderRuleKind
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.services.recipes import DefaultRecipes
from tests.helpers.builders import make_project
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.processing import DEFAULTS
from tests.helpers.processors import (
    FIRST_KEY,
    SECOND_KEY,
    SECOND_REASON,
    THIRD_KEY,
    THIRD_REASON,
    CleanupProcessor,
    FakeProcessor,
    FirstProcessor,
    OpeningProcessor,
    SecondProcessor,
    ThirdProcessor,
)
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project

pytestmark = pytest.mark.anyio

API_PATH: str = '/api/v1'
PROCESSORS_PATH: str = f'{API_PATH}/processors'
PROFILES_PATH: str = f'{API_PATH}/recipe-profiles'
ITEMS: str = 'items'
DETAIL: str = 'detail'
NAME: str = 'Ordered'
FAKE_KEY: str = FakeProcessor.spec.key


class OrderedFakesProvider(Provider):
    """Replaces the catalogue of processors with the fakes of the tests, among which some declare a place."""

    scope = Scope.APP

    @provide(override=True)
    def processor_catalog(self) -> ProcessorCatalog:
        """Offer the fakes of the processing tests and the processors that ask for a place.

        :returns: The catalogue of the tests.
        :rtype: ProcessorCatalog
        """
        return FakeCatalogue(
            [
                SplitNone(),
                FakeProcessor(),
                CleanupProcessor(),
                OpeningProcessor(),
                FirstProcessor(),
                SecondProcessor(),
                ThirdProcessor(),
            ]
        )

    @provide(override=True)
    def default_recipes(self) -> DefaultRecipes:
        """Give the recipes the tests' stages start with.

        :returns: The default recipes of the tests.
        :rtype: DefaultRecipes
        """
        return DEFAULTS


@pytest.fixture
def fx_extra_providers() -> Sequence[Provider]:
    """Replace the catalogue of processors with the one of the tests.

    :returns: The provider of the catalogue and the recipes.
    :rtype: Sequence[Provider]
    """
    return [OrderedFakesProvider()]


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


def recipe_path(project: Project) -> str:
    """Return the path of the active recipe of the geometry stage of a project.

    :param project: The project.
    :type project: Project
    :returns: The path of the request.
    :rtype: str
    """
    return f'{API_PATH}/projects/{project.id}/stages/geometry/recipe'


def variants_path(project: Project) -> str:
    """Return the path of the variants of the geometry stage of a project.

    :param project: The project.
    :type project: Project
    :returns: The path of the request.
    :rtype: str
    """
    return f'{API_PATH}/projects/{project.id}/stages/geometry/variants'


def body_of(*keys: str, order: OrderMode | None = None) -> dict[str, object]:
    """Build the body of a recipe with steps of the processors in the given order.

    :param keys: Keys of the processors.
    :type keys: str
    :param order: The order to keep, or None to leave it to the default.
    :type order: OrderMode | None
    :returns: The JSON body of a request.
    :rtype: dict[str, object]
    """
    body: dict[str, object] = {'name': NAME, 'steps': [{'processor_key': key} for key in keys]}
    if order is not None:
        body['order'] = order
    return body


class TestCatalogue:
    """Tests for the places the processors list."""

    async def test_a_processor_lists_the_places_it_asks_for_with_their_reasons(
        self, fx_client: httpx.AsyncClient
    ) -> None:
        """Verify the usual and the required places are in the listing, each with its reason.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get(PROCESSORS_PATH)
        listed = {item['key']: ProcessorSchema.model_validate(item) for item in response.json()[ITEMS]}
        expect([(rule.processor_key, rule.reason) for rule in listed[SECOND_KEY].after] == [(FIRST_KEY, SECOND_REASON)])
        expect(
            [(rule.processor_key, rule.reason) for rule in listed[THIRD_KEY].requires_after]
            == [(SECOND_KEY, THIRD_REASON)]
        )
        expect(listed[FAKE_KEY].after == [] and listed[FAKE_KEY].requires_after == [])
        assert_expectations()


class TestRecipeOrder:
    """Tests for saving a recipe whose steps stand off their places."""

    async def test_a_step_off_its_usual_place_is_saved_and_named_in_the_answer(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the recipe is stored and the answer says which step is out of place and why.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Book of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.put(recipe_path(fx_project), json=body_of(SECOND_KEY, FIRST_KEY))
        recipe = RecipeSchema.model_validate_json(response.content)
        (issue,) = recipe.order_issues
        expect(response.status_code == status.HTTP_200_OK)
        expect([step.processor_key for step in recipe.steps] == [SECOND_KEY, FIRST_KEY])
        expect((issue.step_id, issue.other_step_id) == (recipe.steps[0].step_id, recipe.steps[1].step_id))
        expect((issue.kind, issue.reason) == (OrderRuleKind.USUAL, SECOND_REASON))
        assert_expectations()

    async def test_a_recipe_in_its_usual_order_has_no_issue(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the answer lists nothing for steps that keep their places.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Book of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.put(recipe_path(fx_project), json=body_of(FIRST_KEY, SECOND_KEY, THIRD_KEY))
        assert RecipeSchema.model_validate_json(response.content).order_issues == []

    async def test_a_step_where_it_cannot_work_is_refused_with_its_reason_and_the_recipe_stays(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the usual order answers 422 with the reason as the detail, and stores nothing.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Book of the signed-in account.
        :type fx_project: Project
        """
        refused = await fx_client.put(recipe_path(fx_project), json=body_of(THIRD_KEY, SECOND_KEY))
        kept = await fx_client.get(recipe_path(fx_project))
        expect(refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(refused.json()[DETAIL] == THIRD_REASON)
        expect([step['processor_key'] for step in kept.json()['steps']] == [FAKE_KEY])
        assert_expectations()

    async def test_the_free_order_saves_the_step_and_warns_of_it_on_every_later_read(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the free order stores the recipe, and the issue of the required kind is read back with it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Book of the signed-in account.
        :type fx_project: Project
        """
        body = body_of(THIRD_KEY, SECOND_KEY, order=OrderMode.FREE)
        saved = await fx_client.put(recipe_path(fx_project), json=body)
        read = await fx_client.get(recipe_path(fx_project))
        expect(saved.status_code == status.HTTP_200_OK)
        for response in (saved, read):
            issues = RecipeSchema.model_validate_json(response.content).order_issues
            expect([(issue.kind, issue.reason) for issue in issues] == [(OrderRuleKind.REQUIRED, THIRD_REASON)])
        assert_expectations()

    async def test_a_variant_is_refused_and_saved_by_the_same_rule(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify a variant that is added or saved keeps the order as the active recipe does.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Book of the signed-in account.
        :type fx_project: Project
        """
        refused = await fx_client.post(variants_path(fx_project), json=body_of(THIRD_KEY, SECOND_KEY))
        created = await fx_client.post(variants_path(fx_project), json=body_of(FIRST_KEY, SECOND_KEY))
        variant = RecipeSchema.model_validate_json(created.content)
        put_url = f'{variants_path(fx_project)}/{variant.id}'
        put_refused = await fx_client.put(put_url, json=body_of(THIRD_KEY, SECOND_KEY))
        put_free = await fx_client.put(put_url, json=body_of(THIRD_KEY, SECOND_KEY, order=OrderMode.FREE))
        listed = await fx_client.get(variants_path(fx_project))
        expect(refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(created.status_code == status.HTTP_201_CREATED and variant.order_issues == [])
        expect(put_refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(put_free.status_code == status.HTTP_200_OK)
        expect([len(item['order_issues']) for item in listed.json()[ITEMS] if item['id'] == str(variant.id)] == [1])
        assert_expectations()


class TestProfileOrder:
    """Tests for saving and applying a profile whose steps stand off their places."""

    async def test_a_profile_is_refused_a_step_where_it_cannot_work_unless_the_order_is_free(
        self, fx_client: httpx.AsyncClient
    ) -> None:
        """Verify saving the steps on the screen as a profile keeps the order as saving a recipe does.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        steps = [{'processor_key': THIRD_KEY}, {'processor_key': SECOND_KEY}]
        body = {'name': NAME, 'stage': 'geometry', 'steps': steps}
        refused = await fx_client.post(PROFILES_PATH, json=body)
        saved = await fx_client.post(PROFILES_PATH, json=body | {'order': OrderMode.FREE})
        expect(refused.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(refused.json()[DETAIL] == THIRD_REASON)
        expect(saved.status_code == status.HTTP_201_CREATED)
        assert_expectations()

    async def test_applying_a_profile_names_the_steps_that_are_out_of_place(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify the recipe a profile makes is answered with its issues, though the profile was saved before them.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Book of the signed-in account.
        :type fx_project: Project
        """
        steps = [{'processor_key': SECOND_KEY}, {'processor_key': FIRST_KEY}]
        created = await fx_client.post(PROFILES_PATH, json={'name': NAME, 'stage': 'geometry', 'steps': steps})
        profile_id = created.json()['id']
        applied = await fx_client.post(
            f'{API_PATH}/projects/{fx_project.id}/recipe-profiles/{profile_id}/apply', json={}
        )
        answer = AppliedProfileSchema.model_validate_json(applied.content)
        expect(applied.status_code == status.HTTP_201_CREATED)
        expect([issue.reason for issue in answer.recipe.order_issues] == [SECOND_REASON])
        assert_expectations()
