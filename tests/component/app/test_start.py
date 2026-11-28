"""Tests for the start of the application, which begins no job and opens no book, and for the first opening of a book.

What an interrupted import left in a book, which no job or request can use any more, is removed when a request first
opens the book in the process, and never again in that process, so an upload of the process is never taken for it.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalBookDirectory
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import JobId, SourceId
from tests.harness.adapters.storage import upload
from tests.harness.builders.domain import directory_of, make_record
from tests.harness.builders.seeding import store_book
from tests.harness.fakes.jobs import FakeBoardProvider, JobFakesProvider

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    import httpx
    from dishka import Provider

    from bookreviver.app.settings import Settings
    from bookreviver.domain.accounts import Actor
    from bookreviver.domain.ids import BookId
    from bookreviver.ports.storage import SourceStore
    from tests.harness.builders.domain import BookOpener
    from tests.harness.fakes.jobs import JobFakes

pytestmark = pytest.mark.anyio

BOOKS_PATH: str = '/api/v1/books'
BOOK_PATH: str = '/api/v1/books/{book_id}'
UPLOAD_LIMIT_BYTES: int = 1024
UPLOAD_NAME: str = 'book.pdf'
PROMOTED_NAME: str = 'part.pdf'


class Uploads:
    """The uploads and the source the test left on disk before the application started.

    :ivar root: The storage root of the application.
    :ivar interrupted: The books with an upload an interrupted import left staged, each with the job it belonged to.
    :ivar kept: The book and the source whose files an earlier import had promoted.
    """

    def __init__(self, root: Path) -> None:
        """Start with nothing on disk.

        :param root: The storage root of the application.
        :type root: Path
        """
        self.root = root
        self.interrupted: list[tuple[BookId, JobId]] = []
        self.kept: tuple[BookId, SourceId] | None = None

    def sources(self, book_id: BookId) -> SourceStore:
        """Open the source store of a book over the storage root.

        :param book_id: The book.
        :type book_id: BookId
        :returns: The source store of the directory of the book.
        :rtype: SourceStore
        """
        return LocalBookDirectory(directory_of(book_id), root=self.root).sources

    async def leave(self, opener: BookOpener, actor: Actor) -> None:
        """Stage an upload in each of two listed books, and promote the files of a source in a third.

        :param opener: Opens the catalogue databases and the databases of the books of the test, which list the books.
        :type opener: BookOpener
        :param actor: Account that owns the books.
        :type actor: Actor
        """
        for _ in range(2):
            book_id = await store_book(
                opener.catalogue(), make_record(), owner=actor.account_id, databases=opener.databases
            )
            job_id = JobId(uuid4())
            await self.sources(book_id).stage(job_id, [upload(UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
            self.interrupted.append((book_id, job_id))
        book_id = await store_book(
            opener.catalogue(), make_record(), owner=actor.account_id, databases=opener.databases
        )
        job_id, source_id = JobId(uuid4()), SourceId(uuid4())
        await self.sources(book_id).stage(job_id, [upload(PROMOTED_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
        await self.sources(book_id).promote(job_id, source_id, names=[PROMOTED_NAME])
        self.kept = (book_id, source_id)


@pytest.fixture
async def fx_uploads(fx_settings: Settings, fx_open_book: BookOpener, fx_actor: Actor) -> Uploads:
    """Leave the uploads on disk, in books the library lists, before the application starts.

    :param fx_settings: Settings of the application, which name the storage root.
    :type fx_settings: Settings
    :param fx_open_book: Opens the catalogue databases and the databases of the books of the test.
    :type fx_open_book: BookOpener
    :param fx_actor: Account that owns the books.
    :type fx_actor: Actor
    :returns: The uploads left on disk.
    :rtype: Uploads
    """
    uploads = Uploads(fx_settings.storage_root)
    await uploads.leave(fx_open_book, fx_actor)
    return uploads


@pytest.fixture
def fx_extra_providers(fx_fakes: JobFakes, fx_uploads: Uploads) -> Sequence[Provider]:
    """Give the application the fakes and the fake board, once the uploads are in place.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :param fx_uploads: The uploads left on disk, which the fixture is ordered after.
    :type fx_uploads: Uploads
    :returns: The providers of the fakes and of the board.
    :rtype: Sequence[Provider]
    """
    del fx_uploads
    return (JobFakesProvider(fx_fakes), FakeBoardProvider(fx_fakes.board))


async def is_staged(sources: SourceStore, job_id: JobId) -> bool:
    """Tell whether the upload of a job is still staged in a book.

    :param sources: The source store of the book.
    :type sources: SourceStore
    :param job_id: The job the upload belongs to.
    :type job_id: JobId
    :returns: Whether the staged files of the job are there.
    :rtype: bool
    """
    try:
        async with sources.staged_files(job_id):
            return True
    except NotFoundError:
        return False


class TestStart:
    """Tests for what the lifespan of the application does before it serves a request."""

    @pytest.mark.usefixtures('fx_app')
    async def test_leaves_the_books_alone(self, fx_uploads: Uploads) -> None:
        """Verify the uploads an interrupted import left are still there when the application runs and no request came.

        :param fx_uploads: The uploads left on disk before the start.
        :type fx_uploads: Uploads
        """
        assert [await is_staged(fx_uploads.sources(book_id), job_id) for book_id, job_id in fx_uploads.interrupted] == [
            True,
            True,
        ]

    @pytest.mark.usefixtures('fx_app')
    async def test_queues_no_job(self, fx_fakes: JobFakes) -> None:
        """Verify nothing is put on the board by the start, so no job begins before the user asks for one.

        :param fx_fakes: Adapters of the test, whose board the application puts its jobs on.
        :type fx_fakes: JobFakes
        """
        expect(fx_fakes.board.put_jobs == [])
        expect(fx_fakes.events.published == [])
        assert_expectations()


class TestFirstOpening:
    """Tests for what the first opening of a book in the process removes from it."""

    async def test_removes_the_staged_upload_of_the_book_alone_and_keeps_the_sources(
        self, fx_client: httpx.AsyncClient, fx_uploads: Uploads
    ) -> None:
        """Verify the book opened loses its staged upload, another book keeps its own, and a promoted source stays.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uploads: The uploads left on disk before the start.
        :type fx_uploads: Uploads
        """
        (opened, opened_job), (other, other_job) = fx_uploads.interrupted
        assert fx_uploads.kept is not None
        kept_book, kept_source = fx_uploads.kept

        for book_id in (opened, kept_book):
            (await fx_client.get(BOOK_PATH.format(book_id=book_id))).raise_for_status()

        async with fx_uploads.sources(kept_book).source_files(kept_source) as files:
            kept = [file.name for file in files]
        expect(not await is_staged(fx_uploads.sources(opened), opened_job))
        expect(await is_staged(fx_uploads.sources(other), other_job))
        expect(kept == [PROMOTED_NAME])
        assert_expectations()

    async def test_an_upload_of_the_process_survives_the_next_opening(
        self, fx_client: httpx.AsyncClient, fx_uploads: Uploads
    ) -> None:
        """Verify an upload staged after the book was first opened is not removed when the book is opened again.

        The library goes between the two openings, which lets go of the book, as the user does when they leave it.

        :param fx_client: Client of the running application.
        :type fx_client: httpx.AsyncClient
        :param fx_uploads: The uploads left on disk before the start.
        :type fx_uploads: Uploads
        """
        book_id, _ = fx_uploads.interrupted[0]
        (await fx_client.get(BOOK_PATH.format(book_id=book_id))).raise_for_status()
        job_id = JobId(uuid4())
        await fx_uploads.sources(book_id).stage(job_id, [upload(UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)

        (await fx_client.get(BOOKS_PATH)).raise_for_status()
        (await fx_client.get(BOOK_PATH.format(book_id=book_id))).raise_for_status()

        assert await is_staged(fx_uploads.sources(book_id), job_id)
