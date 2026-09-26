"""Tests for the IIIF file endpoint."""

import json
from typing import TYPE_CHECKING, Any

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status

from bookreviver.api.routers.iiif import IMMUTABLE_CACHE
from bookreviver.api.schemas.iiif import IIIF_INFO_FILE, IIIF_INFO_MEDIA_TYPE
from bookreviver.domain.enums import PageAsset
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.values import PageAssets
from tests.conftest import TEST_BASE_URL
from tests.helpers.builders import PAGE_HEIGHT_PX, PAGE_WIDTH_PX, make_page, make_project, new_account_id
from tests.helpers.fakes_projects import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Page
    from tests.helpers.fakes_projects import FakeAssetStore

pytestmark = pytest.mark.anyio

IIIF_PATH: str = '/api/v1/iiif'
CACHE_HEADER, CACHE_VALUE = next(iter(IMMUTABLE_CACHE.items()))
TILE_PATH: str = '0,0,512,512/512,/0/default.jpg'
TILE_CONTENT: bytes = b'\xff\xd8 tile'
# What a tiler writes: an id of its own choosing, and properties the viewer needs unchanged
STORED_INFO: dict[str, Any] = {
    '@context': 'http://iiif.io/api/image/3/context.json',
    'id': 'https://example.com/iiif',
    'type': 'ImageService3',
    'protocol': 'http://iiif.io/api/image',
    'profile': 'level0',
    'width': PAGE_WIDTH_PX,
    'height': PAGE_HEIGHT_PX,
    'tiles': [{'width': 512, 'scaleFactors': [1, 2, 4]}],
}


def _tiles_key(page: Page) -> StorageKey:
    """Return the key of the page's tile pyramid."""
    return page.asset_key(PageAsset.TILES)


@pytest.fixture
async def fx_page(fx_database: InMemoryDatabase, fx_asset_store: FakeAssetStore, fx_actor: Actor) -> Page:
    """Commit a ready page of the actor's project and store its info.json, one tile and its thumbnail."""
    project = make_project(owner_id=fx_actor.account_id)
    page = evolve(make_page(project_id=project.id, index=0), assets=PageAssets(ready=True, version=1))
    await commit_project(fx_database, project, page)
    fx_asset_store.put(StorageKey(f'{_tiles_key(page)}/{IIIF_INFO_FILE}'), json.dumps(STORED_INFO).encode())
    fx_asset_store.put(StorageKey(f'{_tiles_key(page)}/{TILE_PATH}'), TILE_CONTENT)
    fx_asset_store.put(page.asset_key(PageAsset.THUMBNAIL), TILE_CONTENT)
    return page


class TestIiifFile:
    """Tests for GET /iiif/{key}."""

    async def test_info_json_points_at_its_public_pyramid(self, fx_client: httpx.AsyncClient, fx_page: Page) -> None:
        """Verify info.json read from a page's address gets the pyramid's URL as id and keeps everything else."""
        manifest = (await fx_client.get(f'/api/v1/projects/{fx_page.project_id}/pages')).json()
        response = await fx_client.get(manifest['items'][0]['info_url'])
        info = response.json()
        expect(response.status_code == status.HTTP_200_OK)
        expect(info == {**STORED_INFO, 'id': f'{TEST_BASE_URL}{IIIF_PATH}/{_tiles_key(fx_page)}'})
        expect(response.headers['content-type'] == IIIF_INFO_MEDIA_TYPE)
        expect(response.headers[CACHE_HEADER] == CACHE_VALUE)
        assert_expectations()

    @pytest.mark.parametrize(
        'key_of',
        [lambda page: f'{_tiles_key(page)}/{TILE_PATH}', lambda page: page.asset_key(PageAsset.THUMBNAIL)],
        ids=['tile', 'thumbnail'],
    )
    async def test_streams_files_as_immutable(
        self, fx_client: httpx.AsyncClient, fx_page: Page, key_of: Callable[[Page], str]
    ) -> None:
        """Verify a tile addressed like a IIIF viewer does, and a thumbnail, stream unchanged and cache forever."""
        response = await fx_client.get(f'{IIIF_PATH}/{key_of(fx_page)}')
        expect(response.status_code == status.HTTP_200_OK)
        expect(response.content == TILE_CONTENT)
        expect(response.headers[CACHE_HEADER] == CACHE_VALUE)
        assert_expectations()

    @pytest.mark.parametrize('asset', [PageAsset.FULL, PageAsset.TILES], ids=['missing-file', 'directory'])
    async def test_key_without_a_file_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_page: Page, asset: PageAsset
    ) -> None:
        """Verify a key with nothing stored, and a key naming a directory, both answer 404."""
        response = await fx_client.get(f'{IIIF_PATH}/{fx_page.asset_key(asset)}')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_another_accounts_file_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_asset_store: FakeAssetStore
    ) -> None:
        """Verify a stored file of another account's project answers exactly like a missing one."""
        project = make_project(owner_id=new_account_id())
        page = make_page(project_id=project.id, index=0)
        await commit_project(fx_database, project, page)
        fx_asset_store.put(page.asset_key(PageAsset.THUMBNAIL), TILE_CONTENT)
        response = await fx_client.get(f'{IIIF_PATH}/{page.asset_key(PageAsset.THUMBNAIL)}')
        assert response.status_code == status.HTTP_404_NOT_FOUND

    async def test_dot_segment_is_rejected(self, fx_client: httpx.AsyncClient, fx_page: Page) -> None:
        """Verify a key climbing with an encoded ``..`` is refused by validation before any lookup."""
        response = await fx_client.get(f'{IIIF_PATH}/{_tiles_key(fx_page)}/%2E%2E/{PageAsset.THUMBNAIL}')
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT
