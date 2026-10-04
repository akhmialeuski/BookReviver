"""Tests for the endpoints that reset the steps of a stage to their defaults, on one page or on every page."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.schemas.processing import RecipeSchema
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
TOTAL: str = 'total'
STRENGTH: str = 'strength'
STRONGER: int = 2
SCOPE: str = 'scope'
PAGE_ID: str = 'page_id'
STEP_ID: str = 'step_id'
ROTATION_FORM: dict[str, str] = {'kind': 'rotation', 'geometry': '{"degrees": 1.5}'}
PAGE_STEP: str = 'page-step'
EVERY_STAGE: str = 'stage'
EVERY_PAGE: str = 'step'
ONE_PAGE: str = 'page'
BATCH_ID: str = 'batch_id'
RESET_SOURCE: str = 'reset'
CONFIRM: str = 'confirm'


class Book(NamedTuple):
    """A book of the signed-in account with three pages and the step of its geometry recipe.

    :ivar project: The project of the book.
    :ivar pages: The pages in book order.
    :ivar step_id: Identifier of the only step of the active geometry recipe.
    """

    project: Project
    pages: Sequence[Page]
    step_id: str

    @property
    def path(self) -> str:
        """The path of the project."""
        return f'{PROJECTS_PATH}/{self.project.id}'

    def page_path(self, index: int) -> str:
        """Give the path of a page of the book.

        :param index: Index of the page in book order.
        :type index: int
        :returns: The path.
        :rtype: str
        """
        return f'{self.path}/pages/{self.pages[index].id}'

    def undo(self, index: int) -> str:
        """Give the path that takes back the newest change of the step on a page of the book.

        :param index: Index of the page in book order.
        :type index: int
        :returns: The path.
        :rtype: str
        """
        return f'{self.page_path(index)}/history/geometry/{self.step_id}/undo'

    @property
    def reset(self) -> str:
        """The path of the reset of the geometry stage."""
        return f'{self.path}/stages/geometry/reset'

    @property
    def impact(self) -> str:
        """The path of the count of what a reset of the geometry stage would take."""
        return f'{self.path}/stages/geometry/reset-impact'

    def body(self, scope: str, *, page: int = 0, confirm: bool = False) -> dict[str, object]:
        """Give the body of a reset that names the first step and a page.

        :param scope: Scope of the reset.
        :type scope: str
        :param page: Index of the open page.
        :type page: int
        :param confirm: Whether the body confirms the reset.
        :type confirm: bool
        :returns: The body.
        :rtype: dict[str, object]
        """
        return {SCOPE: scope, PAGE_ID: str(self.pages[page].id), STEP_ID: self.step_id, CONFIRM: confirm}


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
    recipe = RecipeSchema.model_validate_json(
        (await fx_client.get(f'{PROJECTS_PATH}/{project.id}/stages/geometry/recipe')).content
    )
    return Book(project=project, pages=pages, step_id=str(recipe.steps[0].step_id))


async def work(client: httpx.AsyncClient, book: Book, pages: Sequence[int], *, with_edit: Sequence[int] = ()) -> None:
    """Give pages a setting of the step, and some of them an edit as well.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: The book.
    :type book: Book
    :param pages: Indices of the pages that change the strength of the step.
    :type pages: Sequence[int]
    :param with_edit: Indices of the pages that have a rotation edit of the step.
    :type with_edit: Sequence[int]
    """
    for index in pages:
        await client.put(
            f'{book.page_path(index)}/settings/geometry/{book.step_id}/{STRENGTH}', json={'value': STRONGER}
        )
    for index in with_edit:
        await client.put(f'{book.page_path(index)}/edits/geometry/{book.step_id}', data=ROTATION_FORM)


async def counts(client: httpx.AsyncClient, book: Book) -> list[tuple[int, int]]:
    """Count the settings and the edits each page of the book has in the geometry stage.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: The book.
    :type book: Book
    :returns: One pair of the steps with settings and the edits for each page, in book order.
    :rtype: list[tuple[int, int]]
    """
    found: list[tuple[int, int]] = []
    for index in range(len(book.pages)):
        settings = await client.get(f'{book.page_path(index)}/settings/geometry')
        edits = await client.get(f'{book.page_path(index)}/edits/geometry')
        found.append((settings.json()[TOTAL], edits.json()[TOTAL]))
    return found


class TestResetOfThePage:
    """Tests for the reset of a step, or of every step of the stage, on the open page."""

    @pytest.mark.parametrize('scope', [PAGE_STEP, ONE_PAGE], ids=[PAGE_STEP, ONE_PAGE])
    async def test_the_setting_and_the_edit_go_in_one_batch_and_one_undo_gives_them_back(
        self, fx_client: httpx.AsyncClient, fx_book: Book, scope: str
    ) -> None:
        """Verify both layers go from the open page only, from a reset, and one undo brings them back.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param scope: Scope under test.
        :type scope: str
        """
        await work(fx_client, fx_book, [0, 1], with_edit=[0])
        response = await fx_client.post(fx_book.reset, json=fx_book.body(scope))
        body = response.json()
        gone = await counts(fx_client, fx_book)
        undone = await fx_client.post(fx_book.undo(0), json={})
        back = await counts(fx_client, fx_book)
        expect(response.status_code == status.HTTP_200_OK)
        expect(
            [(change['layer'], change['source']) for change in body[CHANGES]]
            == [('settings', RESET_SOURCE), ('hand', RESET_SOURCE)]
        )
        expect({change[BATCH_ID] for change in body[CHANGES]} == {body[BATCH_ID]})
        expect(gone == [(0, 0), (1, 0), (0, 0)])
        expect(len(undone.json()[CHANGES]) == len(body[CHANGES]))
        expect(back == [(1, 1), (1, 0), (0, 0)])
        assert_expectations()


class TestResetOfEveryPage:
    """Tests for the reset of a step, or of the whole stage, on every page, which asks for a confirmation."""

    @pytest.mark.parametrize('scope', [EVERY_PAGE, EVERY_STAGE], ids=[EVERY_PAGE, EVERY_STAGE])
    async def test_the_count_asks_the_confirmation_and_one_undo_gives_the_work_back_everywhere(
        self, fx_client: httpx.AsyncClient, fx_book: Book, scope: str
    ) -> None:
        """Verify the count names the pages, an unconfirmed reset is a 409, and a confirmed one is undone at once.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param scope: Scope under test.
        :type scope: str
        """
        await work(fx_client, fx_book, [0, 1], with_edit=[2])
        counted = await fx_client.post(fx_book.impact, json=fx_book.body(scope))
        refused = await fx_client.post(fx_book.reset, json=fx_book.body(scope))
        kept = await counts(fx_client, fx_book)
        done = await fx_client.post(fx_book.reset, json=fx_book.body(scope, confirm=True))
        gone = await counts(fx_client, fx_book)
        undone = await fx_client.post(fx_book.undo(1), json={})
        back = await counts(fx_client, fx_book)
        expect(counted.json() == {SCOPE: scope, 'hand_pages': 1, 'settings_pages': 2, 'affected': 3})
        expect(refused.status_code == status.HTTP_409_CONFLICT and '3 pages' in refused.json()['detail'])
        expect(kept == [(1, 0), (1, 0), (0, 1)])
        expect(done.status_code == status.HTTP_200_OK and len(done.json()[CHANGES]) == 3)
        expect({change[BATCH_ID] for change in done.json()[CHANGES]} == {done.json()[BATCH_ID]})
        expect(gone == [(0, 0)] * 3)
        expect(undone.status_code == status.HTTP_200_OK and len(undone.json()[CHANGES]) == 3)
        expect(back == kept)
        assert_expectations()

    async def test_a_reset_that_takes_nothing_needs_no_confirmation(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a book without work answers a reset of every page with an empty batch, not a 409.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(fx_book.reset, json={SCOPE: EVERY_STAGE})
        assert (response.status_code, response.json()[CHANGES]) == (status.HTTP_200_OK, [])


class TestRefusals:
    """Tests for the bodies and the steps a reset refuses."""

    @pytest.mark.parametrize(
        'body',
        [
            {},
            {SCOPE: 'everywhere'},
            {SCOPE: ONE_PAGE},
            {SCOPE: PAGE_STEP, STEP_ID: str(uuid4())},
            {SCOPE: EVERY_PAGE},
            {SCOPE: EVERY_STAGE, 'unknown': True},
        ],
        ids=['no-scope', 'unknown-scope', 'page-without-page', 'page-step-without-page', 'step-without-step', 'extra'],
    )
    @pytest.mark.parametrize('suffix', [RESET_SOURCE, 'reset-impact'])
    async def test_a_body_that_does_not_fit_is_a_422(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, object], suffix: str
    ) -> None:
        """Verify the reset and its count refuse an unknown scope, a missing page or step, and an unknown field.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: Body under test.
        :type body: dict[str, object]
        :param suffix: The last part of the path.
        :type suffix: str
        """
        response = await fx_client.post(f'{fx_book.path}/stages/geometry/{suffix}', json=body)
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_step_that_no_recipe_has_is_a_404(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a reset of a step the recipes of the stage do not have answers 404, and so does its count.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {**fx_book.body(EVERY_PAGE), STEP_ID: str(uuid4())}
        reset = await fx_client.post(fx_book.reset, json=body)
        impact = await fx_client.post(fx_book.impact, json=body)
        assert (reset.status_code, impact.status_code) == (status.HTTP_404_NOT_FOUND,) * 2

    async def test_a_page_of_another_project_is_a_404(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a page that is not one of the project is refused like a missing page.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {**fx_book.body(PAGE_STEP), PAGE_ID: str(uuid4())}
        response = await fx_client.post(fx_book.reset, json=body)
        assert response.status_code == status.HTTP_404_NOT_FOUND
