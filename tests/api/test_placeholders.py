"""Tests for the endpoints that add and delete pages without a scan and bind scans, on the application's own adapters.

The stores, the imaging adapters and the in-process broker are the application's own, so a page's images are written
by the ``prepare-pages`` job as they are for a user, and a test waits for the broker to finish its jobs.
"""

import io
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from PIL import Image
from taskiq import AsyncBroker, InMemoryBroker

from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.enums import PageKind, PageOrigin, Rendition, VersionState
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Renditions
from bookreviver.services.base_versions import BaseVersions
from tests.helpers.builders import EPOCH, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    import httpx
    from dishka import AsyncContainer

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project, Scan
    from bookreviver.domain.entities import Page as BookPage
    from bookreviver.ports.storage import AssetStore

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
LOCATION_HEADER: str = 'location'
SCAN_SIZE_PX: tuple[int, int] = (120, 160)


class Book(NamedTuple):
    """A book of the signed-in account: one page cut from a scan, a placeholder, and a second scan no page shows.

    :ivar project: The project of the book.
    :ivar shown: The page the import made of the first scan.
    :ivar placeholder: The placeholder after it.
    :ivar spare: A cut scan of another source that no page shows.
    """

    project: Project
    shown: BookPage
    placeholder: BookPage
    spare: Scan

    @property
    def pages_path(self) -> str:
        """The path of the project's pages."""
        return f'{PROJECTS_PATH}/{self.project.id}/pages'


def _jpeg() -> bytes:
    """Encode a small gray page as a JPEG.

    :returns: The bytes of the JPEG.
    :rtype: bytes
    """
    buffer = io.BytesIO()
    Image.new('L', SCAN_SIZE_PX, color=200).save(buffer, format='JPEG')
    return buffer.getvalue()


@pytest.fixture
async def fx_broker(fx_container: AsyncContainer) -> InMemoryBroker:
    """Return the broker the application runs its jobs on, which a test waits on until they have finished.

    :param fx_container: Container of the running application.
    :type fx_container: AsyncContainer
    :returns: The started in-process broker.
    :rtype: InMemoryBroker
    """
    broker = await fx_container.get(AsyncBroker)
    assert isinstance(broker, InMemoryBroker)
    return broker


