"""Tests for the endpoint that chooses the image of blank pages, on the application's own adapters.

The stores, the imaging adapters and the in-memory job board are the application's own, so a leaf is drawn by the
``prepare-pages`` job as it is for a user, and a test waits until the jobs of the book have finished.
"""

import io
from typing import TYPE_CHECKING, NamedTuple

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page
from PIL import Image

from bookreviver.api.schemas.jobs import JobSchema
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.images import Rendition, Renditions
from bookreviver.domain.jobs import JobKind, JobState
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.pages import BlankFill, PageKind
from bookreviver.domain.stages import Stage
from bookreviver.domain.versions import VersionState
from bookreviver.services.base_versions import BaseVersions
from tests.harness.builders.domain import EPOCH, make_page, make_page_stage, make_record, make_scan, make_source
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    import httpx

    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.pages import Page as BookPage
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.harness.adapters.storage import SharedBookDirectories
    from tests.harness.concurrency.board_idle import JobsIdle

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
DETAIL_FIELD: str = 'detail'
SCAN_SIZE_PX: tuple[int, int] = (120, 160)
# The directories of the address of an image that name the stage that made it
LEAF_STAGE: str = '/page-order/'
SCAN_STAGE: str = '/page-split/'
FILL_FIELD: str = 'blank_fill'
PAGE_IDS_FIELD: str = 'page_ids'
# Placeholders of a parametrized body, replaced by the identifier of a real page before the request
SAME_PAGE: str = 'same'
ONE_PAGE: str = 'one'
WHITE: BlankFill = BlankFill.WHITE
PAPER: BlankFill = BlankFill.PAPER
SCAN: BlankFill = BlankFill.SCAN


class Book(NamedTuple):
    """A book of the signed-in account: a page of text and two blank pages, each cut from a scan.

    :ivar book_id: Identifier of the book.
    :ivar pages: The three pages in book order.
    """

    book_id: BookId
    pages: list[BookPage]

    @property
    def pages_path(self) -> str:
        """The path of the book's pages."""
        return f'{BOOKS_PATH}/{self.book_id}/pages'

    @property
    def fill_path(self) -> str:
        """The path that chooses the image of blank pages."""
        return f'{self.pages_path}/blank-fill'

    def ids(self, *indexes: int) -> list[str]:
        """Return the identifiers of pages as text, which is how a request names them.

        :param indexes: Indexes of the pages in the book.
        :type indexes: int
        :returns: The identifiers, in the order of the indexes.
        :rtype: list[str]
        """
        return [str(self.pages[index].id) for index in indexes]


def _jpeg() -> bytes:
    """Encode a small gray page as a JPEG.

    :returns: The bytes of the JPEG.
    :rtype: bytes
    """
    buffer = io.BytesIO()
    Image.new('L', SCAN_SIZE_PX, color=200).save(buffer, format='JPEG')
    return buffer.getvalue()


@pytest.fixture
async def fx_book(
    fx_catalogue: CatalogueDatabaseFactory,
    fx_actor: Actor,
    fx_databases: BookDatabasesFactory,
    fx_directories: SharedBookDirectories,
) -> Book:
    """Commit the book and store the full image of every scan and of every base version as a real JPEG.

    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :param fx_directories: Opens the source store and the asset store of a book of the test.
    :type fx_directories: SharedBookDirectories
    :returns: The stored book.
    :rtype: Book
    """
    source = make_source()
    kinds = [PageKind.TEXT, PageKind.BLANK, PageKind.BLANK]
    scans = [
        evolve(
            make_scan(source=source, number=number),
            renditions=Renditions(ready=True),
            facts=evolve(
                make_scan(source=source, number=number).facts, width_px=SCAN_SIZE_PX[0], height_px=SCAN_SIZE_PX[1]
            ),
        )
        for number in range(len(kinds))
    ]
    pages = [
        make_page(order_key=f'a{number}', scan=scan, kind=kind)
        for number, (scan, kind) in enumerate(zip(scans, kinds, strict=True))
    ]
    versions = [
        BaseVersions.split_none(page=page, scan=scan, state=VersionState.READY, moment=EPOCH)
        for page, scan in zip(pages, scans, strict=True)
    ]
    book_id = await store_book(
        fx_catalogue(),
        make_record(),
        *pages,
        owner=fx_actor.account_id,
        sources=[source],
        scans=scans,
        versions=versions,
        databases=fx_databases(),
    )

    def save_heads(repos: BookRepositories, _book_record: BookRecord) -> None:
        """Make the base version of each page the current version of the page split, as the import does.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param _book_record: The record of the book as the change finds it, which the work does not need.
        :type _book_record: BookRecord
        """
        for page, version in zip(pages, versions, strict=True):
            repos.page_stages.save(make_page_stage(page_id=page.id, stage=Stage.PAGE_SPLIT, head_version_id=version.id))

    await fx_databases().open(book_id).change(save_heads)
    for scan in scans:
        async with fx_directories.assets(book_id).writable(BookKeys.scan_rendition(scan, Rendition.FULL_JPEG)) as path:
            path.write_bytes(_jpeg())
    for version in versions:
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL):
            async with fx_directories.assets(book_id).writable(BookKeys.version_rendition(version, rendition)) as path:
                path.write_bytes(_jpeg())
    return Book(book_id=book_id, pages=pages)


