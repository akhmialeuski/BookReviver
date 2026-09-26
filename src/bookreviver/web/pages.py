"""Book import and page viewing: the Import stage, source upload, page viewer and page images."""

from collections.abc import Mapping
from datetime import UTC, datetime
from http import HTTPStatus
from pathlib import PureWindowsPath
from typing import TYPE_CHECKING, Annotated, Any

import anyio
import anyio.to_thread
from attrs import frozen
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, func, select
from sqlalchemy.orm import selectinload

from bookreviver.analysis import (
    IMAGE_SUFFIXES,
    PDF_SUFFIXES,
    UnsupportedSourceError,
    analyze_images,
    analyze_pdf,
)
from bookreviver.models import ColorMode, Orthography, Page, Project, SourceKind
from bookreviver.rendering import RenderRequest, RenderVariant, render_page
from bookreviver.stages import Stage
from bookreviver.web.dependencies import SessionDep, SettingsDep, StorageDep
from bookreviver.web.templating import templates

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sqlalchemy.ext.asyncio import AsyncSession

    from bookreviver.analysis import SourceAnalysis
    from bookreviver.storage import ProjectStorage

UPLOAD_CHUNK_BYTES: int = 1024**2
# Image URLs carry the import time, so a replaced source never hits a stale browser cache
IMAGE_CACHE_CONTROL: str = 'private, max-age=604800'
WEBP_MEDIA_TYPE: str = 'image/webp'
# Neighbouring pages shown on each side of the current one in the viewer strip
VIEWER_STRIP_RADIUS: int = 4
METADATA_KEY_SEPARATOR: str = '.'

IMPORT_TEMPLATE: str = 'pages/import.html'
VIEWER_TEMPLATE: str = 'pages/viewer.html'
NOT_FOUND_TEMPLATE: str = 'pages/not_found.html'

COLOR_MODE_LABELS: dict[ColorMode, str] = {
    ColorMode.BILEVEL: 'Black and white',
    ColorMode.GRAY: 'Grayscale',
    ColorMode.COLOR: 'Colour',
    ColorMode.UNKNOWN: 'Unknown',
}
SOURCE_KIND_LABELS: dict[SourceKind, str] = {
    SourceKind.PDF: 'PDF document',
    SourceKind.IMAGES: 'Page images',
}
ORTHOGRAPHY_LABELS: dict[Orthography, str] = {
    Orthography.UNKNOWN: 'Unknown',
    Orthography.PRE_REFORM: 'Pre-reform',
    Orthography.MODERN: 'Modern',
}
# Project attributes shown as the read-only book details, with their labels
DESCRIPTION_FIELDS: dict[str, str] = {
    'title': 'Title',
    'authors': 'Authors',
    'publisher': 'Publisher',
    'publication_place': 'Place of publication',
    'publication_year': 'Year',
    'edition': 'Edition',
    'series': 'Series',
    'volume': 'Volume',
    'language': 'Language',
    'orthography': 'Orthography',
    'notes': 'Notes',
}
# Description fields a source may fill; the title is always the user's own
SUGGESTED_FIELDS: tuple[str, ...] = ('authors', 'publisher', 'publication_year', 'language')
ACCEPTED_SUFFIXES: str = ','.join(sorted(PDF_SUFFIXES | IMAGE_SUFFIXES))

# Source name of an image set, by whether it holds one image or several
IMAGE_SET_NAMES: tuple[str, str] = ('{count} image', '{count} images')
ERROR_NO_FILES: str = 'Choose a PDF file or page images to upload.'
ERROR_EMPTY_NAME: str = 'Every uploaded file needs a name.'
ERROR_DUPLICATE_NAME: str = 'Two uploaded files are both named "{name}"; every page image needs its own name.'
ERROR_WRONG_TYPES: str = 'Upload exactly one PDF, or one or more page images ({suffixes}); do not mix them.'
ERROR_TOO_LARGE: str = 'The upload is larger than the limit of {limit:,} bytes.'
ERROR_PROJECT_NOT_FOUND: str = 'There is no book with number {project_id}.'
ERROR_PAGE_NOT_FOUND: str = 'This book has no page {number}.'

