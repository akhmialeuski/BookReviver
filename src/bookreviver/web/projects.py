"""Project management: list, create, describe, rename and delete books, and the stage tabs."""

from http import HTTPStatus
from typing import TYPE_CHECKING, Annotated, Any

from attrs import field, frozen
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, StringConstraints, TypeAdapter, ValidationError
from sqlalchemy import delete, func, select

from bookreviver.models import SHORT_TEXT_MAX_LENGTH, TITLE_MAX_LENGTH, Orthography, Page, Project
from bookreviver.stages import Stage
from bookreviver.web.dependencies import SessionDep, StorageDep
from bookreviver.web.templating import templates

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic_core import ErrorDetails
    from sqlalchemy.ext.asyncio import AsyncSession

# Validated field types; surrounding whitespace is stripped before the length is checked
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TITLE_MAX_LENGTH)]
LongText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=TITLE_MAX_LENGTH)]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=SHORT_TEXT_MAX_LENGTH)]
FreeText = Annotated[str, StringConstraints(strip_whitespace=True)]

LIST_TEMPLATE: str = 'projects/list.html'
SETTINGS_TEMPLATE: str = 'projects/settings.html'
STAGE_TEMPLATE: str = 'projects/stage.html'
NOT_FOUND_TEMPLATE: str = 'projects/not_found.html'
ORTHOGRAPHY_LABELS: dict[Orthography, str] = {
    Orthography.UNKNOWN: 'Unknown',
    Orthography.PRE_REFORM: 'Pre-reform',
    Orthography.MODERN: 'Modern',
}
REQUIRED_MESSAGE: str = 'This field is required.'
TOO_LONG_MESSAGE: str = 'Use at most {max_length} characters.'
CHOICE_MESSAGE: str = 'Choose one of the listed values.'
STAGE_URL: str = '/projects/{project_id}/stages/{stage}'
SETTINGS_URL: str = '/projects/{project_id}/settings'
SAVED_URL_SUFFIX: str = '?saved=1'
# Template context keys expected by _project_tabs.html
PROJECT_KEY: str = 'project'
ACTIVE_STAGE_KEY: str = 'active_stage'

TITLE_ADAPTER: TypeAdapter[str] = TypeAdapter(Title)

router = APIRouter()


class BookDetailsForm(BaseModel):
    """The book details form exactly as submitted.

    Every field accepts any text, so FastAPI never rejects the request with its own JSON error:
    the handler validates the values through :class:`BookDetails` and re-renders the form on failure.
    """

    title: str = ''
    authors: str = ''
    publisher: str = ''
    publication_place: str = ''
    publication_year: str = ''
    edition: str = ''
    series: str = ''
    volume: str = ''
    language: str = ''
    orthography: str = Orthography.UNKNOWN.value
    notes: str = ''


class BookDetails(BaseModel):
    """Validated bibliographic description, with the length limits of the database columns."""

    title: Title
    authors: LongText
    publisher: LongText
    publication_place: ShortText
    publication_year: ShortText
    edition: ShortText
    series: LongText
    volume: ShortText
    language: ShortText
    orthography: Orthography
    notes: FreeText


@frozen(kw_only=True)
class DetailsFormState:
    """What the book details form shows: stored or submitted values, their errors and the saved banner."""

    values: Mapping[str, Any]
    errors: Mapping[str, str] = field(factory=dict)
    saved: bool = False


def field_messages(error: ValidationError) -> dict[str, str]:
    """Turn a validation error into one readable message per form field."""
    messages: dict[str, str] = {}
    for detail in error.errors():
        name, *_ = detail['loc']
        messages.setdefault(str(name), error_message(detail))
    return messages


def error_message(detail: ErrorDetails) -> str:
    """Return the message shown under a field for one Pydantic error."""
    match detail['type']:
        case 'string_too_short' | 'missing':
            return REQUIRED_MESSAGE
        case 'string_too_long':
            return TOO_LONG_MESSAGE.format(max_length=detail.get('ctx', {}).get('max_length'))
        case 'enum':
            return CHOICE_MESSAGE
        case _:
            return detail['msg']


def _not_found(request: Request) -> HTMLResponse:
    """Render the readable 404 page shared by every project route."""
    return templates.TemplateResponse(request, NOT_FOUND_TEMPLATE, status_code=HTTPStatus.NOT_FOUND)


async def _page_count(session: AsyncSession, project_id: int) -> int:
    """Count the pages of one project without loading them."""
    count = await session.scalar(select(func.count(Page.id)).where(Page.project_id == project_id))
    return count or 0


async def _render_list(
    *,
    request: Request,
    session: AsyncSession,
    title: str = '',
    error: str = '',
    status_code: int = HTTPStatus.OK,
) -> HTMLResponse:
    """Render the project list, optionally with a rejected create-project submission."""
    rows = await session.execute(
        select(Project, func.count(Page.id))
        .outerjoin(Page, Page.project_id == Project.id)
        .group_by(Project.id)
        .order_by(Project.updated_at.desc(), Project.id.desc()),
    )
    context = {'projects': rows.all(), 'title': title, 'error': error}
    return templates.TemplateResponse(request, LIST_TEMPLATE, context, status_code=status_code)


