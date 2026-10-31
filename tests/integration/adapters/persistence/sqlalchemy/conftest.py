"""Fixtures giving the SQLAlchemy adapter tests an owner and the import rig over the shared ``fx_database``.

The tests of the write blocks get a catalogue and books whose blocks are timed by a tracer that tells them when a block
asks for the writer, a second registry of books over the same files whose blocks give up almost at once, as another
process would, and a book to change.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve

from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.persistence.sqlalchemy.catalogue_database import SqlAlchemyCatalogueDatabase
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.domain.jobs import JobEnd, JobKind, JobState
from bookreviver.services.book import Book
from tests.harness.adapters.database import (
    BookSessions,
    LocatedBookDatabases,
    copy_schema_template,
    database_file,
    waiting,
)
from tests.harness.adapters.imports import DEFAULT_PARALLEL_SCANS, ImportRig
from tests.harness.adapters.tracers import AskingTracer
from tests.harness.builders.domain import make_record
from tests.harness.builders.seeding import commit_account, store_book
from tests.harness.concurrency.wait_limits import IMPATIENT_SECONDS
from tests.harness.fakes.jobs import JobFakes

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from pathlib import Path

    from bookreviver.app.settings import Settings
    from bookreviver.domain.ids import AccountId, BookDirectoryName, BookId
    from bookreviver.domain.jobs import Job
    from bookreviver.ports.persistence import CatalogueDatabase
    from bookreviver.ports.runtime import JobControl
    from tests.harness.builders.domain import BookOpener
    from tests.harness.fakes.jobs import RecordingEventBus

# Long enough that a block which wrongly waits for a lock that is never given fails the test, and does not hang it
TRACED_WAIT_SECONDS: float = 10.0
# How long a traced book stays open after its last use, longer than any test
TRACED_IDLE_SECONDS: float = 3600.0
STORAGE_DIRECTORY: str = 'storage'


@pytest.fixture
async def fx_owner_id(fx_database: SqlDatabase) -> AccountId:
    """Commit an account that may own books, which the owner key of the library requires.

    :param fx_database: Fresh catalogue with every table created.
    :type fx_database: SqlDatabase
    :returns: Identifier of the committed account.
    :rtype: AccountId
    """
    return await commit_account(fx_database)


@pytest.fixture
def fx_book_sessions(fx_books: BookRegistry) -> BookSessions:
    """Give the sessions of the files of the books of the test, for the tests that read and write rows directly.

    :param fx_books: Registry of the books of the test.
    :type fx_books: BookRegistry
    :returns: The sessions over the registry.
    :rtype: BookSessions
    """
    return BookSessions(fx_books)


@pytest.fixture
def fx_import_rig(fx_database: SqlDatabase, fx_open_book: BookOpener, tmp_path: Path) -> Callable[..., ImportRig]:
    """Give a function that builds the import rig over the database, whose board runs only the import jobs.

    The board of an ``ImportRig`` runs every job as an import. The rig this function builds cuts as many scans at once
    as a test asks for, and lets any job that is not an import end at once, since the split an import queues is not
    the subject of these tests.

    :param fx_database: Fresh SQLite database with every table created.
    :type fx_database: SqlDatabase
    :param fx_open_book: Opens the catalogue databases and the databases of the books of the test.
    :type fx_open_book: BookOpener
    :param tmp_path: Temporary directory of the test, which holds the storage root of the rig.
    :type tmp_path: Path
    :returns: Function taking the largest number of scans cut at once and returning the rig.
    :rtype: Callable[..., ImportRig]
    """

    def build(parallel_scans: int = DEFAULT_PARALLEL_SCANS) -> ImportRig:
        """Build the rig over a new storage root and the database.

        :param parallel_scans: Largest number of scans the import cuts at once.
        :type parallel_scans: int
        :returns: The rig, whose board runs an import on the database.
        :rtype: ImportRig
        """
        base = ImportRig.build(tmp_path / STORAGE_DIRECTORY, fx_database, fx_open_book)
        # The runner needs the service of the rig and the rig needs the runner, so the rig is found when a job runs
        built: list[ImportRig] = []

        async def run(dir_name: BookDirectoryName, job: Job, control: JobControl) -> JobEnd:
            """Run an import job on the database, and let any other job succeed at once.

            :param dir_name: Name of the directory of the book whose queue the job was taken from.
            :type dir_name: BookDirectoryName
            :param job: The job the board took from its queue.
            :type job: Job
            :param control: What the job reports through.
            :type control: JobControl
            :returns: How the job ended.
            :rtype: JobEnd
            """
            if job.kind is not JobKind.IMPORT_SOURCE:
                return JobEnd(state=JobState.SUCCEEDED)
            return await built[0].service(parallel_scans=parallel_scans).run_import(Book(dir_name), job, control)

        rig = evolve(base, fakes=JobFakes(events=base.events, runner=run))
        built.append(rig)
        return rig

    return build


@pytest.fixture
def fx_tracer() -> AskingTracer:
    """Build the tracer of the test, which every database of the test is given.

    :returns: A tracer with no span recorded.
    :rtype: AskingTracer
    """
    return AskingTracer()


@pytest.fixture
async def fx_traced_database(
    fx_settings: Settings, fx_schema_template: Path, fx_tracer: AskingTracer
) -> AsyncIterator[SqlDatabase]:
    """Open a catalogue over a fresh file, whose blocks are timed by the tracer of the test.

    :param fx_settings: Settings with a fresh data directory of the test.
    :type fx_settings: Settings
    :param fx_schema_template: File with every table of the catalogue at the head revision.
    :type fx_schema_template: Path
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the catalogue and disposing of its engine afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    await copy_schema_template(fx_schema_template, database_file(fx_settings))
    database = SqlDatabase(
        fx_settings.resolved_catalogue_url, schema=Schema.CATALOGUE, tracer=fx_tracer, wait_seconds=TRACED_WAIT_SECONDS
    )
    yield database
    await database.dispose()