router = APIRouter()


class _UploadRejectedError(Exception):
    """An upload that cannot become a source; the message is shown to the user."""

    def __init__(self, message: str, *, status_code: HTTPStatus = HTTPStatus.BAD_REQUEST) -> None:
        super().__init__(message)
        self.status_code = status_code


@frozen(kw_only=True)
class ProjectScope:
    """The project a request addresses, with the session and storage its handler needs."""

    request: Request
    project_id: int
    session: AsyncSession
    storage: ProjectStorage

    async def get_project(self) -> Project | None:
        """Load the project without its pages."""
        return await self.session.get(Project, self.project_id)

    def not_found(self, message: str | None = None) -> Response:
        """Render a readable 404 page, by default saying the project does not exist."""
        return templates.TemplateResponse(
            self.request,
            NOT_FOUND_TEMPLATE,
            {'message': message or ERROR_PROJECT_NOT_FOUND.format(project_id=self.project_id)},
            status_code=HTTPStatus.NOT_FOUND,
        )

    def render(self, *, template: str, project: Project, context: Mapping[str, Any]) -> Response:
        """Render a page under the Import stage tab, with the stage tabs and the colour mode labels."""
        return templates.TemplateResponse(
            self.request,
            template,
            {'project': project, 'active_stage': Stage.IMPORT, 'color_labels': COLOR_MODE_LABELS, **context},
        )

    async def render_import_stage(self, *, error: str = '', status_code: HTTPStatus = HTTPStatus.OK) -> Response:
        """Render the Import stage: the upload form, or the imported source with its pages."""
        project = await self.session.get(
            Project,
            self.project_id,
            options=[selectinload(Project.pages)],
            populate_existing=True,
        )
        if project is None:
            return self.not_found()
        description = [
            (label, ORTHOGRAPHY_LABELS[value] if isinstance(value, Orthography) else value)
            for field_name, label in DESCRIPTION_FIELDS.items()
            if (value := getattr(project, field_name)) is not None
        ]
        context = {
            'error': error,
            'accept': ACCEPTED_SUFFIXES,
            'description': description,
            'metadata': _flatten_metadata(project.source_metadata),
            'kind_labels': SOURCE_KIND_LABELS,
        }
        response = self.render(template=IMPORT_TEMPLATE, project=project, context=context)
        response.status_code = status_code
        return response


def get_project_scope(*, request: Request, project_id: int, session: SessionDep, storage: StorageDep) -> ProjectScope:
    """Gather what a project handler needs, so handlers take one parameter instead of four."""
    return ProjectScope(request=request, project_id=project_id, session=session, storage=storage)


def _flatten_metadata(metadata: Mapping[str, Any], *, prefix: str = '') -> dict[str, Any]:
    """Flatten nested mappings into one level, joining the keys with dots."""
    flat: dict[str, Any] = {}
    for key, value in metadata.items():
        dotted = f'{prefix}{METADATA_KEY_SEPARATOR}{key}' if prefix else str(key)
        if isinstance(value, Mapping):
            flat |= _flatten_metadata(value, prefix=dotted)
        else:
            flat[dotted] = value
    return flat


