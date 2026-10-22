"""Tests for the project endpoints, on the SQLite file and the local stores under the test's data directory."""

import json
from typing import TYPE_CHECKING, Any, NamedTuple

import attrs
import pytest
from delayed_assert import assert_expectations, expect
from fastapi import status
from fastapi_pagination import Page

from bookreviver.api.schemas.projects import BookDetailsSchema, ProjectCreate, ProjectSchema, ProjectUpdate
from bookreviver.api.schemas.types import CONTRIBUTORS_MAX_LENGTH, HEIGHT_CM_MAX, TITLE_MAX_LENGTH
from bookreviver.domain.enums import ContributorRole, IdentifierScheme, ImagePolicy, Orthography, PageKind
from bookreviver.domain.values import BookDetails, Contributor
from tests.conftest import TEST_BASE_URL
from tests.helpers.builders import FULL_DETAILS, make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import commit_project, store_project

if TYPE_CHECKING:
    import httpx
    from pydantic import BaseModel

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Actor
    from tests.helpers.database import UnitOfWorkFactory
    from tests.helpers.storage import BookFiles

pytestmark = pytest.mark.anyio

PROJECTS_PATH: str = '/api/v1/projects'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
MERGE_PATCH_MEDIA_TYPE: str = 'application/merge-patch+json'
CONTENT_TYPE_HEADER: str = 'content-type'
LOCATION_HEADER: str = 'location'
TITLE_FIELD: str = 'title'
CONTRIBUTORS_FIELD: str = 'contributors'
IDENTIFIERS_FIELD: str = 'identifiers'
LANGUAGES_FIELD: str = 'languages'
HEIGHT_FIELD: str = 'height_cm'
SUBJECTS_FIELD: str = 'subjects'
PUBLISHER_FIELD: str = 'publisher'
SCRIPT_FIELD: str = 'script'
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
AUTHOR: dict[str, str] = {'name': 'Anonymous', 'role': 'aut'}
EDITOR: dict[str, str] = {'name': 'N. N. Ivanov', 'role': 'edt'}
NOTES: str = 'Bought in Vilnia'
VALID_ISBN: str = '0-306-40615-2'
NORMALIZED_ISBN: str = '0306406152'
WRONG_ISBN: str = '0-306-40615-3'


class InvalidBody(NamedTuple):
    """A request body that breaks one declared constraint, and the field it breaks.

    :ivar body: JSON body of the request.
    :ivar field: Name of the field the problem must mention.
    """

    body: dict[str, Any]
    field: str


