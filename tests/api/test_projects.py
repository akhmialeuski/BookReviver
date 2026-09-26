"""Tests for the project endpoints."""

from typing import TYPE_CHECKING, Any, NamedTuple

import attrs
import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.api.schemas.projects import BookDetailsSchema, ProjectCreate, ProjectSchema, ProjectUpdate
from bookreviver.api.schemas.types import TITLE_MAX_LENGTH
from bookreviver.domain.enums import Orthography, PageAsset
from bookreviver.domain.values import BookDetails
from tests.conftest import TEST_BASE_URL
from tests.helpers.builders import make_page, make_project, new_account_id
from tests.helpers.fakes_projects import commit_project

if TYPE_CHECKING:
    import httpx
    from pydantic import BaseModel

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor
    from tests.helpers.fakes_projects import FakeAssetStore, FakeSourceStore

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
TITLE_FIELD: str = 'title'
AUTHORS_FIELD: str = 'authors'
ORTHOGRAPHY_FIELD: str = 'orthography'
UNKNOWN_FIELD: str = 'owner_id'
PAGE_SIZE_PARAM: str = 'size'
PAGE_NUMBER_PARAM: str = 'page'
PROJECT_COUNT: int = 3
PAGE_SIZE: int = 2
TITLE: str = 'Slovo o polku'
AUTHORS: str = 'Anonymous'
ASSET_CONTENT: bytes = b'jpeg'


class InvalidBody(NamedTuple):
    """A request body that breaks one declared constraint, and the field it breaks."""

    body: dict[str, Any]
    field: str


def _project_path(project_id: object) -> str:
    """Return the path of one project."""
    return f'{PROJECTS_PATH}/{project_id}'


async def _read_project(client: httpx.AsyncClient, project_id: object) -> ProjectSchema:
    """Read one project through the API."""
    return ProjectSchema.model_validate_json((await client.get(_project_path(project_id))).content)


async def _list_projects(client: httpx.AsyncClient, params: dict[str, int]) -> Page[ProjectSchema]:
    """Read one page of the project list through the API."""
    return Page[ProjectSchema].model_validate_json((await client.get(PROJECTS_PATH, params=params)).content)


class TestSchemas:
    """Tests for the project schemas against the domain description."""

    @pytest.mark.parametrize('schema', [ProjectCreate, ProjectUpdate, BookDetailsSchema])
    def test_schema_covers_every_description_field(self, schema: type[BaseModel]) -> None:
        """Verify each schema carries exactly the fields of a book description, so they never drift apart."""
        assert schema.model_fields.keys() == attrs.fields_dict(BookDetails).keys()


class TestListProjects:
    """Tests for GET /projects."""

    async def test_pages_through_own_projects_newest_first(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the list is paged, ordered by update time and limited to the signed-in account."""
        projects = [make_project(owner_id=fx_actor.account_id, minutes=minutes) for minutes in range(PROJECT_COUNT)]
        for project in [*projects, make_project(owner_id=new_account_id(), minutes=PROJECT_COUNT)]:
            await commit_project(fx_database, project)
        first = await _list_projects(fx_client, {PAGE_SIZE_PARAM: PAGE_SIZE})
        second = await _list_projects(fx_client, {PAGE_SIZE_PARAM: PAGE_SIZE, PAGE_NUMBER_PARAM: 2})
        newest_first = [project.id for project in reversed(projects)]
        expect([item.id for item in first.items] == newest_first[:PAGE_SIZE])
        expect([item.id for item in second.items] == newest_first[PAGE_SIZE:])
        expect((first.total, first.pages) == (PROJECT_COUNT, 2))
        assert_expectations()

    async def test_counts_the_pages_of_each_project(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify every listed project carries the number of its pages."""
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project, make_page(project_id=project.id, index=0))
        listed = await _list_projects(fx_client, {})
        assert [item.page_count for item in listed.items] == [1]


