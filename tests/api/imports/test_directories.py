"""Tests for the upload of a chosen directory or of chosen files: relative paths, system files and upload order."""

import io
from http import HTTPStatus
from itertools import cycle
from operator import attrgetter
from typing import TYPE_CHECKING, NamedTuple
from unittest.mock import AsyncMock, patch

import pytest
from delayed_assert import assert_expectations, expect
from PIL import Image

from bookreviver.domain.enums import JobState, RejectionReason, UploadProblem
from tests.adapters.imaging.samples import TiffFrame, write_tiff
from tests.helpers.fakes_imports import image_upload, pdf_upload, stored_book

if TYPE_CHECKING:
    from pathlib import Path

    import httpx

    from bookreviver.domain.entities import BookRecord
    from tests.api.imports.watch import ImportWatch
    from tests.helpers.database import UnitOfWorkFactory

pytestmark = pytest.mark.anyio

SOURCES_PATH: str = '/api/v1/books/{book_id}/sources'
JOBS_PATH: str = '/api/v1/books/{book_id}/jobs'
FILES_FIELD: str = 'files'
OCTET_STREAM: str = 'application/octet-stream'
PROBLEM_MEDIA_TYPE: str = 'application/problem+json'
CONTENT_TYPE_HEADER: str = 'content-type'
# Cuts the images of the scans, the one step that takes seconds for hundreds of scans and has tests of its own
CUT_SCANS_TARGET: str = 'bookreviver.services.imports.ImportRun._cut_scans'
# More than the 1000 files Starlette's multipart parser takes unless it is told otherwise
DIRECTORY_SCANS: int = 1500
SCANS_PER_VOLUME: int = 500
SCAN_SIZE_PX: tuple[int, int] = (24, 32)
# Each scan shows its own number as a row of black and white squares of this many pixels, so no two files are equal
BIT_SQUARE_PX: int = 4
TIFF_FORMAT: str = 'TIFF'
# Formats a directory of scans may mix, by the suffix the browser sends
SCAN_FORMATS: dict[str, str] = {'jpg': 'JPEG', 'png': 'PNG', 'tif': TIFF_FORMAT, 'jp2': 'JPEG2000'}
# Fields of the job and of a file of the import report that the API answers with
STATE_FIELD: str = 'state'
REJECTION_FIELD: str = 'rejection'
# Names of the uploaded files and folders
VOLUME_FOLDER: str = 'vol{number}'
FOLDER_SYSTEM_FILE: str = 'kniga/Thumbs.db'
COVER_NAME: str = 'cover.jpg'
TIFF_BOOK_NAME: str = 'book.tif'
FIRST_VOLUME_SCAN: str = 'vol1/001.tif'
PDF_PART_PAGES: dict[str, int] = {'part10.pdf': 1, 'part2.pdf': 2, 'part1.pdf': 3}
PART_WIDTHS_PX: dict[str, int] = {'part10.pdf': 150, 'part2.pdf': 160, 'part1.pdf': 170}


