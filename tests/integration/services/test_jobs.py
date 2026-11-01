"""Tests for the job service: reading and cancelling jobs on the board, and the event stream of a book."""

from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.domain.accounts import Actor
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.ids import BookId, JobId
from bookreviver.domain.jobs import JobKind, JobState
from bookreviver.domain.slices import SliceRequest
from bookreviver.services.jobs import JobService
from tests.harness.adapters.storage import upload
from tests.harness.builders.domain import (
    directory_of,
    make_book_change,
    make_job,
    make_record,
    new_account_id,
    new_book_id,
)
from tests.harness.builders.seeding import commit_record
from tests.harness.fakes.jobs import JobFakes

if TYPE_CHECKING:
    from collections.abc import Callable

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.services.book import Book
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.harness.adapters.storage import SharedBookDirectories
    from tests.harness.builders.domain import BookOpener
    from tests.harness.fakes.jobs import RecordingEventBus

# The tests open books of the library with the databases of the test, whether or not they store any
pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures('fx_databases')]

WINDOW: SliceRequest = SliceRequest(limit=50)
STAGED_UPLOAD_NAME: str = 'book.pdf'
UPLOAD_LIMIT_BYTES: int = 1024
UNKNOWN_JOB: JobId = JobId(new_account_id())


@pytest.fixture
def fx_fakes(fx_book_parts: RecordingEventBus) -> JobFakes:
    """Build the adapters the job service runs on.

    :param fx_book_parts: The recording bus registered as the event channel of every book of the test.
    :type fx_book_parts: RecordingEventBus
    :returns: Fakes with a board with no job and nothing published.
    :rtype: JobFakes
    """
    return JobFakes(events=fx_book_parts)


@pytest.fixture
def fx_job_service() -> JobService:
    """Build the job service of a request, whose books open the board and the event bus a test registered.

    :returns: The job service.
    :rtype: JobService
    """
    return JobService()


@pytest.fixture
def fx_owner() -> Actor:
    """Build the account that owns the book.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())


@pytest.fixture
async def fx_book(
    fx_database: SqlDatabase,
    fx_catalogue: CatalogueDatabaseFactory,
    fx_owner: Actor,
    fx_databases: BookDatabasesFactory,
) -> BookId:
    """Store a book of ``fx_owner``, and the account of the owner.

    :param fx_database: Database of the test.
    :type fx_database: SqlDatabase
    :param fx_catalogue: Function opening a new catalogue database over the database of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_owner: Account owning the book.
    :type fx_owner: Actor
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :returns: The stored book.
    :rtype: BookId
    """
    book_id = await commit_record(
        fx_database, fx_catalogue(), make_record(), owner=fx_owner.account_id, databases=fx_databases()
    )
    return book_id


@pytest.fixture
def fx_open_own_book(fx_owner: Actor, fx_open_book: BookOpener) -> Callable[[BookId], Book]:
    """Return a function opening a book of the acting account with its database.

    :param fx_owner: Account the books belong to.
    :type fx_owner: Actor
    :param fx_open_book: Opens the books of the test with their databases.
    :type fx_open_book: BookOpener
    :returns: Function taking the identifier of the book.
    :rtype: Callable[[BookId], Book]
    """
    return lambda book_id: fx_open_book(fx_owner, book_id)


class TestGet:
    """Tests for JobService.get()."""

    async def test_owner_reads_job(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_owner: Actor,
        fx_book: BookId,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify the owner of the book reads its job as the board holds it.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookId
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        job = fx_fakes.board.put(fx_book, make_job(kind=JobKind.RUN_STAGE))
        assert await fx_job_service.get(fx_open_book(fx_owner, fx_book), job.id) == job

    async def test_job_of_another_book_is_not_found(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_owner: Actor,
        fx_book: BookId,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a job is read only through the book it works on, so an open book tells nothing of other jobs.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_owner: Account owning the books.
        :type fx_owner: Actor
        :param fx_book: Book the job works on.
        :type fx_book: BookId
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        job = fx_fakes.board.put(fx_book, make_job())
        with pytest.raises(NotFoundError):
            await fx_job_service.get(fx_open_book(fx_owner, BookId(uuid4())), job.id)

    async def test_job_the_board_does_not_hold_is_not_found(
        self, fx_job_service: JobService, fx_owner: Actor, fx_open_book: BookOpener
    ) -> None:
        """Verify a job the process does not hold, such as one of before a restart, is reported as missing.

        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_owner: Account asking for the job.
        :type fx_owner: Actor
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        with pytest.raises(NotFoundError):
            await fx_job_service.get(fx_open_book(fx_owner, BookId(uuid4())), UNKNOWN_JOB)


