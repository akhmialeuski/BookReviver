"""Tests for the page endpoints, on in-memory persistence and the local stores under the test's data directory."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.api.pagination import MANIFEST_MAX_SIZE
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.enums import ImagePolicy, PageOrigin, Rendition
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import Renditions
from tests.helpers.builders import make_page, make_page_version, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, PageVersion, Project
    from bookreviver.domain.entities import Page as BookPage
    from bookreviver.ports.storage import AssetStore

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
IIIF_PATH: str = '/api/v1/iiif'
ORDER_KEY_FIELD: str = 'order_key'
THUMBNAIL_CONTENT: bytes = b'thumbnail'
PAGE_SIZE_PARAM: str = 'size'
PAGE_NUMBER_PARAM: str = 'page'
# Order keys the pages are stored with, against the book order: a scan page, a placeholder, an excluded scan page
STORED_KEYS: list[str] = ['a2', 'a0', 'a1']
BOOK_ORDER: list[int] = [1, 2, 0]


class Book(NamedTuple):
    """A book of the signed-in account with three pages stored against the book order.

    The page with the key ``a2`` is cut from a scan and has its base version ready, the page with the key ``a0`` is a
    placeholder without a version, and the page with the key ``a1`` is kept out of the book and has a base version
    whose images are not cut yet.

    :ivar project: The project of the book.
    :ivar pages: The pages in the order they were stored.
    :ivar versions: The base versions of the first and the last page.
    """

    project: Project
    pages: list[BookPage]
    versions: list[PageVersion]

    @property
    def path(self) -> str:
        """The path of the project's page manifest."""
        return f'{PROJECTS_PATH}/{self.project.id}/pages'


@pytest.fixture
async def fx_book(fx_database: InMemoryDatabase, fx_actor: Actor) -> Book:
    """Commit the book of the signed-in account.

    :param fx_database: In-memory database of the application.
    :type fx_database: InMemoryDatabase
    :param fx_actor: The signed-in account.
    :type fx_actor: Actor
    :returns: The stored book.
    :rtype: Book
    """
    project = make_project(owner_id=fx_actor.account_id)
    source = make_source(project_id=project.id)
    scans = [make_scan(source=source, number=number) for number in range(2)]
    shown = [scans[0], None, scans[1]]
    pages = [
        make_page(project_id=project.id, order_key=key, scan=scan) for key, scan in zip(STORED_KEYS, shown, strict=True)
    ]
    pages[2] = evolve(pages[2], included=False)
    versions = [
        evolve(make_page_version(page_id=pages[0].id), renditions=Renditions(ready=True)),
        make_page_version(page_id=pages[2].id),
    ]
    await commit_project(fx_database, project, *pages, sources=[source], scans=scans, versions=versions)
    return Book(project, pages, versions)


async def _list_pages(client: httpx.AsyncClient, path: str, **params: int) -> httpx.Response:
    """Request the manifest.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param path: Path of the manifest.
    :type path: str
    :param params: Query parameters, such as the page number and size.
    :type params: int
    :returns: The response.
    :rtype: httpx.Response
    """
    return await client.get(path, params=params)


