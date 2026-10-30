"""Fixtures shared by the whole suite."""

from typing import TYPE_CHECKING

import httpx
import pytest
from starlette.requests import Request

from bookreviver.adapters.persistence.sqlalchemy.book_database import SqlAlchemyBookDatabase
from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.persistence.sqlalchemy.catalogue_database import SqlAlchemyCatalogueDatabase
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.api.auth import signed_in_user
from bookreviver.app.main import create_app
from bookreviver.app.settings import AuthSettings, Settings
from bookreviver.domain.accounts import Actor
from bookreviver.ports.persistence import BookDatabase
from bookreviver.ports.runtime import BookJobs
from bookreviver.ports.storage import BookDirectory
from tests.helpers.builders import BookOpener, new_account_id
from tests.helpers.database import (
    LocatedBookDatabases,
    SharedDatabaseProvider,
    copy_schema_template,
    create_schema_template,
    database_file,
    waiting,
)
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fakes_accounts import SignedInUser
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.processing import InProcessExecutorProvider
from tests.helpers.scripted_runner import ScriptedRunner
from tests.helpers.seeding import commit_account
from tests.helpers.storage import SharedBookDirectories

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator, Sequence
    from pathlib import Path
    from typing import Any

    from dishka import Provider
    from fastapi import FastAPI
    from httpx_oauth.oauth2 import BaseOAuth2

    from bookreviver.ports.persistence import CatalogueDatabase
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory

TEST_BASE_URL: str = 'http://testserver'
TEST_SECRET: str = 'test-secret-that-is-long-enough-for-signing'
# How long a book of a test stays open after its last use, longer than any test, so no book closes under a test that
# does not ask for it
TEST_BOOK_IDLE_SECONDS: float = 3600.0


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio, the loop the server uses.

    :returns: Name of the anyio backend.
    :rtype: str
    """
    return 'asyncio'


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
def fx_settings(tmp_path: Path) -> Settings:
    """Build settings with a fresh data directory, whose SQLite file the test creates from the schema template.

    :param tmp_path: Temporary directory of the test, holding the data directory.
    :type tmp_path: Path
    :returns: Settings with a test secret, cookies allowed over plain HTTP and no built frontend, so a build on the
              developer's machine never changes what a test sees.
    :rtype: Settings
    """
    return Settings(
        data_dir=tmp_path / 'data',
        frontend_dir=tmp_path / 'no-frontend',
        auth=AuthSettings(secret=TEST_SECRET, cookie_secure=False),
    )


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
def fx_events(fx_book_parts: RecordingEventBus) -> RecordingEventBus:
    """Return the recording event bus of the test, which records every event of every book.

    :param fx_book_parts: The bus registered for the test.
    :type fx_book_parts: RecordingEventBus
    :returns: The bus every service, job and book database of the test publishes to.
    :rtype: RecordingEventBus
    """
    return fx_book_parts


@pytest.fixture
def fx_book_changes(fx_book_parts: RecordingEventBus) -> RecordingEventBus:
    """Return the bus the changes of the books that the test announces are recorded on.

    :param fx_book_parts: The bus registered for the test.
    :type fx_book_parts: RecordingEventBus
    :returns: The bus every book database of the test publishes to, which is the bus of the services too.
    :rtype: RecordingEventBus
    """
    return fx_book_parts


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
def fx_storage_root(fx_settings: Settings) -> Path:
    """Return the storage root of the test, which holds the directory of every book as the application's does.

    :param fx_settings: Settings of the test, which name the storage root.
    :type fx_settings: Settings
    :returns: Directory the stores, the archive and the books of the test write under, created by the first write.
    :rtype: Path
    """
    return fx_settings.storage_root


@pytest.fixture
def fx_request_context() -> dict[Any, Any]:
    """Build the context dishka's FastAPI integration puts into the scope of every HTTP request.

    :returns: The context holding a ``Request``, which a scope opened with it takes the limit of a request from.
    :rtype: dict[Any, Any]
    """
    return {Request: Request({'type': 'http'})}


@pytest.fixture
def fx_actor() -> Actor:
    """Build the account the API tests act as.

    :returns: Actor with a fresh account identifier.
    :rtype: Actor
    """
    return Actor(account_id=new_account_id())


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
def fx_extra_providers() -> Sequence[Provider]:
    """Return providers that override the application's own; a test module overrides this fixture with fakes.

    :returns: No providers, so the application's own adapters are used.
    :rtype: Sequence[Provider]
    """
    return ()


@pytest.fixture
def fx_social_clients() -> Sequence[BaseOAuth2[Any]] | None:
    """Return the social sign-in clients to offer in place of the configured ones; a test module overrides this.

    :returns: ``None``, so the providers whose credentials are in the settings are offered.
    :rtype: Sequence[BaseOAuth2[Any]] | None
    """
    return None


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


@pytest.fixture
async def fx_app(
    fx_settings: Settings,
    fx_shared_database: SharedDatabaseProvider,
    fx_actor: Actor,
    fx_extra_providers: Sequence[Provider],
    fx_social_clients: Sequence[BaseOAuth2[Any]] | None,
) -> AsyncIterator[FastAPI]:
    """Run the application lifespan with ``fx_actor`` signed in and ``fx_extra_providers`` applied.

    The application runs on ``fx_database`` and ``fx_books`` themselves, the catalogue storing the account of the actor,
    so a test that opens a catalogue database with ``fx_catalogue`` and the requests of the application share the write queue of
    every file. The steps run in threads
    instead of the pool of processes the lifespan would start, unless a provider of the test replaces the executor
    itself.

    :param fx_settings: Settings with a fresh data directory.
    :type fx_settings: Settings
    :param fx_shared_database: The catalogue and the books of the test, which the application is given in place of its
                               own.
    :type fx_shared_database: SharedDatabaseProvider
    :param fx_actor: Account every request acts as.
    :type fx_actor: Actor
    :param fx_extra_providers: Providers overriding the application's own.
    :type fx_extra_providers: Sequence[Provider]
    :param fx_social_clients: Social sign-in clients offered in place of the configured ones, or ``None``.
    :type fx_social_clients: Sequence[BaseOAuth2[Any]] | None
    :returns: Iterator yielding the running application and shutting it down afterwards.
    :rtype: AsyncIterator[FastAPI]
    """
    providers = [fx_shared_database, InProcessExecutorProvider(), *fx_extra_providers]
    app = create_app(fx_settings, providers, fx_social_clients)
    # The sign-in of fastapi-users stays reachable for a test that signs out, and the stub is the account of the actor
    app.state.current_user = app.dependency_overrides[signed_in_user]
    user = SignedInUser(id=fx_actor.account_id)
    app.dependency_overrides[signed_in_user] = lambda: user
    async with app.router.lifespan_context(app):
        yield app


@pytest.fixture
async def fx_client(fx_app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    """Open an HTTP client talking to the application in-process.

    An error that leaves the application fails the request, as it would close the connection of a real server after its
    answer, so a route that answers an expected error by a problem but lets the error go on fails its test.

    :param fx_app: The running application.
    :type fx_app: FastAPI
    :returns: Iterator yielding the client and closing it afterwards.
    :rtype: AsyncIterator[httpx.AsyncClient]
    """
    transport = httpx.ASGITransport(app=fx_app)
    async with httpx.AsyncClient(transport=transport, base_url=TEST_BASE_URL) as client:
        yield client
