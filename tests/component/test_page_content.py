"""Tests for what the pages say they show, and for the endpoint that detects it, on the SQLite file of the test."""

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
from bookreviver.domain.jobs import ContentDetection, JobKind, JobState
from bookreviver.domain.pages import ContentSource, ContentType, PageKind
from tests.harness.builders.domain import make_job, make_page, make_record, new_account_id
from tests.harness.builders.seeding import commit_record, store_book
from tests.harness.concurrency.scripted_runner import ScriptedRunner
from tests.harness.fakes.board import FakeJobBoard
from tests.harness.fakes.jobs import FakeBoardProvider

if TYPE_CHECKING:
    from collections.abc import Sequence

    import httpx
    from dishka import Provider

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page as BookPage
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
CONTENT_FIELD: str = 'content_type'
PAGE_IDS_FIELD: str = 'page_ids'
DETECT_SUFFIX: str = '/pages/content-types/detect'
# Kind of the pages of the book, and the content the program found on the second
LAYOUT: tuple[PageKind, ...] = (PageKind.TEXT, PageKind.TEXT, PageKind.PLATE)
FOUND: ContentType = ContentType.BW_PICTURE
SAME_PAGE: str = str(uuid4())


class Book(NamedTuple):
    """A book of three pages of the signed-in account: two of text, the second with a picture found on it, and a plate.

    :ivar book_id: Identifier of the book.
    :ivar pages: The pages in book order.
    """

    book_id: BookId
    pages: list[BookPage]

    def page_path(self, index: int) -> str:
        """Return the path of a page.

        :param index: Index of the page in book order.
        :type index: int
        :returns: The path of the page resource.
        :rtype: str
        """
        return f'{BOOKS_PATH}/{self.book_id}/pages/{self.pages[index].id}'

    @property
    def detect_path(self) -> str:
        """The path of the detection of the content of pages."""
        return f'{BOOKS_PATH}/{self.book_id}{DETECT_SUFFIX}'


@pytest.fixture
def fx_board() -> FakeJobBoard:
    """Build the board the application of these tests puts its jobs on, which runs none until a test says so.

    :returns: A fake board with no job.
    :rtype: FakeJobBoard
    """
    return FakeJobBoard(runner=ScriptedRunner())


@pytest.fixture
def fx_extra_providers(fx_board: FakeJobBoard) -> Sequence[Provider]:
    """Replace the application's job board with ``fx_board``, so a detection stays queued.

    :param fx_board: Board of the test.
    :type fx_board: FakeJobBoard
    :returns: One provider that supplies the board.
    :rtype: Sequence[Provider]
    """
    return (FakeBoardProvider(fx_board),)


@pytest.fixture
async def fx_book(fx_catalogue: CatalogueDatabaseFactory, fx_actor: Actor, fx_databases: BookDatabasesFactory) -> Book:
    """Commit the book for the signed-in account.

    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The stored book.
    :rtype: Book
    """
    keys = FractionalOrderKeys().spread(lower=None, upper=None, count=len(LAYOUT))
    pages = [evolve(make_page(order_key=key), kind=kind) for key, kind in zip(keys, LAYOUT, strict=True)]
    pages[1] = evolve(pages[1], content_type=FOUND)
    book_id = await store_book(
        fx_catalogue(), make_record(), *pages, owner=fx_actor.account_id, databases=fx_databases()
    )
    return Book(book_id=book_id, pages=pages)


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
        response = await fx_client.get(f'{BOOKS_PATH}/{fx_book.book_id}/pages')

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
    """Tests for POST /books/{book_id}/pages/content-types/detect."""

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
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_board: FakeJobBoard
    ) -> None:
        """Verify the pages named are the parameters the job is put on the board with.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_board: Board of the application.
        :type fx_board: FakeJobBoard
        """
        named = [str(fx_book.pages[0].id), str(fx_book.pages[1].id)]

        response = await fx_client.post(fx_book.detect_path, json={PAGE_IDS_FIELD: named})

        job = JobSchema.model_validate_json(response.content)
        stored = ContentDetection.from_map(fx_board.get(job.id).params)
        expect(response.status_code == status.HTTP_202_ACCEPTED)
        expect(stored == ContentDetection(page_ids=(fx_book.pages[0].id, fx_book.pages[1].id)))
        assert_expectations()

    async def test_a_second_request_while_the_job_is_queued_is_a_409_problem(
        self, fx_client: httpx.AsyncClient, fx_board: FakeJobBoard, fx_book: Book
    ) -> None:
        """Verify one detection of the book at a time, which the request is told of while one is queued.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_board: Board of the application.
        :type fx_board: FakeJobBoard
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        queued = fx_board.put(fx_book.book_id, make_job(kind=JobKind.DETECT_CONTENT))

        response = await fx_client.post(fx_book.detect_path, json={})

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect([job.id for job in fx_board.put_jobs] == [queued.id])
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
        self,
        fx_client: httpx.AsyncClient,
        fx_book: Book,
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a page that does not exist and a book that is not the account's answer 404 and queue nothing.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        stranger_id = await commit_record(
            fx_database, fx_catalogue(), make_record(), page, owner=new_account_id(), databases=fx_databases()
        )

        missing = await fx_client.post(fx_book.detect_path, json={PAGE_IDS_FIELD: [str(uuid4())]})
        foreign = await fx_client.post(f'{BOOKS_PATH}/{stranger_id}{DETECT_SUFFIX}', json={})
        borrowed = await fx_client.post(fx_book.detect_path, json={PAGE_IDS_FIELD: [str(page.id)]})

        expect(missing.status_code == status.HTTP_404_NOT_FOUND)
        expect(foreign.status_code == status.HTTP_404_NOT_FOUND)
        expect(borrowed.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()
