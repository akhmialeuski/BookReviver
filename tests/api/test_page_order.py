"""Tests for the endpoints that put the pages of a book in order, on in-memory persistence."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.errors import AnchorInsideMovedPagesError
from tests.helpers.builders import make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import Any

    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project, Source
    from bookreviver.domain.entities import Page as BookPage

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
DETAIL_FIELD: str = 'detail'
FIRST_SOURCE_PAGES: int = 3
SECOND_SOURCE_PAGES: int = 2


class Book(NamedTuple):
    """A book of two sources whose five pages stand one after the other in the order of their upload.

    :ivar project: The project of the book, owned by the signed-in account.
    :ivar first: The source of the first three pages.
    :ivar second: The source of the last two pages.
    :ivar pages: The pages in book order.
    """

    project: Project
    first: Source
    second: Source
    pages: list[BookPage]

    @property
    def path(self) -> str:
        """The path of the project's resources."""
        return f'{PROJECTS_PATH}/{self.project.id}'

    def ids(self, *indexes: int) -> list[str]:
        """Return the identifiers of pages as text, which is how a request names them.

        :param indexes: Indexes of the pages in the book order they were stored in.
        :type indexes: int
        :returns: The identifiers, in the order of the indexes.
        :rtype: list[str]
        """
        return [str(self.pages[index].id) for index in indexes]


