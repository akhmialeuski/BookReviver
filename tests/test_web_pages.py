"""Tests for the Import stage, source upload, page viewer and page image endpoints."""

from http import HTTPStatus
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import MagicMock, call, patch

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from PIL import Image
from sqlalchemy.orm import selectinload

from bookreviver.analysis import MetadataSuggestion, PageFacts, SourceAnalysis, UnsupportedSourceError
from bookreviver.models import ColorMode, Project, SourceKind
from bookreviver.rendering import RenderRequest, RenderVariant

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    import httpx
    from fastapi import FastAPI

# Patch targets: the names as web/pages.py looks them up
ANALYZE_PDF_PATCH: str = 'bookreviver.web.pages.analyze_pdf'
ANALYZE_IMAGES_PATCH: str = 'bookreviver.web.pages.analyze_images'
RENDER_PAGE_PATCH: str = 'bookreviver.web.pages.render_page'

IMPORT_URL: str = '/projects/{project_id}/stages/import'
SOURCE_URL: str = '/projects/{project_id}/source'
SETTINGS_URL: str = '/projects/{project_id}/settings'
PAGE_URL: str = '/projects/{project_id}/pages/{number}'
IMAGE_URL: str = '/projects/{project_id}/pages/{number}/image'
CASE_PARAM: str = 'case'
VARIANT_PARAM: str = 'variant'
DPI_300_TEXT: str = '300 DPI'
WRONG_TYPES_TEXT: str = 'do not mix them'
FIRST_IMAGE_NAME: str = 'p1.png'
PROJECT_TITLE: str = 'Typed title'
TYPED_AUTHORS: str = 'Typed author'
PDF_NAME: str = 'book.pdf'
PDF_BYTES: bytes = b'%PDF-1.7 test body'
IMAGE_BYTES: bytes = b'image body'
UNKNOWN_PROJECT_ID: int = 999
SMALL_UPLOAD_LIMIT: int = 8
UNSUPPORTED_MESSAGE: str = 'The file is not a readable PDF.'
WEBP_MEDIA_TYPE: str = 'image/webp'
NOT_FOUND_TEXT: str = 'Not found'
TIMES: str = '\N{MULTIPLICATION SIGN}'

SAMPLE_PAGE: PageFacts = PageFacts(
    width_px=2480,
    height_px=3508,
    dpi_x=300.0,
    dpi_y=300.0,
    color_mode=ColorMode.GRAY,
    bits_per_component=8,
    image_format='JPEG',
    width_mm=210.0,
    height_mm=297.0,
    has_text_layer=True,
    extra={'rotation': 90},
)
SAMPLE_SUGGESTION: MetadataSuggestion = MetadataSuggestion(
    title='Suggested title',
    authors='Suggested author',
    publisher='Suggested publisher',
    language='ru',
)
SAMPLE_PDF_ANALYSIS: SourceAnalysis = SourceAnalysis(
    kind=SourceKind.PDF,
    pages=[SAMPLE_PAGE, evolve(SAMPLE_PAGE, color_mode=ColorMode.BILEVEL), evolve(SAMPLE_PAGE, dpi_x=None)],
    file_metadata={'format': 'PDF 1.7', 'info': {'producer': 'Scanner', 'xmp': {'lang': 'ru'}}},
    suggestion=SAMPLE_SUGGESTION,
)
# Upload order differs from the natural order the analysis reports
IMAGE_UPLOAD_NAMES: tuple[str, ...] = ('p10.png', 'p2.png', FIRST_IMAGE_NAME)
IMAGE_ANALYSED_NAMES: tuple[str, ...] = (FIRST_IMAGE_NAME, 'p2.png', 'p10.png')
SAMPLE_IMAGES_ANALYSIS: SourceAnalysis = SourceAnalysis(
    kind=SourceKind.IMAGES,
    pages=[evolve(SAMPLE_PAGE, source_file=name, has_text_layer=False) for name in IMAGE_ANALYSED_NAMES],
)

UploadFiles = list[tuple[str, tuple[str, bytes]]]


class RejectedUpload(NamedTuple):
    """A set of uploaded file names and the error it must produce."""

    file_names: tuple[str, ...]
    message: str


class NavigationCase(NamedTuple):
    """A page position and which neighbour links it must show."""

    number: int
    has_previous: bool
    has_next: bool


