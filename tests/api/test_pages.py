"""Tests for the page endpoints."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.api.schemas.iiif import IIIF_INFO_FILE
from bookreviver.api.schemas.pages import PageSchema
from bookreviver.domain.enums import PageAsset
from bookreviver.domain.values import PageAssets
from tests.conftest import TEST_BASE_URL
from tests.helpers.builders import PAGE_WIDTH_PX, make_page, make_project, new_account_id
from tests.helpers.fakes_projects import commit_project

if TYPE_CHECKING:
    import httpx

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project
    from bookreviver.domain.ids import StorageKey

pytestmark = pytest.mark.anyio

IIIF_BASE_URL: str = f'{TEST_BASE_URL}/api/v1/iiif'
PAGE_COUNT: int = 3
PAGE_SIZE: int = 2
READY: PageAssets = PageAssets(ready=True, version=1)


def _pages_path(project_id: object) -> str:
    """Return the path of a project's page manifest."""
    return f'/api/v1/projects/{project_id}/pages'


def _file_url(key: StorageKey | str) -> str:
    """Return the absolute address the IIIF route serves ``key`` at."""
    return f'{IIIF_BASE_URL}/{key}'


@pytest.fixture
async def fx_project(fx_database: InMemoryDatabase, fx_actor: Actor) -> Project:
    """Commit a project of the actor whose first page has its images ready and whose other pages do not."""
    project = make_project(owner_id=fx_actor.account_id)
    pages = [make_page(project_id=project.id, index=index) for index in range(PAGE_COUNT)]
    await commit_project(fx_database, project, evolve(pages[0], assets=READY), *pages[1:])
    return project


class TestListPages:
    """Tests for GET /projects/{project_id}/pages."""

    async def test_manifest_is_paged_with_image_addresses_of_ready_pages(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify pages come in book order, paged, and only a ready page carries its info.json and thumbnail."""
        response = await fx_client.get(_pages_path(fx_project.id), params={'size': PAGE_SIZE})
        manifest = Page[PageSchema].model_validate_json(response.content)
        ready = evolve(make_page(project_id=fx_project.id, index=0), assets=READY)
        first, second = manifest.items
        expect([page.index for page in manifest.items] == list(range(PAGE_SIZE)))
        expect(manifest.total == PAGE_COUNT)
        expect(str(first.info_url) == _file_url(f'{ready.asset_key(PageAsset.TILES)}/{IIIF_INFO_FILE}'))
        expect(str(first.thumbnail_url) == _file_url(ready.asset_key(PageAsset.THUMBNAIL)))
        expect((second.ready, second.info_url, second.thumbnail_url) == (False, None, None))
        expect(first.facts.width_px == PAGE_WIDTH_PX)
        assert_expectations()

    async def test_another_accounts_manifest_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify the pages of another account's project are not listed."""
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project, make_page(project_id=project.id, index=0))
        response = await fx_client.get(_pages_path(project.id))
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestGetPage:
    """Tests for GET /projects/{project_id}/pages/{index}."""

    async def test_returns_the_page_at_the_index(self, fx_client: httpx.AsyncClient, fx_project: Project) -> None:
        """Verify one page is read by its position in the book."""
        response = await fx_client.get(f'{_pages_path(fx_project.id)}/1')
        page = PageSchema.model_validate_json(response.content)
        assert (page.index, page.ready) == (1, False)

    @pytest.mark.parametrize(
        ('index', 'expected'),
        [(PAGE_COUNT, status.HTTP_404_NOT_FOUND), (-1, status.HTTP_422_UNPROCESSABLE_CONTENT)],
        ids=['past-the-end', 'negative'],
    )
    async def test_index_outside_the_book_is_a_problem(
        self, fx_client: httpx.AsyncClient, fx_project: Project, index: int, expected: int
    ) -> None:
        """Verify an index past the end is not found and a negative index is rejected before the route runs."""
        response = await fx_client.get(f'{_pages_path(fx_project.id)}/{index}')
        assert response.status_code == expected

    async def test_another_accounts_page_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify an existing page of another account's project answers 404."""
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project, make_page(project_id=project.id, index=0))
        response = await fx_client.get(f'{_pages_path(project.id)}/0')
        assert response.status_code == status.HTTP_404_NOT_FOUND
