"""Tests for what the pages say they show, and for the endpoint that detects it, on in-memory persistence."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.enums import ContentSource, ContentType, JobKind, JobState, PageKind
from bookreviver.domain.values import ContentDetection
from tests.helpers.builders import make_page, make_project, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project
    from bookreviver.domain.entities import Page as BookPage

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
CONTENT_FIELD: str = 'content_type'
PAGE_IDS_FIELD: str = 'page_ids'
DETECT_SUFFIX: str = '/pages/content-types/detect'
# Kind of the pages of the book, and the content the program found on the second
LAYOUT: tuple[PageKind, ...] = (PageKind.TEXT, PageKind.TEXT, PageKind.PLATE)
FOUND: ContentType = ContentType.BW_PICTURE
SAME_PAGE: str = str(uuid4())


class Book(NamedTuple):
    """A book of three pages of the signed-in account: two of text, the second with a picture found on it, and a plate.

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
    def detect_path(self) -> str:
        """The path of the detection of the content of pages."""
        return f'{PROJECTS_PATH}/{self.project.id}{DETECT_SUFFIX}'


@pytest.fixture
async def fx_book(fx_database: InMemoryDatabase, fx_actor: Actor) -> Book:
    """Commit the book for the signed-in account.

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
        evolve(make_page(project_id=project.id, order_key=key), kind=kind)
        for key, kind in zip(keys, LAYOUT, strict=True)
    ]
    pages[1] = evolve(pages[1], content_type=FOUND)
    await commit_project(fx_database, project, *pages)
    return Book(project=project, pages=pages)


async def _page(client: httpx.AsyncClient, book: Book, index: int) -> PageSchema:
    """Read a page of the book.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: The book.
    :type book: Book
    :param index: Index of the page in book order.
    :type index: int
    :returns: The page resource.
    :rtype: PageSchema
    """
    return PageSchema.model_validate_json((await client.get(book.page_path(index))).content)


class TestPageContent:
    """Tests for the content type a page shows."""

    async def test_the_manifest_says_what_each_page_shows_and_where_that_comes_from(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a page of text, a page with a picture found on it and a plate each say their type and its source.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.get(f'{PROJECTS_PATH}/{fx_book.project.id}/pages')

        pages = Page[PageSchema].model_validate_json(response.content).items
        expect(response.status_code == status.HTTP_200_OK)
        expect(
            [(page.content_type, page.content_source) for page in pages]
            == [
                (ContentType.TEXT, ContentSource.KIND),
                (FOUND, ContentSource.DETECTED),
                (ContentType.COLOR_PICTURE, ContentSource.KIND),
            ]
        )
        assert_expectations()

    async def test_a_type_the_user_sends_is_theirs(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify a content type sent in a patch is shown as set by hand, and the other fields stay.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.patch(fx_book.page_path(0), json={CONTENT_FIELD: ContentType.COLOR_PICTURE})

        changed = PageSchema.model_validate_json(response.content)
        read = await _page(fx_client, fx_book, 0)
        expect(response.status_code == status.HTTP_200_OK)
        expect((changed.content_type, changed.content_source) == (ContentType.COLOR_PICTURE, ContentSource.HAND))
        expect(changed.kind is PageKind.TEXT)
        expect(read == changed)
        assert_expectations()

    @pytest.mark.parametrize('sent', [None, 'drawing', ''], ids=['null', 'unknown', 'blank'])
    async def test_a_type_that_is_cleared_or_unknown_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: Book, sent: str | None
    ) -> None:
        """Verify the content type cannot be cleared, which a request to detect the page does, and only the set is read.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param sent: What is sent for the content type.
        :type sent: str | None
        """
        response = await fx_client.patch(fx_book.page_path(1), json={CONTENT_FIELD: sent})

        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect((await _page(fx_client, fx_book, 1)).content_type is FOUND)
        assert_expectations()


class TestDetectContent:
    """Tests for POST /projects/{project_id}/pages/content-types/detect."""

    async def test_a_request_without_pages_queues_a_job_for_the_pages_not_detected(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the job is answered 202 as queued, and names no pages.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(fx_book.detect_path, json={})

        job = JobSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_202_ACCEPTED)
        expect((job.kind, job.state) == (JobKind.DETECT_CONTENT, JobState.QUEUED))
        assert_expectations()

    async def test_a_request_that_names_pages_queues_a_job_for_those(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify the pages named are the parameters the job is stored with.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        named = [str(fx_book.pages[0].id), str(fx_book.pages[1].id)]

        response = await fx_client.post(fx_book.detect_path, json={PAGE_IDS_FIELD: named})

        job = JobSchema.model_validate_json(response.content)
        stored = ContentDetection.from_map(fx_database.tables.jobs[job.id].params)
        expect(response.status_code == status.HTTP_202_ACCEPTED)
        expect(stored == ContentDetection(page_ids=(fx_book.pages[0].id, fx_book.pages[1].id)))
        assert_expectations()

    async def test_a_second_request_while_the_job_is_queued_is_a_409_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify one detection of the project at a time, which the second request is told of.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        first = await fx_client.post(fx_book.detect_path, json={})
        second = await fx_client.post(fx_book.detect_path, json={})

        expect(first.status_code == status.HTTP_202_ACCEPTED)
        expect(second.status_code == status.HTTP_409_CONFLICT)
        assert_expectations()

    @pytest.mark.parametrize(
        'body',
        [{PAGE_IDS_FIELD: []}, {PAGE_IDS_FIELD: [SAME_PAGE, SAME_PAGE]}, {PAGE_IDS_FIELD: 'one'}, {'pages': []}],
        ids=['no-pages', 'repeated', 'not-a-list', 'unknown-field'],
    )
    async def test_a_body_that_does_not_fit_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, object]
    ) -> None:
        """Verify an empty list, a repeated page, a page that is no list and a field the body lacks are 422 problems.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: The body that does not fit.
        :type body: dict[str, object]
        """
        response = await fx_client.post(fx_book.detect_path, json=body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_page_that_is_not_in_the_book_and_a_book_of_another_account_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify a page that does not exist and a project that is not the account's answer 404 and queue nothing.

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

        missing = await fx_client.post(fx_book.detect_path, json={PAGE_IDS_FIELD: [str(uuid4())]})
        foreign = await fx_client.post(f'{PROJECTS_PATH}/{stranger.id}{DETECT_SUFFIX}', json={})
        borrowed = await fx_client.post(fx_book.detect_path, json={PAGE_IDS_FIELD: [str(page.id)]})

        expect(missing.status_code == status.HTTP_404_NOT_FOUND)
        expect(foreign.status_code == status.HTTP_404_NOT_FOUND)
        expect(borrowed.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()
