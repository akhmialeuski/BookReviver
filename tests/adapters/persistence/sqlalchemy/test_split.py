"""Tests for the split of the shared database of the earlier releases into the catalogue and the files of the books.

The revisions of the shared database are gone from the code, so the tests build a shared database the way its last
revision left it: every table of the catalogue but the library and every table of a book, in one file, with the owner
of a project and the account of a place that the new tables no longer keep, and the version table at the last
revision. The rows are stored through the repositories of the adapter, so they have the shape the application wrote.
"""

from typing import TYPE_CHECKING

import pytest
from attrs import frozen
from delayed_assert import assert_expectations, expect
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.catalogue import LibraryRow
from bookreviver.adapters.persistence.sqlalchemy.database import Schema, SqlDatabase
from bookreviver.adapters.persistence.sqlalchemy.repositories import (
    SqlAlchemyBookRepositories,
    SqlAlchemyCatalogueRepositories,
)
from bookreviver.adapters.persistence.sqlalchemy.split import SHARED_REVISION, SharedDatabaseSplit, SplitRefusedError
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.adapters.tracing.null import NullTracer
from bookreviver.domain.values import SliceRequest
from tests.helpers.builders import (
    make_book_place,
    make_page,
    make_page_stage,
    make_page_version,
    make_project,
    make_recipe_profile,
    make_scan,
    make_source,
    new_account_id,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.books import BookDatabases
    from bookreviver.app.settings import Settings
    from bookreviver.domain.entities import Project
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

SHARED_FILE: str = 'bookreviver.db'
VERSION_TABLE: str = 'alembic_versions'
# A revision of the shared chain before its last one, which the split refuses
OLDER_REVISION: str = 'a6cd07915dcf'
PAGES_OF_FIRST: int = 2
# The parameter of the statements that set the account the earlier releases kept beside a book
OWNER_PARAMETER: str = 'owner'


@frozen(kw_only=True)
class SharedBooks:
    """A shared database of the earlier releases and the books and the owner it holds.

    :ivar path: The shared file.
    :ivar owner_id: The account owning every book.
    :ivar first: The first book, with a source, a scan, pages, a version, a stage record and a place.
    :ivar second: The second book, with one page.
    """

    path: Path
    owner_id: AccountId
    first: Project
    second: Project


def _build_shared(path: Path, *, revision: str) -> SharedBooks:
    """Write a shared database of the earlier releases with one account, one profile and two books.

    :param path: Where the file is made.
    :type path: Path
    :param revision: The revision its version table records.
    :type revision: str
    :returns: What the file stores.
    :rtype: SharedBooks
    """
    owner_id = new_account_id()
    first, second = make_project(title='First'), make_project(title='Second', minutes=1)
    source = make_source(project_id=first.id)
    scan = make_scan(source=source, number=0)
    pages = [
        make_page(project_id=first.id, order_key=f'a{index}', scan=scan if not index else None) for index in range(2)
    ]
    version = make_page_version(page_id=pages[0].id)
    engine = create_engine(f'sqlite:///{path}')
    try:
        with engine.begin() as connection:
            Schema.CATALOGUE.metadata.create_all(connection)
            Schema.BOOK.metadata.create_all(connection)
            connection.execute(text(f'DROP TABLE "{LibraryRow.__tablename__}"'))
            connection.execute(text('ALTER TABLE projects ADD COLUMN owner_id BLOB'))
            connection.execute(text('ALTER TABLE book_places ADD COLUMN account_id BLOB'))
            connection.execute(text(f'CREATE TABLE {VERSION_TABLE} (version_num VARCHAR(32) NOT NULL PRIMARY KEY)'))
            connection.execute(text(f'INSERT INTO {VERSION_TABLE} VALUES (:revision)'), {'revision': revision})
        with Session(engine) as session, session.begin():
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
            repos = SqlAlchemyBookRepositories(session)
            repos.projects.add_many([first, second])
            repos.sources.add(source)
            repos.scans.add(scan)
            repos.pages.add_many([*pages, make_page(project_id=second.id)])
            repos.page_versions.add(version)
            repos.page_stages.add(make_page_stage(page_id=pages[0].id, head_version_id=version.id))
            repos.book_places.save(make_book_place(project_id=first.id, page_id=pages[1].id))
        with engine.begin() as connection:
            owner = {OWNER_PARAMETER: owner_id.bytes}
            connection.execute(text(f'UPDATE projects SET owner_id = :{OWNER_PARAMETER}'), owner)
            connection.execute(text(f'UPDATE book_places SET account_id = :{OWNER_PARAMETER}'), owner)
    finally:
        engine.dispose()
    return SharedBooks(path=path, owner_id=owner_id, first=first, second=second)


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
def fx_shared(fx_settings: Settings) -> SharedBooks:
    """Write the shared database of the test at the last revision of its chain, in the data directory.

    :param fx_settings: Settings pointing at a fresh data directory of the test.
    :type fx_settings: Settings
    :returns: What the shared database stores.
    :rtype: SharedBooks
    """
    fx_settings.data_dir.mkdir(parents=True, exist_ok=True)
    return _build_shared(fx_settings.data_dir / SHARED_FILE, revision=SHARED_REVISION)


class TestSplit:
    """Tests for a split of a shared database at its last revision."""

    async def test_accounts_profiles_and_owners_go_to_the_catalogue_and_each_book_to_its_file(
        self, fx_shared: SharedBooks, fx_empty_catalogue: SqlDatabase, fx_books: BookDatabases
    ) -> None:
        """Verify the catalogue lists the books of the owner and every book file holds its own rows and no other.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue the split builds.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test, which makes their files.
        :type fx_books: BookDatabases
        """
        report = await SharedDatabaseSplit(fx_shared.path, catalogue=fx_empty_catalogue, books=fx_books).run()
        uow = SqlAlchemyUnitOfWork(fx_empty_catalogue, fx_books)
        entries = await uow.read(lambda repos: repos.library.list_for_owner(fx_shared.owner_id, SliceRequest()))
        profiles = await uow.read(lambda repos: repos.recipe_profiles.list_for_account(fx_shared.owner_id))
        first = await uow.read_book(
            fx_shared.first.id,
            lambda repos: (
                repos.projects.get(fx_shared.first.id),
                repos.pages.list_for_project(fx_shared.first.id, SliceRequest()).total,
                len(repos.sources.list_for_project(fx_shared.first.id)),
                repos.book_places.find(fx_shared.first.id),
            ),
        )
        second_pages = await uow.read_book(
            fx_shared.second.id, lambda repos: repos.pages.list_for_project(fx_shared.second.id, SliceRequest()).total
        )
        expect((report.accounts, report.books) == (1, 2))
        expect({entry.project_id for entry in entries.items} == {fx_shared.first.id, fx_shared.second.id})
        expect(
            {entry.project_id: entry.added_at for entry in entries.items}[fx_shared.first.id]
            == fx_shared.first.created_at
        )
        expect(len(profiles) == 1)
        expect(first[0] == fx_shared.first)
        expect(first[1:3] == (PAGES_OF_FIRST, 1))
        expect(first[3] is not None)
        expect(second_pages == 1)
        assert_expectations()

    async def test_the_shared_database_is_left_as_it_was(
        self, fx_shared: SharedBooks, fx_empty_catalogue: SqlDatabase, fx_books: BookDatabases
    ) -> None:
        """Verify the split reads the shared file only, so it stays a copy to go back to.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue the split builds.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        before = fx_shared.path.read_bytes()
        await SharedDatabaseSplit(fx_shared.path, catalogue=fx_empty_catalogue, books=fx_books).run()
        assert fx_shared.path.read_bytes() == before


class TestRefusal:
    """Tests for the states in which the split does not start."""

    async def test_a_shared_database_at_an_older_revision_is_refused(
        self, fx_settings: Settings, fx_empty_catalogue: SqlDatabase, fx_books: BookDatabases
    ) -> None:
        """Verify a shared database the last release did not upgrade is refused with the revision it is at.

        :param fx_settings: Settings pointing at the data directory of the test.
        :type fx_settings: Settings
        :param fx_empty_catalogue: The catalogue the split would build.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        shared = _build_shared(fx_settings.data_dir / SHARED_FILE, revision=OLDER_REVISION)
        with pytest.raises(SplitRefusedError, match=OLDER_REVISION):
            await SharedDatabaseSplit(shared.path, catalogue=fx_empty_catalogue, books=fx_books).run()

    async def test_a_missing_shared_database_is_refused(
        self, fx_settings: Settings, fx_empty_catalogue: SqlDatabase, fx_books: BookDatabases
    ) -> None:
        """Verify a data directory without the shared file is refused, naming the place it looked at.

        :param fx_settings: Settings pointing at the data directory of the test.
        :type fx_settings: Settings
        :param fx_empty_catalogue: The catalogue the split would build.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        missing = fx_settings.data_dir / SHARED_FILE
        with pytest.raises(SplitRefusedError, match=SHARED_FILE):
            await SharedDatabaseSplit(missing, catalogue=fx_empty_catalogue, books=fx_books).run()

    async def test_a_catalogue_that_holds_accounts_is_refused(
        self, fx_shared: SharedBooks, fx_empty_catalogue: SqlDatabase, fx_books: BookDatabases
    ) -> None:
        """Verify a second split, or one over a catalogue already in use, is refused before it writes anything.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue, which the first split fills.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        await SharedDatabaseSplit(fx_shared.path, catalogue=fx_empty_catalogue, books=fx_books).run()
        with pytest.raises(SplitRefusedError, match=AccountTable.__tablename__):
            await SharedDatabaseSplit(fx_shared.path, catalogue=fx_empty_catalogue, books=fx_books).run()

    async def test_a_book_that_has_a_file_already_is_refused(
        self, fx_shared: SharedBooks, fx_empty_catalogue: SqlDatabase, fx_books: BookDatabases
    ) -> None:
        """Verify the split never overwrites the file of a book that exists.

        :param fx_shared: The shared database of the test.
        :type fx_shared: SharedBooks
        :param fx_empty_catalogue: The catalogue the split builds.
        :type fx_empty_catalogue: SqlDatabase
        :param fx_books: Registry of the books of the test.
        :type fx_books: BookDatabases
        """
        await fx_books.create(fx_shared.first.id)
        with pytest.raises(SplitRefusedError, match=str(fx_shared.first.id)):
            await SharedDatabaseSplit(fx_shared.path, catalogue=fx_empty_catalogue, books=fx_books).run()
