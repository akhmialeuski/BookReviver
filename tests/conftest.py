"""Fixtures shared by the whole suite."""

from typing import TYPE_CHECKING

import httpx
import pytest
from starlette.requests import Request

from bookreviver.adapters.persistence.sqlalchemy.book_database import SqlAlchemyBookDatabases
from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.api.auth import current_actor
from bookreviver.app.main import create_app
from bookreviver.app.settings import AuthSettings, Settings
from bookreviver.domain.entities import Actor
from tests.helpers.builders import new_account_id
from tests.helpers.database import SharedDatabaseProvider, copy_schema_template, create_schema_template, database_file
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.processing import InProcessExecutorProvider
from tests.helpers.seeding import commit_account

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path
    from typing import Any

    from dishka import Provider
    from fastapi import FastAPI
    from httpx_oauth.oauth2 import BaseOAuth2

    from bookreviver.ports.persistence import BookDatabases, UnitOfWork
    from tests.helpers.database import BookDatabasesFactory, UnitOfWorkFactory

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
def fx_book_changes() -> RecordingEventBus:
    """Record the changes of the books the units of work of the test announce, apart from the events of the services.

    :returns: The bus every unit of work of the test publishes to.
    :rtype: RecordingEventBus
    """
    return RecordingEventBus()


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
        fx_settings.storage_root,
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
def fx_uow(
    fx_database: SqlDatabase, fx_books: BookRegistry, fx_settings: Settings, fx_book_changes: RecordingEventBus
) -> UnitOfWorkFactory:
    """Return a function opening a new unit of work over the catalogue and the books of the test, as a request would get.

    :param fx_database: Catalogue of the test.
    :type fx_database: SqlDatabase
    :param fx_books: Registry of the books of the test.
    :type fx_books: BookRegistry
    :param fx_settings: Settings of the test, whose job limit is the wait of a unit of work opened with none.
    :type fx_settings: Settings
    :param fx_book_changes: Where the units of work announce the committed changes of the books.
    :type fx_book_changes: RecordingEventBus
    :returns: Function opening a unit of work over ``fx_database`` and ``fx_books``.
    :rtype: UnitOfWorkFactory
    """

    def open_unit_of_work(*, wait_seconds: float | None = None) -> UnitOfWork:
        """Open a unit of work over the catalogue and the books of the test.

        :param wait_seconds: How long a change waits for its turn, or None for the job limit of the settings.
        :type wait_seconds: float | None
        :returns: A new unit of work.
        :rtype: UnitOfWork
        """
        seconds = fx_settings.change_wait.job_seconds if wait_seconds is None else wait_seconds
        return SqlAlchemyUnitOfWork(fx_database, fx_books, publisher=fx_book_changes, wait_seconds=seconds)

    return open_unit_of_work


@pytest.fixture
def fx_databases(
    fx_books: BookRegistry, fx_settings: Settings, fx_book_changes: RecordingEventBus
) -> BookDatabasesFactory:
    """Return a function opening a new opener of the databases of the books of the test, as a request would get.

    :param fx_books: Registry of the books of the test.
    :type fx_books: BookRegistry
    :param fx_settings: Settings of the test, whose job limit is the wait of an opener made with none.
    :type fx_settings: Settings
    :param fx_book_changes: Where the book databases announce the committed changes of the books.
    :type fx_book_changes: RecordingEventBus
    :returns: Function opening the databases of ``fx_books``.
    :rtype: BookDatabasesFactory
    """

    def open_databases(*, wait_seconds: float | None = None) -> BookDatabases:
        """Open the databases of the books of the test.

        :param wait_seconds: How long a change waits for its turn, or None for the job limit of the settings.
        :type wait_seconds: float | None
        :returns: A new opener of the databases of the books.
        :rtype: BookDatabases
        """
        seconds = fx_settings.change_wait.job_seconds if wait_seconds is None else wait_seconds
        return SqlAlchemyBookDatabases(fx_books, publisher=fx_book_changes, wait_seconds=seconds)

    return open_databases


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
def fx_shared_database(fx_database: SqlDatabase, fx_books: BookRegistry) -> SharedDatabaseProvider:
    """Hand the application the catalogue and the books of the test in place of its own.

    :param fx_database: Catalogue of the test.
    :type fx_database: SqlDatabase
    :param fx_books: Registry of the books of the test.
    :type fx_books: BookRegistry
    :returns: The provider overriding the catalogue and the registry of the books of the application.
    :rtype: SharedDatabaseProvider
    """
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
    so a test that opens a unit of work with ``fx_uow`` and the requests of the application share the write queue of
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
    app.dependency_overrides[current_actor] = lambda: fx_actor
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
