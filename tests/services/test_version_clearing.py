"""Tests for the deletion of a batch of versions, which reads the versions again in the change that marks them."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.enums import VersionData, VersionState
from bookreviver.services.book import Book
from bookreviver.services.version_clearing import BEING_COLLECTED, VersionClearing
from tests.helpers.builders import directory_of, make_page, make_page_version, make_record, new_account_id
from tests.helpers.seeding import commit_record

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from tests.helpers.database import BookDatabasesFactory, CatalogueDatabaseFactory
    from tests.helpers.storage import SharedBookDirectories

pytestmark = pytest.mark.anyio

KEPT_COMMENT: str = 'keep'


class TestClear:
    """Tests for ``VersionClearing.clear``."""

    async def test_a_version_deleted_after_the_collection_chose_it_is_skipped_and_the_others_go(
        self,
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify a version a request deleted meanwhile neither fails the batch nor keeps the others from going.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        kept, deleted = (
            evolve(make_page_version(page_id=page.id, minutes=n), state=VersionState.READY) for n in (1, 2)
        )
        book_id = await commit_record(
            fx_database,
            fx_catalogue(),
            make_record(),
            page,
            owner=new_account_id(),
            versions=[kept, deleted],
            databases=fx_databases(),
        )
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_versions.delete_many([deleted.id]))
        )
        clearing = VersionClearing(book=Book(directory_of(book_id)))

        await clearing.clear([kept, deleted])

        remaining = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.list_for_page(page.id))
        expect(remaining == [])
        assert_expectations()

    async def test_the_mark_of_the_user_written_after_the_collection_chose_the_version_is_kept_on_the_marked_row(
        self,
        fx_database: SqlDatabase,
        fx_catalogue: CatalogueDatabaseFactory,
        fx_directories: SharedBookDirectories,
        monkeypatch: pytest.MonkeyPatch,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the version is marked from the row the change reads, so a comment written meanwhile is not lost.

        The directory cannot be removed in this test, so the version stays and its marked row can be read.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_catalogue: Function opening a new catalogue database over the database of the test.
        :type fx_catalogue: CatalogueDatabaseFactory
        :param fx_directories: Opens the source store and the asset store of a book of the test.
        :type fx_directories: SharedBookDirectories
        :param monkeypatch: Fixture that restores the patched store after the test.
        :type monkeypatch: pytest.MonkeyPatch
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        page = make_page()
        chosen = evolve(make_page_version(page_id=page.id), state=VersionState.READY)
        book_id = await commit_record(
            fx_database,
            fx_catalogue(),
            make_record(),
            page,
            owner=new_account_id(),
            versions=[chosen],
            databases=fx_databases(),
        )
        await (
            fx_databases()
            .open(book_id)
            .change(lambda repos, _book_record: repos.page_versions.update(evolve(chosen, comment=KEPT_COMMENT)))
        )

        monkeypatch.setattr(fx_directories.assets(book_id), 'delete_prefix', AsyncMock(side_effect=OSError))
        clearing = VersionClearing(book=Book(directory_of(book_id)))

        await clearing.clear([chosen])

        stored = await fx_databases().open(book_id).read(lambda repos: repos.page_versions.get(chosen.id))
        expect((stored.state, stored.comment) == (VersionState.FAILED, KEPT_COMMENT))
        expect(stored.data[VersionData.ERROR] == BEING_COLLECTED)
        assert_expectations()
