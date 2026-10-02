"""Tests for the endpoints that edit the fields of a page and number a range of pages, on in-memory persistence."""

from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.enums import LabelStyle, PageKind
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
MERGE_PATCH_TYPE: str = 'application/merge-patch+json'
CONTENT_TYPE_HEADER: str = 'content-type'
DETAIL_FIELD: str = 'detail'
STALE_LABEL: str = 'old'
ROMAN_LIMIT: int = 3999
LABEL_MAX_LENGTH: int = 50
# Kind and inclusion of the five pages of the book
LAYOUT: list[tuple[PageKind, bool]] = [
    (PageKind.COVER, True),
    (PageKind.TITLE, True),
    (PageKind.PLATE, True),
    (PageKind.TEXT, False),
    (PageKind.TEXT, True),
]


class Book(NamedTuple):
    """A book of five labelled pages of the signed-in account.

    :ivar project: The project of the book.
    :ivar pages: The pages in book order.
    """

    project: Project
    pages: list[BookPage]

    def page_path(self, index: int) -> str:
        """Return the path of a page.

        :param index: Index of the page in book order.
        :type index: int
        :returns: The path of the page resource.
        :rtype: str
        """
        return f'{PROJECTS_PATH}/{self.project.id}/pages/{self.pages[index].id}'

    @property
    def labels_path(self) -> str:
        """The path of the numbering of a range."""
        return f'{PROJECTS_PATH}/{self.project.id}/pages/labels'