def _check_upload(*, uploads: Sequence[UploadFile], names: Sequence[str], max_bytes: int) -> SourceKind:
    """Decide what the uploaded files are, rejecting anything but one PDF or a set of images within the limit.

    :param uploads:     The uploaded files, in upload order.
    :param names:       Their base names, in the same order.
    :param max_bytes:   The size limit, checked here against the sizes the client declared.
    :raises _UploadRejectedError: If the names are empty, duplicated, of the wrong kinds, or too large together.
    """
    if not names:
        raise _UploadRejectedError(ERROR_NO_FILES)
    seen: set[str] = set()
    for name in names:
        if not name:
            raise _UploadRejectedError(ERROR_EMPTY_NAME)
        # The data directory may live on a case-insensitive Windows drive
        if name.casefold() in seen:
            raise _UploadRejectedError(ERROR_DUPLICATE_NAME.format(name=name))
        seen.add(name.casefold())
    # Checking the declared sizes before anything is written refuses an oversized upload without writing it
    if sum(upload.size or 0 for upload in uploads) > max_bytes:
        raise _UploadRejectedError(ERROR_TOO_LARGE.format(limit=max_bytes), status_code=HTTPStatus.CONTENT_TOO_LARGE)
    suffixes = [PureWindowsPath(name).suffix.lower() for name in names]
    if len(suffixes) == 1 and suffixes[0] in PDF_SUFFIXES:
        return SourceKind.PDF
    if all(suffix in IMAGE_SUFFIXES for suffix in suffixes):
        return SourceKind.IMAGES
    raise _UploadRejectedError(ERROR_WRONG_TYPES.format(suffixes=', '.join(sorted(IMAGE_SUFFIXES))))


