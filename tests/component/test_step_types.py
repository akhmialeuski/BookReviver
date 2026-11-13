"""Tests for the route that replaces the type of a step, and for the save of a recipe that tries to change it."""

from typing import TYPE_CHECKING, override

import pytest
from delayed_assert import assert_expectations, expect
from dishka import provide
from fastapi import status

from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.core.plugins import ProcessorCatalog
from bookreviver.domain.pages import RecipeKind
from bookreviver.plugins.pages.blank import BlankPage
from bookreviver.plugins.split.split_none import SplitNone
from tests.component.test_processing import (
    ITEMS,
    PROBLEM_MEDIA_TYPE,
    Book,
    recipes_of,
    set_strength,
    store_scan_images,
)
from tests.harness.builders.processing import ProcessingFakesProvider
from tests.harness.builders.seeding import BookLayout, ScanLayout, SourceLayout, seed_book
from tests.harness.fakes.processing import FakeCatalogue
from tests.harness.fakes.processors import MEASURING_KEY, CleanupProcessor, FakeProcessor, MeasuringProcessor

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import Provider

    from bookreviver.domain.accounts import Actor
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.harness.adapters.storage import SharedBookDirectories

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
CONTENT_TYPE_HEADER: str = 'content-type'


class TypesProvider(ProcessingFakesProvider):
    """Offers a second processor of the geometry beside the fakes of the tests, which a step can take as its type."""

    @override
    @provide(override=True)
    def processor_catalog(self) -> ProcessorCatalog:
        """Offer the fakes of the tests and ``geometry.measuring``.

        :returns: The catalogue of the tests.
        :rtype: ProcessorCatalog
        """
        return FakeCatalogue([SplitNone(), BlankPage(), FakeProcessor(), CleanupProcessor(), MeasuringProcessor()])


@pytest.fixture
def fx_extra_providers() -> Sequence[Provider]:
    """Replace the catalogue of processors with the fakes of the tests and a second processor of the geometry.

    :returns: The provider of the catalogue and the recipes.
    :rtype: Sequence[Provider]
    """
    return [TypesProvider()]


@pytest.fixture
async def fx_book(
    fx_catalogue: CatalogueDatabaseFactory,
    fx_directories: SharedBookDirectories,
    fx_actor: Actor,
    fx_databases: BookDatabasesFactory,
) -> Book:
    """Commit a book of one page and store the full image of its scan.

    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_directories: Opens the source store and the asset store of a book of the test.
    :type fx_directories: SharedBookDirectories
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The stored book.
    :rtype: Book
    """
    layout = BookLayout.scanned(SourceLayout.of(1, scan=ScanLayout(ready=True)))
    book = await seed_book(fx_catalogue(), layout, owner=fx_actor.account_id, databases=fx_databases())
    await store_scan_images(fx_directories.assets(book.book_id), book.scans[0])
    return Book(book_id=book.book_id, page=book.pages[0])


def type_path(book: Book, recipe: RecipeSchema) -> str:
    """Give the address of the type of the first step of a recipe of the geometry.

    :param book: Book of the signed-in account.
    :type book: Book
    :param recipe: The recipe.
    :type recipe: RecipeSchema
    :returns: The path.
    :rtype: str
    """
    return f'{book.path}/stages/geometry/recipes/{recipe.id}/steps/{recipe.steps[0].step_id}/type'


class TestStepType:
    """Tests for ``PUT .../recipes/{recipe_id}/steps/{step_id}/type``."""

    async def test_the_step_keeps_its_place_and_takes_the_type_and_the_history_shows_it(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the answer is the recipe with the step of the new type, and the history of the page names the change.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        text = (await recipes_of(fx_client, fx_book, 'geometry'))[RecipeKind.TEXT]
        step_id = text.steps[0].step_id
        await set_strength(fx_client, fx_book, str(step_id), 2)
        response = await fx_client.put(type_path(fx_book, text), json={'processor_key': MEASURING_KEY})
        recipe = RecipeSchema.model_validate_json(response.content)
        history = await fx_client.get(f'{fx_book.page_path}/history/geometry/{step_id}')
        expect(response.status_code == status.HTTP_200_OK)
        expect([(step.step_id, step.processor_key) for step in recipe.steps] == [(step_id, MEASURING_KEY)])
        expect([item['layer'] for item in history.json()[ITEMS]] == ['type'])
        assert_expectations()

    async def test_a_processor_of_another_stage_is_a_422_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the type of a step is a processor of the stage of its recipe.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        text = (await recipes_of(fx_client, fx_book, 'geometry'))[RecipeKind.TEXT]
        response = await fx_client.put(type_path(fx_book, text), json={'processor_key': CleanupProcessor.spec.key})
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()

    async def test_a_saved_recipe_that_changes_the_processor_of_a_step_is_a_409_that_names_the_replacement(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a save of the steps never changes a type, and says which operation does.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        text = (await recipes_of(fx_client, fx_book, 'geometry'))[RecipeKind.TEXT]
        body = {'steps': [{'processor_key': MEASURING_KEY, 'step_id': str(text.steps[0].step_id)}]}
        response = await fx_client.put(f'{fx_book.path}/stages/geometry/recipes/{text.id}', json=body)
        kept = (await recipes_of(fx_client, fx_book, 'geometry'))[RecipeKind.TEXT]
        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect('replacement of its type' in response.json()['detail'])
        expect([step.processor_key for step in kept.steps] == [FAKE_KEY])
        assert_expectations()