class TestList:
    """Tests for JobService.list_for_book()."""

    async def test_lists_the_jobs_of_the_book_newest_first_and_the_active_ones_alone(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_owner: Actor,
        fx_book: BookId,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify the list holds the jobs of the book, newest first, and ``active`` leaves the ended ones out.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookId
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        ended = fx_fakes.board.put(fx_book, make_job(minutes=0))
        await fx_fakes.board.run_queued()
        queued = fx_fakes.board.put(fx_book, make_job(minutes=1))
        elsewhere = fx_fakes.board.put(new_book_id(), make_job(minutes=2))
        everything = await fx_job_service.list_for_book(fx_open_book(fx_owner, fx_book), WINDOW, active=False)
        active = await fx_job_service.list_for_book(fx_open_book(fx_owner, fx_book), WINDOW, active=True)
        expect([job.id for job in everything.items] == [queued.id, ended.id])
        expect(everything.total == len(everything.items))
        expect([job.id for job in active.items] == [queued.id])
        expect(elsewhere.id not in {job.id for job in everything.items})
        assert_expectations()


class TestCancel:
    """Tests for JobService.cancel()."""

    async def test_running_job_is_cancelled_and_stops_at_its_next_task(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_owner: Actor,
        fx_book: BookId,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify cancelling a running job answers at once, and the job ends cancelled once its task has returned.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookId
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        job = fx_fakes.board.put(fx_book, make_job(kind=JobKind.RUN_STAGE))
        release = fx_fakes.scripted.hold(job)
        running = cancelled = None
        async with anyio.create_task_group() as group:
            group.start_soon(fx_fakes.board.run_queued)
            await anyio.wait_all_tasks_blocked()
            running = fx_fakes.board.get(job.id).state
            cancelled = await fx_job_service.cancel(fx_open_book(fx_owner, fx_book), job.id)
            release.set()
        expect(running is JobState.RUNNING)
        expect(cancelled is not None and cancelled.state is JobState.CANCELLED)
        expect(fx_fakes.board.get(job.id).state is JobState.CANCELLED)
        expect(fx_fakes.board.get(job.id).progress.done == 0)
        assert_expectations()

    async def test_import_cancelled_before_it_began_loses_its_upload(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_directories: SharedBookDirectories,
        fx_book: BookId,
        fx_open_own_book: Callable[[BookId], Book],
    ) -> None:
        """Verify the upload of an import that never ran is removed, since nothing else would remove it.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_directories: Opens the stores of a book of the test, whose source store holds the uploads.
        :type fx_directories: SharedBookDirectories
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookId
        :param fx_open_own_book: Function opening a book of the acting account with its database.
        :type fx_open_own_book: Callable[[BookId], Book]
        """
        job = fx_fakes.board.put(fx_book, make_job(kind=JobKind.IMPORT_SOURCE))
        await fx_directories.sources(fx_book).stage(job.id, [upload(STAGED_UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
        await fx_job_service.cancel(fx_open_own_book(fx_book), job.id)
        with pytest.raises(NotFoundError):
            async with fx_directories.sources(fx_book).staged_files(job.id):
                pass

    async def test_import_cancelled_while_running_keeps_its_upload_for_its_own_run_to_remove(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_directories: SharedBookDirectories,
        fx_book: BookId,
        fx_open_own_book: Callable[[BookId], Book],
    ) -> None:
        """Verify the service leaves the upload of a running import alone, which its run still reads.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_directories: Opens the stores of a book of the test, whose source store holds the uploads.
        :type fx_directories: SharedBookDirectories
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookId
        :param fx_open_own_book: Function opening a book of the acting account with its database.
        :type fx_open_own_book: Callable[[BookId], Book]
        """
        job = fx_fakes.board.put(fx_book, make_job(kind=JobKind.IMPORT_SOURCE))
        await fx_directories.sources(fx_book).stage(job.id, [upload(STAGED_UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
        release = fx_fakes.scripted.hold(job)
        names: list[str] = []
        async with anyio.create_task_group() as group:
            group.start_soon(fx_fakes.board.run_queued)
            await anyio.wait_all_tasks_blocked()
            await fx_job_service.cancel(fx_open_own_book(fx_book), job.id)
            async with fx_directories.sources(fx_book).staged_files(job.id) as staged:
                names.extend(staged)
            release.set()
        expect(names == [STAGED_UPLOAD_NAME])

    async def test_finished_job_cannot_be_cancelled(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_owner: Actor,
        fx_book: BookId,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a finished job keeps its final state, nothing more is published, and the refusal is a conflict.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookId
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        job = fx_fakes.board.put(fx_book, make_job())
        await fx_fakes.board.run_queued()
        published = list(fx_fakes.events.published)
        with pytest.raises(ConflictError):
            await fx_job_service.cancel(fx_open_book(fx_owner, fx_book), job.id)
        expect(fx_fakes.board.get(job.id).state is JobState.SUCCEEDED)
        expect(fx_fakes.events.published == published)
        assert_expectations()


class TestEvents:
    """Tests for JobService.events()."""

    async def test_owner_receives_only_book_events(
        self,
        fx_fakes: JobFakes,
        fx_job_service: JobService,
        fx_owner: Actor,
        fx_book: BookId,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a subscription keeps its book's events published before the first read, and none of another.

        :param fx_fakes: Adapters of the service.
        :type fx_fakes: JobFakes
        :param fx_job_service: Job service of the request.
        :type fx_job_service: JobService
        :param fx_owner: Account owning the book.
        :type fx_owner: Actor
        :param fx_book: Book of ``fx_owner``.
        :type fx_book: BookId
        :param fx_open_book: Opens the books of the test with their databases.
        :type fx_open_book: BookOpener
        """
        other_id = new_book_id()
        async with await fx_job_service.events(fx_open_book(fx_owner, fx_book)) as stream:
            # Published before anything reads the stream, as while the response headers are still being sent
            await fx_fakes.events.open(directory_of(other_id)).publish(make_book_change(version=1))
            await fx_fakes.events.open(directory_of(fx_book)).publish(make_book_change(version=2))
            expect(await anext(stream) == make_book_change(version=2))
        expect(fx_fakes.events.open_subscriptions == 0)
        assert_expectations()
