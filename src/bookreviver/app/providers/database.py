"""Provider of the catalogue, of the registry of the databases of the books and of the catalogue database over it.

The catalogue and the registry of the books are one per application, and the catalogue database over the catalogue is
built per request or job. The same catalogue database is the ``CatalogueDatabase`` of the port and the one the accounts
run on, so the accounts add no second connection pool and no second catalogue database. The registry is also where a
book opens its database, so building it registers the opener of ``BookDatabase``, which ``prepare_start`` does before
the first request. A book waits for its writer as long as a job does, since the opener knows no scope.

The application never changes the schema of the catalogue. Its migrations are applied by hand, after a copy of the data
directory if it matters, and the provider only compares the revision of the catalogue with the head of the migrations
the code ships. A mismatch stops the start with the command that fixes it, because running against another schema
would fail on the first query of a changed column or, worse, write rows the code does not expect. A catalogue behind
the code is upgraded. A catalogue at a revision the code does not ship, migrated on another branch, cannot be: Alembic
does not know that revision, so the message names the downgrade to run with the code that ships it, or the copy to
restore. A book is brought to the head revision of its schema by the registry when it is opened.
"""

from collections.abc import AsyncIterator
from functools import partial

from dishka import Provider, Scope, alias, provide
from sqlalchemy.orm import configure_mappers

from bookreviver.adapters.persistence.sqlalchemy.book_database import SqlAlchemyBookDatabase
from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
from bookreviver.adapters.persistence.sqlalchemy.catalogue_database import SqlAlchemyCatalogueDatabase
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.app.settings import Settings
from bookreviver.ports.persistence import BookDatabase, CatalogueDatabase, ChangeWaitSeconds
from bookreviver.ports.tracing import Tracer

# The command applying the migrations to the database the settings name, a script of pyproject.toml
MIGRATE_COMMAND: str = 'uv run bookreviver-migrate upgrade head'
# The command returning a database to a revision of this code, run with the code that ships the database's revision
MIGRATE_DOWNGRADE_COMMAND: str = 'uv run bookreviver-migrate downgrade'
# How the message names a database no revision was ever applied to
NO_REVISION: str = 'none'
# What stands between the revisions of a database that is at several of them
REVISION_SEPARATOR: str = ', '
# The start of a message about the schema, which names the revision of the database, and what stands between the problem
# and the command that fixes it, up to the opening backtick
SCHEMA_AT: str = 'Database schema is at revision'
RUN: str = ': run `'


class DatabaseProvider(Provider):
    """One catalogue and one registry of books per application, and one catalogue database per request.

    The catalogue is at its head revision.

    :ivar catalogue_database_port: The catalogue database of the request as the ``CatalogueDatabase`` port.
    """

    @provide(scope=Scope.APP)
    async def database(self, settings: Settings, tracer: Tracer) -> AsyncIterator[SqlDatabase]:
        """Open the configured catalogue, check its schema revision, and close it with the application.

        The mappers of the tables are configured before the database is handed out. SQLAlchemy does that on the first
        use of the ORM otherwise, and the 239 attribute registrations it makes cost 10 to 29 ms on the event loop of
        the first request, while the server answers nothing else and sends no event.

        :param settings: Application settings, of which the URL of the catalogue, the data directory and the wait limit
                         of a job are read, which every connection opens with as its busy timeout.
        :type settings: Settings
        :param tracer: Tracer the write blocks are timed with.
        :type tracer: Tracer
        :returns: Iterator yielding the open catalogue and disposing of its engine afterwards.
        :rtype: AsyncIterator[SqlDatabase]
        :raises RuntimeError: When the catalogue is not at the head revision of the migrations, naming the upgrade to
            run, or the downgrade when the catalogue is at a revision the code does not ship.
        """
        database = SqlDatabase(
            settings.resolved_catalogue_url,
            schema=Schema.CATALOGUE,
            tracer=tracer,
            wait_seconds=settings.change_wait.job_seconds,
        )
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        # SQLAlchemy configures the mappers lazily, on the first use of the ORM, which took 10 to 29 ms on the event
        # loop of the first request. Every table is declared by the imports of this module, so they are configured
        # here instead, before the application serves anything.
        configure_mappers()
        try:
            revisions = await database.schema_revisions()
            at = REVISION_SEPARATOR.join(revisions.database) or NO_REVISION
            expected = REVISION_SEPARATOR.join(revisions.code)
            # upgrade head cannot move a database from a revision it cannot locate, so that case names the way back
            if unknown := REVISION_SEPARATOR.join(revisions.unknown):
                err_msg = (
                    f'{SCHEMA_AT} {at}, which this code does not ship, and code expects '
                    f'{expected}{RUN}{MIGRATE_DOWNGRADE_COMMAND} {expected}` with the code that ships {unknown}, '
                    f'or restore the copy of the data directory made before migrating'
                )
                raise RuntimeError(err_msg)
            if not revisions.is_current:
                err_msg = f'{SCHEMA_AT} {at}, code expects {expected}{RUN}{MIGRATE_COMMAND}`'
                raise RuntimeError(err_msg)
            yield database
        finally:
            await database.dispose()

    @provide(scope=Scope.APP)
    async def books(self, settings: Settings, tracer: Tracer) -> AsyncIterator[BookRegistry]:
        """Open the registry of the databases of the books, and register how a book opens its database over it.

        Every open book is closed, and the registration is given back, when the application closes the container, so
        the books of another application stay registered.

        :param settings: Application settings, of which the storage root, the wait limit of a job and the idle limit of
                         a book are read.
        :type settings: Settings
        :param tracer: Tracer the write blocks of the books are timed with.
        :type tracer: Tracer
        :returns: Iterator yielding the registry and closing its books afterwards.
        :rtype: AsyncIterator[BookRegistry]
        """
        books = BookRegistry(
            tracer=tracer,
            wait_seconds=settings.change_wait.job_seconds,
            idle_seconds=settings.book_idle_seconds,
        )
        previous = BookDatabase.register(
            partial(
                SqlAlchemyBookDatabase,
                root=settings.storage_root,
                books=books,
                wait_seconds=settings.change_wait.job_seconds,
            )
        )
        try:
            yield books
        finally:
            BookDatabase.register(previous)
            await books.dispose()

    @provide(scope=Scope.REQUEST)
    def catalogue_database(
        self,
        database: SqlDatabase,
        wait_seconds: ChangeWaitSeconds,
    ) -> SqlAlchemyCatalogueDatabase:
        """Build the catalogue database of a request or job, which keeps no state but the wait limit of its scope.

        The services and the account database both take this one. It holds no session or connection, so it needs no
        closing.

        :param database: The catalogue, whose threads and connections the transactions of the catalogue use.
        :type database: SqlDatabase
        :param wait_seconds: How long a change of the catalogue database waits for a writer, by the scope it lives in.
        :type wait_seconds: ChangeWaitSeconds
        :returns: Catalogue database over the catalogue.
        :rtype: SqlAlchemyCatalogueDatabase
        """
        return SqlAlchemyCatalogueDatabase(database, wait_seconds=wait_seconds)

    catalogue_database_port = alias(source=SqlAlchemyCatalogueDatabase, provides=CatalogueDatabase)
