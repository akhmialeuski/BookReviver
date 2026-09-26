"""Tests for the project management pages."""

from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, NamedTuple

import pytest
from delayed_assert import assert_expectations, expect
from sqlalchemy import func, select

from bookreviver.models import (
    SHORT_TEXT_MAX_LENGTH,
    TITLE_MAX_LENGTH,
    ColorMode,
    Orthography,
    Page,
    Project,
    SourceKind,
)
from bookreviver.stages import Stage
from bookreviver.storage import ProjectStorage
from bookreviver.web.projects import (
    CHOICE_MESSAGE,
    REQUIRED_MESSAGE,
    SAVED_URL_SUFFIX,
    SETTINGS_URL,
    STAGE_URL,
    TOO_LONG_MESSAGE,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import httpx
    from fastapi import FastAPI

    ProjectFactory = Callable[..., Awaitable[int]]

LOCATION_HEADER: str = 'location'
FIRST_TITLE: str = 'Азбука'
SECOND_TITLE: str = 'Буквар'
EMPTY_LIST_MESSAGE: str = 'No projects yet'
SAVED_BANNER: str = 'role="status">Saved</p>'
NOT_FOUND_HEADING: str = '<h1>Not found</h1>'
PLACEHOLDER_NOTE: str = 'stage is not implemented yet'
CREATE_URL: str = '/projects'
PROJECT_URL: str = '/projects/{project_id}'
DELETE_URL: str = '/projects/{project_id}/delete'
BLANK_TEXT: str = '   '
TITLE_FIELD: str = 'title'
AUTHORS_FIELD: str = 'authors'
YEAR_FIELD: str = 'publication_year'
ORTHOGRAPHY_FIELD: str = 'orthography'
NOTES_FIELD: str = 'notes'
MISSING_PROJECT_ID: int = 9_999
PAGE_WIDTH_PX: int = 1_200
PAGE_HEIGHT_PX: int = 1_800
SOURCE_FILE_NAME: str = 'book.pdf'
SAMPLE_DETAILS: dict[str, str] = {
    TITLE_FIELD: 'Грамматика',
    AUTHORS_FIELD: 'Карский Евфимий',
    'publisher': 'Синодальная типография',
    'publication_place': 'Вильна',
    YEAR_FIELD: '1896',
    'edition': 'Второе',
    'series': 'Учебники',
    'volume': '2',
    'language': 'Belarusian',
    ORTHOGRAPHY_FIELD: Orthography.PRE_REFORM.value,
    NOTES_FIELD: 'Scanned from a library copy.',
}


class InvalidField(NamedTuple):
    """One rejected book details field and the message shown under it."""

    field: str
    value: str
    message: str


@pytest.fixture
def fx_create_project(fx_app: FastAPI) -> ProjectFactory:
    """Return a factory that stores a project with a number of pages and returns its id."""

    async def inner(*, title: str, page_count: int = 0, **attributes: object) -> int:
        async with fx_app.state.session_factory() as session:
            project = Project(title=title, **attributes)
            project.pages = [
                Page(index=index, width_px=PAGE_WIDTH_PX, height_px=PAGE_HEIGHT_PX, color_mode=ColorMode.GRAY)
                for index in range(page_count)
            ]
            session.add(project)
            await session.commit()
            return project.id

    return inner


@pytest.mark.anyio
class TestListProjects:
    """Tests for list_projects()."""

    async def test_empty_list_shows_first_run_message(self, fx_client: httpx.AsyncClient) -> None:
        """Verify an empty database shows the first-run message and the focused title input."""
        response = await fx_client.get('/')
        expect(response.status_code == HTTPStatus.OK)
        expect(EMPTY_LIST_MESSAGE in response.text)
        expect('autofocus' in response.text)
        expect('<label for="new-title">' in response.text)
        assert_expectations()

    async def test_lists_projects_newest_updated_first_with_page_counts(
        self,
        fx_client: httpx.AsyncClient,
        fx_create_project: ProjectFactory,
    ) -> None:
        """Verify a project updated after another one is listed first, with its authors, year and page count."""
        first_id = await fx_create_project(title=FIRST_TITLE, page_count=3)
        await fx_create_project(title=SECOND_TITLE)
        # Saving the older project makes it the most recently updated one
        await fx_client.post(
            SETTINGS_URL.format(project_id=first_id), data={**SAMPLE_DETAILS, TITLE_FIELD: FIRST_TITLE}
        )

        response = await fx_client.get('/')

        text = response.text
        expect(EMPTY_LIST_MESSAGE not in text)
        expect(0 <= text.find(FIRST_TITLE) < text.find(SECOND_TITLE))
        expect(SAMPLE_DETAILS[AUTHORS_FIELD] in text)
        expect(SAMPLE_DETAILS[YEAR_FIELD] in text)
        expect('<td class="number">3</td>' in text)
        expect('<td class="number">0</td>' in text)
        assert_expectations()


@pytest.mark.anyio
class TestCreateProject:
    """Tests for create_project()."""

    async def test_creates_project_and_redirects_to_first_stage(
        self,
        fx_client: httpx.AsyncClient,
        fx_app: FastAPI,
    ) -> None:
        """Verify the trimmed title is stored and the browser is sent to the Import stage."""
        response = await fx_client.post(CREATE_URL, data={TITLE_FIELD: f'  {FIRST_TITLE}  '})
        async with fx_app.state.session_factory() as session:
            project = await session.scalar(select(Project))
        assert project is not None
        expect(response.status_code == HTTPStatus.SEE_OTHER)
        expect(response.headers[LOCATION_HEADER] == STAGE_URL.format(project_id=project.id, stage=Stage.first()))
        expect(project.title == FIRST_TITLE)
        assert_expectations()

    @pytest.mark.parametrize(
        ('raw_title', 'expected_error'),
        [
            ('', REQUIRED_MESSAGE),
            (BLANK_TEXT, REQUIRED_MESSAGE),
            ('x' * (TITLE_MAX_LENGTH + 1), TOO_LONG_MESSAGE.format(max_length=TITLE_MAX_LENGTH)),
        ],
    )
    async def test_invalid_title_rerenders_list_with_error(
        self,
        fx_client: httpx.AsyncClient,
        fx_app: FastAPI,
        raw_title: str,
        expected_error: str,
    ) -> None:
        """Verify an empty or too long title creates nothing and shows the reason with status 422."""
        response = await fx_client.post(CREATE_URL, data={TITLE_FIELD: raw_title})
        async with fx_app.state.session_factory() as session:
            project_count = await session.scalar(select(func.count(Project.id)))
        expect(response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT)
        expect(expected_error in response.text)
        expect('aria-invalid="true"' in response.text)
        expect(project_count == 0)
        assert_expectations()


@pytest.mark.anyio
class TestOpenProject:
    """Tests for open_project()."""

    async def test_redirects_to_first_stage(
        self,
        fx_client: httpx.AsyncClient,
        fx_create_project: ProjectFactory,
    ) -> None:
        """Verify opening a project lands on its first stage."""
        project_id = await fx_create_project(title=FIRST_TITLE)
        response = await fx_client.get(PROJECT_URL.format(project_id=project_id))
        expect(response.status_code == HTTPStatus.SEE_OTHER)
        expect(response.headers[LOCATION_HEADER] == STAGE_URL.format(project_id=project_id, stage=Stage.first()))
        assert_expectations()

    async def test_unknown_project_shows_not_found_page(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a missing project answers with the readable 404 page."""
        response = await fx_client.get(PROJECT_URL.format(project_id=MISSING_PROJECT_ID))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(NOT_FOUND_HEADING in response.text)
        assert_expectations()


@pytest.mark.anyio
class TestShowSettings:
    """Tests for show_settings()."""

    async def test_shows_source_summary_and_danger_zone(
        self,
        fx_client: httpx.AsyncClient,
        fx_create_project: ProjectFactory,
    ) -> None:
        """Verify an imported project shows its source facts, its page count and the delete confirmation."""
        project_id = await fx_create_project(
            title=FIRST_TITLE,
            page_count=2,
            source_kind=SourceKind.PDF,
            source_name=SOURCE_FILE_NAME,
            source_size_bytes=2_500_000,
            imported_at=datetime(2026, 9, 1, 12, 30, tzinfo=UTC),
        )
        response = await fx_client.get(SETTINGS_URL.format(project_id=project_id))
        text = response.text
        expect(response.status_code == HTTPStatus.OK)
        expect(SOURCE_FILE_NAME in text)
        expect('2.5 MB' in text)
        expect('2026-09-01 12:30' in text)
        expect('<dt>Pages</dt><dd>2</dd>' in text)
        expect('confirm(' in text)
        expect(f'action="{DELETE_URL.format(project_id=project_id)}"' in text)
        expect(SAVED_BANNER not in text)
        assert_expectations()

    async def test_project_without_source_says_so(
        self,
        fx_client: httpx.AsyncClient,
        fx_create_project: ProjectFactory,
    ) -> None:
        """Verify a project with nothing imported shows no source summary."""
        project_id = await fx_create_project(title=FIRST_TITLE)
        response = await fx_client.get(SETTINGS_URL.format(project_id=project_id))
        assert 'No source imported yet.' in response.text

    async def test_saved_flag_shows_banner(
        self,
        fx_client: httpx.AsyncClient,
        fx_create_project: ProjectFactory,
    ) -> None:
        """Verify the query flag set by a successful save shows the banner."""
        project_id = await fx_create_project(title=FIRST_TITLE)
        response = await fx_client.get(SETTINGS_URL.format(project_id=project_id), params={'saved': '1'})
        assert SAVED_BANNER in response.text

    async def test_unknown_project_shows_not_found_page(self, fx_client: httpx.AsyncClient) -> None:
        """Verify a missing project answers with the readable 404 page."""
        response = await fx_client.get(SETTINGS_URL.format(project_id=MISSING_PROJECT_ID))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(NOT_FOUND_HEADING in response.text)
        assert_expectations()


@pytest.mark.anyio
class TestSaveSettings:
    """Tests for save_settings()."""

    async def test_round_trips_every_field(
        self,
        fx_client: httpx.AsyncClient,
        fx_app: FastAPI,
        fx_create_project: ProjectFactory,
    ) -> None:
        """Verify every submitted field is trimmed, stored and shown again, then the saved banner appears."""
        project_id = await fx_create_project(title=FIRST_TITLE)
        padded = {name: f' {value} ' for name, value in SAMPLE_DETAILS.items() if name != ORTHOGRAPHY_FIELD}

        response = await fx_client.post(SETTINGS_URL.format(project_id=project_id), data={**SAMPLE_DETAILS, **padded})

        expect(response.status_code == HTTPStatus.SEE_OTHER)
        expect(response.headers[LOCATION_HEADER] == SETTINGS_URL.format(project_id=project_id) + SAVED_URL_SUFFIX)
        async with fx_app.state.session_factory() as session:
            project = await session.get(Project, project_id)
        assert project is not None
        for name, value in SAMPLE_DETAILS.items():
            expect(getattr(project, name) == value, f'{name} was not stored')
        page = await fx_client.get(response.headers[LOCATION_HEADER])
        expect(SAVED_BANNER in page.text)
        expect(f'value="{Orthography.PRE_REFORM.value}" selected' in page.text)
        for name in SAMPLE_DETAILS.keys() - {ORTHOGRAPHY_FIELD, NOTES_FIELD}:
            expect(f'id="{name}" name="{name}" type="text" value="{SAMPLE_DETAILS[name]}"' in page.text, name)
        expect(f'>{SAMPLE_DETAILS[NOTES_FIELD]}</textarea>' in page.text)
        assert_expectations()

    @pytest.mark.parametrize(
        'case',
        [
            InvalidField(TITLE_FIELD, BLANK_TEXT, REQUIRED_MESSAGE),
            InvalidField(
                AUTHORS_FIELD, 'a' * (TITLE_MAX_LENGTH + 1), TOO_LONG_MESSAGE.format(max_length=TITLE_MAX_LENGTH)
            ),
            InvalidField(
                YEAR_FIELD, '1' * (SHORT_TEXT_MAX_LENGTH + 1), TOO_LONG_MESSAGE.format(max_length=SHORT_TEXT_MAX_LENGTH)
            ),
            InvalidField(ORTHOGRAPHY_FIELD, 'old', CHOICE_MESSAGE),
        ],
        ids=lambda case: case.field,
    )
    async def test_invalid_field_keeps_submitted_values(
        self,
        fx_client: httpx.AsyncClient,
        fx_app: FastAPI,
        fx_create_project: ProjectFactory,
        case: InvalidField,
    ) -> None:
        """Verify an invalid field re-renders the form with its message, keeps other input and stores nothing."""
        project_id = await fx_create_project(title=FIRST_TITLE)

        response = await fx_client.post(
            SETTINGS_URL.format(project_id=project_id), data={**SAMPLE_DETAILS, case.field: case.value}
        )

        async with fx_app.state.session_factory() as session:
            project = await session.get(Project, project_id)
        assert project is not None
        expect(response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT)
        expect(f'<p class="field-error" id="{case.field}-error">{case.message}</p>' in response.text)
        expect(SAMPLE_DETAILS[NOTES_FIELD] in response.text)
        expect(SAMPLE_DETAILS['publisher'] in response.text)
        expect(project.title == FIRST_TITLE)
        expect(project.publisher == '')
        assert_expectations()

    async def test_unknown_project_shows_not_found_page(self, fx_client: httpx.AsyncClient) -> None:
        """Verify saving a missing project answers with the readable 404 page."""
        response = await fx_client.post(SETTINGS_URL.format(project_id=MISSING_PROJECT_ID), data=SAMPLE_DETAILS)
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(NOT_FOUND_HEADING in response.text)
        assert_expectations()


@pytest.mark.anyio
class TestDeleteProject:
    """Tests for delete_project()."""

    async def test_removes_row_pages_and_files(
        self,
        fx_client: httpx.AsyncClient,
        fx_app: FastAPI,
        fx_create_project: ProjectFactory,
    ) -> None:
        """Verify deletion removes the project, its pages and its directory, and leaves other projects alone."""
        project_id = await fx_create_project(title=FIRST_TITLE, page_count=2)
        kept_id = await fx_create_project(title=SECOND_TITLE, page_count=1)
        storage = fx_app.state.storage
        assert isinstance(storage, ProjectStorage)
        source_dir = storage.source_dir(project_id)
        source_dir.mkdir(parents=True)
        (source_dir / SOURCE_FILE_NAME).write_bytes(b'%PDF')

        response = await fx_client.post(DELETE_URL.format(project_id=project_id))

        async with fx_app.state.session_factory() as session:
            project_ids = (await session.scalars(select(Project.id))).all()
            page_owners = (await session.scalars(select(Page.project_id))).all()
        expect(response.status_code == HTTPStatus.SEE_OTHER)
        expect(response.headers[LOCATION_HEADER] == '/')
        expect(project_ids == [kept_id])
        expect(page_owners == [kept_id])
        expect(not storage.project_dir(project_id).exists())
        assert_expectations()

    async def test_unknown_project_shows_not_found_page(self, fx_client: httpx.AsyncClient) -> None:
        """Verify deleting a missing project answers with the readable 404 page."""
        response = await fx_client.post(DELETE_URL.format(project_id=MISSING_PROJECT_ID))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(NOT_FOUND_HEADING in response.text)
        assert_expectations()


@pytest.mark.anyio
class TestShowStage:
    """Tests for show_stage()."""

    @pytest.mark.parametrize('stage', [stage for stage in Stage if stage != Stage.IMPORT])
    async def test_placeholder_marks_its_tab_active(
        self,
        fx_client: httpx.AsyncClient,
        fx_create_project: ProjectFactory,
        stage: Stage,
    ) -> None:
        """Verify every stage without an implementation renders the tabs with its own tab active."""
        project_id = await fx_create_project(title=FIRST_TITLE)
        response = await fx_client.get(STAGE_URL.format(project_id=project_id, stage=stage))
        active_tab = (
            f'href="{STAGE_URL.format(project_id=project_id, stage=stage)}"\n       class="active" aria-current="page"'
        )
        expect(response.status_code == HTTPStatus.OK)
        expect(PLACEHOLDER_NOTE in response.text)
        expect(active_tab in response.text)
        expect(response.text.count('aria-current="page"') == 1)
        assert_expectations()

    @pytest.mark.parametrize(
        ('project_exists', 'stage_value'),
        # The Import stage is left out: in the assembled app the pages router serves it before this one
        [(True, 'binding'), (False, Stage.CLEANUP.value)],
        ids=['unknown-stage', 'unknown-project'],
    )
    async def test_unknown_target_shows_not_found_page(
        self,
        fx_client: httpx.AsyncClient,
        fx_create_project: ProjectFactory,
        *,
        project_exists: bool,
        stage_value: str,
    ) -> None:
        """Verify an unknown stage or a missing project answers with the readable 404 page."""
        project_id = await fx_create_project(title=FIRST_TITLE) if project_exists else MISSING_PROJECT_ID
        response = await fx_client.get(STAGE_URL.format(project_id=project_id, stage=stage_value))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(NOT_FOUND_HEADING in response.text)
        assert_expectations()
