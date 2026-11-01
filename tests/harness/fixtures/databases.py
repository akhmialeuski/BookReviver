"""The SQLite catalogue and books of a test, copied from schema templates, and the parts that every book opens."""

from typing import TYPE_CHECKING

import pytest

from bookreviver.adapters.persistence.sqlalchemy.book_database import SqlAlchemyBookDatabase
from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.persistence.sqlalchemy.catalogue_database import SqlAlchemyCatalogueDatabase
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.ports.persistence import BookDatabase
from bookreviver.ports.runtime import BookJobs
from bookreviver.ports.storage import BookDirectory
from tests.harness.adapters.database import (
    LocatedBookDatabases,
    SharedDatabaseProvider,
    copy_schema_template,
    create_schema_template,
    database_file,
    waiting,
)
from tests.harness.adapters.storage import SharedBookDirectories
from tests.harness.builders.domain import BookOpener
from tests.harness.builders.seeding import commit_account
from tests.harness.concurrency.scripted_runner import ScriptedRunner
from tests.harness.fakes.board import FakeJobBoard
from tests.harness.fakes.jobs import RecordingEventBus

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator
    from pathlib import Path

    from bookreviver.app.settings import Settings
    from bookreviver.domain.accounts import Actor
    from bookreviver.ports.persistence import CatalogueDatabase
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory


# How long a book of a test stays open after its last use, longer than any test, so no book closes under a test that
# does not ask for it
TEST_BOOK_IDLE_SECONDS: float = 3600.0


@pytest.fixture(scope='session')
def fx_schema_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the catalogue with every table at the head revision, which each test copies instead of building it.

    Every worker process of a parallel run builds its own once, since a session lasts as long as the process does.

    :param tmp_path_factory: Factory of the temporary directories that outlive one test.
    :type tmp_path_factory: pytest.TempPathFactory
    :returns: The file the catalogues of the tests are copied from.
    :rtype: Path
    """
    template = tmp_path_factory.mktemp('schema') / 'catalogue.db'
    create_schema_template(template, Schema.CATALOGUE)
    return template


@pytest.fixture(scope='session')
def fx_book_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the database of a book with every table at the head revision, which every new book of a test copies.

    :param tmp_path_factory: Factory of the temporary directories that outlive one test.
    :type tmp_path_factory: pytest.TempPathFactory
    :returns: The blank file of a book.
    :rtype: Path
    """
    template = tmp_path_factory.mktemp('schema') / 'book.db'
    create_schema_template(template, Schema.BOOK)
    return template


@pytest.fixture
def fx_book_parts(fx_directories: SharedBookDirectories, fx_books: BookRegistry) -> Iterator[RecordingEventBus]:
    """Register the parts every book of the test opens: its directory, its database, its events and its job queue.

    A book opens its parts by the name of its directory, so every test has them registered. The directory is a local
    one under the storage root of the test, and the database is the SQLAlchemy one over the registry of the test. The
    event bus records what is published, and the job board runs nothing. A test that builds a part of its own registers
    that one in place of the one registered here. Every test registers fresh parts at its start, so none is given back.

    :param fx_directories: The directories of the books of the test, which every book opens its directory from.
    :type fx_directories: SharedBookDirectories
    :param fx_books: Registry of the books of the test, which every book opens its database over.
    :type fx_books: BookRegistry
    :returns: The recording bus that is the event channel of every book of the test.
    :rtype: Iterator[RecordingEventBus]
    """
    BookDirectory.register(fx_directories.open)
    BookDatabase.register(lambda dir_name: SqlAlchemyBookDatabase(dir_name, books=fx_books))
    with RecordingEventBus().installed() as bus:
        BookJobs.register(FakeJobBoard(runner=ScriptedRunner()).open)
        yield bus


@pytest.fixture
def fx_directories(fx_storage_root: Path) -> SharedBookDirectories:
    """Return the directories of the books of the test, with their database files, source stores and asset stores.

    :param fx_storage_root: Storage root of the test, which holds the directories of its books.
    :type fx_storage_root: Path
    :returns: The opener of the local directories of the books under the storage root, one directory per book.
    :rtype: SharedBookDirectories
    """
    return SharedBookDirectories(root=fx_storage_root)


