"""Tests for the project endpoints, on in-memory persistence and the local stores under the test's data directory."""

import json
from typing import TYPE_CHECKING, Any, NamedTuple
from uuid import uuid4

import attrs
import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.api.schemas.projects import BookDetailsSchema, ProjectCreate, ProjectSchema, ProjectUpdate
from bookreviver.api.schemas.types import TITLE_MAX_LENGTH
from bookreviver.domain.enums import ImagePolicy, Orthography
from bookreviver.domain.values import BookDetails
from tests.conftest import TEST_BASE_URL
from tests.helpers.builders import make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project

if TYPE_CHECKING:
    import httpx
    from pydantic import BaseModel

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Actor
    from tests.helpers.storage import BookFiles

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
MERGE_PATCH_MEDIA_TYPE: str = 'application/merge-patch+json'
CONTENT_TYPE_HEADER: str = 'content-type'
LOCATION_HEADER: str = 'location'
TITLE_FIELD: str = 'title'
AUTHORS_FIELD: str = 'authors'
NOTES_FIELD: str = 'notes'
COVER_FIELD: str = 'cover_page_id'
IMAGE_POLICY_FIELD: str = 'image_policy'
ORTHOGRAPHY_FIELD: str = 'orthography'
UNKNOWN_FIELD: str = 'owner_id'
PAGE_SIZE_PARAM: str = 'size'
PAGE_NUMBER_PARAM: str = 'page'
SCHEMA_ARG: str = 'schema'
EXTRAS_ARG: str = 'extras'
CASE_ARG: str = 'case'
PROJECT_COUNT: int = 3
PAGE_SIZE: int = 2
TITLE: str = 'Slovo o polku'
AUTHORS: str = 'Anonymous'
NOTES: str = 'Bought in Vilnia'


class InvalidBody(NamedTuple):
    """A request body that breaks one declared constraint, and the field it breaks.

    :ivar body: JSON body of the request.
    :ivar field: Name of the field the problem must mention.
    """

    body: dict[str, Any]
    field: str


INVALID_CREATE_CASES: list[InvalidBody] = [
    InvalidBody({AUTHORS_FIELD: AUTHORS}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: '   '}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: 'x' * (TITLE_MAX_LENGTH + 1)}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: TITLE, ORTHOGRAPHY_FIELD: 'phonetic'}, ORTHOGRAPHY_FIELD),
    InvalidBody({TITLE_FIELD: TITLE, UNKNOWN_FIELD: str(new_account_id())}, UNKNOWN_FIELD),
]
INVALID_CREATE_IDS: list[str] = ['no-title', 'blank-title', 'long-title', 'unknown-orthography', 'unknown-field']
INVALID_UPDATE_CASES: list[InvalidBody] = [
    InvalidBody({TITLE_FIELD: ' '}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: None}, TITLE_FIELD),
    InvalidBody({UNKNOWN_FIELD: str(new_account_id())}, UNKNOWN_FIELD),
]
INVALID_UPDATE_IDS: list[str] = ['blank-title', 'null-title', 'unknown-field']


def _project_path(project_id: object) -> str:
    """Return the path of one project.

    :param project_id: Identifier of the project, or any text standing in for one.
    :type project_id: object
    :returns: Path of the project resource.
    :rtype: str
    """
    return f'{PROJECTS_PATH}/{project_id}'


async def _read_project(client: httpx.AsyncClient, project_id: object) -> ProjectSchema:
    """Read one project through the API.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param project_id: Identifier of the project.
    :type project_id: object
    :returns: The project resource.
    :rtype: ProjectSchema
    """
    return ProjectSchema.model_validate_json((await client.get(_project_path(project_id))).content)


async def _list_projects(client: httpx.AsyncClient, params: dict[str, int]) -> Page[ProjectSchema]:
    """Read one page of the project list through the API.

    :param client: Client of the running application.
    :type client: httpx.AsyncClient
    :param params: Page number and size to ask for.
    :type params: dict[str, int]
    :returns: The page of projects.
    :rtype: Page[ProjectSchema]
    """
    return Page[ProjectSchema].model_validate_json((await client.get(PROJECTS_PATH, params=params)).content)