@pytest.fixture
async def fx_book(fx_database: InMemoryDatabase, fx_actor: Actor) -> Book:
    """Commit a book of five pages of two sources for the signed-in account.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=fx_actor.account_id)
    first, second = (
        make_source(project_id=project.id, name='part1.pdf'),
        make_source(project_id=project.id, name='part2.pdf'),
    )
    scans = [
        *(make_scan(source=first, number=number) for number in range(FIRST_SOURCE_PAGES)),
        *(make_scan(source=second, number=number) for number in range(SECOND_SOURCE_PAGES)),
    ]
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(scans))
    pages = [make_page(project_id=project.id, order_key=key, scan=scan) for key, scan in zip(keys, scans, strict=True)]
    await commit_project(fx_database, project, *pages, sources=[first, second], scans=scans)
    return Book(project=project, first=first, second=second, pages=pages)


async def _manifest(client: httpx.AsyncClient, book: Book, **params: Any) -> Page[PageSchema]:
    """Read the manifest of the book.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: The book whose manifest is read.
    :type book: Book
    :param params: Query parameters, such as ``included``.
    :type params: Any
    :returns: The first page of the manifest.
    :rtype: Page[PageSchema]
    """
    response = await client.get(f'{book.path}/pages', params=params)
    return Page[PageSchema].model_validate_json(response.content)


def _order(manifest: Page[PageSchema], book: Book) -> list[int]:
    """Translate the order of a manifest into indexes of the book's pages as stored.

    :param manifest: Manifest of the book.
    :type manifest: Page[PageSchema]
    :param book: The book.
    :type book: Book
    :returns: The index of every listed page in the book as it was stored, in the order listed.
    :rtype: list[int]
    """
    index = {page.id: position for position, page in enumerate(book.pages)}
    return [index[item.id] for item in manifest.items]


class TestMovePage:
    """Tests for POST /projects/{project_id}/pages/{page_id}/move."""

    @pytest.mark.parametrize(
        ('anchor_field', 'expected'),
        [('before_page_id', [4, 0, 1, 2, 3]), ('after_page_id', [0, 4, 1, 2, 3])],
        ids=['before', 'after'],
    )
    async def test_moves_the_page_and_returns_it_at_its_new_position(
        self, fx_client: httpx.AsyncClient, fx_book: Book, anchor_field: str, expected: list[int]
    ) -> None:
        """Verify a moved page answers with its new position and the manifest shows the new order.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param anchor_field: Field of the body naming the side of the anchor.
        :type anchor_field: str
        :param expected: Indexes of the stored pages in the order the manifest then lists them.
        :type expected: list[int]
        """
        response = await fx_client.post(
            f'{fx_book.path}/pages/{fx_book.pages[4].id}/move', json={anchor_field: str(fx_book.pages[0].id)}
        )

        moved = PageSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect(moved.id == fx_book.pages[4].id)
        expect(moved.position == expected.index(4))
        expect(_order(await _manifest(fx_client, fx_book), fx_book) == expected)
        assert_expectations()

    async def test_the_manifest_follows_a_series_of_moves_with_positions_from_zero(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the order of the manifest after several moves, among them one into an old gap and one to the end.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        moves = [(4, 'before_page_id', 0), (2, 'after_page_id', 4), (0, 'after_page_id', 3), (1, 'before_page_id', 4)]
        for moved, field, anchor in moves:
            response = await fx_client.post(
                f'{fx_book.path}/pages/{fx_book.pages[moved].id}/move', json={field: str(fx_book.pages[anchor].id)}
            )
            assert response.status_code == status.HTTP_200_OK

        manifest = await _manifest(fx_client, fx_book)

        expect(_order(manifest, fx_book) == [1, 4, 2, 3, 0])
        expect([item.position for item in manifest.items] == list(range(len(fx_book.pages))))
        expect('order_key' not in manifest.model_dump_json())
        assert_expectations()

    async def test_anchor_that_is_the_page_itself_is_a_conflict_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a page put next to itself is answered with a 409 problem.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        page_id = fx_book.pages[1].id

        response = await fx_client.post(f'{fx_book.path}/pages/{page_id}/move', json={'after_page_id': str(page_id)})

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()[DETAIL_FIELD] == str(AnchorInsideMovedPagesError()))
        assert_expectations()

    @pytest.mark.parametrize(
        'body',
        [{}, {'before_page_id': str(uuid4()), 'after_page_id': str(uuid4())}, {'above_page_id': str(uuid4())}],
        ids=['no-anchor', 'two-anchors', 'unknown-field'],
    )
    async def test_body_without_exactly_one_anchor_is_invalid(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, str]
    ) -> None:
        """Verify a body naming no side, both sides or a field of no meaning is refused before the route runs.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: The request body.
        :type body: dict[str, str]
        """
        response = await fx_client.post(f'{fx_book.path}/pages/{fx_book.pages[1].id}/move', json=body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_missing_anchor_and_page_of_another_account_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify an unknown anchor, and a project of another account, answer 404.

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

        unknown = await fx_client.post(
            f'{fx_book.path}/pages/{fx_book.pages[1].id}/move', json={'before_page_id': str(uuid4())}
        )
        foreign = await fx_client.post(
            f'{PROJECTS_PATH}/{stranger.id}/pages/{page.id}/move', json={'before_page_id': str(page.id)}
        )

        expect(unknown.status_code == status.HTTP_404_NOT_FOUND)
        expect(foreign.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()


class TestMovePages:
    """Tests for POST /projects/{project_id}/pages/move."""

    async def test_puts_the_group_in_a_run_in_book_order_and_answers_without_a_body(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a group named out of order stands together after the anchor, in the order it had in the book.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(
            f'{fx_book.path}/pages/move',
            json={'page_ids': fx_book.ids(3, 1), 'after_page_id': str(fx_book.pages[4].id)},
        )

        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect(response.content == b'')
        expect(_order(await _manifest(fx_client, fx_book), fx_book) == [0, 2, 4, 1, 3])
        assert_expectations()

    async def test_anchor_inside_the_group_is_a_conflict_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a group put next to one of its own pages is answered with a 409 problem and changes nothing.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(
            f'{fx_book.path}/pages/move',
            json={'page_ids': fx_book.ids(1, 2), 'before_page_id': str(fx_book.pages[2].id)},
        )

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()[DETAIL_FIELD] == str(AnchorInsideMovedPagesError()))
        expect(_order(await _manifest(fx_client, fx_book), fx_book) == [0, 1, 2, 3, 4])
        assert_expectations()

    @pytest.mark.parametrize(
        'page_ids',
        [[], ['same', 'same']],
        ids=['empty', 'repeated'],
    )
    async def test_empty_and_repeating_lists_are_invalid(
        self, fx_client: httpx.AsyncClient, fx_book: Book, page_ids: Sequence[str]
    ) -> None:
        """Verify a group of no page, and one naming a page twice, is refused before the route runs.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param page_ids: Identifiers of the group, where ``same`` stands for one page named twice.
        :type page_ids: Sequence[str]
        """
        named = [str(fx_book.pages[1].id) if page_id == 'same' else page_id for page_id in page_ids]

        response = await fx_client.post(
            f'{fx_book.path}/pages/move', json={'page_ids': named, 'before_page_id': str(fx_book.pages[0].id)}
        )

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_page_of_another_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a group that names a page of another project is answered 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        other = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=other.id)
        await commit_project(fx_database, other, page)

        response = await fx_client.post(
            f'{fx_book.path}/pages/move',
            json={'page_ids': [*fx_book.ids(1), str(page.id)], 'before_page_id': str(fx_book.pages[0].id)},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestMoveSourcePages:
    """Tests for POST /projects/{project_id}/sources/{source_id}/pages/move."""

    async def test_puts_every_page_of_the_source_after_the_page_in_one_request(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the pages of the first source stand together after the last page, which one request does.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(
            f'{fx_book.path}/sources/{fx_book.first.id}/pages/move', json={'after_page_id': str(fx_book.pages[4].id)}
        )

        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        manifest = await _manifest(fx_client, fx_book)
        expect(_order(manifest, fx_book) == [3, 4, 0, 1, 2])
        expect({item.source_id for item in manifest.items} == {fx_book.first.id, fx_book.second.id})
        assert_expectations()

    async def test_anchor_among_the_pages_of_the_source_is_a_conflict_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a source put next to one of its own pages is answered with a 409 problem.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(
            f'{fx_book.path}/sources/{fx_book.first.id}/pages/move', json={'before_page_id': str(fx_book.pages[1].id)}
        )

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()[DETAIL_FIELD] == str(AnchorInsideMovedPagesError()))
        assert_expectations()

    async def test_unknown_source_is_not_found(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a source that does not exist is answered 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(
            f'{fx_book.path}/sources/{uuid4()}/pages/move', json={'after_page_id': str(fx_book.pages[4].id)}
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestListIncludedPages:
    """Tests for the ``included`` filter of GET /projects/{project_id}/pages."""

    async def test_lists_only_the_pages_of_the_book_numbered_among_themselves(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify a viewer's request leaves out a page kept out of the book, and numbers the pages it lists.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        uow = InMemoryUnitOfWork(fx_database)
        await uow.pages.update(evolve(fx_book.pages[1], included=False))
        await uow.commit()

        everything = await _manifest(fx_client, fx_book)
        included = await _manifest(fx_client, fx_book, included='true')

        expect(len(everything.items) == len(fx_book.pages))
        expect(_order(included, fx_book) == [0, 2, 3, 4])
        expect([item.position for item in included.items] == [0, 1, 2, 3])
        expect(included.total == len(fx_book.pages) - 1)
        assert_expectations()
