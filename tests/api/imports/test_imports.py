"""Tests for POST /projects/{project_id}/sources, from an upload to the sources, pages and events of the book."""

from http import HTTPStatus
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import anyio
import pytest
from delayed_assert import assert_expectations, expect
from fastapi import Request

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.api.auth import current_actor
from bookreviver.api.route_names import RouteName
from bookreviver.api.schemas.jobs import EventName
from bookreviver.domain.enums import JobKind, JobState, RejectionReason
from bookreviver.domain.values import SliceRequest
from tests.adapters.imaging.samples import DjvuPage, requires_djvulibre, write_djvu_indirect
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_imports import djvu_uploads, image_upload, pdf_upload
from tests.helpers.sse import EventStreamReader

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from fastapi import FastAPI, UploadFile
    from taskiq import InMemoryBroker

    from bookreviver.domain.entities import Project
    from tests.helpers.fakes_jobs import JobFakes

pytestmark = pytest.mark.anyio

SOURCES_PATH: str = '/api/v1/projects/{project_id}/sources'
JOB_PATH: str = '/api/v1/jobs/{job_id}'
FILES_FIELD: str = 'files'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
EVERY_SLICE: SliceRequest = SliceRequest(limit=1000)
FIRST_PART_PAGES: int = 2
SECOND_PART_PAGES: int = 3
SCAN_COUNT: int = FIRST_PART_PAGES + SECOND_PART_PAGES + 1
SOURCE_COUNT: int = 3
SECOND_PART_WIDTH_PX: int = 210
# Pages of the sample indirect DjVu documents
DJVU_PAGES: tuple[DjvuPage, ...] = (DjvuPage(), DjvuPage(size_px=(250, 350)), DjvuPage(size_px=(320, 420)))
# The events of an import of one image, in the order they are published: the job queued and running, the job with
# its total, the source and its pages, the job with its first scan done, the scan, the stage of its page and the job
# with its result
ONE_IMAGE_EVENTS: list[EventName] = [
    EventName.JOB_CHANGED,
    EventName.JOB_CHANGED,
    EventName.JOB_CHANGED,
    EventName.SOURCE_IMPORTED,
    EventName.PAGES_CHANGED,
    EventName.JOB_CHANGED,
    EventName.SCAN_READY,
    EventName.PAGE_STAGE_CHANGED,
    EventName.JOB_CHANGED,
]


def _multipart(files: list[UploadFile]) -> list[tuple[str, tuple[str | None, bytes, str]]]:
    """Turn uploads into the parts of a multipart request body, the way a browser sends a chosen set of files.

    :param files: Uploads with the names and the content to send.
    :type files: list[UploadFile]
    :returns: Parts named ``files``, each with its file name, content and content type.
    :rtype: list[tuple[str, tuple[str | None, bytes, str]]]
    """
    return [(FILES_FIELD, (file.filename, file.file.read(), 'application/octet-stream')) for file in files]