def _files(names: Sequence[str], *, body: bytes = IMAGE_BYTES) -> UploadFiles:
    """Build the multipart ``files`` field for uploaded file names."""
    return [('files', (name, body)) for name in names]


async def _load_project(app: FastAPI, project_id: int) -> Project:
    """Read the project and its pages in a fresh session."""
    async with app.state.session_factory() as session:
        project = await session.get(Project, project_id, options=[selectinload(Project.pages)])
    assert isinstance(project, Project)
    return project


@pytest.fixture
async def fx_project_id(fx_app: FastAPI) -> int:
    """Create a project whose user already typed the title and the authors."""
    async with fx_app.state.session_factory() as session:
        project = Project(title=PROJECT_TITLE, authors=TYPED_AUTHORS)
        session.add(project)
        await session.commit()
        return project.id


@pytest.fixture
async def fx_pdf_project_id(fx_client: httpx.AsyncClient, fx_project_id: int) -> int:
    """Create a project with a three-page PDF imported."""
    with patch(ANALYZE_PDF_PATCH, return_value=SAMPLE_PDF_ANALYSIS):
        response = await fx_client.post(
            SOURCE_URL.format(project_id=fx_project_id), files=_files([PDF_NAME], body=PDF_BYTES)
        )
    assert response.status_code == HTTPStatus.SEE_OTHER
    return fx_project_id


@pytest.fixture
def fx_analysis() -> Iterator[MagicMock]:
    """Patch both analysis functions with children of one mock, so a test can assert neither ran."""
    analysis = MagicMock()
    with patch(ANALYZE_PDF_PATCH, analysis.analyze_pdf), patch(ANALYZE_IMAGES_PATCH, analysis.analyze_images):
        yield analysis


@pytest.fixture
def fx_webp(tmp_path: Path) -> Path:
    """Write a small real WebP image, standing in for a rendered page."""
    path = tmp_path / 'page.webp'
    Image.new('L', (4, 6), color=128).save(path, format='WEBP')
    return path


@pytest.mark.anyio
class TestImportStage:
    """Tests for import_stage()."""

    async def test_without_source_shows_upload_form(self, fx_client: httpx.AsyncClient, fx_project_id: int) -> None:
        """Verify a project without a source offers a multipart upload accepting PDFs and images."""
        response = await fx_client.get(IMPORT_URL.format(project_id=fx_project_id))
        expect(response.status_code == HTTPStatus.OK)
        expect('enctype="multipart/form-data"' in response.text)
        expect('accept=".jpeg,.jpg,.pdf,.png,.tif,.tiff"' in response.text)
        expect('Replace source' not in response.text)
        assert_expectations()

    async def test_with_source_shows_summary_metadata_and_thumbnails(
        self, fx_client: httpx.AsyncClient, fx_pdf_project_id: int
    ) -> None:
        """Verify the imported source is summarised, its metadata flattened and every page linked."""
        response = await fx_client.get(IMPORT_URL.format(project_id=fx_pdf_project_id))
        text = response.text
        expect(response.status_code == HTTPStatus.OK)
        expect(PDF_NAME in text)
        expect('PDF document' in text)
        expect('info.xmp.lang' in text)
        expect('info.producer' in text)
        expect(f'href="{SETTINGS_URL.format(project_id=fx_pdf_project_id)}"' in text)
        expect(
            all(
                f'href="{PAGE_URL.format(project_id=fx_pdf_project_id, number=number)}"' in text for number in (1, 2, 3)
            )
        )
        expect(text.count('loading="lazy"') == len(SAMPLE_PDF_ANALYSIS.pages))
        expect('Black and white' in text)
        expect(DPI_300_TEXT in text)
        expect('DPI unknown' in text)
        expect('confirm(' in text)
        assert_expectations()

    async def test_unknown_project_returns_readable_404(self, fx_client: httpx.AsyncClient) -> None:
        """Verify an unknown project renders an HTML 404 page."""
        response = await fx_client.get(IMPORT_URL.format(project_id=UNKNOWN_PROJECT_ID))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(NOT_FOUND_TEXT in response.text)
        assert_expectations()


