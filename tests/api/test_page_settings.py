"""Tests for the endpoints that set and take back the values of a step for pages, the odd pages, the even pages, groups."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.schemas.processing import RecipeSchema
from bookreviver.domain.enums import RecipeKind
from bookreviver.domain.values import Renditions
from tests.helpers.builders import make_page, make_project, make_scan, make_source
from tests.helpers.processing import ProcessingFakesProvider
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import Provider

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Page, Project

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2')
ITEMS: str = 'items'
CHANGES: str = 'changes'
STRENGTH: str = 'strength'
STRONGER: int = 2
STRONGEST: int = 3
SCOPE: str = 'scope'
PAGE_IDS: str = 'page_ids'
GROUP_LABEL: str = 'group_label'
VALUE: str = 'value'
BATCH_ID: str = 'batch_id'
EFFECTIVE: str = 'effective'
PARTS: str = 'parts'
PARAMS: str = 'params'


class Book(NamedTuple):
    """A book of the signed-in account with three pages and the step of its geometry recipe.

    :ivar project: The project of the book.
    :ivar pages: The pages in book order.
    :ivar step_id: Identifier of the only step of the geometry recipe of text pages.
    """

    project: Project
    pages: Sequence[Page]
    step_id: str

    @property
    def values(self) -> str:
        """The path of the values of the strength of the step."""
        return f'{PROJECTS_PATH}/{self.project.id}/stages/geometry/steps/{self.step_id}/values/{STRENGTH}'

    def settings(self, index: int) -> str:
        """Give the path of the settings of a page of the book in the geometry stage.

        :param index: Index of the page in book order.
        :type index: int
        :returns: The path.
        :rtype: str
        """
        return f'{PROJECTS_PATH}/{self.project.id}/pages/{self.pages[index].id}/settings/geometry'

    def undo(self, index: int) -> str:
        """Give the path that takes back the newest change of the step on a page of the book.

        :param index: Index of the page in book order.
        :type index: int
        :returns: The path.
        :rtype: str
        """
        return f'{PROJECTS_PATH}/{self.project.id}/pages/{self.pages[index].id}/history/geometry/{self.step_id}/undo'


@pytest.fixture
def fx_extra_providers() -> Sequence[Provider]:
    """Replace the catalogue of processors with the fakes of the tests.

    :returns: The provider of the fake catalogue and recipes.
    :rtype: Sequence[Provider]
    """
    return [ProcessingFakesProvider()]


@pytest.fixture
async def fx_book(fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor) -> Book:
    """Commit a book of three pages and read the step of its geometry recipe.

    :param fx_client: Client of the running application.
    :type fx_client: httpx.AsyncClient
    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=fx_actor.account_id)
    sources = [make_source(project_id=project.id, name=f'{order_key}.pdf') for order_key in ORDER_KEYS]
    scans = [evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True)) for source in sources]
    pages = [
        make_page(project_id=project.id, order_key=order_key, scan=scan)
        for order_key, scan in zip(ORDER_KEYS, scans, strict=True)
    ]
    await commit_project(fx_database, project, *pages, sources=sources, scans=scans)
    listed = await fx_client.get(f'{PROJECTS_PATH}/{project.id}/stages/geometry/recipes')
    recipe = next(
        recipe
        for recipe in (RecipeSchema.model_validate(item) for item in listed.json()[ITEMS])
        if recipe.kind is RecipeKind.TEXT
    )
    return Book(project=project, pages=pages, step_id=str(recipe.steps[0].step_id))


async def strengths(client: httpx.AsyncClient, book: Book) -> list[int]:
    """Read the strength each page of the book runs the step with, as the listing of its settings gives it.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: The book.
    :type book: Book
    :returns: The strength of each page in book order, which is the recipe's for a page the listing leaves out.
    :rtype: list[int]
    """
    found: list[int] = []
    for index in range(len(book.pages)):
        listed = (await client.get(book.settings(index))).json()[ITEMS]
        found.append(listed[0][EFFECTIVE][STRENGTH] if listed else 1)
    return found