async def _render_settings(
    *,
    request: Request,
    session: AsyncSession,
    project: Project,
    state: DetailsFormState,
) -> HTMLResponse:
    """Render the book details page with the source summary and the danger zone."""
    context = {
        PROJECT_KEY: project,
        ACTIVE_STAGE_KEY: None,
        'form': state,
        'orthographies': ORTHOGRAPHY_LABELS,
        'page_count': await _page_count(session, project.id),
    }
    status_code = HTTPStatus.UNPROCESSABLE_CONTENT if state.errors else HTTPStatus.OK
    return templates.TemplateResponse(request, SETTINGS_TEMPLATE, context, status_code=status_code)


@router.get('/', response_class=HTMLResponse)
async def list_projects(*, request: Request, session: SessionDep) -> HTMLResponse:
    """Show every project, most recently updated first, with the create-project form."""
    return await _render_list(request=request, session=session)


@router.post('/projects', response_model=None)
async def create_project(
    *,
    request: Request,
    session: SessionDep,
    title: Annotated[str, Form()] = '',
) -> HTMLResponse | RedirectResponse:
    """Create a project from its title and open its first stage."""
    try:
        valid_title = TITLE_ADAPTER.validate_python(title)
    except ValidationError as exc:
        return await _render_list(
            request=request,
            session=session,
            title=title,
            error=error_message(exc.errors()[0]),
            status_code=HTTPStatus.UNPROCESSABLE_CONTENT,
        )
    project = Project(title=valid_title)
    session.add(project)
    await session.commit()
    url = STAGE_URL.format(project_id=project.id, stage=Stage.first())
    return RedirectResponse(url, status_code=HTTPStatus.SEE_OTHER)


@router.get('/projects/{project_id}', response_model=None)
async def open_project(*, request: Request, session: SessionDep, project_id: int) -> HTMLResponse | RedirectResponse:
    """Redirect to the stage a project opens on."""
    if await session.get(Project, project_id) is None:
        return _not_found(request)
    url = STAGE_URL.format(project_id=project_id, stage=Stage.first())
    return RedirectResponse(url, status_code=HTTPStatus.SEE_OTHER)


@router.get(SETTINGS_URL, response_class=HTMLResponse)
async def show_settings(
    *,
    request: Request,
    session: SessionDep,
    project_id: int,
    saved: bool = False,
) -> HTMLResponse:
    """Show the book details form, the imported source summary and the delete button."""
    if (project := await session.get(Project, project_id)) is None:
        return _not_found(request)
    values = {name: getattr(project, name) for name in BookDetailsForm.model_fields}
    state = DetailsFormState(values=values, saved=saved)
    return await _render_settings(request=request, session=session, project=project, state=state)


@router.post(SETTINGS_URL, response_model=None)
async def save_settings(
    *,
    request: Request,
    session: SessionDep,
    project_id: int,
    form: Annotated[BookDetailsForm, Form()],
) -> HTMLResponse | RedirectResponse:
    """Validate and store the book details, or show the form again with a message per invalid field."""
    if (project := await session.get(Project, project_id)) is None:
        return _not_found(request)
    submitted = form.model_dump()
    try:
        details = BookDetails.model_validate(submitted)
    except ValidationError as exc:
        return await _render_settings(
            request=request,
            session=session,
            project=project,
            state=DetailsFormState(values=submitted, errors=field_messages(exc)),
        )
    for name, value in details.model_dump().items():
        setattr(project, name, value)
    await session.commit()
    url = SETTINGS_URL.format(project_id=project_id) + SAVED_URL_SUFFIX
    return RedirectResponse(url, status_code=HTTPStatus.SEE_OTHER)


@router.post('/projects/{project_id}/delete', response_model=None)
async def delete_project(
    *,
    request: Request,
    session: SessionDep,
    storage: StorageDep,
    project_id: int,
) -> HTMLResponse | RedirectResponse:
    """Delete the project row, its pages through the foreign key cascade, then its files."""
    if await session.get(Project, project_id) is None:
        return _not_found(request)
    # A bulk delete lets the database cascade remove the pages instead of loading every row first
    await session.execute(delete(Project).where(Project.id == project_id))
    await session.commit()
    await storage.delete_project(project_id)
    return RedirectResponse('/', status_code=HTTPStatus.SEE_OTHER)


@router.get(STAGE_URL, response_class=HTMLResponse)
async def show_stage(*, request: Request, session: SessionDep, project_id: int, stage: str) -> HTMLResponse:
    """Show the placeholder of a stage that has no implementation yet."""
    # The Import stage belongs to the pages router, which is matched first
    if stage not in Stage or stage == Stage.IMPORT:
        return _not_found(request)
    if (project := await session.get(Project, project_id)) is None:
        return _not_found(request)
    context = {PROJECT_KEY: project, ACTIVE_STAGE_KEY: Stage(stage)}
    return templates.TemplateResponse(request, STAGE_TEMPLATE, context)
