"""Tests of the versions of the pages in a transaction of a book that only reads, which reads each version once.

The rows of a stage walk the same versions back from the current ones more than once, so a read transaction keeps every
version it read by its identifier and asks the database only for the ones it has not read yet.
"""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from tests.harness.adapters.statements import BoundValues
from tests.harness.builders.domain import directory_of, make_page, make_page_version, make_record
from tests.harness.builders.seeding import store_book

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.adapters.persistence.sqlalchemy.books import BookRegistry
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import AccountId, BookId
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories
    from tests.harness.adapters.database import BookDatabasesFactory, CatalogueDatabaseFactory

pytestmark = pytest.mark.anyio

COUNT: int = 5
FIRST_READ: int = 3


async def _seed_book(
    fx_catalogue: CatalogueDatabaseFactory, owner_id: AccountId, databases: BookDatabasesFactory
) -> tuple[BookId, list[PageVersion]]:
    """Commit a book of five pages, each with one version created a minute after the one before.

    :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
    :type fx_catalogue: CatalogueDatabaseFactory
    :param owner_id: Account owning the book.
    :type owner_id: AccountId
    :param databases: Function opening a new opener of the databases of the books of the test.
    :type databases: BookDatabasesFactory
    :returns: The book and its versions in the order they were created.
    :rtype: tuple[BookId, list[PageVersion]]
    """
    pages = [make_page(order_key=f'a{number}') for number in range(COUNT)]
    versions = [make_page_version(page_id=page.id, minutes=number) for number, page in enumerate(pages)]
    book_id = await store_book(
        fx_catalogue(), make_record(), *pages, owner=owner_id, versions=versions, databases=databases()
    )
    return book_id, versions


class TestReadTransaction:
    """Tests for the versions read by their identifiers in a transaction that only reads."""

    async def test_a_second_read_asks_only_for_the_versions_not_read_before(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_books: BookRegistry,
        fx_owner_id: AccountId,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a read of all five versions after three of them binds the two others alone, and lists all five.

        The identifiers of the second read are given in reverse, and the versions still come earliest first. A version
        read before is found again with no statement.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_books: Registry of the books of the test, whose engines of the book are listened to.
        :type fx_books: BookRegistry
        :param fx_owner_id: Account that may own books.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, versions = await _seed_book(fx_catalogue, fx_owner_id, fx_databases)
        version_ids = [version.id for version in versions]

        def read_twice(repos: BookRepositories) -> tuple[BoundValues, Sequence[PageVersion], PageVersion | None]:
            """Read three versions, then all five and one of the first three again, counting the later statements.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :returns: The counter of the statements of the later reads, the five versions and the one found again.
            :rtype: tuple[BoundValues, Sequence[PageVersion], PageVersion | None]
            """
            repos.page_versions.list_by_ids(version_ids[:FIRST_READ])
            with BoundValues(book.read_engine) as counted:
                listed = repos.page_versions.list_by_ids(list(reversed(version_ids)))
                found = repos.page_versions.find(version_ids[0])
            return counted, listed, found

        async with fx_books.use(directory_of(book_id)) as book:
            counted, listed, found = await fx_databases().open(book_id).read(read_twice)
        expect(counted.bound == [COUNT - FIRST_READ], f'bound {counted.bound}')
        expect([version.id for version in listed] == version_ids)
        expect(found == versions[0])
        assert_expectations()

    async def test_a_change_reads_every_version_from_the_database(
        self,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_books: BookRegistry,
        fx_owner_id: AccountId,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a transaction that writes asks the database again for a version it has read, since it may change it.

        :param fx_catalogue: Function opening a catalogue database over the catalogue and the books of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_books: Registry of the books of the test, whose engines of the book are listened to.
        :type fx_books: BookRegistry
        :param fx_owner_id: Account that may own books.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_id, versions = await _seed_book(fx_catalogue, fx_owner_id, fx_databases)
        version_ids = [version.id for version in versions]

        def read_twice(repos: BookRepositories, _book_record: BookRecord) -> BoundValues:
            """Read the versions twice, counting the statements of the second read.

            :param repos: BookRepositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            :returns: The counter of the statements of the second read.
            :rtype: BoundValues
            """
            repos.page_versions.list_by_ids(version_ids)
            with BoundValues(book.engine) as counted:
                repos.page_versions.list_by_ids(version_ids)
            return counted

        async with fx_books.use(directory_of(book_id)) as book:
            counted = await fx_databases().open(book_id).change(read_twice)
        assert counted.bound == [COUNT], f'bound {counted.bound}'
