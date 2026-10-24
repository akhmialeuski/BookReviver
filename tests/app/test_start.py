"""Tests for the start of the application: it begins no job, and removes what no job or request can use any more."""

from typing import TYPE_CHECKING, NamedTuple
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import BookId, JobId, SourceId
from tests.helpers.builders import make_record
from tests.helpers.fakes_jobs import FakeBoardProvider, JobFakes, JobFakesProvider
from tests.helpers.seeding import store_book
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider

    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Actor, BookRecord
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

UPLOAD_LIMIT_BYTES: int = 1024
UPLOAD_NAME: str = 'book.pdf'
PROMOTED_NAME: str = 'part.pdf'


class Uploads:
    """The uploads and the source the test left on disk before the application started.

    :ivar store: Source store over the storage root of the application.
    :ivar interrupted: The books with an upload an interrupted import left staged, each with the job it belonged to.
    :ivar kept: The book and the source whose files an earlier import had promoted.
    """

    def __init__(self, store: LocalSourceStore) -> None:
        """Start with nothing on disk.

        :param store: Source store over the storage root of the application.
        :type store: LocalSourceStore
        """
        self.store = store
        self.interrupted: list[tuple[BookId, JobId]] = []
        self.kept: tuple[BookId, SourceId] | None = None

    async def leave(self) -> None:
        """Stage an upload in each of two books, and promote the files of a source in a third."""
        for _ in range(2):
            book_id, job_id = BookId(uuid4()), JobId(uuid4())
            await self.store.stage(book_id, job_id, [upload(UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
            self.interrupted.append((book_id, job_id))
        book_id, job_id, source_id = BookId(uuid4()), JobId(uuid4()), SourceId(uuid4())
        await self.store.stage(book_id, job_id, [upload(PROMOTED_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
        await self.store.promote(book_id, job_id, source_id, names=[PROMOTED_NAME])
        self.kept = (book_id, source_id)


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the application runs on, with an empty database and a board that runs nothing.

    :returns: Fakes with nothing stored and no job on the board.
    :rtype: JobFakes
    """
    return JobFakes()


@pytest.fixture
async def fx_uploads(fx_settings: Settings) -> Uploads:
    """Leave the uploads on disk before the application starts.

    :param fx_settings: Settings of the application, which name the storage root.
    :type fx_settings: Settings
    :returns: The uploads left on disk.
    :rtype: Uploads
    """
    uploads = Uploads(LocalSourceStore(root=fx_settings.storage_root))
    await uploads.leave()
    return uploads


class HalfWay(NamedTuple):
    """The books a creation and a deletion left broken off half way.

    :ivar created: A book that has a store and no entry in the library.
    :ivar deleted: A book that has an entry in the library and no store.
    """

    created: BookRecord
    deleted: BookRecord


@pytest.fixture
async def fx_half_way(fx_uow: UnitOfWorkFactory, fx_actor: Actor, fx_databases: BookDatabasesFactory) -> HalfWay:
    """Leave a creation and a deletion broken off half way before the application starts.

    :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
    :type fx_uow: UnitOfWorkFactory
    :param fx_actor: Account that owns the book of the deletion.
    :type fx_actor: Actor
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The two books.
    :rtype: HalfWay
    """
    created, deleted = make_record(), make_record()
    await fx_uow().create_book(created)
    await store_book(fx_uow(), deleted, owner=fx_actor.account_id, databases=fx_databases())
    await fx_uow().delete_book(deleted.id)
    return HalfWay(created=created, deleted=deleted)


@pytest.fixture
def fx_extra_providers(fx_fakes: JobFakes, fx_uploads: Uploads, fx_half_way: HalfWay) -> Sequence[Provider]:
    """Give the application the fakes and the fake board, once the uploads and the broken books are in place.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :param fx_uploads: The uploads left on disk, which the fixture is ordered after.
    :type fx_uploads: Uploads
    :param fx_half_way: The books left broken off half way, which the fixture is ordered after.
    :type fx_half_way: HalfWay
    :returns: The providers of the fakes and of the board.
    :rtype: Sequence[Provider]
    """
    del fx_uploads, fx_half_way
    return (JobFakesProvider(fx_fakes), FakeBoardProvider(fx_fakes.board))


class TestStart:
    """Tests for what the lifespan of the application does before it serves a request."""

    @pytest.mark.usefixtures('fx_app')
    async def test_removes_the_staged_uploads_and_keeps_the_sources(self, fx_uploads: Uploads) -> None:
        """Verify the upload an interrupted import left in each book is gone, and a promoted source stays.

        The running application is only started, and no request is made, so the removal can only come from the start.

        :param fx_uploads: The uploads left on disk before the start.
        :type fx_uploads: Uploads
        """
        gone: list[bool] = []
        for book_id, job_id in fx_uploads.interrupted:
            try:
                async with fx_uploads.store.staged_files(book_id, job_id):
                    gone.append(False)
            except NotFoundError:
                gone.append(True)
        assert fx_uploads.kept is not None
        async with fx_uploads.store.source_files(*fx_uploads.kept) as files:
            kept = [file.name for file in files]
        expect(gone == [True, True])
        expect(kept == [PROMOTED_NAME])
        assert_expectations()

    @pytest.mark.usefixtures('fx_app')
    async def test_finishes_the_creation_and_the_deletion_that_were_broken_off(
        self, fx_uow: UnitOfWorkFactory, fx_half_way: HalfWay
    ) -> None:
        """Verify the book without an entry loses its store, and the entry without a book loses its entry.

        The running application is only started, and no request is made, so the sweep can only come from the start.

        :param fx_uow: Function opening a new unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_half_way: The books left broken off half way before the start.
        :type fx_half_way: HalfWay
        """
        stored = await fx_uow().stored_books()
        entries = await fx_uow().read(lambda repos: repos.library.list_all())
        expect(fx_half_way.created.id not in stored)
        expect(fx_half_way.deleted.id not in {entry.book_id for entry in entries})
        assert_expectations()

    @pytest.mark.usefixtures('fx_app')
    async def test_queues_no_job(self, fx_fakes: JobFakes) -> None:
        """Verify nothing is put on the board by the start, so no job begins before the user asks for one.

        :param fx_fakes: Adapters of the test, whose board the application puts its jobs on.
        :type fx_fakes: JobFakes
        """
        expect(fx_fakes.board.put_jobs == [])
        expect(fx_fakes.events.published == [])
        assert_expectations()
