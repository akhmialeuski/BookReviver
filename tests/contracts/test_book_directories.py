"""Contract of the BookDirectory port, run against every registered storage backend.

A new book is an empty directory, an archive of a book unpacks into a new directory, and the directory of a book is
deleted whole.
"""

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
from tests.helpers.builders import directory_of, make_page_version
from tests.helpers.storage import SOURCE_NAME, upload

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageVersion
    from bookreviver.ports.storage import BookDirectory

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


async def _book(stores: type[BookDirectory]) -> tuple[BookId, SourceId, PageVersion]:
    """Store a book with one source, one derived file of a page, and an upload still being received.

    :param stores: Opens the stores of a book of the backend.
    :type stores: type[BookDirectory]
    :returns: The book, its source and the version of its page.
    :rtype: tuple[BookId, SourceId, PageVersion]
    """
    book_id, source_id, version = BookId(uuid4()), SourceId(uuid4()), make_page_version(page_id=PageId(uuid4()))
    sources = stores.open(directory_of(book_id)).sources
    job_id = JobId(uuid4())
    staged = await sources.stage(job_id, [upload(SOURCE_NAME, content=SOURCE_CONTENT)], max_bytes=UNLIMITED_BYTES)
    await sources.promote(job_id, source_id, names=[file.name for file in staged])
    await sources.stage(JobId(uuid4()), [upload(UPLOAD_NAME)], max_bytes=UNLIMITED_BYTES)
    key = BookKeys().version_rendition(version, Rendition.FULL_JPEG)
    async with stores.open(directory_of(book_id)).assets.writable(key) as path:
        path.write_bytes(IMAGE_CONTENT)
    return book_id, source_id, version


async def _exported(stores: type[BookDirectory], book_id: BookId) -> bytes:
    """Read the whole archive of a book.

    :param stores: The directories of the books of the backend.
    :type stores: type[BookDirectory]
    :param book_id: The book.
    :type book_id: BookId
    :returns: The archive.
    :rtype: bytes
    """
    return b''.join([piece async for piece in stores.open(directory_of(book_id)).export(DATABASE)])


class TestExportAndUnpack:
    """Tests for an archive of a book unpacked as a new book."""

    async def test_the_new_book_holds_the_sources_and_the_derived_files_and_no_upload(
        self, fx_stores: type[BookDirectory]
    ) -> None:
        """Verify the archive carries the database first, the source and the page image, and leaves the upload out.

        :param fx_stores: The directories of the books of the backend.
        :type fx_stores: type[BookDirectory]
        """
        book_id, source_id, version = await _book(fx_stores)
        exported = await _exported(fx_stores, book_id)
        copy_id = BookId(uuid4())

        await fx_stores.open(directory_of(copy_id)).unpack(io.BytesIO(exported))

        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            names = archive.namelist()
            expect(names[0] == DATABASE_NAME and archive.read(DATABASE_NAME) == DATABASE)
        expect(not any(UPLOAD_NAME in name for name in names))
        async with fx_stores.open(directory_of(copy_id)).sources.source_files(source_id) as paths:
            expect({path.name: path.read_bytes() for path in paths} == {SOURCE_NAME: SOURCE_CONTENT})
        key = BookKeys().version_rendition(version, Rendition.FULL_JPEG)
        async with fx_stores.open(directory_of(copy_id)).assets.readable(key) as path:
            expect(path.read_bytes() == IMAGE_CONTENT)
        assert_expectations()

    async def test_an_archive_is_not_unpacked_twice_into_one_book(self, fx_stores: type[BookDirectory]) -> None:
        """Verify a book that has a directory refuses a second archive.

        :param fx_stores: The directories of the books of the backend.
        :type fx_stores: type[BookDirectory]
        """
        book_id, _, _ = await _book(fx_stores)
        exported = await _exported(fx_stores, book_id)
        copy_id = BookId(uuid4())
        await fx_stores.open(directory_of(copy_id)).unpack(io.BytesIO(exported))

        with pytest.raises(ConflictError):
            await fx_stores.open(directory_of(copy_id)).unpack(io.BytesIO(exported))


