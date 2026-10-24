"""Tests for the split of the shared database of the earlier releases into the catalogue and the files of the books.

The revisions of the shared database are gone from the code, so the tests build a shared database the way its last
revision left it: every table of the catalogue but the library and every table of a book, in one file, with the owner
of a book and the account of a place that the new tables no longer keep, and the version table at the last
revision. The tables come from the baselines of the catalogue and of a book, which have the tables of that revision
without the accounts of the books, and the rows are stored through the repositories of the adapter at the head and
carried down to the baselines by the downgrades, so they have the shape the application wrote.
"""

import shutil
import sqlite3
from contextlib import closing
from typing import TYPE_CHECKING

import pytest
from anyio import to_thread
from attrs import evolve, frozen
from delayed_assert import assert_expectations, expect
from sqlalchemy.orm import Session

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.catalogue_database import SqlAlchemyCatalogueDatabase
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.repositories import (
    SqlAlchemyBookRepositories,
    SqlAlchemyCatalogueRepositories,
)
from bookreviver.adapters.persistence.sqlalchemy.split import SHARED_REVISION, SharedDatabaseSplit, SplitRefusedError
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.domain.values import SliceRequest
from tests.helpers.builders import (
    directory_of,
    make_book_place,
    make_page,
    make_page_stage,
    make_page_version,
    make_recipe_profile,
    make_record,
    make_scan,
    make_source,
    new_account_id,
    new_book_id,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Collection, Sequence
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.ids import AccountId, BookId, PageId
    from tests.helpers.database import BookDatabasesFactory
    from tests.helpers.storage import SharedBookDirectories

pytestmark = pytest.mark.anyio

SHARED_FILE: str = 'bookreviver.db'
VERSION_TABLE: str = 'alembic_versions'
# A revision of the shared chain before its last one, which the split refuses
OLDER_REVISION: str = 'a6cd07915dcf'
PAGES_OF_FIRST: int = 2
# The baselines of the catalogue and of a book, whose tables are those of the shared database
CATALOGUE_BASELINE: str = '56d52a4a1656'
BOOK_BASELINE: str = '99e96bc47c3c'
# The directory of the files the shared database is gathered from, and the name each is attached under
PARTS: str = 'parts'
PART: str = 'part'
MANUAL_LABEL: str = 'xii'


@frozen(kw_only=True)
class SharedBooks:
    """A shared database of the earlier releases and the books and the owner it holds.

    :ivar path: The shared file.
    :ivar owner_id: The account owning every book.
    :ivar first_id: The identifier of the first book.
    :ivar first: The first book, with a source, a scan, pages, a version, a stage record and a place.
    :ivar second_id: The identifier of the second book.
    :ivar second: The second book, with one page.
    :ivar labelled: The page of the first book whose label was typed by hand.
    """

    path: Path
    owner_id: AccountId
    first_id: BookId
    first: BookRecord
    second_id: BookId
    second: BookRecord
    labelled: PageId


async def _write_at_baseline(path: Path, schema: Schema, baseline: str, store: Callable[[Session], None]) -> None:
    """Write rows into a new file through the tables of the code, at the head, and take the file down to a baseline.

    The tables of the code describe the head, and the downgrades carry the rows, so the file ends with the tables and
    the rows a file of that baseline had, whatever the tables of the code look like now. The file of a book lies in a
    directory named by its book, from which a downgrade takes the identifier of the book back.

    :param path: Where the file is made.
    :type path: Path
    :param schema: The schema of the file.
    :type schema: Schema
    :param baseline: The revision to take the file down to.
    :type baseline: str
    :param store: Work that writes the rows in one transaction.
    :type store: Callable[[Session], None]
    """
    database = SqlDatabase(f'sqlite:///{path}', schema=schema, tracer=NullTracer())
    try:
        await to_thread.run_sync(database.migrations.upgrade)
        with Session(database.engine) as session, session.begin():
            store(session)
        await database.dispose()
        await to_thread.run_sync(database.migrations.downgrade, baseline)
    finally:
        await database.dispose()


def _merge(target: Path, parts: Sequence[Path], *, leaving_out: Collection[str]) -> None:
    """Gather the tables and the rows of several files into one, each table made once from the first file that has it.

    :param target: The file the tables and rows are gathered in.
    :type target: Path
    :param parts: The files to gather, whose tables of one name have one definition.
    :type parts: Sequence[Path]
    :param leaving_out: Names of the tables not to gather, such as the version tables.
    :type leaving_out: Collection[str]
    """
    with closing(sqlite3.connect(target)) as connection:
        for part in parts:
            connection.execute(f"ATTACH DATABASE '{part}' AS {PART}")
            # The tables come before the indexes, which need them
            schema = connection.execute(
                f"SELECT type, name, tbl_name, sql FROM {PART}.sqlite_master WHERE sql IS NOT NULL ORDER BY type = 'index'"
            ).fetchall()
            made = {row[0] for row in connection.execute('SELECT name FROM main.sqlite_master')}
            for kind, name, table, sql in schema:
                if table in leaving_out or (kind == 'index' and name in made):
                    continue
                if name not in made:
                    connection.execute(sql)
                if kind == 'table':
                    connection.execute(f'INSERT INTO main."{name}" SELECT * FROM {PART}."{name}"')
            connection.commit()
            connection.execute(f'DETACH DATABASE {PART}')


async def _build_shared(directory: Path, *, revision: str) -> SharedBooks:
    """Write a shared database of the earlier releases with one account, one profile and two books.

    The tables come from the baselines of the catalogue and of a book, which the split was written against, and the
    rows from the repositories of the adapter, so they have the shape the application wrote.

    :param directory: The data directory, which holds the shared file and the parts it is gathered from.
    :type directory: Path
    :param revision: The revision its version table records.
    :type revision: str
    :returns: What the file stores.
    :rtype: SharedBooks
    """
    owner_id, first_id, second_id = new_account_id(), new_book_id(), new_book_id()
    first, second = make_record(title='First'), make_record(title='Second', minutes=1)
    source = make_source()
    scan = make_scan(source=source, number=0)
    pages = [make_page(order_key=f'a{index}', scan=scan if not index else None) for index in range(2)]
    pages[1] = evolve(pages[1], manual_label=MANUAL_LABEL)
    version = make_page_version(page_id=pages[0].id)
    parts = directory / PARTS
    parts.mkdir()

    def store_catalogue(session: Session) -> None:
        """Store the account and its profile.

        :param session: Session of the transaction.
        :type session: Session
        """
        session.add(
            AccountTable(
                id=owner_id,
                email='owner@example.org',
                hashed_password='',
                is_active=True,
                is_superuser=False,
                is_verified=True,
            )
        )
        session.flush()
        SqlAlchemyCatalogueRepositories(session).recipe_profiles.add(make_recipe_profile(account_id=owner_id))

    def store_first(session: Session) -> None:
        """Store the first book, with every kind of row the split copies.

        :param session: Session of the transaction.
        :type session: Session
        """
        repos = SqlAlchemyBookRepositories(session)
        repos.book_record.add(first)
        repos.sources.add(source)
        repos.scans.add(scan)
        repos.pages.add_many(pages)
        repos.page_versions.add(version)
        repos.page_stages.add(make_page_stage(page_id=pages[0].id, head_version_id=version.id))
        repos.book_places.save(make_book_place(page_id=pages[1].id))

    def store_second(session: Session) -> None:
        """Store the second book, with one page.

        :param session: Session of the transaction.
        :type session: Session
        """
        repos = SqlAlchemyBookRepositories(session)
        repos.book_record.add(second)
        repos.pages.add(make_page())

    files = [parts / 'catalogue.db', *(parts / str(book_id) / 'book.db' for book_id in (first_id, second_id))]
    for file in files:
        file.parent.mkdir(exist_ok=True)
    await _write_at_baseline(files[0], Schema.CATALOGUE, CATALOGUE_BASELINE, store_catalogue)
    await _write_at_baseline(files[1], Schema.BOOK, BOOK_BASELINE, store_first)
    await _write_at_baseline(files[2], Schema.BOOK, BOOK_BASELINE, store_second)
    path = directory / SHARED_FILE
    _merge(path, files, leaving_out={VERSION_TABLE, LibraryRow.__tablename__})
    shutil.rmtree(parts)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute('ALTER TABLE projects ADD COLUMN owner_id BLOB')
        connection.execute('ALTER TABLE book_places ADD COLUMN account_id BLOB')
        connection.execute('UPDATE projects SET owner_id = ?', (owner_id.bytes,))
        connection.execute('UPDATE book_places SET account_id = ?', (owner_id.bytes,))
        connection.execute(f'CREATE TABLE {VERSION_TABLE} (version_num VARCHAR(32) NOT NULL PRIMARY KEY)')
        connection.execute(f'INSERT INTO {VERSION_TABLE} VALUES (?)', (revision,))
        connection.commit()
    return SharedBooks(
        path=path,
        owner_id=owner_id,
        first_id=first_id,
        first=first,
        second_id=second_id,
        second=second,
        labelled=pages[1].id,
    )


@pytest.fixture
async def fx_empty_catalogue(fx_settings: Settings) -> AsyncIterator[SqlDatabase]:
    """Open the catalogue of the settings, a file that does not exist yet, which the split builds and fills.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: Iterator yielding the catalogue and disposing of its engines afterwards.
    :rtype: AsyncIterator[SqlDatabase]
    """
    fx_settings.data_dir.mkdir(parents=True, exist_ok=True)
    database = SqlDatabase(fx_settings.resolved_catalogue_url, schema=Schema.CATALOGUE, tracer=NullTracer())
    yield database
    await database.dispose()


@pytest.fixture
async def fx_shared(fx_settings: Settings) -> SharedBooks:
    """Write the shared database of the test at the last revision of its chain, in the data directory.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: What the shared database stores.
    :rtype: SharedBooks
    """
    fx_settings.data_dir.mkdir(parents=True, exist_ok=True)
    return await _build_shared(fx_settings.data_dir, revision=SHARED_REVISION)


class TestSplit:
    """Tests for a split of a shared database at its last revision."""

    async def test_accounts_profiles_and_owners_go_to_the_catalogue_and_each_book_to_its_file(
        self,
        fx_shared: SharedBooks,
        fx_empty_catalogue: SqlDatabase,
        fx_books: BookRegistry,
        fx_directories: SharedBookDirectories,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the catalogue lists the books of the owner and every book file holds its own rows and no other.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue the split builds.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test, which makes their files.
        :type fx_books: BookRegistry
        :param fx_directories: The directories of the books of the test, which the split makes.
        :type fx_directories: SharedBookDirectories
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        report = await SharedDatabaseSplit(
            fx_shared.path, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
        ).run()
        catalogue = SqlAlchemyCatalogueDatabase(fx_empty_catalogue)
        entries = await catalogue.read(lambda repos: repos.library.list_for_owner(fx_shared.owner_id, SliceRequest()))
        profiles = await catalogue.read(lambda repos: repos.recipe_profiles.list_for_account(fx_shared.owner_id))
        first = (
            await fx_databases()
            .open(fx_shared.first_id)
            .read(
                lambda repos: (
                    repos.book_record.get(),
                    repos.pages.list_window(SliceRequest()).total,
                    len(repos.sources.list_all()),
                    repos.book_places.find(),
                ),
            )
        )
        second_pages = (
            await fx_databases()
            .open(fx_shared.second_id)
            .read(lambda repos: repos.pages.list_window(SliceRequest()).total)
        )
        expect((report.accounts, report.books) == (1, 2))
        expect({entry.book_id for entry in entries.items} == {fx_shared.first_id, fx_shared.second_id})
        expect(
            {entry.book_id: entry.added_at for entry in entries.items}[fx_shared.first_id] == fx_shared.first.created_at
        )
        expect(len(profiles) == 1)
        expect(first[0] == fx_shared.first)
        expect(first[1:3] == (PAGES_OF_FIRST, 1))
        expect(first[3] is not None)
        expect(second_pages == 1)
        assert_expectations()

    async def test_a_label_typed_by_hand_stays_a_manual_label(
        self,
        fx_shared: SharedBooks,
        fx_empty_catalogue: SqlDatabase,
        fx_books: BookRegistry,
        fx_directories: SharedBookDirectories,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the rows go through the revisions after the baseline, so a label typed by hand keeps its page.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue the split builds.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_directories: The directories of the books of the test, which the split makes.
        :type fx_directories: SharedBookDirectories
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        await SharedDatabaseSplit(
            fx_shared.path, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
        ).run()

        page = await fx_databases().open(fx_shared.first_id).read(lambda repos: repos.pages.get(fx_shared.labelled))
        assert page.manual_label == MANUAL_LABEL

    async def test_the_shared_database_is_left_as_it_was(
        self,
        fx_shared: SharedBooks,
        fx_empty_catalogue: SqlDatabase,
        fx_books: BookRegistry,
        fx_directories: SharedBookDirectories,
    ) -> None:
        """Verify the split reads the shared file only, so it stays a copy to go back to.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue the split builds.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_directories: The directories of the books of the test, which the split makes.
        :type fx_directories: SharedBookDirectories
        """
        before = fx_shared.path.read_bytes()
        await SharedDatabaseSplit(
            fx_shared.path, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
        ).run()
        assert fx_shared.path.read_bytes() == before


class TestRefusal:
    """Tests for the states in which the split does not start."""

    async def test_a_shared_database_at_an_older_revision_is_refused(
        self,
        fx_settings: Settings,
        fx_empty_catalogue: SqlDatabase,
        fx_books: BookRegistry,
        fx_directories: SharedBookDirectories,
    ) -> None:
        """Verify a shared database the last release did not upgrade is refused with the revision it is at.

        :param fx_settings: Settings pointing at the data directory of the test.
        :type fx_settings: Settings
        :param fx_empty_catalogue: The catalogue the split would build.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_directories: The directories of the books of the test, which the split makes.
        :type fx_directories: SharedBookDirectories
        """
        fx_settings.data_dir.mkdir(parents=True, exist_ok=True)
        shared = await _build_shared(fx_settings.data_dir, revision=OLDER_REVISION)
        with pytest.raises(SplitRefusedError, match=OLDER_REVISION):
            await SharedDatabaseSplit(
                shared.path, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
            ).run()

    async def test_a_missing_shared_database_is_refused(
        self,
        fx_settings: Settings,
        fx_empty_catalogue: SqlDatabase,
        fx_books: BookRegistry,
        fx_directories: SharedBookDirectories,
    ) -> None:
        """Verify a data directory without the shared file is refused, naming the place it looked at.

        :param fx_settings: Settings pointing at the data directory of the test.
        :type fx_settings: Settings
        :param fx_empty_catalogue: The catalogue the split would build.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_directories: The directories of the books of the test, which the split makes.
        :type fx_directories: SharedBookDirectories
        """
        missing = fx_settings.data_dir / SHARED_FILE
        with pytest.raises(SplitRefusedError, match=SHARED_FILE):
            await SharedDatabaseSplit(
                missing, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
            ).run()

    async def test_a_catalogue_that_holds_accounts_is_refused(
        self,
        fx_shared: SharedBooks,
        fx_empty_catalogue: SqlDatabase,
        fx_books: BookRegistry,
        fx_directories: SharedBookDirectories,
    ) -> None:
        """Verify a second split, or one over a catalogue already in use, is refused before it writes anything.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue, which the first split fills.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_directories: The directories of the books of the test, which the split makes.
        :type fx_directories: SharedBookDirectories
        """
        await SharedDatabaseSplit(
            fx_shared.path, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
        ).run()
        with pytest.raises(SplitRefusedError, match=AccountTable.__tablename__):
            await SharedDatabaseSplit(
                fx_shared.path, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
            ).run()

    async def test_a_book_that_has_a_file_already_is_refused(
        self,
        fx_shared: SharedBooks,
        fx_empty_catalogue: SqlDatabase,
        fx_books: BookRegistry,
        fx_directories: SharedBookDirectories,
    ) -> None:
        """Verify the split never overwrites the file of a book that exists.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue the split builds.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookRegistry
        :param fx_directories: The directories of the books of the test, which the split makes.
        :type fx_directories: SharedBookDirectories
        """
        await fx_directories.create(directory_of(fx_shared.first_id), await fx_books.blank())
        with pytest.raises(SplitRefusedError, match=str(fx_shared.first_id)):
            await SharedDatabaseSplit(
                fx_shared.path, catalogue_db=fx_empty_catalogue, books=fx_books, directories=fx_directories
            ).run()
