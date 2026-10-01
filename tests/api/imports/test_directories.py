"""Tests for the upload of a chosen directory or of chosen files: relative paths, system files and upload order."""

import io
from http import HTTPStatus
from itertools import cycle
from operator import attrgetter
from typing import TYPE_CHECKING, Any, NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.enums import JobState, RejectionReason, UploadProblem
from bookreviver.domain.values import SliceRequest
from tests.adapters.imaging.samples import TiffFrame, write_tiff
from tests.helpers.fakes_imports import image_upload, pdf_upload

if TYPE_CHECKING:
    from pathlib import Path

    import httpx
    from taskiq import InMemoryBroker

    from bookreviver.domain.entities import Project
    from tests.helpers.fakes_jobs import JobFakes

pytestmark = pytest.mark.anyio

SOURCES_PATH: str = '/api/v1/projects/{project_id}/sources'
JOB_PATH: str = '/api/v1/jobs/{job_id}'
FILES_FIELD: str = 'files'
OCTET_STREAM: str = 'application/octet-stream'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
EVERY_SLICE: SliceRequest = SliceRequest(limit=5000)
# Cuts the images of the scans, the one step that takes seconds for hundreds of scans and has tests of its own
CUT_SCANS_TARGET: str = 'bookreviver.services.imports.ImportRun._cut_scans'
# More than the 1000 files Starlette's multipart parser takes unless it is told otherwise
DIRECTORY_SCANS: int = 1500
SCANS_PER_VOLUME: int = 500
SCAN_SIZE_PX: tuple[int, int] = (24, 32)
# Each scan shows its own number as a row of black and white squares of this many pixels, so no two files are equal
BIT_SQUARE_PX: int = 4
# Formats a directory of scans may mix, by the suffix the browser sends
SCAN_FORMATS: dict[str, str] = {'jpg': 'JPEG', 'png': 'PNG', 'tif': 'TIFF', 'jp2': 'JPEG2000'}
PDF_PART_PAGES: dict[str, int] = {'part10.pdf': 1, 'part2.pdf': 2, 'part1.pdf': 3}
PART_WIDTHS_PX: dict[str, int] = {'part10.pdf': 150, 'part2.pdf': 160, 'part1.pdf': 170}


class UnsafePathCase(NamedTuple):
    """A file name a client sends that must not become a path inside the project, with the rule it breaks.

    :ivar name: File name as sent in the multipart body.
    :ivar problem: Upload problem the answer must carry.
    """

    name: str
    problem: UploadProblem


def _part(name: str, content: bytes) -> tuple[str, tuple[str, bytes, str]]:
    """Build the multipart part of one file, named as a browser names a file of a chosen directory.

    :param name: Relative path of the file, which the browser sends as the name of the part.
    :type name: str
    :param content: Bytes of the file.
    :type content: bytes
    :returns: The part, in the shape ``httpx`` takes for a ``files`` field.
    :rtype: tuple[str, tuple[str, bytes, str]]
    """
    return FILES_FIELD, (name, content, OCTET_STREAM)


def _scan(number: int, *, image_format: str) -> bytes:
    """Build a small gray scan that no other number gives, in the given format.

    :param number: Number of the scan, written into the image as bits.
    :type number: int
    :param image_format: Pillow name of the format to save the image in.
    :type image_format: str
    :returns: The bytes of the image file.
    :rtype: bytes
    """
    image = Image.new('L', SCAN_SIZE_PX)
    columns = SCAN_SIZE_PX[0] // BIT_SQUARE_PX
    for bit in range(number.bit_length()):
        if number >> bit & 1:
            left, top = bit % columns * BIT_SQUARE_PX, bit // columns * BIT_SQUARE_PX
            image.paste(255, (left, top, left + BIT_SQUARE_PX, top + BIT_SQUARE_PX))
    buffer = io.BytesIO()
    image.save(buffer, format=image_format)
    return buffer.getvalue()


async def _finished_job(client: httpx.AsyncClient, broker: InMemoryBroker, job_id: str) -> dict[str, Any]:
    """Wait until the import has run, and read the job as the API shows it.

    :param client: Client of the signed-in account.
    :type client: httpx.AsyncClient
    :param broker: In-process broker running the import job.
    :type broker: InMemoryBroker
    :param job_id: Identifier of the job the upload answered with.
    :type job_id: str
    :returns: The job document.
    :rtype: dict[str, Any]
    """
    await broker.wait_all()
    return (await client.get(JOB_PATH.format(job_id=job_id))).json()