@pytest.fixture
async def fx_open_books(
    fx_book_template: Path, fx_storage_root: Path, fx_tracer: AskingTracer
) -> AsyncIterator[Callable[[float], BookRegistry]]:
    """Give a function that opens a registry of the books of the test, with the busy timeout it is told.

    Each registry opens a database of its own over the file of a book, with a write queue of its own, so the blocks of
    two registries meet at the busy timeout of SQLite, as the blocks of two processes do.

    :param fx_book_template: The blank file every new book copies.
    :type fx_book_template: Path
    :param fx_storage_root: Storage root of the test, which holds the files of its books.
    :type fx_storage_root: Path
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: Iterator yielding the opening function and closing every registry it opened afterwards.
    :rtype: AsyncIterator[Callable[[float], BookRegistry]]
    """
    opened: list[BookRegistry] = []

    def open_books(wait_seconds: float) -> BookRegistry:
        """Open a registry of the books of the test.

        :param wait_seconds: The busy timeout every connection of a book opens with.
        :type wait_seconds: float
        :returns: The registry, which the fixture closes.
        :rtype: BookRegistry
        """
        opened.append(
            BookRegistry(
                root=fx_storage_root,
                tracer=fx_tracer,
                wait_seconds=wait_seconds,
                idle_seconds=TRACED_IDLE_SECONDS,
                blank=fx_book_template,
            )
        )
        return opened[-1]

    yield open_books
    for books in opened:
        await books.dispose()


@pytest.fixture
def fx_traced_books(fx_open_books: Callable[[float], BookRegistry]) -> BookRegistry:
    """Open the registry of the books whose blocks are timed by the tracer of the test and wait long.

    :param fx_open_books: Function that opens a registry of the books of the test.
    :type fx_open_books: Callable[[float], BookRegistry]
    :returns: The registry.
    :rtype: BookRegistry
    """
    return fx_open_books(TRACED_WAIT_SECONDS)


@pytest.fixture
def fx_impatient_books(fx_open_books: Callable[[float], BookRegistry]) -> BookRegistry:
    """Open a second registry over the books of the first, for the catalogue databases that give up for the lock at once.

    Its databases have write queues of their own, so its blocks meet those of the first registry at the busy timeout of
    SQLite, as the blocks of another process do, and meet each other in its queues. The limit of a block is that of its
    catalogue database, so a test that wants a refusal opens its catalogue database with ``IMPATIENT_SECONDS``.

    :param fx_open_books: Function that opens a registry of the books of the test.
    :type fx_open_books: Callable[[float], BookRegistry]
    :returns: The registry.
    :rtype: BookRegistry
    """
    return fx_open_books(IMPATIENT_SECONDS)