async def _page(client: httpx.AsyncClient, book: Book, index: int) -> PageSchema:
    """Read one page of the book.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param book: The book.
    :type book: Book
    :param index: Index of the page in the book.
    :type index: int
    :returns: The page.
    :rtype: PageSchema
    """
    return PageSchema.model_validate_json((await client.get(f'{book.pages_path}/{book.pages[index].id}')).content)


class TestFillBlankPages:
    """Tests for POST /books/{book_id}/pages/blank-fill."""

    async def test_a_white_leaf_replaces_the_scan_once_the_job_has_drawn_it_and_the_scan_comes_back(
        self, fx_client: httpx.AsyncClient, fx_idle: JobsIdle, fx_book: Book
    ) -> None:
        """Verify the answer has no body, the page names its choice and shows the leaf, and then shows its scan again.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_idle: Wait that lets the jobs of the book finish.
        :type fx_idle: JobsIdle
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        before = await _page(fx_client, fx_book, 1)

        response = await fx_client.post(fx_book.fill_path, json={PAGE_IDS_FIELD: fx_book.ids(1), FILL_FIELD: WHITE})
        await fx_idle(fx_book.book_id)

        leaf = await _page(fx_client, fx_book, 1)
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect(response.content == b'')
        expect(before.blank_fill is BlankFill.SCAN)
        expect((leaf.blank_fill, leaf.scan_id) == (BlankFill.WHITE, before.scan_id))
        assert leaf.images is not None
        expect(LEAF_STAGE in leaf.images.full)
        expect((await fx_client.get(leaf.images.full)).status_code == status.HTTP_200_OK)

        back = await fx_client.post(fx_book.fill_path, json={PAGE_IDS_FIELD: fx_book.ids(1), FILL_FIELD: SCAN})
        await fx_idle(fx_book.book_id)

        shown = await _page(fx_client, fx_book, 1)
        expect(back.status_code == status.HTTP_204_NO_CONTENT)
        expect(shown.blank_fill is BlankFill.SCAN)
        expect(shown.images is not None and SCAN_STAGE in shown.images.full)
        assert_expectations()

    async def test_the_job_that_drew_the_leaf_is_followed_by_the_detection_of_the_content_of_the_pages(
        self, fx_client: httpx.AsyncClient, fx_idle: JobsIdle, fx_book: Book
    ) -> None:
        """Verify a preparation of images that made a version asks for the detection, which ends well without OpenCV.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_idle: Wait that lets the jobs of the book finish.
        :type fx_idle: JobsIdle
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await fx_client.post(fx_book.fill_path, json={PAGE_IDS_FIELD: fx_book.ids(1), FILL_FIELD: WHITE})
        await fx_idle(fx_book.book_id)

        listed = await fx_client.get(f'{BOOKS_PATH}/{fx_book.book_id}/jobs?size=50')
        jobs = Page[JobSchema].model_validate_json(listed.content).items
        expect((JobKind.PREPARE_PAGES, JobState.SUCCEEDED) in {(job.kind, job.state) for job in jobs})
        expect((JobKind.DETECT_CONTENT, JobState.SUCCEEDED) in {(job.kind, job.state) for job in jobs})
        assert_expectations()

    async def test_several_pages_get_the_paper_of_the_book_in_one_request(
        self, fx_client: httpx.AsyncClient, fx_idle: JobsIdle, fx_book: Book
    ) -> None:
        """Verify every page named takes the choice, and the page that is not named keeps its scan.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_idle: Wait that lets the jobs of the book finish.
        :type fx_idle: JobsIdle
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(fx_book.fill_path, json={PAGE_IDS_FIELD: fx_book.ids(1, 2), FILL_FIELD: PAPER})
        await fx_idle(fx_book.book_id)

        pages = [await _page(fx_client, fx_book, index) for index in range(3)]
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect([page.blank_fill for page in pages] == [BlankFill.SCAN, BlankFill.PAPER, BlankFill.PAPER])
        expect(all(page.images is not None and LEAF_STAGE in page.images.full for page in pages[1:]))
        assert_expectations()

    async def test_a_page_that_is_not_blank_is_a_conflict_problem_and_no_page_changes(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a page of text among the pages named is answered with a 409 problem, and the blank page stays a scan.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.post(fx_book.fill_path, json={PAGE_IDS_FIELD: fx_book.ids(0, 1), FILL_FIELD: WHITE})

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect('not a blank page' in response.json()[DETAIL_FIELD])
        expect((await _page(fx_client, fx_book, 1)).blank_fill is BlankFill.SCAN)
        assert_expectations()

    @pytest.mark.parametrize(
        'body',
        [
            {PAGE_IDS_FIELD: [], FILL_FIELD: WHITE},
            {PAGE_IDS_FIELD: [SAME_PAGE, SAME_PAGE], FILL_FIELD: WHITE},
            {PAGE_IDS_FIELD: [ONE_PAGE], FILL_FIELD: 'black'},
            {PAGE_IDS_FIELD: [ONE_PAGE]},
        ],
        ids=['empty', 'repeated', 'unknown-fill', 'no-fill'],
    )
    async def test_a_body_that_does_not_fit_the_schema_is_unprocessable(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, object]
    ) -> None:
        """Verify an empty or repeating list of pages, an unknown choice and a missing one are refused before the route.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: The request body under test, whose page names are replaced by real identifiers.
        :type body: dict[str, object]
        """
        named = fx_book.ids(1)[0]
        ids = body[PAGE_IDS_FIELD]
        assert isinstance(ids, list)
        body = {**body, PAGE_IDS_FIELD: [named if name in {SAME_PAGE, ONE_PAGE} else name for name in ids]}

        response = await fx_client.post(fx_book.fill_path, json=body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_a_page_of_another_book_is_not_found(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify an identifier that is no page of the book is answered 404, whatever it is.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        stranger = '00000000-0000-4000-8000-000000000000'

        response = await fx_client.post(fx_book.fill_path, json={PAGE_IDS_FIELD: [stranger], FILL_FIELD: WHITE})

        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_changing_the_kind_of_a_page_with_a_leaf_gives_the_scan_back(
        self, fx_client: httpx.AsyncClient, fx_idle: JobsIdle, fx_book: Book
    ) -> None:
        """Verify the update of the page answers with its choice reset, and the page shows its scan again.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_idle: Wait that lets the jobs of the book finish.
        :type fx_idle: JobsIdle
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        await fx_client.post(fx_book.fill_path, json={PAGE_IDS_FIELD: fx_book.ids(1), FILL_FIELD: WHITE})
        await fx_idle(fx_book.book_id)

        response = await fx_client.patch(f'{fx_book.pages_path}/{fx_book.pages[1].id}', json={'kind': 'text'})

        changed = PageSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((changed.kind, changed.blank_fill) == (PageKind.TEXT, BlankFill.SCAN))
        expect(changed.images is not None and SCAN_STAGE in changed.images.full)
        assert_expectations()