@pytest.mark.anyio
class TestUploadSource:
    """Tests for upload_source()."""

    @patch(ANALYZE_PDF_PATCH, return_value=SAMPLE_PDF_ANALYSIS)
    async def test_pdf_upload_persists_pages_and_metadata(
        self, mock_analyze_pdf: MagicMock, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_project_id: int
    ) -> None:
        """Verify a PDF is stored, analysed and persisted, then the browser is sent to the Import stage."""
        response = await fx_client.post(
            SOURCE_URL.format(project_id=fx_project_id), files=_files([PDF_NAME], body=PDF_BYTES)
        )
        storage = fx_app.state.storage
        project = await _load_project(fx_app, fx_project_id)
        expect(response.status_code == HTTPStatus.SEE_OTHER)
        expect(response.headers['location'] == IMPORT_URL.format(project_id=fx_project_id))
        expect((storage.source_dir(fx_project_id) / PDF_NAME).read_bytes() == PDF_BYTES)
        expect(not storage.incoming_dir(fx_project_id).exists())
        # The analysis reads the upload before it replaces the current source
        expect(mock_analyze_pdf.call_args_list == [call(storage.incoming_dir(fx_project_id) / PDF_NAME)])
        expect(project.source_kind is SourceKind.PDF)
        expect(project.source_name == PDF_NAME)
        expect(project.source_size_bytes == len(PDF_BYTES))
        expect(project.source_metadata == SAMPLE_PDF_ANALYSIS.file_metadata)
        expect(project.imported_at is not None)
        expect([page.index for page in project.pages] == [0, 1, 2])
        expect([page.color_mode for page in project.pages] == [page.color_mode for page in SAMPLE_PDF_ANALYSIS.pages])
        expect(project.pages[0].extra == SAMPLE_PAGE.extra)
        assert_expectations()

    @patch(ANALYZE_PDF_PATCH, return_value=SAMPLE_PDF_ANALYSIS)
    async def test_suggestion_fills_only_empty_description_fields(
        self, _mock_analyze_pdf: MagicMock, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_project_id: int
    ) -> None:
        """Verify suggestions never overwrite what the user typed, never touch the title, and skip empty ones."""
        await fx_client.post(SOURCE_URL.format(project_id=fx_project_id), files=_files([PDF_NAME], body=PDF_BYTES))
        project = await _load_project(fx_app, fx_project_id)
        expect(project.title == PROJECT_TITLE)
        expect(project.authors == TYPED_AUTHORS)
        expect(project.publisher == SAMPLE_SUGGESTION.publisher)
        expect(project.language == SAMPLE_SUGGESTION.language)
        expect(project.publication_year == '')
        assert_expectations()

    @patch(ANALYZE_IMAGES_PATCH, return_value=SAMPLE_IMAGES_ANALYSIS)
    async def test_image_upload_keeps_analysed_order(
        self, mock_analyze_images: MagicMock, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_project_id: int
    ) -> None:
        """Verify every image is stored and the pages follow the order the analysis returned."""
        response = await fx_client.post(SOURCE_URL.format(project_id=fx_project_id), files=_files(IMAGE_UPLOAD_NAMES))
        source_dir = fx_app.state.storage.source_dir(fx_project_id)
        incoming_dir = fx_app.state.storage.incoming_dir(fx_project_id)
        project = await _load_project(fx_app, fx_project_id)
        expect(response.status_code == HTTPStatus.SEE_OTHER)
        expect(mock_analyze_images.call_args_list == [call([incoming_dir / name for name in IMAGE_UPLOAD_NAMES])])
        expect(sorted(path.name for path in source_dir.iterdir()) == sorted(IMAGE_UPLOAD_NAMES))
        expect([page.source_file for page in project.pages] == list(IMAGE_ANALYSED_NAMES))
        expect(project.source_kind is SourceKind.IMAGES)
        expect(project.source_name == f'{len(IMAGE_UPLOAD_NAMES)} images')
        expect(project.source_size_bytes == len(IMAGE_BYTES) * len(IMAGE_UPLOAD_NAMES))
        assert_expectations()

    @pytest.mark.parametrize(
        CASE_PARAM,
        [
            RejectedUpload(file_names=('book.pdf', FIRST_IMAGE_NAME), message=WRONG_TYPES_TEXT),
            RejectedUpload(file_names=('a.pdf', 'b.pdf'), message=WRONG_TYPES_TEXT),
            RejectedUpload(file_names=('notes.txt',), message=WRONG_TYPES_TEXT),
            RejectedUpload(file_names=(FIRST_IMAGE_NAME, 'P1.PNG'), message='both named'),
        ],
        ids=['mixed-types', 'two-pdfs', 'wrong-suffix', 'duplicate-names'],
    )
    async def test_rejected_upload_rerenders_with_400(
        self,
        fx_analysis: MagicMock,
        fx_app: FastAPI,
        fx_client: httpx.AsyncClient,
        fx_project_id: int,
        case: RejectedUpload,
    ) -> None:
        """Verify a wrong set of files is refused before anything is written or analysed."""
        response = await fx_client.post(SOURCE_URL.format(project_id=fx_project_id), files=_files(case.file_names))
        project = await _load_project(fx_app, fx_project_id)
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(case.message in response.text)
        expect(not fx_app.state.storage.source_dir(fx_project_id).exists())
        expect(fx_analysis.mock_calls == [])
        expect(project.source_kind is None)
        assert_expectations()

    async def test_upload_over_limit_returns_413_and_keeps_previous_source(
        self, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_pdf_project_id: int
    ) -> None:
        """Verify an upload larger than the limit is refused without destroying the imported source."""
        fx_app.state.settings.max_upload_bytes = SMALL_UPLOAD_LIMIT
        response = await fx_client.post(
            SOURCE_URL.format(project_id=fx_pdf_project_id),
            files=_files([FIRST_IMAGE_NAME], body=b'x' * (SMALL_UPLOAD_LIMIT + 1)),
        )
        project = await _load_project(fx_app, fx_pdf_project_id)
        expect(response.status_code == HTTPStatus.CONTENT_TOO_LARGE)
        expect(f'limit of {SMALL_UPLOAD_LIMIT} bytes' in response.text)
        expect((fx_app.state.storage.source_dir(fx_pdf_project_id) / PDF_NAME).read_bytes() == PDF_BYTES)
        expect(len(project.pages) == len(SAMPLE_PDF_ANALYSIS.pages))
        assert_expectations()

    @patch(ANALYZE_PDF_PATCH, side_effect=UnsupportedSourceError(UNSUPPORTED_MESSAGE))
    async def test_unsupported_source_removes_files_and_returns_400(
        self, _mock_analyze_pdf: MagicMock, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_project_id: int
    ) -> None:
        """Verify an unreadable source is deleted, nothing is persisted and the reason is shown."""
        response = await fx_client.post(
            SOURCE_URL.format(project_id=fx_project_id), files=_files([PDF_NAME], body=PDF_BYTES)
        )
        project = await _load_project(fx_app, fx_project_id)
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(UNSUPPORTED_MESSAGE in response.text)
        expect(not fx_app.state.storage.source_dir(fx_project_id).exists())
        expect(not fx_app.state.storage.incoming_dir(fx_project_id).exists())
        expect(project.source_kind is None)
        expect(project.pages == [])
        assert_expectations()

    @patch(ANALYZE_IMAGES_PATCH, side_effect=UnsupportedSourceError(UNSUPPORTED_MESSAGE))
    async def test_unsupported_replacement_keeps_previous_source(
        self, _mock_analyze_images: MagicMock, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_pdf_project_id: int
    ) -> None:
        """Verify an unreadable replacement is refused while the imported book, its pages and files survive."""
        response = await fx_client.post(
            SOURCE_URL.format(project_id=fx_pdf_project_id), files=_files(IMAGE_UPLOAD_NAMES)
        )
        project = await _load_project(fx_app, fx_pdf_project_id)
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(UNSUPPORTED_MESSAGE in response.text)
        expect((fx_app.state.storage.source_dir(fx_pdf_project_id) / PDF_NAME).read_bytes() == PDF_BYTES)
        expect(not fx_app.state.storage.incoming_dir(fx_pdf_project_id).exists())
        expect(project.source_kind is SourceKind.PDF)
        expect(len(project.pages) == len(SAMPLE_PDF_ANALYSIS.pages))
        assert_expectations()

    @patch(ANALYZE_IMAGES_PATCH, return_value=SAMPLE_IMAGES_ANALYSIS)
    async def test_replacing_source_replaces_pages(
        self, _mock_analyze_images: MagicMock, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_pdf_project_id: int
    ) -> None:
        """Verify a new upload drops the previous files and pages instead of adding to them."""
        response = await fx_client.post(
            SOURCE_URL.format(project_id=fx_pdf_project_id), files=_files(IMAGE_UPLOAD_NAMES)
        )
        source_dir = fx_app.state.storage.source_dir(fx_pdf_project_id)
        project = await _load_project(fx_app, fx_pdf_project_id)
        expect(response.status_code == HTTPStatus.SEE_OTHER)
        expect(not (source_dir / PDF_NAME).exists())
        expect(project.source_kind is SourceKind.IMAGES)
        expect([page.source_file for page in project.pages] == list(IMAGE_ANALYSED_NAMES))
        assert_expectations()

    @patch(ANALYZE_IMAGES_PATCH, return_value=SAMPLE_IMAGES_ANALYSIS)
    async def test_single_image_source_name_is_singular(
        self, _mock_analyze_images: MagicMock, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_project_id: int
    ) -> None:
        """Verify one uploaded image is named "1 image", not "1 images"."""
        await fx_client.post(SOURCE_URL.format(project_id=fx_project_id), files=_files([FIRST_IMAGE_NAME]))
        project = await _load_project(fx_app, fx_project_id)
        assert project.source_name == '1 image'

    async def test_unknown_project_returns_404(self, fx_client: httpx.AsyncClient) -> None:
        """Verify uploading to an unknown project is refused with a 404 page."""
        response = await fx_client.post(SOURCE_URL.format(project_id=UNKNOWN_PROJECT_ID), files=_files([PDF_NAME]))
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.anyio
class TestViewPage:
    """Tests for view_page()."""

    @pytest.mark.parametrize(
        CASE_PARAM,
        [
            NavigationCase(number=1, has_previous=False, has_next=True),
            NavigationCase(number=2, has_previous=True, has_next=True),
            NavigationCase(number=3, has_previous=True, has_next=False),
        ],
        ids=['first', 'middle', 'last'],
    )
    async def test_navigation_links_depend_on_position(
        self, fx_client: httpx.AsyncClient, fx_pdf_project_id: int, case: NavigationCase
    ) -> None:
        """Verify previous and next links exist only where there is a page to go to."""
        response = await fx_client.get(PAGE_URL.format(project_id=fx_pdf_project_id, number=case.number))
        text = response.text
        expect(response.status_code == HTTPStatus.OK)
        expect(f'Page {case.number} of {len(SAMPLE_PDF_ANALYSIS.pages)}' in text)
        expect(('rel="prev"' in text) is case.has_previous)
        expect(('rel="next"' in text) is case.has_next)
        expect(
            (f'{PAGE_URL.format(project_id=fx_pdf_project_id, number=case.number - 1)}" rel="prev"' in text)
            is case.has_previous
        )
        expect(
            (f'{PAGE_URL.format(project_id=fx_pdf_project_id, number=case.number + 1)}" rel="next"' in text)
            is case.has_next
        )
        expect(f'{IMAGE_URL.format(project_id=fx_pdf_project_id, number=case.number)}?variant=preview' in text)
        assert_expectations()

    async def test_shows_page_facts(self, fx_client: httpx.AsyncClient, fx_pdf_project_id: int) -> None:
        """Verify the facts table renders every technical fact with human labels."""
        response = await fx_client.get(PAGE_URL.format(project_id=fx_pdf_project_id, number=1))
        text = response.text
        expect(f'2480 {TIMES} 3508 px' in text)
        expect(DPI_300_TEXT in text)
        expect(f'210.0 {TIMES} 297.0 mm' in text)
        expect('Grayscale' in text)
        expect('JPEG' in text)
        expect('rotation' in text)
        expect(PDF_NAME in text)
        expect('viewer.js' in text)
        expect(text.count('/image?variant=thumb') == len(SAMPLE_PDF_ANALYSIS.pages))
        assert_expectations()

    @pytest.mark.parametrize('number', [0, 4], ids=['before-first', 'after-last'])
    async def test_out_of_range_page_returns_404(
        self, fx_client: httpx.AsyncClient, fx_pdf_project_id: int, number: int
    ) -> None:
        """Verify a page number outside the book renders a readable 404 page."""
        response = await fx_client.get(PAGE_URL.format(project_id=fx_pdf_project_id, number=number))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(f'no page {number}' in response.text)
        assert_expectations()

    async def test_unknown_project_returns_404(self, fx_client: httpx.AsyncClient) -> None:
        """Verify viewing a page of an unknown project is a 404."""
        response = await fx_client.get(PAGE_URL.format(project_id=UNKNOWN_PROJECT_ID, number=1))
        assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.anyio