class TestSchemas:
    """Tests for the project schemas against the domain description."""

    @pytest.mark.parametrize(
        (SCHEMA_ARG, EXTRAS_ARG),
        [
            (ProjectCreate, frozenset[str]()),
            (ProjectUpdate, frozenset({COVER_FIELD, IMAGE_POLICY_FIELD})),
            (BookDetailsSchema, frozenset[str]()),
        ],
        ids=['create', 'update', 'details'],
    )
    def test_schema_covers_every_description_field(self, schema: type[BaseModel], extras: frozenset[str]) -> None:
        """Verify each schema carries exactly the fields of a book description and its own, so they never drift apart.

        :param schema: Schema of a description in a request or a response.
        :type schema: type[BaseModel]
        :param extras: Fields of the schema beyond the description, such as the project settings a patch changes.
        :type extras: frozenset[str]
        """
        assert schema.model_fields.keys() == attrs.fields_dict(BookDetails).keys() | extras


class TestListProjects:
    """Tests for GET /projects."""

    async def test_pages_through_own_projects_newest_first(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify the list is paged, ordered by update time and limited to the signed-in account.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
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

    async def test_counts_the_book_of_each_project(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify every listed project carries the numbers of its pages, sources and scans.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        source = make_source(project_id=project.id)
        scans = [make_scan(source=source, number=number) for number in range(2)]
        # The second scan is a spread whose page has not been made yet, so the book has one page
        page = make_page(project_id=project.id, scan=scans[0])
        await commit_project(fx_database, project, page, sources=[source], scans=scans)

        listed = await _list_projects(fx_client, {})

        assert [(item.page_count, item.source_count, item.scan_count) for item in listed.items] == [(1, 1, 2)]


class TestCreateProject:
    """Tests for POST /projects."""

    async def test_creates_and_points_to_the_new_project(self, fx_client: httpx.AsyncClient) -> None:
        """Verify the answer is 201 with the stored project, trimmed input and a Location that reads it back.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.post(
            PROJECTS_PATH, json={TITLE_FIELD: f'  {TITLE} ', ORTHOGRAPHY_FIELD: Orthography.PRE_REFORM}
        )

        created = ProjectSchema.model_validate_json(response.content)
        location = response.headers[LOCATION_HEADER]
        expect(response.status_code == status.HTTP_201_CREATED)
        expect(location == f'{TEST_BASE_URL}{_project_path(created.id)}')
        expect((created.details.title, created.details.orthography) == (TITLE, Orthography.PRE_REFORM))
        expect((created.page_count, created.source_count, created.scan_count) == (0, 0, 0))
        expect((created.image_policy, created.cover_page_id) == (ImagePolicy.COMPACT, None))
        expect(ProjectSchema.model_validate_json((await fx_client.get(location)).content) == created)
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, INVALID_CREATE_CASES, ids=INVALID_CREATE_IDS)
    async def test_invalid_body_is_a_problem(self, fx_client: httpx.AsyncClient, case: InvalidBody) -> None:
        """Verify each broken constraint answers 422 as a problem naming the field, and nothing is created.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param case: Body breaking one constraint, and the field it breaks.
        :type case: InvalidBody
        """
        response = await fx_client.post(PROJECTS_PATH, json=case.body)

        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(case.field in response.text)
        expect((await _list_projects(fx_client, {})).total == 0)
        assert_expectations()


class TestGetProject:
    """Tests for GET /projects/{project_id}."""

    @pytest.mark.parametrize('owned_by_other', [True, False], ids=['other-account', 'unknown'])
    async def test_invisible_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, *, owned_by_other: bool
    ) -> None:
        """Verify another account's project answers exactly like a project that does not exist.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param owned_by_other: Whether the project is stored for another account, rather than not stored at all.
        :type owned_by_other: bool
        """
        project = make_project(owner_id=new_account_id())
        if owned_by_other:
            await commit_project(fx_database, project)

        response = await fx_client.get(_project_path(project.id))

        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect(str(project.id) not in response.text)
        assert_expectations()

    async def test_malformed_identifier_is_a_problem(self, fx_client: httpx.AsyncClient) -> None:
        """Verify an identifier that is not a UUID is rejected before the route runs.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        response = await fx_client.get(_project_path('not-a-uuid'))

        assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


class TestUpdateProject:
    """Tests for PATCH /projects/{project_id}."""

    async def test_changes_only_the_fields_sent(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a partial body changes its fields and keeps the rest of the description.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id, title=TITLE)
        await commit_project(fx_database, project)

        response = await fx_client.patch(_project_path(project.id), json={AUTHORS_FIELD: f' {AUTHORS} '})

        updated = ProjectSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((updated.details.title, updated.details.authors) == (TITLE, AUTHORS))
        expect((await _read_project(fx_client, project.id)).details == updated.details)
        assert_expectations()

    async def test_null_clears_a_field_as_a_merge_patch(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a merge patch sent with its own media type clears the fields sent as null and keeps the omitted ones.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        details = BookDetails(title=TITLE, authors=AUTHORS, notes=NOTES, orthography=Orthography.PRE_REFORM)
        project = attrs.evolve(make_project(owner_id=fx_actor.account_id), details=details)
        await commit_project(fx_database, project)

        response = await fx_client.patch(
            _project_path(project.id),
            content=json.dumps({AUTHORS_FIELD: None, ORTHOGRAPHY_FIELD: None}),
            headers={CONTENT_TYPE_HEADER: MERGE_PATCH_MEDIA_TYPE},
        )

        updated = ProjectSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((updated.details.authors, updated.details.orthography) == ('', Orthography.UNKNOWN))
        expect((updated.details.title, updated.details.notes) == (TITLE, NOTES))
        assert_expectations()

    async def test_sets_the_cover_and_the_image_policy_and_null_clears_them(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page of the project becomes the cover and the policy changes, and null restores their defaults.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id)
        await commit_project(fx_database, project, page)

        set_response = await fx_client.patch(
            _project_path(project.id), json={COVER_FIELD: str(page.id), IMAGE_POLICY_FIELD: ImagePolicy.LOSSLESS}
        )
        cleared_response = await fx_client.patch(
            _project_path(project.id), json={COVER_FIELD: None, IMAGE_POLICY_FIELD: None}
        )

        was, now = (
            ProjectSchema.model_validate_json(response.content) for response in (set_response, cleared_response)
        )
        expect((was.cover_page_id, was.image_policy) == (page.id, ImagePolicy.LOSSLESS))
        expect((now.cover_page_id, now.image_policy) == (None, ImagePolicy.COMPACT))
        assert_expectations()

    async def test_cover_that_is_not_a_page_of_the_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor
    ) -> None:
        """Verify a page identifier the project does not have is refused with 404 and the cover stays empty.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        await commit_project(fx_database, project)

        response = await fx_client.patch(_project_path(project.id), json={COVER_FIELD: str(uuid4())})

        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect((await _read_project(fx_client, project.id)).cover_page_id is None)
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, INVALID_UPDATE_CASES, ids=INVALID_UPDATE_IDS)
    async def test_invalid_body_is_a_problem_and_changes_nothing(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_actor: Actor, case: InvalidBody
    ) -> None:
        """Verify the title cannot be emptied or cleared, unknown fields are refused, and the project stays.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param case: Body breaking one constraint, and the field it breaks.
        :type case: InvalidBody
        """
        project = make_project(owner_id=fx_actor.account_id, title=TITLE)
        await commit_project(fx_database, project)

        response = await fx_client.patch(_project_path(project.id), json=case.body)

        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(case.field in response.text)
        expect((await _read_project(fx_client, project.id)).details.title == TITLE)
        assert_expectations()

    async def test_another_accounts_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase
    ) -> None:
        """Verify another account's project cannot be changed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        """
        project = make_project(owner_id=new_account_id())
        await commit_project(fx_database, project)

        response = await fx_client.patch(_project_path(project.id), json={AUTHORS_FIELD: AUTHORS})

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestDeleteProject:
    """Tests for DELETE /projects/{project_id}."""

    async def test_deletes_the_project_and_its_files(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_files: BookFiles, fx_actor: Actor
    ) -> None:
        """Verify the answer is 204, the project is gone, and nothing of it is left on disk.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_files: Files of imported books in the application's stores.
        :type fx_files: BookFiles
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(owner_id=fx_actor.account_id)
        page = make_page(project_id=project.id)
        await commit_project(fx_database, project, page)
        await fx_files.store(page)

        response = await fx_client.delete(_project_path(project.id))

        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect((await fx_client.get(_project_path(project.id))).status_code == status.HTTP_404_NOT_FOUND)
        expect(fx_files.gone(project.id))
        assert_expectations()

    async def test_another_accounts_project_is_not_found_and_kept(
        self, fx_client: httpx.AsyncClient, fx_database: InMemoryDatabase, fx_files: BookFiles
    ) -> None:
        """Verify another account's project and files survive a delete request.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: In-memory database of the application.
        :type fx_database: InMemoryDatabase
        :param fx_files: Files of imported books in the application's stores.
        :type fx_files: BookFiles
        """
        project = make_project(owner_id=new_account_id())
        page = make_page(project_id=project.id)
        await commit_project(fx_database, project, page)
        await fx_files.store(page)

        response = await fx_client.delete(_project_path(project.id))

        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect(fx_files.kept(page))
        assert_expectations()
