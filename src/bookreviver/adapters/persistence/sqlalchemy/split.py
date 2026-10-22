"""The one pass that splits the shared database of the earlier releases into the catalogue and a file for each book.

Before the catalogue and the files of the books, every account and every book lived in one SQLite file,
``bookreviver.db`` in the data directory, at revision ``15e015051f5c`` of its chain. :class:`SharedDatabaseSplit`
reads that file and never writes to it. It fills an empty catalogue at its head revision with the accounts, the recipe
profiles and one library entry for each book, whose time of joining the library is the time the book was created, and
makes the file of each book in the directory the book already has under the storage root, filled with the rows of the
book. A row moves with the columns the new table still has, so the owner of a book and the account of a place, which
no table of a book keeps any more, stay behind.

The file of a book is filled at the baseline revision of its schema, ``99e96bc47c3c``, whose tables are those of the
shared database without the accounts, and then brought to the head, so the rows go through every later revision as the
file of a book of that time would: a label typed by hand becomes a manual label, for one. The new file is made at the
head like any new book, taken down to the baseline, filled and taken up again. The tables the copy writes are read from
the file itself, never from the tables of the code, which describe the head.

Each file is filled through SQLite's ``ATTACH``: the shared file is attached read only to a connection of the new file,
and one ``INSERT ... SELECT`` for each table copies the rows that belong to the book. Which rows those are follows from
the keys of the tables of a book at the baseline: a table with a ``project_id`` names its book, and any other table
reaches it through the key to a table already reached, as the versions of a page reach the book through the page. The
rows are written in one transaction with foreign keys off, as the migrations run, and ``PRAGMA foreign_key_check`` runs
before the commit, so a file is written whole or not at all.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import TYPE_CHECKING, Final
from uuid import UUID

from anyio import to_thread
from attrs import frozen
from sqlalchemy import MetaData, create_engine
from sqlalchemy.engine import make_url

from bookreviver.adapters.persistence.sqlalchemy.accounts import USER_TABLE
from bookreviver.adapters.persistence.sqlalchemy.catalogue import LIBRARY_TABLE
from bookreviver.adapters.persistence.sqlalchemy.database import Schema
from bookreviver.adapters.persistence.sqlalchemy.tables import PROJECTS_TABLE
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.ids import ProjectId

if TYPE_CHECKING:
    from sqlalchemy import Table

    from bookreviver.adapters.persistence.sqlalchemy.books import BookDatabases
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase

# The last revision of the shared database, the only one the split reads
SHARED_REVISION: Final = '15e015051f5c'
# The revision of the schema of a book whose tables hold the rows of the shared database as they were
BOOK_BASELINE: Final = '99e96bc47c3c'
# The column of the tables of a book at the baseline that names the book
BOOK_COLUMN: Final = 'project_id'
# The name the shared file is attached under on the connection of a new file
SHARED: Final = 'shared'
# The name SQLite gives the database of the connection itself
MAIN: Final = 'main'
# The parameter of the statements that holds the identifier of the book being copied, as the 16 bytes of the column
BOOK_PARAMETER: Final = 'book'
# The query parameters of a file URI that open the file read only, or for reading and writing without creating it
READ_ONLY: Final = '?mode=ro'
READ_WRITE: Final = '?mode=rw'
# The statement that begins the transaction of a copy and takes the write lock at once
BEGIN_COPY: Final = 'BEGIN IMMEDIATE'
# What stands between two names in a list of a statement or a message
LIST_SEPARATOR: Final = ', '


class SplitRefusedError(RuntimeError):
    """The split cannot start, because the shared database or the catalogue is not in the state it needs."""


@frozen(kw_only=True)
class SplitReport:
    """What a split moved.

    :ivar accounts: How many accounts the catalogue received.
    :ivar books: How many books got a file of their own.
    """

    accounts: int
    books: int


class SharedDatabaseSplit:
    """Moves the rows of the shared database into the catalogue and the files of the books."""

    def __init__(self, shared: Path, *, catalogue: SqlDatabase, books: BookDatabases) -> None:
        """Split ``shared`` into the given catalogue and books.

        :param shared: The shared database file of the earlier releases.
        :type shared: Path
        :param catalogue: The database of the catalogue, which must hold no account yet.
        :type catalogue: SqlDatabase
        :param books: The registry of the databases of the books, which makes their files.
        :type books: BookDatabases
        """
        self._shared = shared
        self._catalogue = catalogue
        self._books = books

    async def run(self) -> SplitReport:
        """Fill the catalogue, then make and fill the file of every book.

        :returns: How many accounts and books were moved.
        :rtype: SplitReport
        :raises SplitRefusedError: If the shared database is missing or at another revision, or the catalogue holds an
            account or a book already, or a book has a file already.
        """
        await to_thread.run_sync(self._require_shared)
        await to_thread.run_sync(self._catalogue.migrations.upgrade)
        catalogue = self._catalogue_path()
        accounts = await to_thread.run_sync(self._fill_catalogue, catalogue)
        project_ids = await to_thread.run_sync(self._project_ids)
        for project_id in project_ids:
            try:
                await self._books.create(project_id)
            except ConflictError as error:
                err_msg = f'The book {project_id} has a database already, so the split would overwrite it.'
                raise SplitRefusedError(err_msg) from error
            async with self._books.use(project_id) as database:
                await to_thread.run_sync(database.migrations.downgrade, BOOK_BASELINE)
                await database.dispose()
                versions = database.config.alembic_config.version_table_name
                await to_thread.run_sync(self._fill_book, self._books.path(project_id), project_id, versions)
                await to_thread.run_sync(database.migrations.upgrade)
        return SplitReport(accounts=accounts, books=len(project_ids))

    def _require_shared(self) -> None:
        """Refuse a shared database that is missing or at a revision other than the last of its chain.

        :raises SplitRefusedError: If so.
        """
        if not self._shared.is_file():
            err_msg = f'There is no shared database at {self._shared} to split.'
            raise SplitRefusedError(err_msg)
        with closing(sqlite3.connect(f'{self._shared.as_uri()}{READ_ONLY}', uri=True)) as connection:
            revisions = [row[0] for row in connection.execute('SELECT version_num FROM alembic_versions')]
        if revisions != [SHARED_REVISION]:
            err_msg = (
                f'The shared database is at revision {LIST_SEPARATOR.join(revisions) or "none"}, and the split reads '
                f'{SHARED_REVISION}: upgrade it with the release before the split first.'
            )
            raise SplitRefusedError(err_msg)

    def _catalogue_path(self) -> Path:
        """Return the file of the catalogue.

        :returns: The file the URL of the catalogue names.
        :rtype: Path
        :raises SplitRefusedError: If the URL of the catalogue names no file.
        """
        database = make_url(self._catalogue.config.connection_string or '').database
        if not database:
            err_msg = 'The catalogue has no file to fill.'
            raise SplitRefusedError(err_msg)
        return Path(database).resolve()

    def _project_ids(self) -> list[ProjectId]:
        """Read the identifiers of the books of the shared database.

        :returns: The identifiers, in order.
        :rtype: list[ProjectId]
        """
        with closing(sqlite3.connect(f'{self._shared.as_uri()}{READ_ONLY}', uri=True)) as connection:
            rows = connection.execute(f'SELECT id FROM {_table(MAIN, PROJECTS_TABLE)} ORDER BY id').fetchall()
        return [ProjectId(UUID(bytes=row[0])) for row in rows]

    def _fill_catalogue(self, catalogue: Path) -> int:
        """Copy the accounts and the recipe profiles into the catalogue, and give every book its library entry.

        :param catalogue: The file of the catalogue, at its head revision.
        :type catalogue: Path
        :returns: How many accounts were copied.
        :rtype: int
        :raises SplitRefusedError: If the catalogue holds an account or a book already.
        """
        metadata = Schema.CATALOGUE.metadata
        with closing(self._attached(catalogue)) as connection:
            for table in metadata.sorted_tables:
                if _count(connection, table.name):
                    err_msg = f'The catalogue holds rows of {table.name} already, so the split would mix two of them.'
                    raise SplitRefusedError(err_msg)
            connection.execute(BEGIN_COPY)
            for table in metadata.sorted_tables:
                if table.name != LIBRARY_TABLE:
                    _copy(connection, table, condition=None)
            # The entry of a book is its owner, and the book joined the library when it was created
            _insert(
                connection,
                LIBRARY_TABLE,
                'project_id, owner_id, added_at',
                _select('id, owner_id, created_at', PROJECTS_TABLE, condition=None),
            )
            _commit_checked(connection)
            return _count(connection, USER_TABLE)

    def _fill_book(self, target: Path, project_id: ProjectId, versions: str) -> None:
        """Copy the rows of one book into its new file, at the baseline revision of the schema of a book.

        :param target: The new file of the book, empty and at the baseline.
        :type target: Path
        :param project_id: The book.
        :type project_id: ProjectId
        :param versions: Name of the version table of the file, which the copy leaves alone.
        :type versions: str
        """
        metadata = _tables_of(target, leaving_out=versions)
        conditions = book_conditions(metadata)
        with closing(self._attached(target)) as connection:
            connection.execute(BEGIN_COPY)
            for table in metadata.sorted_tables:
                _copy(connection, table, condition=conditions[table.name], book=project_id)
            _commit_checked(connection)

    def _attached(self, target: Path) -> sqlite3.Connection:
        """Open a connection of a new file with the shared file attached to it read only, and foreign keys off.

        :param target: The new file.
        :type target: Path
        :returns: The connection, in autocommit mode, so the statements decide where a transaction begins.
        :rtype: sqlite3.Connection
        """
        connection = sqlite3.connect(f'{target.as_uri()}{READ_WRITE}', uri=True, isolation_level=None)
        connection.execute('PRAGMA foreign_keys=OFF')
        connection.execute(f"ATTACH DATABASE '{self._shared.as_uri()}{READ_ONLY}' AS {SHARED}")
        return connection


def book_conditions(metadata: MetaData) -> dict[str, str]:
    """Say for every table of a book which rows of the shared database belong to the book being copied.

    :param metadata: The tables of a book at the baseline, read from its file.
    :type metadata: MetaData
    :returns: For each table, the condition of a ``WHERE`` over the shared table, with the ``book`` parameter.
    :rtype: dict[str, str]
    :raises RuntimeError: If a table neither names its book nor has a key to a table that does.
    """
    conditions = {PROJECTS_TABLE: f'id = :{BOOK_PARAMETER}'}
    for table in metadata.sorted_tables:
        if table.name in conditions:
            continue
        if BOOK_COLUMN in table.c:
            conditions[table.name] = f'{BOOK_COLUMN} = :{BOOK_PARAMETER}'
            continue
        parent = next((key for key in table.foreign_keys if key.column.table.name in conditions), None)
        if parent is None:
            err_msg = f'The table {table.name} has no key that leads to its book.'
            raise RuntimeError(err_msg)
        reached = parent.column.table
        subquery = _select(f'"{parent.column.name}"', reached.name, condition=conditions[reached.name])
        conditions[table.name] = f'"{parent.parent.name}" IN ({subquery})'
    return conditions


def _tables_of(path: Path, *, leaving_out: str) -> MetaData:
    """Read the tables of a database file as they are, with their keys.

    :param path: The file.
    :type path: Path
    :param leaving_out: Name of a table not to read, such as the version table.
    :type leaving_out: str
    :returns: The tables, sorted by their keys.
    :rtype: MetaData
    """
    engine = create_engine(f'sqlite:///{path}')
    try:
        metadata = MetaData()
        metadata.reflect(engine, only=lambda name, _metadata: name != leaving_out)
    finally:
        engine.dispose()
    return metadata


def _copy(
    connection: sqlite3.Connection, table: Table, *, condition: str | None, book: ProjectId | None = None
) -> None:
    """Copy the rows of one table from the shared file, with the columns both tables have.

    :param connection: Connection of the new file with the shared file attached.
    :type connection: sqlite3.Connection
    :param table: The table of the new file.
    :type table: Table
    :param condition: Which rows of the shared table to copy, or None for every row.
    :type condition: str | None
    :param book: The book the condition names, or None.
    :type book: ProjectId | None
    """
    shared_columns = {row[1] for row in connection.execute(f'PRAGMA {SHARED}.table_info("{table.name}")')}
    columns = LIST_SEPARATOR.join(f'"{column.name}"' for column in table.columns if column.name in shared_columns)
    parameters = {} if book is None else {BOOK_PARAMETER: book.bytes}
    _insert(connection, table.name, columns, _select(columns, table.name, condition=condition), parameters)


def _select(columns: str, source: str, *, condition: str | None) -> str:
    """Write the query of rows of a table of the shared file.

    :param columns: The columns or expressions to select, separated by commas.
    :type columns: str
    :param source: Name of the table of the shared file.
    :type source: str
    :param condition: Which rows to select, or None for every row.
    :type condition: str | None
    :returns: The query.
    :rtype: str
    """
    where = '' if condition is None else f' WHERE {condition}'
    return f'SELECT {columns} FROM {_table(SHARED, source)}{where}'


def _insert(
    connection: sqlite3.Connection,
    target: str,
    columns: str,
    select: str,
    parameters: dict[str, bytes] | None = None,
) -> None:
    """Insert the rows of a query of the shared file into a table of the new file.

    :param connection: Connection of the new file with the shared file attached.
    :type connection: sqlite3.Connection
    :param target: Name of the table of the new file.
    :type target: str
    :param columns: The columns of the table that the query fills, separated by commas, in the order of the query.
    :type columns: str
    :param select: The query, from :func:`_select`.
    :type select: str
    :param parameters: Values of the parameters of the query, or None for none.
    :type parameters: dict[str, bytes] | None
    """
    connection.execute(f'INSERT INTO {_table(MAIN, target)} ({columns}) {select}', parameters or {})


def _count(connection: sqlite3.Connection, name: str) -> int:
    """Count the rows of a table of the new file.

    :param connection: Connection of the new file.
    :type connection: sqlite3.Connection
    :param name: Name of the table.
    :type name: str
    :returns: The number of rows.
    :rtype: int
    """
    (count,) = connection.execute(f'SELECT count(*) FROM {_table(MAIN, name)}').fetchone()
    return int(count)


def _commit_checked(connection: sqlite3.Connection) -> None:
    """Commit the copy when no row points nowhere, and roll it back otherwise.

    :param connection: Connection whose transaction holds the copy.
    :type connection: sqlite3.Connection
    :raises RuntimeError: If a copied row has a foreign key to no row, after the rollback.
    """
    if violations := connection.execute('PRAGMA main.foreign_key_check').fetchall():
        connection.execute('ROLLBACK')
        err_msg = f'The copy left rows with a foreign key to no row, as (table, rowid, parent, key): {violations}.'
        raise RuntimeError(err_msg)
    connection.execute('COMMIT')


def _table(database: str, name: str) -> str:
    """Name a table of one of the databases of a connection in a statement.

    :param database: ``main`` or the name the shared file is attached under.
    :type database: str
    :param name: Name of the table.
    :type name: str
    :returns: The qualified and quoted name.
    :rtype: str
    """
    return f'{database}."{name}"'
