"""Tests for the endpoints that list, make, replace and remove the pagination sections of a book."""

from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.api.schemas.pagination import PaginationSectionSchema
from bookreviver.domain.enums import NumberDisplay
from tests.helpers.builders import make_page, make_project, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project
    from bookreviver.domain.entities import Page as BookPage

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
PLATE_PREFIX: str = 'Plate '
ROMAN_LIMIT: int = 3999
PAGE_COUNT: int = 4


class Book(NamedTuple):
    """A book of four pages of the signed-in account.

    :ivar project: The project of the book.
    :ivar pages: The pages in book order.
    """

    project: Project
    pages: list[BookPage]

    @property
    def sections_path(self) -> str:
        """The path of the pagination sections of the book."""
        return f'{PROJECTS_PATH}/{self.project.id}/pagination-sections'

    def body(self, index: int, **fields: Any) -> dict[str, Any]:
        """Build the body of a section that starts at a page, in Arabic numerals unless given another rule.

        :param index: Index of the first page in book order.
        :type index: int
        :param fields: Fields of the body other than the first page.
        :type fields: Any
        :returns: The request body.
        :rtype: dict[str, Any]
        """
        return {'first_page_id': str(self.pages[index].id), 'style': 'arabic', **fields}


@pytest.fixture
async def fx_book(fx_database: InMemoryDatabase, fx_actor: Actor) -> Book:
    """Commit a book of four text pages for the signed-in account.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=fx_actor.account_id)
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=PAGE_COUNT)
    pages = [make_page(project_id=project.id, order_key=key) for key in keys]
    await commit_project(fx_database, project, *pages)
    return Book(project=project, pages=pages)


async def _labels(client: httpx.AsyncClient, book: Book) -> list[str]:
    """Read the labels of the book's pages in book order.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: The book.
    :type book: Book
    :returns: The label of each page in the manifest.
    :rtype: list[str]
    """
    response = await client.get(f'{PROJECTS_PATH}/{book.project.id}/pages')
    return [item.label for item in Page[PageSchema].model_validate_json(response.content).items]


class TestCreateSection:
    """Tests for POST /projects/{project_id}/pagination-sections."""

    async def test_makes_a_section_that_numbers_the_pages_and_answers_it(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the answer is the stored section with a 201, and the pages from its first page take its numbers.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = fx_book.body(1, name='Preface', style='roman-lower', start=3, display='counted')

        response = await fx_client.post(fx_book.sections_path, json=body)

        section = PaginationSectionSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_201_CREATED)
        expect((section.first_page_id, section.name, section.start) == (fx_book.pages[1].id, 'Preface', 3))
        expect((section.display, section.kinds, section.project_id) == (NumberDisplay.COUNTED, [], fx_book.project.id))
        expect(await _labels(fx_client, fx_book) == ['', '[iii]', '[iv]', '[v]'])
        assert_expectations()

    async def test_a_series_by_kind_keeps_the_spaces_of_its_prefix(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the prefix is stored as sent, so ``Plate `` reaches the numbers with its space, and the kinds come back.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = fx_book.body(0, prefix=PLATE_PREFIX, kinds=['plate', 'frontispiece'], style='roman-upper')

        response = await fx_client.post(fx_book.sections_path, json=body)

        section = PaginationSectionSchema.model_validate_json(response.content)
        expect((section.prefix, [str(kind) for kind in section.kinds]) == (PLATE_PREFIX, ['frontispiece', 'plate']))
        assert_expectations()

    @pytest.mark.parametrize(
        'changes',
        [
            {'start': 0},
            {'start': ROMAN_LIMIT + 1, 'style': 'roman-lower'},
            {'style': 'greek'},
            {'display': 'hidden'},
            {'kinds': ['appendix']},
            {'kinds': ['plate', 'plate']},
            {'name': 'x' * 301},
            {'order_key': 'a0'},
        ],
        ids=[
            'zero-start',
            'roman-start',
            'unknown-style',
            'unknown-display',
            'unknown-kind',
            'repeated-kind',
            'long-name',
            'extra',
        ],
    )
    async def test_invalid_body_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: Book, changes: dict[str, Any]
    ) -> None:
        """Verify a start of zero, a Roman start past 3999, an unknown value, a repeated kind and an extra field answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param changes: Fields of the request body that differ from a valid one.
        :type changes: dict[str, Any]
        """
        response = await fx_client.post(fx_book.sections_path, json=fx_book.body(0, **changes))

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_second_section_of_the_flow_at_one_page_is_a_conflict_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a clash answers a 409 problem that names its reason and stores nothing.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await fx_client.post(fx_book.sections_path, json=fx_book.body(0))

        response = await fx_client.post(fx_book.sections_path, json=fx_book.body(0, start=5))

        listed = await fx_client.get(fx_book.sections_path)
        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect('starts at this page already' in response.json()['detail'])
        expect(len(listed.json()['items']) == 1)
        assert_expectations()

    async def test_numbers_past_the_roman_limit_are_a_conflict(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a section that runs on to 4000 in Roman numerals answers 409 and leaves the pages unnumbered.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(
            fx_book.sections_path, json=fx_book.body(0, style='roman-upper', start=ROMAN_LIMIT - 1)
        )

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(await _labels(fx_client, fx_book) == [''] * PAGE_COUNT)
        assert_expectations()

    async def test_page_of_another_project_and_project_of_another_account_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify a first page that is another project's, and a project of another account, both answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        stranger = make_project(owner_id=new_account_id())
        page = make_page(project_id=stranger.id)
        await commit_project(fx_database, stranger, page)

        foreign_page = await fx_client.post(
            fx_book.sections_path, json={'first_page_id': str(page.id), 'style': 'arabic'}
        )
        foreign_project = await fx_client.post(
            f'{PROJECTS_PATH}/{stranger.id}/pagination-sections',
            json={'first_page_id': str(page.id), 'style': 'arabic'},
        )

        expect(foreign_page.status_code == status.HTTP_404_NOT_FOUND)
        expect(foreign_project.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()


class TestListSections:
    """Tests for GET /projects/{project_id}/pagination-sections."""

    async def test_lists_the_sections_in_book_order(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify the sections come in the order of the pages they start at, whatever order they were made in.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await fx_client.post(fx_book.sections_path, json=fx_book.body(2))
        await fx_client.post(fx_book.sections_path, json=fx_book.body(0, style='roman-lower'))

        response = await fx_client.get(fx_book.sections_path)

        page = Page[PaginationSectionSchema].model_validate_json(response.content)
        expect([section.first_page_id for section in page.items] == [fx_book.pages[0].id, fx_book.pages[2].id])
        expect(page.total == len(page.items))
        assert_expectations()

    async def test_a_project_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify the sections of another account's project are answered not found.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        stranger = make_project(owner_id=new_account_id())
        await commit_project(fx_database, stranger)

        response = await fx_client.get(f'{PROJECTS_PATH}/{stranger.id}/pagination-sections')

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestChangeSections:
    """Tests for PUT and DELETE /projects/{project_id}/pagination-sections/{section_id}."""

    async def test_put_replaces_the_section_and_renumbers_the_pages(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the section is replaced whole, keeps its identifier, and the labels follow it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        made = (await fx_client.post(fx_book.sections_path, json=fx_book.body(0))).json()

        response = await fx_client.put(
            f'{fx_book.sections_path}/{made["id"]}', json=fx_book.body(2, start=7, style='alpha-lower')
        )

        section = PaginationSectionSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((str(section.id), section.first_page_id) == (made['id'], fx_book.pages[2].id))
        expect(await _labels(fx_client, fx_book) == ['', '', 'g', 'h'])
        assert_expectations()

    async def test_delete_removes_the_section_and_empties_its_labels(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a deleted section answers 204 with no body, and a second delete answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        made = (await fx_client.post(fx_book.sections_path, json=fx_book.body(0))).json()
        path = f'{fx_book.sections_path}/{made["id"]}'

        deleted = await fx_client.delete(path)
        again = await fx_client.delete(path)

        expect((deleted.status_code, deleted.content) == (status.HTTP_204_NO_CONTENT, b''))
        expect(again.status_code == status.HTTP_404_NOT_FOUND)
        expect(await _labels(fx_client, fx_book) == [''] * PAGE_COUNT)
        assert_expectations()

    async def test_an_unknown_section_is_not_found(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify replacing a section that does not exist answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(f'{fx_book.sections_path}/{uuid4()}', json=fx_book.body(0))

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_a_page_exposes_whether_its_label_is_an_exception(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the page schema tells a label typed by hand from one a section gave.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await fx_client.post(fx_book.sections_path, json=fx_book.body(0))
        path = f'{PROJECTS_PATH}/{fx_book.project.id}/pages/{fx_book.pages[1].id}'
        computed = PageSchema.model_validate((await fx_client.get(path)).json())

        typed = PageSchema.model_validate((await fx_client.patch(path, json={'label': '2a'})).json())

        expect((computed.label, computed.label_manual) == ('2', False))
        expect((typed.label, typed.label_manual) == ('2a', True))
        assert_expectations()
