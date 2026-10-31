"""The SQLite databases of a test: a template of each schema built once per session, copied for every database.

The application refuses a catalogue that is not at the head revision, and a book is migrated when it is opened, so a
test cannot start from an empty file without paying for the revisions. Creating the tables from the models and
recording the head revision, as ``alembic stamp head`` does, takes about a second, and copying a file that already holds
them takes a couple of milliseconds. So ``create_schema_template`` builds the template of a schema once for each test
process, ``copy_schema_template`` puts a copy of the catalogue where the settings of a test name it, the registry of the
books of a test copies the template of a book for every new book, and ``test_migrations.py`` proves once that the
migrations build the same schemas as the models.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, override

import anyio
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from attrs import frozen
from dishka import Provider, Scope, provide
from sqlalchemy.engine import make_url

from bookreviver.adapters.persistence.sqlalchemy.book_database import SqlAlchemyBookDatabase
from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.domain.keys import KeySegment
from bookreviver.domain.spans import BlockKind
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS
from bookreviver.ports.storage import BookDirectory
from tests.helpers.builders import directory_of

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import Schema
    from bookreviver.app.settings import Settings
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookDirectoryName, BookId
    from bookreviver.ports.persistence import CatalogueDatabase

NO_DATABASE_FILE: str = (
    'The settings name a catalogue without a file, so there is nowhere to copy the schema template to.'
)


class CatalogueDatabaseFactory(Protocol):
    """A function opening a new catalogue database over the database of the test."""

    def __call__(self, *, wait_seconds: float | None = None) -> CatalogueDatabase:
        """Open a catalogue database over the database of the test.

        :param wait_seconds: How long a change of the catalogue database waits for its turn before it gives up with
                             ``BookBusyError``, which a request gets shorter than a job. None for the limit of a job
                             in the settings of the test.
        :type wait_seconds: float | None
        :returns: A new catalogue database.
        :rtype: CatalogueDatabase
        """


class WaitingBookRegistry(BookRegistry):
    """A registry of the books that shares the open books of another one, and whose changes wait for a limit of its own.

    The book database takes its wait limit from the registry it opens over, as the application's one does, so a test
    that needs another limit over the same books and the same write queues opens its databases over this view.
    """

    def __init__(self, books: BookRegistry, *, wait_seconds: float) -> None:
        """Wrap a registry with another wait limit for the changes of its books.

        :param books: The registry whose open books, write queues and blank file are used.
        :type books: BookRegistry
        :param wait_seconds: How long a change waits for the writer of its book.
        :type wait_seconds: float
        """
        self._books = books
        self._wait_seconds = wait_seconds

    @override
    @asynccontextmanager
    async def use(self, dir_name: BookDirectoryName) -> AsyncIterator[SqlDatabase]:
        """Hold the database of a book of the wrapped registry open for the caller.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        :returns: Iterator yielding the open database of the book once.
        :rtype: AsyncIterator[SqlDatabase]
        """
        async with self._books.use(dir_name) as database:
            yield database

    @override
    async def close(self, dir_name: BookDirectoryName) -> None:
        """Close the database of a book of the wrapped registry for good.

        :param dir_name: Name of the directory of the book.
        :type dir_name: BookDirectoryName
        """
        await self._books.close(dir_name)

    @override
    async def copy_blank(self, dir_name: BookDirectoryName) -> None:
        """Copy the blank file of the wrapped registry into the directory of a book.

        :param dir_name: Name of the directory of the book, which must exist.
        :type dir_name: BookDirectoryName
        """
        await self._books.copy_blank(dir_name)


def waiting(books: BookRegistry, wait_seconds: float) -> BookRegistry:
    """Return the registry itself when its wait limit is the one asked for, and a view with that limit otherwise.

    :param books: The registry of the books of the test.
    :type books: BookRegistry
    :param wait_seconds: How long a change of a book waits for its writer.
    :type wait_seconds: float
    :returns: A registry whose wait limit is ``wait_seconds``, over the books of ``books``.
    :rtype: BookRegistry
    """
    return books if books.wait_seconds == wait_seconds else WaitingBookRegistry(books, wait_seconds=wait_seconds)


class LocatedBookDatabases:
    """The databases of the books of a test, each opened over a registry of the test.

    A book opens its own database through the opener the application registers, with the wait limit of its registry. A
    test that needs another limit, or another registry over the same files, opens the database of a book here instead,
    over a registry with that limit.

    :ivar books: The registry of the databases of the books of the test.
    :ivar root: The storage root of the test, which holds the directories of the books and their database files.
    """

    def __init__(self, books: BookRegistry, *, root: Path) -> None:
        """Open the databases of the books of a test over a registry.

        :param books: The registry of the databases of the books of the test.
        :type books: BookRegistry
        :param root: The storage root of the test.
        :type root: Path
        """
        self.books = books
        self.root = root

    def open(self, book_id: BookId) -> SqlAlchemyBookDatabase:
        """Return the database of a book, from the file of its directory.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :returns: The database of the book.
        :rtype: SqlAlchemyBookDatabase
        """
        return SqlAlchemyBookDatabase(directory_of(book_id), books=self.books)

    def path(self, book_id: BookId) -> Path:
        """Name the database file of a book, which may not exist yet.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        :returns: The path of ``book.db`` in the directory of the book.
        :rtype: Path
        """
        return self.root / KeySegment.BOOKS / directory_of(book_id) / KeySegment.DATABASE

    async def create_book(self, book_id: BookId, book_record: BookRecord) -> None:
        """Make the directory of a new book and its database with the record, as a creation does.

        :param book_id: Identifier of the new book.
        :type book_id: BookId
        :param book_record: The record of the book.
        :type book_record: BookRecord
        :raises ConflictError: If the book has a database already.
        """
        await BookDirectory.open(directory_of(book_id)).create()
        await self.open(book_id).create(book_record)

    async def delete_book(self, book_id: BookId) -> None:
        """Close the database of a book once its transactions have ended, and delete its directory, as a deletion does.

        :param book_id: Identifier of the book.
        :type book_id: BookId
        """
        await self.open(book_id).close()
        await BookDirectory.open(directory_of(book_id)).delete()


class BookDatabasesFactory(Protocol):
    """A function opening a new opener of the databases of the books of the test."""

    def __call__(self, *, wait_seconds: float | None = None) -> LocatedBookDatabases:
        """Open the databases of the books of the test.

        :param wait_seconds: How long a change of a book waits for its turn before it gives up with
                             ``BookBusyError``, which a request gets shorter than a job. None for the limit of a job
                             in the settings of the test.
        :type wait_seconds: float | None
        :returns: A new opener of the databases of the books.
        :rtype: LocatedBookDatabases
        """


class SharedDatabaseProvider(Provider):
    """Hands the application the catalogue and the books the test opened, so both have one write queue of each file.

    The application would open a second database over the same file, with a write queue of its own, and a change of
    the test and a change of the application would meet at the lock of SQLite instead of in one queue. The test owns
    the catalogue and the registry of the books, so the container neither opens nor disposes of them.
    """

    scope = Scope.APP

    def __init__(self, database: SqlDatabase, books: BookRegistry) -> None:
        """Provide ``database`` and ``books`` in place of the ones the application would open.

        :param database: Catalogue of the test.
        :type database: SqlDatabase
        :param books: Registry of the books of the test.
        :type books: BookRegistry
        """
        super().__init__()
        self._database = database
        self._books = books

    @provide(override=True)
    def database(self) -> SqlDatabase:
        """Return the catalogue of the test.

        :returns: The catalogue every request and job of the application runs on.
        :rtype: SqlDatabase
        """
        return self._database

    @provide(override=True)
    def books(self) -> BookRegistry:
        """Return the registry of the books of the test.

        :returns: The registry every request and job of the application opens books through.
        :rtype: BookRegistry
        """
        return self._books


def create_schema_template(path: Path, schema: Schema) -> None:
    """Create every table of a schema in a new SQLite file, and record the file at the head revision of the schema.

    The template is built once per test process, before any event loop runs, so both engines are disposed of here
    directly. ``SqlDatabase.dispose`` is the same disposal moved to a worker thread to keep a running loop free.

    :param path: Where the file is created, which must not exist yet.
    :type path: Path
    :param schema: The schema whose tables the file holds.
    :type schema: Schema
    """
    database = SqlDatabase(f'sqlite:///{path}', schema=schema, tracer=NullTracer())
    script = ScriptDirectory.from_config(database.migrations.config)
    options = {'version_table': database.config.alembic_config.version_table_name}
    try:
        with database.engine.begin() as connection:
            schema.metadata.create_all(connection)
            MigrationContext.configure(connection, opts=options).stamp(script, 'head')
    finally:
        database.engine.dispose()
        database.read_engine.dispose()


def database_file(settings: Settings) -> Path:
    """Name the SQLite file of the catalogue the settings of a test point the application at.

    :param settings: Settings naming the catalogue.
    :type settings: Settings
    :returns: The path of the file, which may not exist yet.
    :rtype: Path
    :raises ValueError: If the settings name a catalogue without a file.
    """
    if (file_name := make_url(settings.resolved_catalogue_url).database) is None:
        raise ValueError(NO_DATABASE_FILE)
    return Path(file_name)


async def copy_schema_template(template: Path, target: Path) -> None:
    """Copy the schema template to the place a test opens its database at, and create the directory that holds it.

    :param template: File ``create_schema_template`` made.
    :type template: Path
    :param target: Where the database of the test is to be, such as ``database_file(fx_settings)``.
    :type target: Path
    """
    await anyio.Path(target.parent).mkdir(parents=True, exist_ok=True)
    await anyio.Path(template).copy(target)


@frozen
class BookSessions:
    """Runs a function over a session of the file of a book, for a test that reads or writes rows with plain SQL.

    :ivar books: Registry of the books of the test.
    """

    books: BookRegistry

    async def read[T](self, book_id: BookId, work: Callable[[Session], T]) -> T:
        """Run ``work`` on a transaction of the book that only reads.

        :param book_id: The book.
        :type book_id: BookId
        :param work: Function over the session.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        """
        async with self.books.use(directory_of(book_id)) as database:
            return await database.read(work)

    async def write[T](self, book_id: BookId, work: Callable[[Session], T]) -> T:
        """Run ``work`` on a transaction of the book that writes, and commit it.

        :param book_id: The book.
        :type book_id: BookId
        :param work: Function over the session.
        :type work: Callable[[Session], T]
        :returns: What the work returns.
        :rtype: T
        """
        async with self.books.use(directory_of(book_id)) as database:
            return await database.write(
                work, kind=BlockKind.BOOK, book=directory_of(book_id), wait_seconds=DEFAULT_CHANGE_WAIT_SECONDS
            )