@pytest.fixture
async def fx_book(fx_database: InMemoryDatabase, fx_actor: Actor) -> Book:
    """Commit a book of five pages, each labelled ``old``, for the signed-in account.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=fx_actor.account_id)
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(LAYOUT))
    pages = [
        evolve(
            make_page(project_id=project.id, order_key=key),
            kind=kind,
            included=included,
            label=STALE_LABEL,
            notes='Kept',
        )
        for key, (kind, included) in zip(keys, LAYOUT, strict=True)
    ]
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


class FieldCase(NamedTuple):
    """One field of a page, a new value for it, and what the page then shows.

    :ivar field: Name of the field in the body.
    :ivar sent: Value sent for it.
    :ivar shown: What the page's schema holds for it afterwards.
    """

    field: str
    sent: Any
    shown: Any


FIELD_CASES: list[FieldCase] = [
    FieldCase('label', '[4]', '[4]'),
    FieldCase('label', None, ''),
    FieldCase('kind', 'endpaper', 'endpaper'),
    FieldCase(field='included', sent=False, shown=False),
    FieldCase('notes', 'Library stamp', 'Library stamp'),
    FieldCase('notes', None, ''),
]


class NumberCase(NamedTuple):
    """A style of numbering and the labels it writes for the numbers 5 and 6.

    :ivar style: Value of the style in the body.
    :ivar bracketed: Whether the labels are enclosed in square brackets.
    :ivar labels: The labels of the two pages that are numbered.
    """

    style: str
    bracketed: bool
    labels: tuple[str, str]


NUMBER_CASES: list[NumberCase] = [
    NumberCase(style='arabic', bracketed=False, labels=('5', '6')),
    NumberCase(style='arabic', bracketed=True, labels=('[5]', '[6]')),
    NumberCase(style='roman-lower', bracketed=False, labels=('v', 'vi')),
    NumberCase(style='roman-lower', bracketed=True, labels=('[v]', '[vi]')),
    NumberCase(style='roman-upper', bracketed=False, labels=('V', 'VI')),
    NumberCase(style='roman-upper', bracketed=True, labels=('[V]', '[VI]')),
    NumberCase(style='none', bracketed=False, labels=('', '')),
    NumberCase(style='none', bracketed=True, labels=('', '')),
]


class TestUpdatePage:
    """Tests for PATCH /projects/{project_id}/pages/{page_id}."""

    @pytest.mark.parametrize('case', FIELD_CASES, ids=[f'{case.field}-{case.sent}' for case in FIELD_CASES])
    async def test_changes_the_field_sent_and_keeps_the_others(
        self, fx_client: httpx.AsyncClient, fx_book: Book, case: FieldCase
    ) -> None:
        """Verify the field in the body changes, a null label or note is cleared, and every other field is kept.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param case: The field, the value sent and what the page shows.
        :type case: FieldCase
        """
        before = PageSchema.model_validate((await fx_client.get(fx_book.page_path(1))).json())

        response = await fx_client.patch(fx_book.page_path(1), json={case.field: case.sent})

        after = PageSchema.model_validate_json(response.content)
        unchanged = {'id', 'position', 'created_at', 'scan_id', 'slot', 'origin', 'images', 'updated_at', case.field}
        expect(response.status_code == status.HTTP_200_OK)
        expect(getattr(after, case.field) == case.shown)
        expect(after.model_dump(exclude=unchanged) == before.model_dump(exclude=unchanged))
        expect((after.id, after.position) == (before.id, before.position))
        assert_expectations()

    async def test_accepts_the_merge_patch_media_type_and_changes_several_fields(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a body sent as ``application/merge-patch+json`` changes all the fields it names at once.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.patch(
            fx_book.page_path(2),
            content=b'{"label": "xii", "kind": "text", "included": false}',
            headers={CONTENT_TYPE_HEADER: MERGE_PATCH_TYPE},
        )

        page = PageSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((page.label, page.kind, page.included, page.notes) == ('xii', PageKind.TEXT, False, 'Kept'))
        assert_expectations()

    @pytest.mark.parametrize(
        'body',
        [
            {'kind': None},
            {'included': None},
            {'kind': 'appendix'},
            {'label': 'x' * (LABEL_MAX_LENGTH + 1)},
            {'order_key': 'a0'},
            {'origin': 'blank'},
            {'scan_id': str(uuid4())},
        ],
        ids=['null-kind', 'null-included', 'unknown-kind', 'long-label', 'order-key', 'origin', 'scan'],
    )
    async def test_invalid_body_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, Any]
    ) -> None:
        """Verify a null kind or inclusion, an unknown kind, a long label and a field no patch may set answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: The request body.
        :type body: dict[str, Any]
        """
        response = await fx_client.patch(fx_book.page_path(1), json=body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_missing_page_and_page_of_another_account_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify a page that does not exist and a page of another account's project both answer 404.

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

        missing = await fx_client.patch(f'{PROJECTS_PATH}/{fx_book.project.id}/pages/{uuid4()}', json={'label': '1'})
        foreign = await fx_client.patch(f'{PROJECTS_PATH}/{stranger.id}/pages/{page.id}', json={'label': '1'})

        expect(missing.status_code == status.HTTP_404_NOT_FOUND)
        expect(foreign.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()


class TestNumberPages:
    """Tests for POST /projects/{project_id}/pages/labels."""

    @pytest.mark.parametrize('case', NUMBER_CASES, ids=[f'{case.style}-{case.bracketed}' for case in NUMBER_CASES])
    async def test_writes_the_labels_of_every_style_skipping_pages_kept_out_and_kinds_to_skip(
        self, fx_client: httpx.AsyncClient, fx_book: Book, case: NumberCase
    ) -> None:
        """Verify each style numbers from 5, and the page kept out and the plate keep their labels, with no body back.

        The cover is outside the range, the plate is a kind to skip, and one page is kept out of the book, so the title
        page and the last text page take the numbers 5 and 6.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param case: Style, brackets and the labels of the numbers 5 and 6.
        :type case: NumberCase
        """
        body = {
            'first_page_id': str(fx_book.pages[1].id),
            'last_page_id': str(fx_book.pages[4].id),
            'style': case.style,
            'start': 5,
            'bracketed': case.bracketed,
            'skip_kinds': ['plate'],
        }

        response = await fx_client.post(fx_book.labels_path, json=body)

        first, second = case.labels
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect(response.content == b'')
        expect(await _labels(fx_client, fx_book) == [STALE_LABEL, first, STALE_LABEL, STALE_LABEL, second])
        assert_expectations()

    @pytest.mark.parametrize(
        'body_changes',
        [
            {'start': 0},
            {'start': ROMAN_LIMIT + 1, 'style': 'roman-lower'},
            {'style': 'greek'},
            {'skip_kinds': ['appendix']},
        ],
        ids=['zero-start', 'roman-start-past-the-limit', 'unknown-style', 'unknown-kind'],
    )
    async def test_invalid_range_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body_changes: dict[str, Any]
    ) -> None:
        """Verify a start of zero, a Roman start past 3999, an unknown style and an unknown kind answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body_changes: Fields of the request body that differ from a valid one.
        :type body_changes: dict[str, Any]
        """
        body = {
            'first_page_id': str(fx_book.pages[0].id),
            'last_page_id': str(fx_book.pages[4].id),
            'style': 'arabic',
            **body_changes,
        }

        response = await fx_client.post(fx_book.labels_path, json=body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_range_that_runs_backwards_is_a_conflict_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a first page after the last page answers a 409 problem and changes no label.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {
            'first_page_id': str(fx_book.pages[4].id),
            'last_page_id': str(fx_book.pages[1].id),
            'style': 'arabic',
        }

        response = await fx_client.post(fx_book.labels_path, json=body)

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()[DETAIL_FIELD] == 'The numbering runs from a later page to an earlier one.')
        expect(await _labels(fx_client, fx_book) == [STALE_LABEL] * len(fx_book.pages))
        assert_expectations()

    async def test_unknown_page_is_not_found(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a range that names a page that does not exist answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {'first_page_id': str(fx_book.pages[0].id), 'last_page_id': str(uuid4()), 'style': LabelStyle.ARABIC}

        response = await fx_client.post(fx_book.labels_path, json=body)

        assert response.status_code == status.HTTP_404_NOT_FOUND