class TestCreateProject:
    """Tests for POST /projects."""

    async def test_creates_and_points_to_the_new_project(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the answer is 201 with the stored project, trimmed input and a Location that reads it back."""
        response = await fx_client.post(
            PROJECTS_PATH, json={TITLE_FIELD: f'  {TITLE} ', ORTHOGRAPHY_FIELD: Orthography.PRE_REFORM}
        )
        created = ProjectSchema.model_validate_json(response.content)
        location = response.headers['location']
        expect(response.status_code == status.HTTP_201_CREATED)
        expect(location == f'{TEST_BASE_URL}{_project_path(created.id)}')
        expect((created.details.title, created.details.orthography) == (TITLE, Orthography.PRE_REFORM))
        expect((created.page_count, created.source) == (0, None))
        expect(ProjectSchema.model_validate_json((await fx_client.get(location)).content) == created)
        assert_expectations()

    @pytest.mark.parametrize(
        'case',
        [
            InvalidBody({AUTHORS_FIELD: AUTHORS}, TITLE_FIELD),
            InvalidBody({TITLE_FIELD: '   '}, TITLE_FIELD),
            InvalidBody({TITLE_FIELD: 'x' * (TITLE_MAX_LENGTH + 1)}, TITLE_FIELD),
            InvalidBody({TITLE_FIELD: TITLE, ORTHOGRAPHY_FIELD: 'phonetic'}, ORTHOGRAPHY_FIELD),
            InvalidBody({TITLE_FIELD: TITLE, UNKNOWN_FIELD: str(new_account_id())}, UNKNOWN_FIELD),
        ],
        ids=['no-title', 'blank-title', 'long-title', 'unknown-orthography', 'unknown-field'],
    )
    async def test_invalid_body_is_a_problem(self, fx_client: httpx.AsyncClient, case: InvalidBody) -> None:
        """Verify each broken constraint answers 422 as a problem naming the field, and nothing is created."""
        response = await fx_client.post(PROJECTS_PATH, json=case.body)
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(response.headers['content-type'].startswith(PROBLEM_MEDIA_TYPE))
        expect(case.field in response.text)
        expect((await _list_projects(fx_client, {})).total == 0)
        assert_expectations()


class TestGetProject:
    """Tests for GET /projects/{project_id}."""

    @pytest.mark.parametrize('owned_by_other', [True, False], ids=['other-account', 'unknown'])
    async def test_invisible_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, *, owned_by_other: bool
    ) -> None:
        """Verify another account's project answers exactly like a project that does not exist."""
        project = make_project(owner_id=new_account_id())
        if owned_by_other:
            await commit_project(fx_database, project)
        response = await fx_client.get(_project_path(project.id))
        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect(str(project.id) not in response.text)
        assert_expectations()

    async def test_malformed_identifier_is_a_problem(self, fx_client: httpx.AsyncClient) -> None:
        """Verify an identifier that is not a UUID is rejected before the route runs."""
        response = await fx_client.get(_project_path('not-a-uuid'))
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestUpdateProject:
    """Tests for PATCH /projects/{project_id}."""

    async def test_changes_only_the_fields_sent(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a partial body changes its fields and keeps the rest of the description."""
        project = make_project(owner_id=fx_actor.account_id, title=TITLE)
        await commit_project(fx_database, project)
        response = await fx_client.patch(_project_path(project.id), json={AUTHORS_FIELD: f' {AUTHORS} '})
        updated = ProjectSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((updated.details.title, updated.details.authors) == (TITLE, AUTHORS))
        expect((await _read_project(fx_client, project.id)).details == updated.details)
        assert_expectations()

    async def test_blank_title_is_a_problem(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the title cannot be cleared, and the description stays as it was."""
        project = make_project(owner_id=fx_actor.account_id, title=TITLE)
        await commit_project(fx_database, project)
        response = await fx_client.patch(_project_path(project.id), json={TITLE_FIELD: ' '})
        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect((await _read_project(fx_client, project.id)).details.title == TITLE)
        assert_expectations()

    async def test_another_accounts_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify another account's project cannot be changed."""
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project)
        response = await fx_client.patch(_project_path(project.id), json={AUTHORS_FIELD: AUTHORS})
        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestDeleteProject:
    """Tests for DELETE /projects/{project_id}."""

    async def test_deletes_the_project_and_its_files(
        self,
        fx_client: httpx.AsyncClient,
        fx_database: InMemoryDatabase,
        fx_source_store: FakeSourceStore,
        fx_asset_store: FakeAssetStore,
        fx_actor: Actor,
    ) -> None:
        """Verify the answer is 204, the project is gone, and its source and derived files were removed."""
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id, index=0)
        await commit_project(fx_database, project, page)
        fx_asset_store.put(page.asset_key(PageAsset.FULL), ASSET_CONTENT)
        response = await fx_client.delete(_project_path(project.id))
        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect((await fx_client.get(_project_path(project.id))).status_code == status.HTTP_404_NOT_FOUND)
        expect(fx_source_store.deleted == [project.id])
        expect(not fx_asset_store.exists(page.asset_key(PageAsset.FULL)))
        assert_expectations()

    async def test_another_accounts_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_source_store: FakeSourceStore
    ) -> None:
        """Verify another account's project and files survive a delete request."""
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project)
        response = await fx_client.delete(_project_path(project.id))
        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect(fx_source_store.deleted == [])
        assert_expectations()