@pytest.fixture
async def fx_book(fx_database: InMemoryDatabase, fx_asset_store: AssetStore, fx_actor: Actor) -> Book:
    """Commit the book and store the full image of both scans as a real JPEG.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_asset_store: Asset store of the application.
    :type fx_asset_store: AssetStore
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=fx_actor.account_id)
    first, second = make_source(project_id=project.id, name='a.pdf'), make_source(project_id=project.id, name='b.jpg')
    scans = [
        evolve(
            make_scan(source=source, number=0),
            renditions=Renditions(ready=True),
            source_label=label,
            facts=evolve(make_scan(source=source, number=0).facts, width_px=SCAN_SIZE_PX[0], height_px=SCAN_SIZE_PX[1]),
        )
        for source, label in ((first, ''), (second, 'xii'))
    ]
    shown = make_page(project_id=project.id, order_key='a0', scan=scans[0])
    placeholder = evolve(make_page(project_id=project.id, order_key='a1'), kind=PageKind.COVER, notes='Missing')
    base = BaseVersions.split_none(page=shown, scan=scans[0], state=VersionState.READY, moment=EPOCH)
    await commit_project(
        fx_database, project, shown, placeholder, sources=[first, second], scans=scans, versions=[base]
    )
    keys = ProjectKeys(project.id)
    for scan in scans:
        async with fx_asset_store.writable(keys.scan_rendition(scan, Rendition.FULL_JPEG)) as path:
            path.write_bytes(_jpeg())
    return Book(project=project, shown=shown, placeholder=placeholder, spare=scans[1])


async def _page(client: httpx.AsyncClient, book: Book, page_id: object) -> PageSchema:
    """Read one page of the book.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param page_id: Identifier of the page.
    :type page_id: object
    :returns: The page.
    :rtype: PageSchema
    """
    return PageSchema.model_validate_json((await client.get(f'{book.pages_path}/{page_id}')).content)


class TestCreatePage:
    """Tests for POST /projects/{project_id}/pages."""

    async def test_adds_a_placeholder_that_has_no_images_and_points_to_it(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a placeholder is answered 201 with a ``Location`` that reads it back, and with no images.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {
            'origin': 'placeholder',
            'kind': 'title',
            'label': '[3]',
            'notes': 'Lost',
            'before_page_id': str(fx_book.shown.id),
        }

        response = await fx_client.post(fx_book.pages_path, json=body)

        created = PageSchema.model_validate_json(response.content)
        located = await fx_client.get(response.headers[LOCATION_HEADER])
        expect(response.status_code == status.HTTP_201_CREATED)
        expect(
            (created.origin, created.kind, created.label, created.notes)
            == (PageOrigin.PLACEHOLDER, PageKind.TITLE, '[3]', 'Lost')
        )
        expect((created.position, created.images, created.scan_id, created.source_id) == (0, None, None, None))
        expect(PageSchema.model_validate_json(located.content) == created)
        assert_expectations()

    async def test_adds_a_blank_leaf_of_the_median_size_whose_images_a_job_writes(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify a blank leaf has no images when it is created, and once the job is done has paths that serve its files.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {'origin': 'blank', 'kind': 'endpaper', 'after_page_id': str(fx_book.shown.id)}

        response = await fx_client.post(fx_book.pages_path, json=body)
        await fx_broker.wait_all()

        created = PageSchema.model_validate_json(response.content)
        leaf = await _page(fx_client, fx_book, created.id)
        assert leaf.images is not None
        thumbnail = await fx_client.get(leaf.images.thumbnail)
        full = await fx_client.get(leaf.images.full)
        with Image.open(io.BytesIO(full.content)) as image:
            mode, size = image.mode, image.size
        expect(response.status_code == status.HTTP_201_CREATED)
        expect((created.origin, created.position, created.images) == (PageOrigin.BLANK, 1, None))
        expect(leaf.images.full.endswith('full.png') and thumbnail.status_code == status.HTTP_200_OK)
        expect((mode, size) == ('1', SCAN_SIZE_PX))
        assert_expectations()

    async def test_blank_leaf_without_a_size_in_a_book_without_images_is_a_conflict_problem(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a leaf with no size given and no page to take the median of is answered with a 409 problem.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        response = await fx_client.post(
            f'{PROJECTS_PATH}/{project.id}/pages', json={'origin': 'blank', 'kind': 'blank'}
        )

        expect(response.status_code == status.HTTP_409_CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()

    async def test_a_given_size_is_used_instead_of_the_median(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify a leaf of a size and a resolution given has that size, whatever the pages of the book are.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        body = {'origin': 'blank', 'kind': 'blank', 'width_px': 50, 'height_px': 70, 'dpi': 200}

        response = await fx_client.post(fx_book.pages_path, json=body)
        await fx_broker.wait_all()

        leaf = await _page(fx_client, fx_book, PageSchema.model_validate_json(response.content).id)
        assert leaf.images is not None
        with Image.open(io.BytesIO((await fx_client.get(leaf.images.full)).content)) as image:
            assert image.size == (50, 70)

    @pytest.mark.parametrize(
        'body',
        [
            {'origin': 'scan', 'kind': 'text'},
            {'origin': 'placeholder', 'kind': 'text', 'width_px': 10, 'height_px': 10},
            {'origin': 'blank', 'kind': 'text', 'width_px': 10},
            {'origin': 'blank', 'kind': 'text', 'dpi': 300},
            {'origin': 'blank', 'kind': 'text', 'width_px': 0, 'height_px': 10},
            {'origin': 'blank', 'kind': 'text', 'before_page_id': str(uuid4()), 'after_page_id': str(uuid4())},
            {'origin': 'blank', 'kind': 'appendix'},
            {'origin': 'blank', 'kind': 'text', 'order_key': 'a0'},
            {'kind': 'text'},
        ],
        ids=[
            'scan-origin',
            'placeholder-with-size',
            'width-without-height',
            'dpi-without-size',
            'zero-width',
            'two-anchors',
            'unknown-kind',
            'order-key',
            'no-origin',
        ],
    )
    async def test_invalid_body_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, Any]
    ) -> None:
        """Verify a page of origin scan, a size where there is no image, half a size and the like answer 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: The request body.
        :type body: dict[str, Any]
        """
        response = await fx_client.post(fx_book.pages_path, json=body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_unknown_anchor_and_project_of_another_account_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify an anchor that is no page of the project, and a project of another account, answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        stranger = make_project(owner_id=new_account_id())
        await commit_project(fx_database, stranger)

        unknown = await fx_client.post(
            fx_book.pages_path, json={'origin': 'placeholder', 'kind': 'text', 'after_page_id': str(uuid4())}
        )
        foreign = await fx_client.post(
            f'{PROJECTS_PATH}/{stranger.id}/pages', json={'origin': 'placeholder', 'kind': 'text'}
        )

        expect(unknown.status_code == status.HTTP_404_NOT_FOUND)
        expect(foreign.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()


class TestDeletePage:
    """Tests for DELETE /projects/{project_id}/pages/{page_id}."""

    async def test_deletes_the_page_and_leaves_the_scan_to_be_bound_again(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase
    ) -> None:
        """Verify a deleted page answers 204 and is gone, while its scan and source stay.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        response = await fx_client.delete(f'{fx_book.pages_path}/{fx_book.shown.id}')

        again = await fx_client.get(f'{fx_book.pages_path}/{fx_book.shown.id}')
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect(again.status_code == status.HTTP_404_NOT_FOUND)
        expect(fx_book.shown.scan_id in fx_database.tables.scans)
        assert_expectations()

    async def test_page_that_does_not_exist_is_not_found(self, fx_client: httpx.AsyncClient, fx_book: Book) -> None:
        """Verify deleting a page that does not exist answers 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.delete(f'{fx_book.pages_path}/{uuid4()}')

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestAttachScan:
    """Tests for PUT /projects/{project_id}/pages/{page_id}/scan."""

    async def test_binds_the_scan_and_the_job_writes_the_images_of_the_page(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify the page is a page of the scan at once, without images, and has them when the job is done.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(
            f'{fx_book.pages_path}/{fx_book.placeholder.id}/scan', json={'scan_id': str(fx_book.spare.id)}
        )
        await fx_broker.wait_all()

        bound = PageSchema.model_validate_json(response.content)
        ready = await _page(fx_client, fx_book, fx_book.placeholder.id)
        assert ready.images is not None
        copy = await fx_client.get(ready.images.full)
        expect(response.status_code == status.HTTP_200_OK)
        expect(
            (bound.origin, bound.scan_id, bound.slot, bound.label, bound.images)
            == (PageOrigin.SCAN, fx_book.spare.id, 0, 'xii', None)
        )
        expect((bound.kind, bound.notes, bound.id) == (PageKind.COVER, 'Missing', fx_book.placeholder.id))
        expect(copy.content == _jpeg())
        assert_expectations()

    async def test_scan_another_page_shows_is_a_conflict_problem_unless_taken_over(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_book: Book
    ) -> None:
        """Verify the scan of an imported page is refused with a 409 problem, and with ``take_over`` moves to the placeholder.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the job.
        :type fx_broker: InMemoryBroker
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        path = f'{fx_book.pages_path}/{fx_book.placeholder.id}/scan'
        body: dict[str, Any] = {'scan_id': str(fx_book.shown.scan_id)}

        refused = await fx_client.put(path, json=body)
        taken = await fx_client.put(path, json={**body, 'take_over': True})
        await fx_broker.wait_all()

        gone = await fx_client.get(f'{fx_book.pages_path}/{fx_book.shown.id}')
        expect(refused.status_code == status.HTTP_409_CONFLICT)
        expect(refused.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(str(fx_book.shown.id) in refused.text)
        expect(taken.status_code == status.HTTP_200_OK)
        expect(gone.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()

    async def test_page_that_is_not_a_placeholder_is_a_conflict_problem(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a page cut from a scan cannot be bound to another scan.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await fx_client.put(
            f'{fx_book.pages_path}/{fx_book.shown.id}/scan', json={'scan_id': str(fx_book.spare.id)}
        )

        assert response.status_code == status.HTTP_409_CONFLICT

    async def test_scan_of_another_project_and_unknown_scan_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a scan is bound only through the project that holds it.

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
        source = make_source(project_id=other.id)
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))
        await commit_project(fx_database, other, sources=[source], scans=[scan])
        path = f'{fx_book.pages_path}/{fx_book.placeholder.id}/scan'

        foreign = await fx_client.put(path, json={'scan_id': str(scan.id)})
        unknown = await fx_client.put(path, json={'scan_id': str(uuid4())})

        expect(foreign.status_code == status.HTTP_404_NOT_FOUND)
        expect(unknown.status_code == status.HTTP_404_NOT_FOUND)
        assert_expectations()

    @pytest.mark.parametrize('body', [{}, {'scan_id': 'not-an-id'}, {'scan_id': str(uuid4()), 'slot': 1}])
    async def test_invalid_body_is_refused(
        self, fx_client: httpx.AsyncClient, fx_book: Book, body: dict[str, Any]
    ) -> None:
        """Verify a body without a scan identifier, with a bad one, or with a field of no meaning answers 422.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param body: The request body.
        :type body: dict[str, Any]
        """
        response = await fx_client.put(f'{fx_book.pages_path}/{fx_book.placeholder.id}/scan', json=body)

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