class TestRefusedArchive:
    """Tests for the archives that are not the archive of a book."""

    @pytest.mark.parametrize(CASE_ARG, list(REFUSED.values()), ids=list(REFUSED))
    async def test_a_refused_archive_leaves_nothing_behind(
        self, fx_stores: type[BookDirectory], case: RefusedArchive
    ) -> None:
        """Verify the refusal names the problem, and the new book can still take a good archive afterwards.

        :param fx_stores: The directories of the books of the backend.
        :type fx_stores: type[BookDirectory]
        :param case: The archive and the expected message.
        :type case: RefusedArchive
        """
        copy_id = BookId(uuid4())

        with pytest.raises(InvalidArchiveError, match=case.match):
            await fx_stores.open(directory_of(copy_id)).unpack(io.BytesIO(case.content))

        with pytest.raises(NotFoundError):
            async with fx_stores.open(directory_of(copy_id)).sources.source_files(SourceId(uuid4())):
                pass
        await fx_stores.open(directory_of(copy_id)).unpack(io.BytesIO(_zip(DATABASE_NAME)))


class TestCreate:
    """Contract of BookDirectory.create(), which makes the empty directory of a new book."""

    async def test_the_new_directory_holds_no_source(self, fx_stores: type[BookDirectory]) -> None:
        """Verify the new book has a directory and no source yet, and a book without a database is not an archive.

        :param fx_stores: The directories of the books of the backend.
        :type fx_stores: type[BookDirectory]
        """
        directory = fx_stores.open(directory_of(BookId(uuid4())))

        await directory.create()

        with pytest.raises(NotFoundError):
            async with directory.sources.source_files(SourceId(uuid4())):
                pass
        with pytest.raises(ConflictError):
            await directory.unpack(io.BytesIO(_zip(DATABASE_NAME)))

    async def test_a_directory_that_exists_is_left_as_it_is(self, fx_stores: type[BookDirectory]) -> None:
        """Verify making the directory of a book again keeps the files the book holds.

        :param fx_stores: The directories of the books of the backend.
        :type fx_stores: type[BookDirectory]
        """
        book_id, source_id, _ = await _book(fx_stores)

        await fx_stores.open(directory_of(book_id)).create()

        async with fx_stores.open(directory_of(book_id)).sources.source_files(source_id) as paths:
            assert [path.name for path in paths] == [SOURCE_NAME]


class TestDelete:
    """Contract of BookDirectory.delete(), which removes the directory of a book whole."""

    async def test_removes_the_database_the_sources_the_uploads_and_the_derived_files_of_that_book_only(
        self, fx_stores: type[BookDirectory]
    ) -> None:
        """Verify nothing of the deleted book is left, and another book keeps its source and its page image.

        :param fx_stores: The directories of the books of the backend.
        :type fx_stores: type[BookDirectory]
        """
        book_id, source_id, version = await _book(fx_stores)
        other_id, other_source_id, other_version = await _book(fx_stores)
        key = BookKeys().version_rendition(version, Rendition.FULL_JPEG)
        directory = fx_stores.open(directory_of(book_id))

        await directory.delete()

        # A directory that is gone whole takes an archive, which a directory that exists refuses
        await directory.unpack(io.BytesIO(_zip(DATABASE_NAME)))
        async with fx_stores.open(directory_of(other_id)).sources.source_files(other_source_id) as paths:
            expect([path.name for path in paths] == [SOURCE_NAME])
        async with fx_stores.open(directory_of(other_id)).assets.readable(
            BookKeys().version_rendition(other_version, Rendition.FULL_JPEG)
        ) as path:
            expect(path.read_bytes() == IMAGE_CONTENT)
        assert_expectations()
        with pytest.raises(NotFoundError):
            async with directory.sources.source_files(source_id):
                pass
        with pytest.raises(NotFoundError):
            async with directory.assets.readable(key):
                pass

    async def test_a_directory_deleted_already_or_never_made_is_not_an_error(
        self, fx_stores: type[BookDirectory]
    ) -> None:
        """Verify a deletion broken off half way can run again, and a book that has no directory is left as it is.

        :param fx_stores: The directories of the books of the backend.
        :type fx_stores: type[BookDirectory]
        """
        book_id, _, _ = await _book(fx_stores)

        await fx_stores.open(directory_of(book_id)).delete()
        await fx_stores.open(directory_of(book_id)).delete()
        await fx_stores.open(directory_of(BookId(uuid4()))).delete()

        # The directory is gone, so it takes an archive, which a directory that exists refuses
        await fx_stores.open(directory_of(book_id)).unpack(io.BytesIO(_zip(DATABASE_NAME)))