class TestPageImage:
    """Tests for page_image()."""

    @patch(RENDER_PAGE_PATCH)
    async def test_pdf_page_returns_webp_rendered_from_the_pdf(
        self,
        mock_render_page: MagicMock,
        fx_app: FastAPI,
        fx_client: httpx.AsyncClient,
        fx_pdf_project_id: int,
        fx_webp: Path,
    ) -> None:
        """Verify a PDF page is rendered from the PDF file and served as a privately cached WebP."""
        mock_render_page.return_value = fx_webp
        storage = fx_app.state.storage
        response = await fx_client.get(
            IMAGE_URL.format(project_id=fx_pdf_project_id, number=2), params={VARIANT_PARAM: 'thumb'}
        )
        expect(response.status_code == HTTPStatus.OK)
        expect(response.headers['content-type'] == WEBP_MEDIA_TYPE)
        expect(response.headers['cache-control'].startswith('private, max-age='))
        expect(response.content == await anyio.Path(fx_webp).read_bytes())
        expected_request = RenderRequest(
            kind=SourceKind.PDF,
            source_path=storage.source_dir(fx_pdf_project_id) / PDF_NAME,
            page_index=1,
            variant=RenderVariant.THUMBNAIL,
            cache_dir=storage.cache_dir(fx_pdf_project_id),
        )
        expect(mock_render_page.call_args_list == [call(expected_request)])
        assert_expectations()

    @patch(RENDER_PAGE_PATCH)
    @patch(ANALYZE_IMAGES_PATCH, return_value=SAMPLE_IMAGES_ANALYSIS)
    async def test_image_page_is_rendered_from_its_own_file(
        self,
        _mock_analyze_images: MagicMock,
        mock_render_page: MagicMock,
        fx_app: FastAPI,
        fx_client: httpx.AsyncClient,
        fx_project_id: int,
        fx_webp: Path,
    ) -> None:
        """Verify a page of an image set is rendered from that page's file, as a preview by default."""
        mock_render_page.return_value = fx_webp
        storage = fx_app.state.storage
        await fx_client.post(SOURCE_URL.format(project_id=fx_project_id), files=_files(IMAGE_UPLOAD_NAMES))
        response = await fx_client.get(IMAGE_URL.format(project_id=fx_project_id, number=3))
        expect(response.status_code == HTTPStatus.OK)
        expected_request = RenderRequest(
            kind=SourceKind.IMAGES,
            source_path=storage.source_dir(fx_project_id) / IMAGE_ANALYSED_NAMES[2],
            page_index=2,
            variant=RenderVariant.PREVIEW,
            cache_dir=storage.cache_dir(fx_project_id),
        )
        expect(mock_render_page.call_args_list == [call(expected_request)])
        assert_expectations()

    @patch(RENDER_PAGE_PATCH)
    async def test_invalid_variant_returns_422(
        self, mock_render_page: MagicMock, fx_client: httpx.AsyncClient, fx_pdf_project_id: int
    ) -> None:
        """Verify an unknown variant is a validation error and nothing is rendered."""
        response = await fx_client.get(
            IMAGE_URL.format(project_id=fx_pdf_project_id, number=1), params={VARIANT_PARAM: 'huge'}
        )
        expect(response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT)
        expect(mock_render_page.call_count == 0)
        assert_expectations()

    @pytest.mark.parametrize(
        'path',
        [
            IMAGE_URL.format(project_id=UNKNOWN_PROJECT_ID, number=1),
            IMAGE_URL.format(project_id='{project_id}', number=4),
        ],
        ids=['unknown-project', 'out-of-range-page'],
    )
    @patch(RENDER_PAGE_PATCH)
    async def test_missing_page_returns_404(
        self, mock_render_page: MagicMock, fx_client: httpx.AsyncClient, fx_pdf_project_id: int, path: str
    ) -> None:
        """Verify an image of a page that does not exist is a 404 and nothing is rendered."""
        response = await fx_client.get(path.format(project_id=fx_pdf_project_id))
        expect(response.status_code == HTTPStatus.NOT_FOUND)
        expect(mock_render_page.call_count == 0)
        assert_expectations()