@pytest.fixture
def fx_traced_catalogue(fx_traced_database: SqlDatabase) -> Callable[..., CatalogueDatabase]:
    """Give a function opening a catalogue database over the traced catalogue, with the limit it is told.

    :param fx_traced_database: The traced catalogue.
    :type fx_traced_database: SqlDatabase
    :returns: Function taking an optional wait limit and returning a new catalogue database.
    :rtype: Callable[..., CatalogueDatabase]
    """

    def open_catalogue_database(*, wait_seconds: float = TRACED_WAIT_SECONDS) -> CatalogueDatabase:
        """Open a catalogue database over the traced catalogue.

        :param wait_seconds: How long a change waits for its writer.
        :type wait_seconds: float
        :returns: A new catalogue database.
        :rtype: CatalogueDatabase
        """
        return SqlAlchemyCatalogueDatabase(fx_traced_database, wait_seconds=wait_seconds)

    return open_catalogue_database


@pytest.fixture
def fx_traced_databases(
    fx_traced_books: BookRegistry, fx_storage_root: Path, fx_book_parts: RecordingEventBus
) -> Callable[..., LocatedBookDatabases]:
    """Give a function opening the databases of the traced books, with the limit it is told.

    :param fx_traced_books: The traced registry of the books.
    :type fx_traced_books: BookRegistry
    :param fx_storage_root: Storage root of the test, which holds the database files of its books.
    :type fx_storage_root: Path
    :param fx_book_parts: The bus and the job board registered as the parts of the books the test opens.
    :type fx_book_parts: RecordingEventBus
    :returns: Function taking an optional wait limit and returning a new opener of the databases of the books.
    :rtype: Callable[..., LocatedBookDatabases]
    """
    del fx_book_parts

    def open_databases(*, wait_seconds: float = TRACED_WAIT_SECONDS) -> LocatedBookDatabases:
        """Open the databases of the traced books.

        :param wait_seconds: How long a change waits for its writer.
        :type wait_seconds: float
        :returns: A new opener of the databases of the books.
        :rtype: LocatedBookDatabases
        """
        return LocatedBookDatabases(waiting(fx_traced_books, wait_seconds), root=fx_storage_root)

    return open_databases


@pytest.fixture
def fx_impatient_databases(
    fx_impatient_books: BookRegistry, fx_storage_root: Path, fx_book_parts: RecordingEventBus
) -> Callable[..., LocatedBookDatabases]:
    """Give a function opening the databases of the impatient registry of the books, with the limit it is told.

    :param fx_impatient_books: The registry over the same books whose connections give up for the lock at once.
    :type fx_impatient_books: BookRegistry
    :param fx_storage_root: Storage root of the test, which holds the database files of its books.
    :type fx_storage_root: Path
    :param fx_book_parts: The bus and the job board registered as the parts of the books the test opens.
    :type fx_book_parts: RecordingEventBus
    :returns: Function taking an optional wait limit and returning a new opener of the databases of the books.
    :rtype: Callable[..., LocatedBookDatabases]
    """
    del fx_book_parts

    def open_databases(*, wait_seconds: float = TRACED_WAIT_SECONDS) -> LocatedBookDatabases:
        """Open the databases of the impatient books.

        :param wait_seconds: How long a change waits for its writer.
        :type wait_seconds: float
        :returns: A new opener of the databases of the books.
        :rtype: LocatedBookDatabases
        """
        return LocatedBookDatabases(waiting(fx_impatient_books, wait_seconds), root=fx_storage_root)

    return open_databases


@pytest.fixture
async def fx_book(
    fx_traced_database: SqlDatabase,
    fx_traced_catalogue: Callable[..., CatalogueDatabase],
    fx_traced_databases: Callable[..., LocatedBookDatabases],
    fx_tracer: AskingTracer,
) -> BookId:
    """Commit an account and a book of it, which a ``Book.change`` block needs to find.

    The blocks that store the book are spans too, which are not what a test looks at, so they are forgotten.

    :param fx_traced_database: Catalogue to store the owner into.
    :type fx_traced_database: SqlDatabase
    :param fx_traced_catalogue: Function opening a catalogue database over the traced catalogue and books.
    :type fx_traced_catalogue: Callable[..., CatalogueDatabase]
    :param fx_traced_databases: Function opening the databases of the traced books.
    :type fx_traced_databases: Callable[..., LocatedBookDatabases]
    :param fx_tracer: Tracer of the test.
    :type fx_tracer: AskingTracer
    :returns: The book.
    :rtype: BookId
    """
    book_id = await store_book(
        fx_traced_catalogue(),
        make_record(),
        owner=await commit_account(fx_traced_database),
        databases=fx_traced_databases(),
    )
    fx_tracer.finished.clear()
    return book_id