INVALID_FIELD_BODIES: list[dict[str, Any]] = [
    {IDENTIFIERS_FIELD: [{'scheme': 'isbn', 'value': WRONG_ISBN}]},
    {IDENTIFIERS_FIELD: [{'scheme': 'url', 'value': 'ftp://example.org'}]},
    {CONTRIBUTORS_FIELD: [{'name': 'Anonymous', 'role': 'xyz'}]},
    {CONTRIBUTORS_FIELD: [{'name': ' ', 'role': 'aut'}]},
    {CONTRIBUTORS_FIELD: [AUTHOR] * (CONTRIBUTORS_MAX_LENGTH + 1)},
    {LANGUAGES_FIELD: ['ru']},
    {LANGUAGES_FIELD: ['rus', 'rus']},
    {HEIGHT_FIELD: 0},
    {HEIGHT_FIELD: HEIGHT_CM_MAX + 1},
    {SCRIPT_FIELD: 'runic'},
]
INVALID_CREATE_CASES: list[InvalidBody] = [
    InvalidBody({PUBLISHER_FIELD: 'Publisher'}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: '   '}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: 'x' * (TITLE_MAX_LENGTH + 1)}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: TITLE, ORTHOGRAPHY_FIELD: 'phonetic'}, ORTHOGRAPHY_FIELD),
    InvalidBody({TITLE_FIELD: TITLE, UNKNOWN_FIELD: str(new_account_id())}, UNKNOWN_FIELD),
    *(InvalidBody({TITLE_FIELD: TITLE, **body}, next(iter(body))) for body in INVALID_FIELD_BODIES),
]
INVALID_CREATE_IDS: list[str] = [
    'no-title',
    'blank-title',
    'long-title',
    'unknown-orthography',
    'unknown-field',
    'wrong-isbn',
    'non-web-url',
    'unknown-role',
    'blank-name',
    'too-many-contributors',
    'two-letter-language',
    'repeated-language',
    'zero-height',
    'too-high',
    'unknown-script',
]
INVALID_UPDATE_CASES: list[InvalidBody] = [
    InvalidBody({TITLE_FIELD: ' '}, TITLE_FIELD),
    InvalidBody({TITLE_FIELD: None}, TITLE_FIELD),
    InvalidBody({UNKNOWN_FIELD: str(new_account_id())}, UNKNOWN_FIELD),
    InvalidBody({IDENTIFIERS_FIELD: [{'scheme': 'isbn', 'value': WRONG_ISBN}]}, IDENTIFIERS_FIELD),
    InvalidBody({HEIGHT_FIELD: -3}, HEIGHT_FIELD),
]
INVALID_UPDATE_IDS: list[str] = ['blank-title', 'null-title', 'unknown-field', 'wrong-isbn', 'negative-height']


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
            (ProjectUpdate, frozenset({IMAGE_POLICY_FIELD})),
            (BookDetailsSchema, frozenset({'primary_author'})),
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
        self, fx_client: httpx.AsyncClient, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the list is paged, the latest added first, and limited to the signed-in account.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        projects = [make_project(minutes=minutes) for minutes in range(PROJECT_COUNT)]
        for project in projects:
            await store_project(fx_uow(), project, owner=fx_actor.account_id)
        await commit_project(fx_database, fx_uow(), make_project(minutes=PROJECT_COUNT), owner=new_account_id())

        first = await _list_projects(fx_client, {PAGE_SIZE_PARAM: PAGE_SIZE})
        second = await _list_projects(fx_client, {PAGE_SIZE_PARAM: PAGE_SIZE, PAGE_NUMBER_PARAM: 2})

        newest_first = [project.id for project in reversed(projects)]
        expect([item.id for item in first.items] == newest_first[:PAGE_SIZE])
        expect([item.id for item in second.items] == newest_first[PAGE_SIZE:])
        expect((first.total, first.pages) == (PROJECT_COUNT, 2))
        assert_expectations()

    async def test_counts_the_book_of_each_project(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify every listed project carries the numbers of its pages, sources and scans.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project()
        source = make_source(project_id=project.id)
        scans = [make_scan(source=source, number=number) for number in range(2)]
        # The second scan is a spread whose page has not been made yet, so the book has one page
        page = make_page(project_id=project.id, scan=scans[0])
        await store_project(fx_uow(), project, page, owner=fx_actor.account_id, sources=[source], scans=scans)

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

    async def test_stores_the_full_description_with_normalized_identifiers(self, fx_client: httpx.AsyncClient) -> None:
        """Verify every group of fields is stored, an ISBN comes back without hyphens and the first author is named.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        """
        body = {
            TITLE_FIELD: TITLE,
            'subtitle': 'with notes',
            'parallel_titles': ['Zbiór'],
            CONTRIBUTORS_FIELD: [EDITOR, AUTHOR],
            'printer': 'Government press',
            'censorship': 'Permitted by the censor. Vilna, 12 May 1896',
            LANGUAGES_FIELD: ['bel', 'rus'],
            SCRIPT_FIELD: 'cyrillic',
            HEIGHT_FIELD: 22,
            IDENTIFIERS_FIELD: [
                {'scheme': 'isbn', 'value': f' {VALID_ISBN} '},
                {'scheme': 'shelfmark', 'value': '18.1'},
            ],
            SUBJECTS_FIELD: ['Folklore'],
            'rights': 'public-domain',
            'copy_holder': 'Private collection',
        }

        response = await fx_client.post(PROJECTS_PATH, json=body)

        details = ProjectSchema.model_validate_json(response.content).details
        expect(response.status_code == status.HTTP_201_CREATED)
        expect(
            [(person.name, person.role) for person in details.contributors]
            == [
                (EDITOR['name'], ContributorRole.EDITOR),
                (AUTHOR['name'], ContributorRole.AUTHOR),
            ]
        )
        expect(details.primary_author == AUTHOR['name'])
        expect(
            [(item.scheme, item.value) for item in details.identifiers]
            == [
                (IdentifierScheme.ISBN, NORMALIZED_ISBN),
                (IdentifierScheme.SHELFMARK, '18.1'),
            ]
        )
        expect((details.languages, details.height_cm, details.printer) == (['bel', 'rus'], 22, 'Government press'))
        expect(
            (details.parallel_titles, details.subjects, details.copy_holder)
            == (['Zbiór'], ['Folklore'], 'Private collection')
        )
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
        self, fx_client: httpx.AsyncClient, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, *, owned_by_other: bool
    ) -> None:
        """Verify another account's project answers exactly like a project that does not exist.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param owned_by_other: Whether the project is stored for another account, rather than not stored at all.
        :type owned_by_other: bool
        """
        project = make_project()
        if owned_by_other:
            await commit_project(fx_database, fx_uow(), project, owner=new_account_id())

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
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a partial body changes its fields and keeps the rest of the description.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project(title=TITLE)
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        response = await fx_client.patch(_project_path(project.id), json={PUBLISHER_FIELD: ' Press '})

        updated = ProjectSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((updated.details.title, updated.details.publisher) == (TITLE, 'Press'))
        expect((await _read_project(fx_client, project.id)).details == updated.details)
        assert_expectations()

    async def test_a_sent_list_replaces_the_whole_list_and_omitted_fields_keep_their_values(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a list is replaced as a whole and a normalized identifier is stored, while unsent fields stay.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = attrs.evolve(make_project(), details=FULL_DETAILS)
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        response = await fx_client.patch(
            _project_path(project.id),
            json={CONTRIBUTORS_FIELD: [AUTHOR], IDENTIFIERS_FIELD: [{'scheme': 'isbn', 'value': VALID_ISBN}]},
        )

        details = ProjectSchema.model_validate_json(response.content).details
        expect(response.status_code == status.HTTP_200_OK)
        expect([person.name for person in details.contributors] == [AUTHOR['name']])
        expect(
            [(item.scheme, item.value) for item in details.identifiers] == [(IdentifierScheme.ISBN, NORMALIZED_ISBN)]
        )
        expect(
            (details.languages, details.height_cm, details.subjects)
            == (['bel', 'rus'], 22, ['Фольклор', 'Народные песни'])
        )
        expect((details.title, details.printer) == (FULL_DETAILS.title, FULL_DETAILS.printer))
        assert_expectations()

    async def test_null_clears_lists_the_height_and_enums_and_omitted_fields_stay(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify null empties a list, clears the height and sets an enum to unknown, and other fields stay.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = attrs.evolve(make_project(), details=FULL_DETAILS)
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        response = await fx_client.patch(
            _project_path(project.id),
            json={
                CONTRIBUTORS_FIELD: None,
                HEIGHT_FIELD: None,
                SCRIPT_FIELD: None,
                LANGUAGES_FIELD: None,
                'printer': None,
            },
        )

        details = ProjectSchema.model_validate_json(response.content).details
        expect(response.status_code == status.HTTP_200_OK)
        expect((details.contributors, details.languages, details.height_cm) == ([], [], None))
        expect((details.script.value, details.printer, details.primary_author) == ('unknown', '', ''))
        expect((details.publisher, details.subjects) == (FULL_DETAILS.publisher, ['Фольклор', 'Народные песни']))
        assert_expectations()

    async def test_null_clears_a_field_as_a_merge_patch(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify a merge patch sent with its own media type clears the fields sent as null and keeps the omitted ones.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        author = (Contributor(name=AUTHOR['name'], role=ContributorRole.AUTHOR),)
        details = BookDetails(title=TITLE, contributors=author, notes=NOTES, orthography=Orthography.PRE_REFORM)
        project = attrs.evolve(make_project(), details=details)
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        response = await fx_client.patch(
            _project_path(project.id),
            content=json.dumps({CONTRIBUTORS_FIELD: None, ORTHOGRAPHY_FIELD: None}),
            headers={CONTENT_TYPE_HEADER: MERGE_PATCH_MEDIA_TYPE},
        )

        updated = ProjectSchema.model_validate_json(response.content)
        expect(response.status_code == status.HTTP_200_OK)
        expect((updated.details.contributors, updated.details.orthography) == ([], Orthography.UNKNOWN))
        expect((updated.details.title, updated.details.notes) == (TITLE, NOTES))
        assert_expectations()

    async def test_sets_the_image_policy_and_null_restores_the_default(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the image policy changes, and null restores the policy a new project has.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project()
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        set_response = await fx_client.patch(_project_path(project.id), json={IMAGE_POLICY_FIELD: ImagePolicy.LOSSLESS})
        cleared_response = await fx_client.patch(_project_path(project.id), json={IMAGE_POLICY_FIELD: None})

        was, now = (
            ProjectSchema.model_validate_json(response.content) for response in (set_response, cleared_response)
        )
        expect(was.image_policy == ImagePolicy.LOSSLESS)
        expect(now.image_policy == ImagePolicy.COMPACT)
        assert_expectations()

    async def test_the_cover_is_the_first_page_of_the_kind_cover_and_is_not_chosen_by_hand(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor
    ) -> None:
        """Verify the project names its first page of the kind cover as the cover, and a request cannot choose one.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project()
        title = make_page(project_id=project.id, order_key='a0', kind=PageKind.TITLE)
        cover = make_page(project_id=project.id, order_key='a1', kind=PageKind.COVER)
        await store_project(fx_uow(), project, title, cover, owner=fx_actor.account_id)

        chosen = await fx_client.patch(_project_path(project.id), json={COVER_FIELD: str(title.id)})

        expect((await _read_project(fx_client, project.id)).cover_page_id == cover.id)
        expect(chosen.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        assert_expectations()

    @pytest.mark.parametrize(CASE_ARG, INVALID_UPDATE_CASES, ids=INVALID_UPDATE_IDS)
    async def test_invalid_body_is_a_problem_and_changes_nothing(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_actor: Actor, case: InvalidBody
    ) -> None:
        """Verify the title cannot be emptied or cleared, unknown fields are refused, and the project stays.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        :param case: Body breaking one constraint, and the field it breaks.
        :type case: InvalidBody
        """
        project = make_project(title=TITLE)
        await store_project(fx_uow(), project, owner=fx_actor.account_id)

        response = await fx_client.patch(_project_path(project.id), json=case.body)

        expect(response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(case.field in response.text)
        expect((await _read_project(fx_client, project.id)).details.title == TITLE)
        assert_expectations()

    async def test_another_accounts_project_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory
    ) -> None:
        """Verify another account's project cannot be changed.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        project = make_project()
        await commit_project(fx_database, fx_uow(), project, owner=new_account_id())

        response = await fx_client.patch(_project_path(project.id), json={PUBLISHER_FIELD: 'Press'})

        assert response.status_code == status.HTTP_404_NOT_FOUND


class TestDeleteProject:
    """Tests for DELETE /projects/{project_id}."""

    async def test_deletes_the_project_and_its_files(
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_files: BookFiles, fx_actor: Actor
    ) -> None:
        """Verify the answer is 204, the project is gone, and nothing of it is left on disk.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the application's stores.
        :type fx_files: BookFiles
        :param fx_actor: The signed-in account.
        :type fx_actor: Actor
        """
        project = make_project()
        page = make_page(project_id=project.id)
        await store_project(fx_uow(), project, page, owner=fx_actor.account_id)
        await fx_files.store(page)

        response = await fx_client.delete(_project_path(project.id))

        expect(response.status_code == status.HTTP_204_NO_CONTENT)
        expect((await fx_client.get(_project_path(project.id))).status_code == status.HTTP_404_NOT_FOUND)
        expect(fx_files.gone(project.id))
        assert_expectations()

    async def test_another_accounts_project_is_not_found_and_kept(
        self, fx_client: httpx.AsyncClient, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_files: BookFiles
    ) -> None:
        """Verify another account's project and files survive a delete request.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_database: Database of the test, which the application runs on.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_files: Files of imported books in the application's stores.
        :type fx_files: BookFiles
        """
        project = make_project()
        page = make_page(project_id=project.id)
        await commit_project(fx_database, fx_uow(), project, page, owner=new_account_id())
        await fx_files.store(page)

        response = await fx_client.delete(_project_path(project.id))

        expect(response.status_code == status.HTTP_404_NOT_FOUND)
        expect(fx_files.kept(page))
        assert_expectations()
