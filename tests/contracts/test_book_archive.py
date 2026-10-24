"""Contract of the BookArchive port, run against every storage backend the application registers."""

import io
import zipfile
from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import Rendition
from bookreviver.domain.errors import ConflictError, InvalidArchiveError, NotFoundError
from bookreviver.domain.ids import BookId, JobId, PageId, SourceId
from bookreviver.domain.keys import BookKeys
from tests.helpers.builders import make_page_version
from tests.helpers.storage import SOURCE_NAME, upload

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion
    from bookreviver.ports.storage import BookArchive, BookFileStores

pytestmark = pytest.mark.anyio

DATABASE: bytes = b'the database of the book'
SOURCE_CONTENT: bytes = b'the scanned book'
IMAGE_CONTENT: bytes = b'a page image'
UPLOAD_NAME: str = 'still-uploading.pdf'
UNLIMITED_BYTES: int = 1024**3
CASE_ARG: str = 'case'
DATABASE_NAME: str = 'book.db'


class RefusedArchive(NamedTuple):
    """An archive the port refuses, and why.

    :ivar content: The bytes of the uploaded file.
    :ivar match: Part of the message of the refusal.
    """

    content: bytes
    match: str


def _zip(*names: str) -> bytes:
    """Build a ZIP file holding an empty file under each name.

    :param names: The names of the files.
    :type names: str
    :returns: The bytes of the ZIP file.
    :rtype: bytes
    """
    written = io.BytesIO()
    with zipfile.ZipFile(written, mode='w') as archive:
        for name in names:
            archive.writestr(name, b'')
    return written.getvalue()


# The archives the port refuses, by the name of their case
REFUSED: dict[str, RefusedArchive] = {
    'not-a-zip-file': RefusedArchive(b'not an archive', 'not a ZIP archive'),
    'no-database': RefusedArchive(_zip('assets/book/cover.jpg'), 'no database'),
    'climbing-out': RefusedArchive(_zip(DATABASE_NAME, 'assets/../../other/book.db'), 'not a file of a book'),
    'an-upload': RefusedArchive(_zip(DATABASE_NAME, 'incoming/job/book.pdf'), 'not a file of a book'),
    'outside-the-areas': RefusedArchive(_zip(DATABASE_NAME, 'notes.txt'), 'not a file of a book'),
}


async def _book(stores: BookFileStores) -> tuple[BookId, SourceId, PageVersion]:
    """Store a book with one source, one derived file of a page, and an upload still being received.

    :param stores: Opens the stores of a book of the backend.
    :type stores: BookFileStores
    :returns: The book, its source and the version of its page.
    :rtype: tuple[BookId, SourceId, PageVersion]
    """
    book_id, source_id, version = BookId(uuid4()), SourceId(uuid4()), make_page_version(page_id=PageId(uuid4()))
    sources = stores.sources(book_id)
    job_id = JobId(uuid4())
    staged = await sources.stage(job_id, [upload(SOURCE_NAME, content=SOURCE_CONTENT)], max_bytes=UNLIMITED_BYTES)
    await sources.promote(job_id, source_id, names=[file.name for file in staged])
    await sources.stage(JobId(uuid4()), [upload(UPLOAD_NAME)], max_bytes=UNLIMITED_BYTES)
    key = BookKeys().version_rendition(version, Rendition.FULL_JPEG)
    async with stores.assets(book_id).writable(key) as path:
        path.write_bytes(IMAGE_CONTENT)
    return book_id, source_id, version


async def _exported(archive: BookArchive, book_id: BookId) -> bytes:
    """Read the whole archive of a book.

    :param archive: The book archive of the backend.
    :type archive: BookArchive
    :param book_id: The book.
    :type book_id: BookId
    :returns: The archive.
    :rtype: bytes
    """
    return b''.join([piece async for piece in archive.export(book_id, DATABASE)])


class TestExportAndUnpack:
    """Tests for an archive of a book unpacked as a new book."""

    async def test_the_new_book_holds_the_sources_and_the_derived_files_and_no_upload(
        self, fx_book_archive: BookArchive, fx_stores: BookFileStores
    ) -> None:
        """Verify the archive carries the database first, the source and the page image, and leaves the upload out.

        :param fx_book_archive: The book archive of the backend.
        :type fx_book_archive: BookArchive
        :param fx_stores: Opens the stores of a book of the backend.
        :type fx_stores: BookFileStores
        """
        book_id, source_id, version = await _book(fx_stores)
        exported = await _exported(fx_book_archive, book_id)
        copy_id = BookId(uuid4())

        await fx_book_archive.unpack(copy_id, io.BytesIO(exported))

        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            names = archive.namelist()
            expect(names[0] == DATABASE_NAME and archive.read(DATABASE_NAME) == DATABASE)
        expect(not any(UPLOAD_NAME in name for name in names))
        async with fx_stores.sources(copy_id).source_files(source_id) as paths:
            expect({path.name: path.read_bytes() for path in paths} == {SOURCE_NAME: SOURCE_CONTENT})
        key = BookKeys().version_rendition(version, Rendition.FULL_JPEG)
        async with fx_stores.assets(copy_id).readable(key) as path:
            expect(path.read_bytes() == IMAGE_CONTENT)
        assert_expectations()

    async def test_an_archive_is_not_unpacked_twice_into_one_book(
        self, fx_book_archive: BookArchive, fx_stores: BookFileStores
    ) -> None:
        """Verify a book that has a directory refuses a second archive.

        :param fx_book_archive: The book archive of the backend.
        :type fx_book_archive: BookArchive
        :param fx_stores: Opens the stores of a book of the backend.
        :type fx_stores: BookFileStores
        """
        book_id, _, _ = await _book(fx_stores)
        exported = await _exported(fx_book_archive, book_id)
        copy_id = BookId(uuid4())
        await fx_book_archive.unpack(copy_id, io.BytesIO(exported))

        with pytest.raises(ConflictError):
            await fx_book_archive.unpack(copy_id, io.BytesIO(exported))


class TestRefusedArchive:
    """Tests for the archives that are not the archive of a book."""

    @pytest.mark.parametrize(CASE_ARG, list(REFUSED.values()), ids=list(REFUSED))
    async def test_a_refused_archive_leaves_nothing_behind(
        self, fx_book_archive: BookArchive, fx_stores: BookFileStores, case: RefusedArchive
    ) -> None:
        """Verify the refusal names the problem, and the new book can still take a good archive afterwards.

        :param fx_book_archive: The book archive of the backend.
        :type fx_book_archive: BookArchive
        :param fx_stores: Opens the stores of a book of the backend.
        :type fx_stores: BookFileStores
        :param case: The archive and the expected message.
        :type case: RefusedArchive
        """
        copy_id = BookId(uuid4())

        with pytest.raises(InvalidArchiveError, match=case.match):
            await fx_book_archive.unpack(copy_id, io.BytesIO(case.content))

        with pytest.raises(NotFoundError):
            async with fx_stores.sources(copy_id).source_files(SourceId(uuid4())):
                pass
        await fx_book_archive.unpack(copy_id, io.BytesIO(_zip(DATABASE_NAME)))