async def _save_uploads(*, uploads: Sequence[UploadFile], paths: Sequence[Path], max_bytes: int) -> int:
    """Stream every upload to its path in chunks and return the total size written.

    :raises _UploadRejectedError: If the total exceeds ``max_bytes``; files written so far stay on disk.
    """
    total = 0
    for upload, path in zip(uploads, paths, strict=True):
        async with await anyio.open_file(path, 'wb') as target:
            while chunk := await upload.read(UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise _UploadRejectedError(
                        ERROR_TOO_LARGE.format(limit=max_bytes), status_code=HTTPStatus.CONTENT_TOO_LARGE
                    )
                await target.write(chunk)
    return total


def _apply_analysis(*, project: Project, analysis: SourceAnalysis) -> list[Page]:
    """Copy the analysed facts onto the project and return its new page rows.

    Suggested description fields fill only what the user left empty.
    """
    project.source_metadata = dict(analysis.file_metadata)
    for field_name in SUGGESTED_FIELDS:
        suggested = getattr(analysis.suggestion, field_name)
        if suggested and not getattr(project, field_name):
            setattr(project, field_name, suggested)
    return [
        Page(
            project_id=project.id,
            index=index,
            source_file=facts.source_file,
            width_px=facts.width_px,
            height_px=facts.height_px,
            dpi_x=facts.dpi_x,
            dpi_y=facts.dpi_y,
            color_mode=facts.color_mode,
            bits_per_component=facts.bits_per_component,
            image_format=facts.image_format,
            width_mm=facts.width_mm,
            height_mm=facts.height_mm,
            has_text_layer=facts.has_text_layer,
            extra=dict(facts.extra),
        )
        for index, facts in enumerate(analysis.pages)
    ]


@router.get('/projects/{project_id}/stages/import', response_class=HTMLResponse)
async def import_stage(scope: Annotated[ProjectScope, Depends(get_project_scope)]) -> Response:
    """Show the upload form, or the summary and pages of the imported source."""
    return await scope.render_import_stage()


@router.post('/projects/{project_id}/source', response_class=HTMLResponse)
async def upload_source(
    files: Annotated[list[UploadFile], File()],
    scope: Annotated[ProjectScope, Depends(get_project_scope)],
    settings: SettingsDep,
) -> Response:
    """Replace the project's source with the uploaded PDF or page images, then analyse it."""
    project = await scope.get_project()
    if project is None:
        return scope.not_found()
    names = [PureWindowsPath(upload.filename or '').name for upload in files]
    try:
        kind = _check_upload(uploads=files, names=names, max_bytes=settings.max_upload_bytes)
    except _UploadRejectedError as error:
        return await scope.render_import_stage(error=str(error), status_code=error.status_code)

    # The upload lands in the incoming directory, so a rejected one leaves the current source as it was
    incoming_dir = await scope.storage.start_incoming(scope.project_id)
    paths = [incoming_dir / name for name in names]
    try:
        size_bytes = await _save_uploads(uploads=files, paths=paths, max_bytes=settings.max_upload_bytes)
        if kind is SourceKind.PDF:
            analysis = await anyio.to_thread.run_sync(analyze_pdf, paths[0])
        else:
            analysis = await anyio.to_thread.run_sync(analyze_images, paths)
    except (_UploadRejectedError, UnsupportedSourceError) as error:
        await scope.storage.discard_incoming(scope.project_id)
        status_code = error.status_code if isinstance(error, _UploadRejectedError) else HTTPStatus.BAD_REQUEST
        return await scope.render_import_stage(error=str(error), status_code=status_code)

    await scope.storage.promote_incoming(scope.project_id)
    await scope.session.execute(delete(Page).where(Page.project_id == scope.project_id))
    scope.session.add_all(_apply_analysis(project=project, analysis=analysis))
    project.source_kind = kind
    project.source_name = (
        names[0] if kind is SourceKind.PDF else IMAGE_SET_NAMES[len(names) > 1].format(count=len(names))
    )
    project.source_size_bytes = size_bytes
    project.imported_at = datetime.now(UTC)
    await scope.session.commit()
    return RedirectResponse(f'/projects/{scope.project_id}/stages/{Stage.IMPORT}', status_code=HTTPStatus.SEE_OTHER)


@router.get('/projects/{project_id}/pages/{number}', response_class=HTMLResponse)
async def view_page(number: int, scope: Annotated[ProjectScope, Depends(get_project_scope)]) -> Response:
    """Show one page with its technical facts and links to its neighbours."""
    project = await scope.get_project()
    if project is None:
        return scope.not_found()
    page_count = await scope.session.scalar(
        select(func.count()).select_from(Page).where(Page.project_id == scope.project_id)
    )
    if not page_count or not 1 <= number <= page_count:
        return scope.not_found(ERROR_PAGE_NOT_FOUND.format(number=number))
    strip = (
        await scope.session.scalars(
            select(Page)
            .where(
                Page.project_id == scope.project_id,
                Page.index.between(number - 1 - VIEWER_STRIP_RADIUS, number - 1 + VIEWER_STRIP_RADIUS),
            )
            .order_by(Page.index)
        )
    ).all()
    context = {
        'page': next(page for page in strip if page.index == number - 1),
        'number': number,
        'page_count': page_count,
        'strip': strip,
    }
    return scope.render(template=VIEWER_TEMPLATE, project=project, context=context)


@router.get('/projects/{project_id}/pages/{number}/image')
async def page_image(
    number: int,
    scope: Annotated[ProjectScope, Depends(get_project_scope)],
    variant: RenderVariant = RenderVariant.PREVIEW,
) -> Response:
    """Return the page rendered as WebP, from the cache when it was rendered before."""
    project = await scope.get_project()
    if project is None:
        return scope.not_found()
    page = await scope.session.scalar(select(Page).where(Page.project_id == scope.project_id, Page.index == number - 1))
    if page is None or project.source_kind is None:
        return scope.not_found(ERROR_PAGE_NOT_FOUND.format(number=number))
    source_file = project.source_name if project.source_kind is SourceKind.PDF else page.source_file
    render_request = RenderRequest(
        kind=project.source_kind,
        source_path=scope.storage.source_dir(scope.project_id) / source_file,
        page_index=page.index,
        variant=variant,
        cache_dir=scope.storage.cache_dir(scope.project_id),
    )
    image_path = await anyio.to_thread.run_sync(render_page, render_request)
    return FileResponse(image_path, media_type=WEBP_MEDIA_TYPE, headers={'Cache-Control': IMAGE_CACHE_CONTROL})