class TestValuesForParts:
    """Tests for setting and taking back a value for the even pages, a group and the pages of the user's choice."""

    async def test_even_pages_take_the_value_and_one_undo_gives_it_back(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the value goes to the even pages in one batch, is listed on every page, and an undo takes it back.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(fx_book.values, json={SCOPE: 'even', VALUE: STRONGER})
        body = response.json()
        seen = await strengths(fx_client, fx_book)
        listed = (await fx_client.get(fx_book.settings(0))).json()[ITEMS][0]
        undone = await fx_client.post(fx_book.undo(1), json={})
        expect(response.status_code == status.HTTP_200_OK)
        expect([change['page_id'] for change in body[CHANGES]] == [str(fx_book.pages[1].id)])
        expect({change[BATCH_ID] for change in body[CHANGES]} == {body[BATCH_ID]})
        expect([(change['scope'], change[GROUP_LABEL]) for change in body[CHANGES]] == [('even', '')])
        expect(seen == [1, STRONGER, 1])
        expect(
            listed[PARAMS] == {}
            and [(part[SCOPE], part[PARAMS]) for part in listed[PARTS]] == [('even', {STRENGTH: STRONGER})]
        )
        expect(undone.status_code == status.HTTP_200_OK and await strengths(fx_client, fx_book) == [1, 1, 1])
        assert_expectations()

    async def test_the_pages_the_user_chose_take_the_value_and_it_is_taken_back_from_one(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the pages named take the value as their own, and a page takes its own back by the query.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        chosen = [str(fx_book.pages[0].id), str(fx_book.pages[2].id)]
        await fx_client.put(fx_book.values, json={SCOPE: 'pages', PAGE_IDS: chosen, VALUE: STRONGEST})
        seen = await strengths(fx_client, fx_book)
        deleted = await fx_client.delete(fx_book.values, params={SCOPE: 'pages', PAGE_IDS: chosen[0]})
        expect(seen == [STRONGEST, 1, STRONGEST])
        expect(deleted.status_code == status.HTTP_200_OK and await strengths(fx_client, fx_book) == [1, 1, STRONGEST])
        assert_expectations()

    async def test_a_group_nobody_is_in_has_nobody_to_take_the_value(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a value for a group no page is in is refused instead of stored for nobody.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        nobody = await fx_client.put(fx_book.values, json={SCOPE: 'group', GROUP_LABEL: 'Index', VALUE: STRONGER})
        assert nobody.status_code == status.HTTP_404_NOT_FOUND

    async def test_taking_back_what_is_not_set_is_a_404(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify taking a field back from a part of the pages that has no value for it is refused.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.delete(fx_book.values, params={SCOPE: 'odd'})
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestRefusals:
    """Tests for the bodies and the fields a value refuses."""

    @pytest.mark.parametrize(
        'body',
        [
            {VALUE: 1},
            {SCOPE: 'everywhere', VALUE: 1},
            {SCOPE: 'pages', VALUE: 1},
            {SCOPE: 'group', VALUE: 1},
            {SCOPE: 'even', PAGE_IDS: [str(uuid4())], VALUE: 1},
            {SCOPE: 'odd', GROUP_LABEL: 'Index', VALUE: 1},
            {SCOPE: 'odd'},
            {SCOPE: 'odd', VALUE: 1, 'unknown': True},
        ],
        ids=[
            'no-scope',
            'unknown-scope',
            'pages-without-pages',
            'group-without-label',
            'pages-for-a-side',
            'label-for-a-side',
            'no-value',
            'extra',
        ],
    )
    async def test_a_body_that_does_not_fit_is_a_422(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, object]
    ) -> None:
        """Verify an unknown scope, a missing page, label or value, and an unknown field are refused.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: Body under test.
        :type body: dict[str, object]
        """
        response = await fx_client.put(fx_book.values, json=body)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_field_the_processor_does_not_have_is_a_422_and_nothing_is_stored(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a value out of the parameters of the processor is refused and the pages stay as they were.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(
            fx_book.values.replace(STRENGTH, 'no_such_field'), json={SCOPE: 'even', VALUE: 1}
        )
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(await strengths(fx_client, fx_book) == [1, 1, 1])
        assert_expectations()

    async def test_a_step_that_no_recipe_has_is_a_404(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a value for a step the recipes of the stage do not have is refused.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        path = fx_book.values.replace(fx_book.step_id, str(uuid4()))
        response = await fx_client.put(path, json={SCOPE: 'even', VALUE: STRONGER})
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_a_page_of_another_project_is_a_404(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a page that is not one of the project is refused like a missing page.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(fx_book.values, json={SCOPE: 'pages', PAGE_IDS: [str(uuid4())], VALUE: STRONGER})
        assert response.status_code == status.HTTP_404_NOT_FOUND