class TestUploadSources:
    """Tests for the upload of the files of a book."""

    async def test_two_part_pdf_and_a_cover_become_three_sources_with_their_pages_in_order(
        self,
        fx_client: httpx.AsyncClient,
        fx_broker: InMemoryBroker,
        fx_fakes: JobFakes,
        fx_project: Project,
        tmp_path: Path,
    ) -> None:
        """Verify a book uploaded as two PDF parts and a cover is imported as three sources, pages in book order.

        The files are sent in an order that differs from the natural order of their names, and the sources and the
        pages follow the order of the upload: the cover, part 2 and part 1.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the import job.
        :type fx_broker: InMemoryBroker
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        samples = tmp_path / 'samples'
        samples.mkdir()
        files = [
            image_upload(samples, 'part3-cover.jpg'),
            pdf_upload(samples, 'part2.pdf', pages=SECOND_PART_PAGES, width_px=SECOND_PART_WIDTH_PX),
            pdf_upload(samples, 'part1.pdf', pages=FIRST_PART_PAGES),
        ]

        response = await fx_client.post(SOURCES_PATH.format(project_id=fx_project.id), files=_multipart(files))
        queued = response.json()
        await fx_broker.wait_all()

        finished = (await fx_client.get(JOB_PATH.format(job_id=queued['id']))).json()
        uow = InMemoryUnitOfWork(fx_fakes.database)
        sources = await uow.sources.list_for_project(fx_project.id)
        scans = {scan.id: scan for scan in (await uow.scans.list_for_project(fx_project.id, EVERY_SLICE)).items}
        pages = (await uow.pages.list_for_project(fx_project.id, EVERY_SLICE)).items
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect((queued['state'], queued['kind'], queued['result']) == (JobState.QUEUED, JobKind.IMPORT_SOURCE, None))
        expect(finished['state'] == JobState.SUCCEEDED)
        expect(finished['progress'] == {'done': SCAN_COUNT, 'total': SCAN_COUNT, 'fraction': 1.0})
        expect(finished['result']['imported'] == [str(source.id) for source in sources])
        expect((finished['result']['rejected'], finished['result']['skipped']) == ([], []))
        expect([source.file_name for source in sources] == ['part3-cover.jpg', 'part2.pdf', 'part1.pdf'])
        expect([source.scan_count for source in sources] == [1, SECOND_PART_PAGES, FIRST_PART_PAGES])
        expect(
            [(scans[page.scan_id].source_id, scans[page.scan_id].number) for page in pages if page.scan_id]
            == [(source.id, number) for source in sources for number in range(source.scan_count)]
        )
        expect(len(pages) == SCAN_COUNT and all(scan.renditions.ready for scan in scans.values()))
        assert_expectations()

    async def test_stream_carries_the_events_of_an_import_and_its_result(
        self,
        fx_app: FastAPI,
        fx_client: httpx.AsyncClient,
        fx_fakes: JobFakes,
        fx_project: Project,
        tmp_path: Path,
    ) -> None:
        """Verify the events a browser follows during an import: the job, the source, its pages and scan, the result.

        There is no event per page, since the page manifest is read in one request.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        reader = EventStreamReader(count=len(ONE_IMAGE_EVENTS))
        async with anyio.create_task_group() as group:
            group.start_soon(
                reader.read, fx_app, fx_app.url_path_for(RouteName.PROJECT_EVENTS, project_id=fx_project.id)
            )
            await fx_fakes.events.subscribed.wait()
            await fx_client.post(
                SOURCES_PATH.format(project_id=fx_project.id), files=_multipart([image_upload(tmp_path, 'cover.jpg')])
            )

        uow = InMemoryUnitOfWork(fx_fakes.database)
        [source] = await uow.sources.list_for_project(fx_project.id)
        [scan] = (await uow.scans.list_for_project(fx_project.id, EVERY_SLICE)).items
        final = reader.events[-1].data
        expect([event.name for event in reader.events] == ONE_IMAGE_EVENTS)
        expect(reader.events[3].data == {'project_id': str(fx_project.id), 'source_id': str(source.id)})
        expect(reader.events[6].data == {'project_id': str(fx_project.id), 'scan_id': str(scan.id)})
        expect((final['state'], final['result']['imported']) == (JobState.SUCCEEDED, [str(source.id)]))
        assert_expectations()

    async def test_rejected_files_are_reported_in_the_result_of_the_job(
        self, fx_client: httpx.AsyncClient, fx_broker: InMemoryBroker, fx_project: Project, tmp_path: Path
    ) -> None:
        """Verify a file that is not a source is reported with its reason in the job, and the others are imported.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the import job.
        :type fx_broker: InMemoryBroker
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        files = [image_upload(tmp_path, 'page.jpg')]
        parts = [*_multipart(files), (FILES_FIELD, ('notes.txt', b'not a page', 'text/plain'))]

        queued = (await fx_client.post(SOURCES_PATH.format(project_id=fx_project.id), files=parts)).json()
        await fx_broker.wait_all()

        finished = (await fx_client.get(JOB_PATH.format(job_id=queued['id']))).json()
        expect(finished['state'] == JobState.SUCCEEDED)
        expect(len(finished['result']['imported']) == 1)
        expect(
            [(file['file_name'], file['reason']) for file in finished['result']['rejected']]
            == [('notes.txt', RejectionReason.UNSUPPORTED_TYPE)]
        )
        assert_expectations()

    @requires_djvulibre
    async def test_indirect_djvu_document_is_one_source_and_an_incomplete_one_is_rejected_by_name(
        self,
        fx_client: httpx.AsyncClient,
        fx_broker: InMemoryBroker,
        fx_fakes: JobFakes,
        fx_project: Project,
        tmp_path: Path,
    ) -> None:
        """Verify an index with its page files is one source of several files, and one lacking a file is rejected.

        Two indirect documents are uploaded together, and the second has lost its last page file.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the import job.
        :type fx_broker: InMemoryBroker
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        complete = write_djvu_indirect(tmp_path, pages=DJVU_PAGES, index_name='a-index.djvu', prefix='a')
        incomplete = write_djvu_indirect(tmp_path, pages=DJVU_PAGES, index_name='b-index.djvu', prefix='b')

        queued = (
            await fx_client.post(
                SOURCES_PATH.format(project_id=fx_project.id),
                files=_multipart(djvu_uploads([*complete, *incomplete[:-1]])),
            )
        ).json()
        await fx_broker.wait_all()

        finished = (await fx_client.get(JOB_PATH.format(job_id=queued['id']))).json()
        [source] = await InMemoryUnitOfWork(fx_fakes.database).sources.list_for_project(fx_project.id)
        [rejected] = finished['result']['rejected']
        expect(finished['state'] == JobState.SUCCEEDED)
        expect((source.file_name, source.scan_count) == ('a-index.djvu', len(DJVU_PAGES)))
        expect([file.name for file in source.files] == [path.name for path in complete])
        expect(finished['result']['imported'] == [str(source.id)])
        expect((rejected['file_name'], rejected['reason']) == ('b-index.djvu', RejectionReason.UNREADABLE))
        expect('b0003.djvu' in rejected['detail'])
        assert_expectations()

    async def test_second_upload_while_an_import_is_running_is_a_conflict(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_project: Project, tmp_path: Path
    ) -> None:
        """Verify an upload to a project that is importing answers 409 with a problem.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        uow = InMemoryUnitOfWork(fx_fakes.database)
        async with uow.change():
            await uow.jobs.add(make_job(project_id=fx_project.id, state=JobState.RUNNING))

        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=_multipart([image_upload(tmp_path, 'a.jpg')])
        )

        expect(response.status_code == HTTPStatus.CONFLICT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()

    async def test_project_of_another_account_is_not_found(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, tmp_path: Path
    ) -> None:
        """Verify an upload to a project of another account answers 404, as if the project did not exist.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        stranger_project = make_project(owner_id=new_account_id())
        await fx_fakes.store(stranger_project)

        response = await fx_client.post(
            SOURCES_PATH.format(project_id=stranger_project.id), files=_multipart([image_upload(tmp_path, 'a.jpg')])
        )

        assert response.status_code == HTTPStatus.NOT_FOUND

    async def test_request_without_files_is_a_validation_problem(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify a request that names no file is refused before the route runs.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.post(SOURCES_PATH.format(project_id=fx_project.id), data={'other': 'value'})

        expect(response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()

    async def test_address_of_the_single_source_is_not_published(self, fx_app: FastAPI) -> None:
        """Verify the schema has the plural address only, since the client is generated from it.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        """
        paths = fx_app.openapi()['paths']
        expect('/api/v1/projects/{project_id}/sources' in paths)
        expect('/api/v1/projects/{project_id}/source' not in paths)
        assert_expectations()

    async def test_schema_describes_the_multipart_body_although_the_route_declares_none(self, fx_app: FastAPI) -> None:
        """Verify the schema names the files as a required array of file parts, which the client is generated from.

        The route reads its body in a dependency, so the schema comes from ``openapi_extra``, not from a parameter.

        :param fx_app: The running application.
        :type fx_app: FastAPI
        """
        operation = fx_app.openapi()['paths']['/api/v1/projects/{project_id}/sources']['post']
        body = operation['requestBody']
        schema = body['content']['multipart/form-data']['schema']
        expect(body['required'] is True)
        expect(schema['required'] == ['files'])
        expect(schema['properties']['files']['type'] == 'array')
        expect(
            schema['properties']['files']['items'] == {'type': 'string', 'contentMediaType': 'application/octet-stream'}
        )
        expect(schema['properties']['files']['minItems'] == 1)
        assert_expectations()

    async def test_files_field_that_holds_text_is_a_validation_problem(
        self, fx_client: httpx.AsyncClient, fx_project: Project
    ) -> None:
        """Verify a ``files`` field that is text and not a file is refused with 422 and nothing is imported.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=[(FILES_FIELD, (None, b'plain text'))]
        )

        expect(response.status_code == HTTPStatus.UNPROCESSABLE_CONTENT)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        assert_expectations()


class UploadParsedError(Exception):
    """Raised by the patched body parser, so a test learns that a request body was read at all."""


@patch.object(Request, 'form', AsyncMock(side_effect=UploadParsedError))
class TestUploadIsRefusedBeforeItsBodyIsRead:
    """Tests that a request the server will refuse is refused before its body is parsed and spooled to disk."""

    async def test_project_of_another_account_is_refused_unread(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, tmp_path: Path
    ) -> None:
        """Verify a non-owner gets 404 without the server reading the files it sent.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        stranger_project = make_project(owner_id=new_account_id())
        await fx_fakes.store(stranger_project)

        response = await fx_client.post(
            SOURCES_PATH.format(project_id=stranger_project.id), files=_multipart([image_upload(tmp_path, 'a.jpg')])
        )

        assert response.status_code == HTTPStatus.NOT_FOUND

    async def test_project_that_is_importing_is_refused_unread(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_project: Project, tmp_path: Path
    ) -> None:
        """Verify an upload to a project that already imports gets 409 without its files being read.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        uow = InMemoryUnitOfWork(fx_fakes.database)
        async with uow.change():
            await uow.jobs.add(make_job(project_id=fx_project.id, state=JobState.QUEUED))

        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=_multipart([image_upload(tmp_path, 'a.jpg')])
        )

        assert response.status_code == HTTPStatus.CONFLICT

    async def test_caller_who_is_not_signed_in_is_refused_unread(
        self, fx_app: FastAPI, fx_client: httpx.AsyncClient, fx_project: Project, tmp_path: Path
    ) -> None:
        """Verify a caller with no session gets 401 without the server reading the files it sent.

        :param fx_app: The running application, whose test sign-in is taken off.
        :type fx_app: FastAPI
        :param fx_client: Client without a session.
        :type fx_client: httpx.AsyncClient
        :param fx_project: Project of the account the test otherwise signs in as.
        :type fx_project: Project
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        fx_app.dependency_overrides.pop(current_actor)

        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=_multipart([image_upload(tmp_path, 'a.jpg')])
        )

        assert response.status_code == HTTPStatus.UNAUTHORIZED