class UnsafePathCase(NamedTuple):
    """A file name a client sends that must not become a path inside the book, with the rule it breaks.

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


class TestUploadDirectories:
    """Tests for POST /books/{book_id}/sources with the files of a directory or a hand-picked set."""

    async def test_directory_of_pdf_parts_becomes_sources_and_pages_in_the_order_of_the_list(
        self,
        fx_client: httpx.AsyncClient,
        fx_import: ImportWatch,
        fx_uow: UnitOfWorkFactory,
        fx_book: BookRecord,
        tmp_path: Path,
    ) -> None:
        """Verify the parts of a directory keep their order, which differs from the natural order of their names.

        The directory holds ``part10``, ``part2`` and ``part1`` in that order, and its system file is reported and
        skipped. Every part is a source named by its relative path, and the pages follow the list.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_import: Reader of the imports the application runs.
        :type fx_import: ImportWatch
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the signed-in account.
        :type fx_book: BookRecord
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
        parts.insert(1, _part(FOLDER_SYSTEM_FILE, b'thumbnails'))

        response = await fx_client.post(SOURCES_PATH.format(book_id=fx_book.id), files=parts)
        finished = await fx_import.finished(response.json())

        book = await stored_book(fx_uow(), fx_book.id)
        sources, pages = book.sources, book.pages
        scans = {scan.id: scan for scan in book.scans}
        report = await fx_import.report(fx_book.id)
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished[STATE_FIELD] == JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == ['kniga/part10.pdf', 'kniga/part2.pdf', 'kniga/part1.pdf'])
        expect([source.scan_count for source in sources] == list(PDF_PART_PAGES.values()))
        expect(
            [(file['file_name'], file[REJECTION_FIELD]) for file in report]
            == [(FOLDER_SYSTEM_FILE, RejectionReason.SYSTEM_FILE)]
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
        fx_import: ImportWatch,
        fx_uow: UnitOfWorkFactory,
        fx_book: BookRecord,
    ) -> None:
        """Verify more files than Starlette's default limit are accepted, each a source in the order of the list.

        The scans are JPEG, PNG, TIFF and JPEG 2000 files of three volume folders, listed volume by volume with the
        system files of the folders among them, and the folders reuse the names of their files. The upload, the
        grouping, the inspection of every file and the creation of the pages run as they do for a user, and only the
        cutting of the images, which takes a minute for this many scans, is left out.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_import: Reader of the imports the application runs.
        :type fx_import: ImportWatch
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the signed-in account.
        :type fx_book: BookRecord
        """
        suffixes = cycle(SCAN_FORMATS)
        names = []
        parts = []
        for number in range(DIRECTORY_SCANS):
            volume, page = divmod(number, SCANS_PER_VOLUME)
            suffix = next(suffixes)
            folder = VOLUME_FOLDER.format(number=volume + 1)
            names.append(f'{folder}/{page + 1:04}.{suffix}')
            parts.append(_part(names[-1], _scan(number + 1, image_format=SCAN_FORMATS[suffix])))
            if page == 0:
                parts.append(_part(f'{folder}/Thumbs.db', b'thumbnails'))

        response = await fx_client.post(SOURCES_PATH.format(book_id=fx_book.id), files=parts)
        finished = await fx_import.finished(response.json())

        book = await stored_book(fx_uow(), fx_book.id)
        sources, pages = book.sources, book.pages
        scans = {scan.id: scan for scan in book.scans}
        report = await fx_import.report(fx_book.id)
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished[STATE_FIELD] == JobState.SUCCEEDED)
        # The progress counts every file dealt with, and a scan to cut for each source
        expect(finished['progress']['total'] == len(parts) + DIRECTORY_SCANS)
        expect([source.file_name for source in sources] == names)
        expect([scans[page.scan_id].source_id for page in pages if page.scan_id] == [source.id for source in sources])
        expect(len(report) == len({name.split('/')[0] for name in names}))
        expect({file[REJECTION_FIELD] for file in report} == {RejectionReason.SYSTEM_FILE})
        assert_expectations()

    async def test_one_chosen_file_is_one_source_named_by_its_name(
        self,
        fx_client: httpx.AsyncClient,
        fx_import: ImportWatch,
        fx_uow: UnitOfWorkFactory,
        fx_book: BookRecord,
        tmp_path: Path,
    ) -> None:
        """Verify a single file chosen alone, with no folder, becomes one source, as a directory of files does.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_import: Reader of the imports the application runs.
        :type fx_import: ImportWatch
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the signed-in account.
        :type fx_book: BookRecord
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        cover = image_upload(tmp_path, COVER_NAME).file.read()

        response = await fx_client.post(SOURCES_PATH.format(book_id=fx_book.id), files=[_part(COVER_NAME, cover)])
        finished = await fx_import.finished(response.json())

        sources = (await stored_book(fx_uow(), fx_book.id)).sources
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished[STATE_FIELD] == JobState.SUCCEEDED)
        expect([(source.file_name, source.scan_count) for source in sources] == [(COVER_NAME, 1)])
        expect(await fx_import.report(fx_book.id) == [])
        assert_expectations()

    async def test_multi_page_tiff_is_one_source_whose_frames_are_the_scans_and_pages_in_frame_order(
        self,
        fx_client: httpx.AsyncClient,
        fx_import: ImportWatch,
        fx_uow: UnitOfWorkFactory,
        fx_book: BookRecord,
        tmp_path: Path,
    ) -> None:
        """Verify a TIFF of three frames, each of its own width, is one source with three scans numbered from zero.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_import: Reader of the imports the application runs.
        :type fx_import: ImportWatch
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the signed-in account.
        :type fx_book: BookRecord
        :param tmp_path: Temporary directory of the test.
        :type tmp_path: Path
        """
        widths = [96, 64, 80]
        book = write_tiff(
            tmp_path / TIFF_BOOK_NAME, frames=[TiffFrame(mode='L', size_px=(width, 72)) for width in widths]
        )

        response = await fx_client.post(
            SOURCES_PATH.format(book_id=fx_book.id), files=[_part(TIFF_BOOK_NAME, book.read_bytes())]
        )
        finished = await fx_import.finished(response.json())

        stored = await stored_book(fx_uow(), fx_book.id)
        [source], pages = stored.sources, stored.pages
        scans = {scan.id: scan for scan in stored.scans}
        expect(finished[STATE_FIELD] == JobState.SUCCEEDED)
        expect((source.file_name, source.scan_count) == (TIFF_BOOK_NAME, len(widths)))
        expect(
            [(scan.number, scan.facts.width_px) for scan in sorted(scans.values(), key=attrgetter('number'))]
            == list(enumerate(widths))
        )
        # The split the import queues halves the wider frames, so a frame may give two pages, but never out of order
        frames = [scans[page.scan_id].number for page in pages if page.scan_id]
        expect((frames == sorted(frames), list(dict.fromkeys(frames))) == (True, [0, 1, 2]), str(frames))
        expect(all(scan.renditions.ready for scan in scans.values()))
        assert_expectations()

    async def test_files_of_different_folders_with_one_name_are_two_sources(
        self,
        fx_client: httpx.AsyncClient,
        fx_import: ImportWatch,
        fx_uow: UnitOfWorkFactory,
        fx_book: BookRecord,
    ) -> None:
        """Verify ``vol1/001.tif`` and ``vol2/001.tif`` are two sources with those names, not a duplicate name.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_import: Reader of the imports the application runs.
        :type fx_import: ImportWatch
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the signed-in account.
        :type fx_book: BookRecord
        """
        parts = [
            _part(FIRST_VOLUME_SCAN, _scan(1, image_format=TIFF_FORMAT)),
            _part('vol2\\001.tif', _scan(2, image_format=TIFF_FORMAT)),
        ]

        response = await fx_client.post(SOURCES_PATH.format(book_id=fx_book.id), files=parts)
        finished = await fx_import.finished(response.json())

        sources = (await stored_book(fx_uow(), fx_book.id)).sources
        expect(response.status_code == HTTPStatus.ACCEPTED)
        expect(finished[STATE_FIELD] == JobState.SUCCEEDED)
        expect([source.file_name for source in sources] == [FIRST_VOLUME_SCAN, 'vol2/001.tif'])
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
        self, fx_client: httpx.AsyncClient, fx_uow: UnitOfWorkFactory, fx_book: BookRecord, case: UnsafePathCase
    ) -> None:
        """Verify an unsafe path refuses the whole upload with a problem, before a job is put on the board.

        :param fx_client: Client of the signed-in account.
        :type fx_client: httpx.AsyncClient
        :param fx_uow: Function opening a new unit of work over the database of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book: Book of the signed-in account.
        :type fx_book: BookRecord
        :param case: A file name the upload sends, and the problem it must be refused with.
        :type case: UnsafePathCase
        """
        parts = [
            _part('001.tif', _scan(1, image_format=TIFF_FORMAT)),
            _part(case.name, _scan(2, image_format=TIFF_FORMAT)),
        ]

        response = await fx_client.post(SOURCES_PATH.format(book_id=fx_book.id), files=parts)

        jobs = (await fx_client.get(JOBS_PATH.format(book_id=fx_book.id))).json()
        expect(response.status_code == HTTPStatus.BAD_REQUEST)
        expect(response.headers[CONTENT_TYPE_HEADER].startswith(PROBLEM_MEDIA_TYPE))
        expect(response.json()['detail'] == case.problem.label)
        expect(jobs['items'] == [])
        expect((await stored_book(fx_uow(), fx_book.id)).sources == [])
        assert_expectations()
