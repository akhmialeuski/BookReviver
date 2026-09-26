"""Tests for the page use cases, against in-memory persistence and an asset store fake."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import PageAsset
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import StorageKey
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import SliceRequest
from bookreviver.services.pages import PageService
from tests.helpers.builders import make_page, make_project, new_account_id
from tests.helpers.fakes_projects import commit_project

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor, Project
    from tests.helpers.fakes_projects import FakeAssetStore

pytestmark = pytest.mark.anyio

PAGE_COUNT: int = 3
ASSET_CONTENT: bytes = b'jpeg'


@pytest.fixture
def fx_service(fx_database: InMemoryDatabase, fx_assets: FakeAssetStore) -> Callable[[], PageService]:
    """Return a function building the service for one request."""
    return lambda: PageService(InMemoryUnitOfWork(fx_database), fx_assets)


@pytest.fixture
async def fx_own_project(fx_database: InMemoryDatabase, fx_actor: Actor) -> Project:
    """Commit a project of the actor with ``PAGE_COUNT`` pages, stored in reverse order."""
    project = make_project(owner_id=fx_actor.account_id)
    pages = [make_page(project_id=project.id, index=index) for index in reversed(range(PAGE_COUNT))]
    await commit_project(fx_database, project, *pages)
    return project


@pytest.fixture
async def fx_foreign_project(fx_database: InMemoryDatabase) -> Project:
    """Commit a project of another account with one page."""
    project = make_project(owner_id=new_account_id())
    await commit_project(fx_database, project, make_page(project_id=project.id, index=0))
    return project


class TestManifest:
    """Tests for PageService.manifest()."""

    async def test_returns_a_slice_in_book_order(
        self, fx_service: Callable[[], PageService], fx_own_project: Project, fx_actor: Actor
    ) -> None:
        """Verify the pages come by index, sliced, with the size of the whole book."""
        result = await fx_service().manifest(fx_actor, fx_own_project.id, SliceRequest(offset=1, limit=PAGE_COUNT))
        expect([page.index for page in result.items] == list(range(1, PAGE_COUNT)))
        expect(result.total == PAGE_COUNT)
        assert_expectations()

    async def test_another_accounts_project_is_not_found(
        self, fx_service: Callable[[], PageService], fx_foreign_project: Project, fx_actor: Actor
    ) -> None:
        """Verify the pages of another account's project are not listed."""
        with pytest.raises(NotFoundError):
            await fx_service().manifest(fx_actor, fx_foreign_project.id, SliceRequest())


class TestGet:
    """Tests for PageService.get()."""

    async def test_returns_the_page_at_the_index(
        self, fx_service: Callable[[], PageService], fx_own_project: Project, fx_actor: Actor
    ) -> None:
        """Verify one page is read by its position."""
        page = await fx_service().get(fx_actor, fx_own_project.id, 1)
        assert (page.project_id, page.index) == (fx_own_project.id, 1)

    async def test_missing_index_is_not_found(
        self, fx_service: Callable[[], PageService], fx_own_project: Project, fx_actor: Actor
    ) -> None:
        """Verify an index past the end of the book is reported as missing."""
        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, fx_own_project.id, PAGE_COUNT)

    async def test_another_accounts_page_is_not_found(
        self, fx_service: Callable[[], PageService], fx_foreign_project: Project, fx_actor: Actor
    ) -> None:
        """Verify an existing page of another account's project is reported as missing."""
        with pytest.raises(NotFoundError):
            await fx_service().get(fx_actor, fx_foreign_project.id, 0)


class TestOpenAsset:
    """Tests for PageService.open_asset()."""

    async def test_gives_the_stored_file(
        self,
        fx_service: Callable[[], PageService],
        fx_assets: FakeAssetStore,
        fx_own_project: Project,
        fx_actor: Actor,
    ) -> None:
        """Verify the path of a derived file of the actor's page is given."""
        key = make_page(project_id=fx_own_project.id, index=0).asset_key(PageAsset.THUMBNAIL)
        fx_assets.put(key, ASSET_CONTENT)
        async with fx_service().open_asset(fx_actor, key) as path:
            assert path.read_bytes() == ASSET_CONTENT

    async def test_another_accounts_file_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_assets: FakeAssetStore,
        fx_foreign_project: Project,
        fx_actor: Actor,
    ) -> None:
        """Verify a stored file of another account's project is reported as missing."""
        key = make_page(project_id=fx_foreign_project.id, index=0).asset_key(PageAsset.THUMBNAIL)
        fx_assets.put(key, ASSET_CONTENT)
        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(fx_actor, key):
                pass

    async def test_missing_file_is_not_found(
        self, fx_service: Callable[[], PageService], fx_own_project: Project, fx_actor: Actor
    ) -> None:
        """Verify a key of the actor's project with nothing stored at it is reported as missing."""
        key = make_page(project_id=fx_own_project.id, index=0).asset_key(PageAsset.THUMBNAIL)
        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(fx_actor, key):
                pass

    async def test_key_climbing_into_another_project_is_not_found(
        self,
        fx_service: Callable[[], PageService],
        fx_assets: FakeAssetStore,
        fx_own_project: Project,
        fx_foreign_project: Project,
        fx_actor: Actor,
    ) -> None:
        """Verify a key starting in the actor's project cannot reach a file of another account through ``..``."""
        foreign_key = make_page(project_id=fx_foreign_project.id, index=0).asset_key(PageAsset.THUMBNAIL)
        fx_assets.put(foreign_key, ASSET_CONTENT)
        # A file of the actor's own makes the project directory exist, so the climbing path resolves on disk
        fx_assets.put(make_page(project_id=fx_own_project.id, index=0).asset_key(PageAsset.THUMBNAIL), ASSET_CONTENT)
        escape_key = StorageKey(
            f'{ProjectKeys(fx_own_project.id).prefix}..{foreign_key.removeprefix(ProjectKeys.ROOT)}'
        )
        with pytest.raises(NotFoundError):
            async with fx_service().open_asset(fx_actor, escape_key):
                pass