class TestListPages:
    """Tests for GET /projects/{project_id}/pages."""

    async def test_lists_the_pages_in_book_order_without_their_order_keys(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the pages come in the order of their keys with positions from zero, and no key in the answer.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await _list_pages(fx_client, fx_book.path)

        manifest = Page[PageSchema].model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect([item.id for item in manifest.items] == [fx_book.pages[index].id for index in BOOK_ORDER])
        expect([item.position for item in manifest.items] == [0, 1, 2])
        expect(ORDER_KEY_FIELD not in response.text)
        expect(manifest.total == len(fx_book.pages))
        assert_expectations()

    async def test_lists_the_excluded_page_and_the_placeholder_with_their_state(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify an excluded page is listed with its flag, and only a page with ready images has image paths.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        manifest = Page[PageSchema].model_validate_json((await _list_pages(fx_client, fx_book.path)).content)

        placeholder, excluded, shown = manifest.items
        expect((placeholder.origin, placeholder.images) == (PageOrigin.PLACEHOLDER, None))
        expect((excluded.included, excluded.images) == (False, None))
        expect((shown.included, shown.images is not None) == (True, True))
        assert_expectations()

    async def test_image_paths_have_no_scheme_or_host_and_lead_to_the_files(
        self, fx_client: httpx.AsyncClient, fx_book: Book, fx_asset_store: AssetStore
    ) -> None:
        """Verify every image address is a path of the IIIF route, and the thumbnail path serves the stored file.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_asset_store: Asset store of the application.
        :type fx_asset_store: AssetStore
        """
        keys = ProjectKeys(fx_book.project.id)
        version = fx_book.versions[0]
        async with fx_asset_store.writable(keys.version_rendition(version, Rendition.THUMBNAIL)) as path:
            path.write_bytes(THUMBNAIL_CONTENT)
        manifest = Page[PageSchema].model_validate_json((await _list_pages(fx_client, fx_book.path)).content)

        images = manifest.items[2].images
        assert images is not None
        addresses = [images.full, images.preview, images.thumbnail, images.iiif_info]
        served = await fx_client.get(images.thumbnail)
        expect(all(address.startswith(f'{IIIF_PATH}/{keys.prefix}') for address in addresses))
        expect(images.iiif_info == f'{IIIF_PATH}/{keys.version_rendition(version, Rendition.TILES)}/info.json')
        expect(served.content == THUMBNAIL_CONTENT)
        assert_expectations()

    @pytest.mark.parametrize('full', [Rendition.FULL_JPEG, Rendition.FULL_PNG])
    async def test_full_image_path_has_the_extension_of_the_format_recorded_with_the_version(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor, full: Rendition
    ) -> None:
        """Verify the path of the full image names the format its version was stored in, whatever the project's policy.

        The project's policy is the opposite of what the version records, as after a change of the policy.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param full: Format the base version of the page records for its full image.
        :type full: Rendition
        """
        policy = ImagePolicy.LOSSLESS if full is Rendition.FULL_JPEG else ImagePolicy.COMPACT
        project = evolve(make_project(owner_id=fx_actor.account_id), image_policy=policy)
        page = make_page(project_id=project.id)
        version = evolve(make_page_version(page_id=page.id), renditions=Renditions(ready=True, full=full))
        await commit_project(fx_database, project, page, versions=[version])

        response = await _list_pages(fx_client, f'{PROJECTS_PATH}/{project.id}/pages')

        images = Page[PageSchema].model_validate_json(response.content).items[0].images
        assert images is not None
        expected = f'{IIIF_PATH}/{ProjectKeys(project.id).version_rendition(version, full)}'
        assert images.full == expected

    async def test_pages_through_the_book_with_positions_of_the_whole_book(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify the second page of one-page windows holds the second page of the book, at position one.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        response = await _list_pages(fx_client, fx_book.path, **{PAGE_SIZE_PARAM: 1, PAGE_NUMBER_PARAM: 2})

        manifest = Page[PageSchema].model_validate_json(response.content)
        expect([(item.id, item.position) for item in manifest.items] == [(fx_book.pages[2].id, 1)])
        expect((manifest.total, manifest.pages) == (len(fx_book.pages), len(fx_book.pages)))
        assert_expectations()

    @pytest.mark.parametrize(
        ('size', 'expected'),
        [(MANIFEST_MAX_SIZE, status.HTTP_200_OK), (MANIFEST_MAX_SIZE + 1, status.HTTP_422_UNPROCESSABLE_CONTENT)],
        ids=['largest-window', 'window-too-large'],
    )
    async def test_window_is_limited_to_a_thousand_pages(
        self, fx_client: httpx.AsyncClient, fx_book: Book, size: int, expected: int
    ) -> None:
        """Verify the manifest takes a thousand pages at once, and refuses more.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param size: Requested number of pages in one window.
        :type size: int
        :param expected: Status the API answers with.
        :type expected: int
        """
        assert (await _list_pages(fx_client, fx_book.path, **{PAGE_SIZE_PARAM: size})).status_code == expected

    async def test_another_accounts_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify the pages of another account's project cannot be listed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project, make_page(project_id=project.id))

        response = await fx_client.get(f'{PROJECTS_PATH}/{project.id}/pages')

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestGetPage:
    """Tests for GET /projects/{project_id}/pages/{page_id}."""

    async def test_returns_the_page_with_its_position_and_images(
        self, fx_client: httpx.AsyncClient, fx_book: Book
    ) -> None:
        """Verify a page is addressed by its identifier and knows its place in the book.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        """
        page = fx_book.pages[0]

        response = await fx_client.get(f'{fx_book.path}/{page.id}')

        schema = PageSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((schema.id, schema.position, schema.scan_id, schema.slot) == (page.id, 2, page.scan_id, page.slot))
        expect(schema.images is not None)
        expect(ORDER_KEY_FIELD not in response.text)
        assert_expectations()

    @pytest.mark.parametrize('address', ['0', 'not-an-identifier'], ids=['index', 'malformed'])
    async def test_address_that_is_no_identifier_is_invalid(
        self, fx_client: httpx.AsyncClient, fx_book: Book, address: str
    ) -> None:
        """Verify a page has no address by index: a number is not a page identifier, and is refused by validation.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param address: Last segment of the path.
        :type address: str
        """
        response = await fx_client.get(f'{fx_book.path}/{address}')

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT

    async def test_missing_page_and_page_of_another_project_are_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_book: Book, fx_actor: Actor
    ) -> None:
        """Verify an identifier no page has, and a page of another project of the account, both answer 404.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_book: Book of the signed-in account.
        :type fx_book: Book
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        other = make_project(owner_id=fx_actor.account_id)
        foreign = make_page(project_id=other.id)
        await commit_project(fx_database, other, foreign)

        responses = [await fx_client.get(f'{fx_book.path}/{page_id}') for page_id in (uuid4(), foreign.id)]

        assert [response.status_code for response in responses] == [status.HTTP_404_NOT_FOUND] * 2

    async def test_page_of_another_accounts_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify a page of another account's project answers exactly like a missing one.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        project = make_project(owner_id=new_account_id())
        page = make_page(project_id=project.id)
        await commit_project(fx_database, project, page)

        response = await fx_client.get(f'{PROJECTS_PATH}/{project.id}/pages/{page.id}')

        assert response.status_code == status.HTTP_404_NOT_FOUND