@pytest.fixture
async def fx_database(fx_settings: Settings, fx_schema_template: Path, fx_actor: Actor) -> AsyncIterator[SqlDatabase]:
    """Open the catalogue of the test, a copy of the schema template that stores the account of ``fx_actor``.

    The running application of ``fx_app`` is given this very object, so a test and the application have one write
    queue and one pool, and this fixture alone disposes of it.

    :param fx_settings: Settings naming the file of the database.
    :type fx_settings: Settings
    :param fx_schema_template: File with every table at the head revision.
    :type fx_schema_template: Path
    :param fx_actor: Actor whose account owns the books the test stores.
    :type fx_actor: Actor
    :returns: Iterator yielding the database, with the job wait limit of the settings, and disposing of it afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    await copy_schema_template(fx_schema_template, database_file(fx_settings))
    database = SqlDatabase(
        fx_settings.resolved_catalogue_url,
        schema=Schema.CATALOGUE,
        tracer=NullTracer(),
        wait_seconds=fx_settings.change_wait.job_seconds,
    )
    try:
        await commit_account(database, account_id=fx_actor.account_id)
        yield database
    finally:
        await database.dispose()


@pytest.fixture
async def fx_books(fx_settings: Settings, fx_book_template: Path) -> AsyncIterator[BookRegistry]:
    """Open the registry of the books of the test, under the storage root of the settings, and close its books after.

    :param fx_settings: Settings naming the storage root.
    :type fx_settings: Settings
    :param fx_book_template: The blank file every new book copies.
    :type fx_book_template: Path
    :returns: Iterator yielding the registry and closing every book it opened afterwards.
    :rtype: AsyncIterator[BookRegistry]
    """
    books = BookRegistry(
        root=fx_settings.storage_root,
        tracer=NullTracer(),
        wait_seconds=fx_settings.change_wait.job_seconds,
        idle_seconds=TEST_BOOK_IDLE_SECONDS,
        blank=fx_book_template,
    )
    try:
        yield books
    finally:
        await books.dispose()


@pytest.fixture
def fx_catalogue(fx_database: SqlDatabase, fx_settings: Settings) -> CatalogueDatabaseFactory:
    """Return a function opening a new catalogue database over the catalogue of the test, as a request would get.

    :param fx_database: Catalogue of the test.
    :type fx_database: SqlDatabase
    :param fx_settings: Settings of the test, whose job limit is the wait of a catalogue database opened with none.
    :type fx_settings: Settings
    :returns: Function opening a catalogue database over ``fx_database``.
    :rtype: CatalogueDatabaseFactory
    """

    def open_catalogue_database(*, wait_seconds: float | None = None) -> CatalogueDatabase:
        """Open a catalogue database over the catalogue of the test.

        :param wait_seconds: How long a change waits for its turn, or None for the job limit of the settings.
        :type wait_seconds: float | None
        :returns: A new catalogue database.
        :rtype: CatalogueDatabase
        """
        seconds = fx_settings.change_wait.job_seconds if wait_seconds is None else wait_seconds
        return SqlAlchemyCatalogueDatabase(fx_database, wait_seconds=seconds)

    return open_catalogue_database


@pytest.fixture
def fx_databases(
    fx_books: BookRegistry, fx_storage_root: Path, fx_settings: Settings, fx_book_parts: RecordingEventBus
) -> BookDatabasesFactory:
    """Return a function opening a new opener of the databases of the books of the test, as a request would get.

    :param fx_books: Registry of the books of the test.
    :type fx_books: BookRegistry
    :param fx_storage_root: Storage root of the test, which holds the database files of its books.
    :type fx_storage_root: Path
    :param fx_settings: Settings of the test, whose job limit is the wait of an opener made with none.
    :type fx_settings: Settings
    :param fx_book_parts: The parts registered for the books the test opens, which a database reaches the event channel
                          of its book through and a book is created in the directory of.
    :type fx_book_parts: RecordingEventBus
    :returns: Function opening the databases of ``fx_books``.
    :rtype: BookDatabasesFactory
    """
    del fx_book_parts

    def open_databases(*, wait_seconds: float | None = None) -> LocatedBookDatabases:
        """Open the databases of the books of the test.

        :param wait_seconds: How long a change waits for its turn, or None for the job limit of the settings.
        :type wait_seconds: float | None
        :returns: A new opener of the databases of the books.
        :rtype: LocatedBookDatabases
        """
        seconds = fx_settings.change_wait.job_seconds if wait_seconds is None else wait_seconds
        return LocatedBookDatabases(waiting(fx_books, seconds), root=fx_storage_root)

    return open_databases


@pytest.fixture
def fx_open_book(
    fx_catalogue: CatalogueDatabaseFactory, fx_databases: BookDatabasesFactory, fx_directories: SharedBookDirectories
) -> BookOpener:
    """Return the opener of the books of the test, which opens a book of an account with its database.

    :param fx_catalogue: Function opening a new catalogue database over the catalogue and the books of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param fx_databases: Function opening a new opener of the databases of the books of the test.
    :type fx_databases: BookDatabasesFactory
    :param fx_directories: Opens the source store and the asset store of a book of the test.
    :type fx_directories: SharedBookDirectories
    :returns: The opener, called with the actor and the identifier of the book.
    :rtype: BookOpener
    """
    return BookOpener(catalogue=fx_catalogue, databases=fx_databases(), files=fx_directories)


@pytest.fixture
def fx_shared_database(
    fx_database: SqlDatabase, fx_books: BookRegistry, fx_book_parts: RecordingEventBus
) -> SharedDatabaseProvider:
    """Hand the application the catalogue and the books of the test in place of its own.

    :param fx_database: Catalogue of the test.
    :type fx_database: SqlDatabase
    :param fx_books: Registry of the books of the test.
    :type fx_books: BookRegistry
    :param fx_book_parts: The bus and the job board registered as the parts of the books the application or the
                          container opens, which a running application registers its own over.
    :type fx_book_parts: RecordingEventBus
    :returns: The provider overriding the catalogue and the registry of the books of the application.
    :rtype: SharedDatabaseProvider
    """
    del fx_book_parts
    return SharedDatabaseProvider(fx_database, fx_books)