class TestUploadDirectories:
    """Tests for POST /projects/{project_id}/sources with the files of a directory or a hand-picked set."""

    async def test_directory_of_pdf_parts_becomes_sources_and_pages_in_the_order_of_the_list(
        self,
        fx_client: httpx.AsyncClient,
        fx_broker: InMemoryBroker,
        fx_fakes: JobFakes,
        fx_project: Project,
        tmp_path: Path,
    ) -> None:
        """Verify the parts of a directory keep their order, which differs from the natural order of their names.

        The directory holds ``part10``, ``part2`` and ``part1`` in that order, and its system file is reported and
        skipped. Every part is a source named by its relative path, and the pages follow the list.

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
        parts = [
            _part(
                f'kniga/{name}',
                pdf_upload(tmp_path, name, pages=PDF_PART_PAGES[name], width_px=PART_WIDTHS_PX[name]).file.read(),
            )
            for name in PDF_PART_PAGES
        ]
        parts.insert(1, _part('kniga/Thumbs.db', b'thumbnails'))

        response = await fx_client.post(SOURCES_PATH.format(project_id=fx_project.id), files=parts)
        finished = await _finished_job(fx_client, fx_broker, response.json()['id'])

        uow = InMemoryUnitOfWork(fx_fakes.database)
        sources = await uow.sources.list_for_project(fx_project.id)
        scans = {scan.id: scan for scan in (await uow.scans.list_for_project(fx_project.id, EVERY_SLICE)).items}
        pages = (await uow.pages.list_for_project(fx_project.id, EVERY_SLICE)).items
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished['state'] == JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == ['kniga/part10.pdf', 'kniga/part2.pdf', 'kniga/part1.pdf'])
        expect([source.scan_count for source in sources] == list(PDF_PART_PAGES.values()))
        expect(
            [file['file_name'] for file in finished['result']['rejected']] == ['kniga/Thumbs.db']
            and [file['reason'] for file in finished['result']['rejected']] == [RejectionReason.SYSTEM_FILE]
        )
        expect(
            [(scans[page.scan_id].source_id, scans[page.scan_id].number) for page in pages if page.scan_id]
            == [(source.id, number) for source in sources for number in range(source.scan_count)]
        )
        assert_expectations()

    @patch(CUT_SCANS_TARGET, new=AsyncMock())
    async def test_directory_of_1500_scans_in_several_formats_is_imported_in_the_order_of_the_list(
        self,
        fx_client: httpx.AsyncClient,
        fx_broker: InMemoryBroker,
        fx_fakes: JobFakes,
        fx_project: Project,
    ) -> None:
        """Verify more files than Starlette's default limit are accepted, each a source in the order of the list.

        The scans are JPEG, PNG, TIFF and JPEG 2000 files of three volume folders, listed volume by volume with the
        system files of the folders among them, and the folders reuse the names of their files. The upload, the
        grouping, the inspection of every file and the creation of the pages run as they do for a user, and only the
        cutting of the images, which takes a minute for this many scans, is left out.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the import job.
        :type fx_broker: InMemoryBroker
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        suffixes = cycle(SCAN_FORMATS)
        names = []
        parts = []
        for number in range(DIRECTORY_SCANS):
            volume, page = divmod(number, SCANS_PER_VOLUME)
            suffix = next(suffixes)
            names.append(f'vol{volume + 1}/{page + 1:04}.{suffix}')
            parts.append(_part(names[-1], _scan(number + 1, image_format=SCAN_FORMATS[suffix])))
            if page == 0:
                parts.append(_part(f'vol{volume + 1}/Thumbs.db', b'thumbnails'))

        response = await fx_client.post(SOURCES_PATH.format(project_id=fx_project.id), files=parts)
        finished = await _finished_job(fx_client, fx_broker, response.json()['id'])

        uow = InMemoryUnitOfWork(fx_fakes.database)
        sources = await uow.sources.list_for_project(fx_project.id)
        scans = {scan.id: scan for scan in (await uow.scans.list_for_project(fx_project.id, EVERY_SLICE)).items}
        pages = (await uow.pages.list_for_project(fx_project.id, EVERY_SLICE)).items
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished['state'] == JobState.SUCCEEDED)
        expect(finished['progress']['total'] == DIRECTORY_SCANS)
        expect([source.file_name for source in sources] == names)
        expect([scans[page.scan_id].source_id for page in pages if page.scan_id] == [source.id for source in sources])
        expect(len(finished['result']['rejected']) == len({name.split('/')[0] for name in names}))
        expect({file['reason'] for file in finished['result']['rejected']} == {RejectionReason.SYSTEM_FILE})
        assert_expectations()

    async def test_one_chosen_file_is_one_source_named_by_its_name(
        self,
        fx_client: httpx.AsyncClient,
        fx_broker: InMemoryBroker,
        fx_fakes: JobFakes,
        fx_project: Project,
        tmp_path: Path,
    ) -> None:
        """Verify a single file chosen alone, with no folder, becomes one source, as a directory of files does.

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
        cover = image_upload(tmp_path, 'cover.jpg').file.read()

        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=[_part('cover.jpg', cover)]
        )
        finished = await _finished_job(fx_client, fx_broker, response.json()['id'])

        sources = await InMemoryUnitOfWork(fx_fakes.database).sources.list_for_project(fx_project.id)
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished['state'] == JobState.SUCCEEDED)
        expect([(source.file_name, source.scan_count) for source in sources] == [('cover.jpg', 1)])
        expect(finished['result']['rejected'] == [])
        assert_expectations()

    async def test_multi_page_tiff_is_one_source_whose_frames_are_the_scans_and_pages_in_frame_order(
        self,
        fx_client: httpx.AsyncClient,
        fx_broker: InMemoryBroker,
        fx_fakes: JobFakes,
        fx_project: Project,
        tmp_path: Path,
    ) -> None:
        """Verify a TIFF of three frames, each of its own width, is one source with three scans numbered from zero.

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
        widths = [96, 64, 80]
        book = write_tiff(tmp_path / 'book.tif', frames=[TiffFrame(mode='L', size_px=(width, 72)) for width in widths])

        response = await fx_client.post(
            SOURCES_PATH.format(project_id=fx_project.id), files=[_part('book.tif', book.read_bytes())]
        )
        finished = await _finished_job(fx_client, fx_broker, response.json()['id'])

        uow = InMemoryUnitOfWork(fx_fakes.database)
        [source] = await uow.sources.list_for_project(fx_project.id)
        scans = {scan.id: scan for scan in (await uow.scans.list_for_project(fx_project.id, EVERY_SLICE)).items}
        pages = (await uow.pages.list_for_project(fx_project.id, EVERY_SLICE)).items
        expect(finished['state'] == JobState.SUCCEEDED)
        expect((source.file_name, source.scan_count) == ('book.tif', len(widths)))
        expect(
            [(scan.number, scan.facts.width_px) for scan in sorted(scans.values(), key=attrgetter('number'))]
            == list(enumerate(widths))
        )
        expect([scans[page.scan_id].number for page in pages if page.scan_id] == [0, 1, 2])
        expect(all(scan.renditions.ready for scan in scans.values()))
        assert_expectations()

    async def test_files_of_different_folders_with_one_name_are_two_sources(
        self,
        fx_client: httpx.AsyncClient,
        fx_broker: InMemoryBroker,
        fx_fakes: JobFakes,
        fx_project: Project,
    ) -> None:
        """Verify ``vol1/001.tif`` and ``vol2/001.tif`` are two sources with those names, not a duplicate name.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_broker: In-process broker running the import job.
        :type fx_broker: InMemoryBroker
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        """
        parts = [
            _part('vol1/001.tif', _scan(1, image_format='TIFF')),
            _part('vol2\\001.tif', _scan(2, image_format='TIFF')),
        ]

        response = await fx_client.post(SOURCES_PATH.format(project_id=fx_project.id), files=parts)
        finished = await _finished_job(fx_client, fx_broker, response.json()['id'])

        sources = await InMemoryUnitOfWork(fx_fakes.database).sources.list_for_project(fx_project.id)
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished['state'] == JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == ['vol1/001.tif', 'vol2/001.tif'])
        assert_expectations()

    # The multipart parser of Starlette cuts a full Windows path such as ``C:\dir\001.tif`` down to its file name before
    # the route runs, so a drive letter reaches the service only without a backslash after it, as in ``C:001.tif``
    @pytest.mark.parametrize(
        'case',
        [
            UnsafePathCase(name='../001.tif', problem=UploadProblem.UNSAFE_PATH),
            UnsafePathCase(name='vol1/../../001.tif', problem=UploadProblem.UNSAFE_PATH),
            UnsafePathCase(name='/etc/001.tif', problem=UploadProblem.UNSAFE_PATH),
            UnsafePathCase(name='C:001.tif', problem=UploadProblem.UNSAFE_PATH),
            UnsafePathCase(name='vol1/..', problem=UploadProblem.EMPTY_NAME),
        ],
        ids=['parent-folder', 'parent-folder-in-the-middle', 'absolute-path', 'drive-relative', 'no-file-name'],
    )
    async def test_path_that_leaves_its_folder_is_a_400_problem_and_imports_nothing(
        self, fx_client: httpx.AsyncClient, fx_fakes: JobFakes, fx_project: Project, case: UnsafePathCase
    ) -> None:
        """Verify an unsafe path refuses the whole upload with a problem, before a job is recorded.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_fakes: Adapters the application runs on.
        :type fx_fakes: JobFakes
        :param fx_project: Project of the signed-in account.
        :type fx_project: Project
        :param case: A file name the upload sends, and the problem it must be refused with.
        :type case: UnsafePathCase
        """
        parts = [_part('001.tif', _scan(1, image_format='TIFF')), _part(case.name, _scan(2, image_format='TIFF'))]

        response = await fx_client.post(SOURCES_PATH.format(project_id=fx_project.id), files=parts)

        uow = InMemoryUnitOfWork(fx_fakes.database)
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()['detail'] == case.problem.label)
        expect(await uow.jobs.list_for_project(fx_project.id, set(JobState)) == [])
        expect(await uow.sources.list_for_project(fx_project.id) == [])
        assert_expectations()
