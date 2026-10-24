"""Tests for the SQLAlchemy tables: the file of a book ties its rows to it, and the library ties the book to an owner."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.mappers import SourceMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import PageRow, SourceRow, StepChangeRow
from bookreviver.domain.entities import LibraryEntry, PageHistoryEntry
from bookreviver.domain.enums import BlockKind, ChangeSource, RejectionReason, SourceStatus, ValueScope
from bookreviver.domain.errors import NotFoundError
from bookreviver.ports.persistence import DEFAULT_CHANGE_WAIT_SECONDS
from tests.helpers.builders import (
    EPOCH,
    make_page,
    make_page_history_entry,
    make_record,
    make_source,
    make_step_values,
    new_account_id,
)
from tests.helpers.seeding import store_book

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.persistence.sqlalchemy.tables import BookRecordRow, ScanRow
    from bookreviver.domain.ids import AccountId
    from tests.helpers.database import BookDatabasesFactory, BookSessions, UnitOfWorkFactory

pytestmark = pytest.mark.anyio

# The message SQLite gives for every violated foreign key
SQLITE_FOREIGN_KEY_FAILED: str = 'FOREIGN KEY constraint failed'
# The message SQLite gives for every violated unique index
SQLITE_UNIQUE_FAILED: str = 'UNIQUE constraint failed'
SCAN_COUNT: int = 3
# Names of the files of the tests of the digest index, which have different digests
FIRST_NAME: str = 'first.pdf'
SECOND_NAME: str = 'second.pdf'
# The files of an upload that no import took in, which may repeat the digest of an imported source
UNIMPORTED_STATUSES: tuple[SourceStatus, ...] = (SourceStatus.REJECTED, SourceStatus.REJECTED, SourceStatus.SKIPPED)


def count_rows(session: Session, table: type[PageRow | BookRecordRow | ScanRow | SourceRow | StepChangeRow]) -> int:
    """Count the rows of a table in the transaction of the session.

    :param session: Session of the transaction.
    :type session: Session
    :param table: Table to count.
    :type table: type[PageRow | BookRecordRow | ScanRow | SourceRow | StepChangeRow]
    :returns: Number of rows the transaction sees.
    :rtype: int
    """
    return session.scalar(select(func.count()).select_from(table)) or 0


class TestBookRecordRow:
    """Tests for BookRecordRow together with the foreign keys and relationships of its pages."""

    async def test_update_keeps_pages(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book_sessions: BookSessions,
        fx_owner_id: AccountId,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify updating a book leaves its pages, which the relationships must not merge away.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        uow = fx_uow()
        await store_book(uow, book_record, make_page(), owner=fx_owner_id, databases=fx_databases())

        renamed = evolve(book_record, details=evolve(book_record.details, title='Renamed'))
        await fx_databases().open(book_record.id).change(lambda repos, _book_record: repos.book_record.update(renamed))

        assert await fx_book_sessions.read(book_record.id, lambda session: count_rows(session, PageRow)) == 1


class TestStepChangeRow:
    """Tests for StepChangeRow, which the histories of the pages it reached name."""

    async def test_a_deleted_page_takes_its_own_change_and_leaves_one_another_page_names(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book_sessions: BookSessions,
        fx_owner_id: AccountId,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify deleting a page removes the change of its own layer, and keeps a change of values another page has.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record, deleted, kept = make_record(), make_page(order_key='a0'), make_page(order_key='b0')
        uow = fx_uow()
        await store_book(uow, book_record, deleted, kept, owner=fx_owner_id, databases=fx_databases())
        values = make_step_values(scope=ValueScope.ODD)
        shared = PageHistoryEntry.of_values([deleted.id, kept.id], values, values, ChangeSource.USER)
        own = make_page_history_entry(page_id=deleted.id)
        await (
            fx_databases()
            .open(book_record.id)
            .change(lambda repos, _book_record: repos.page_history.add_many([*shared, own]))
        )

        await fx_databases().open(book_record.id).change(lambda repos, _book_record: repos.pages.delete(deleted.id))

        ids = await fx_book_sessions.read(
            book_record.id, lambda session: session.scalars(select(StepChangeRow.id)).all()
        )
        assert ids == [shared[0].id]


class TestLibraryRow:
    """Tests for the library of the catalogue, whose key to the owner keeps an account while it owns a book."""

    async def test_owner_with_a_book_cannot_be_deleted(
        self,
        fx_database: SqlDatabase,
        fx_uow: UnitOfWorkFactory,
        fx_owner_id: AccountId,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the owner key restricts: an account is deleted only after its books, whose files a cascade keeps.

        :param fx_database: Catalogue of the test, from which the account is deleted with plain SQL.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        await store_book(fx_uow(), make_record(), owner=fx_owner_id, databases=fx_databases())
        with pytest.raises(IntegrityError, match=SQLITE_FOREIGN_KEY_FAILED):
            await fx_database.write(
                lambda session: session.execute(delete(AccountTable).filter_by(id=fx_owner_id)),
                kind=BlockKind.OTHER,
                book=None,
                wait_seconds=DEFAULT_CHANGE_WAIT_SECONDS,
            )

    async def test_entry_of_a_missing_owner_raises_not_found(self, fx_uow: UnitOfWorkFactory) -> None:
        """Verify a book cannot join the library of an account that does not exist, reported by the account.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        """
        owner_id = new_account_id()
        entry = LibraryEntry(book_id=make_record().id, owner_id=owner_id, added_at=EPOCH)
        with pytest.raises(NotFoundError, match=str(owner_id)):
            await fx_uow().change(lambda repos: repos.library.add(entry))


class TestSourceRow:
    """Tests for the partial unique index that keeps the digest of an imported source unique in its book."""

    async def test_database_refuses_a_second_imported_source_with_one_digest(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book_sessions: BookSessions,
        fx_owner_id: AccountId,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify the database itself refuses a second imported source with a digest, whatever a service checked first.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        first = make_source(name=FIRST_NAME)
        await store_book(fx_uow(), book_record, owner=fx_owner_id, sources=[first], databases=fx_databases())
        second = SourceMapper().to_row(evolve(make_source(name=SECOND_NAME), sha256=first.sha256))

        def add_and_flush(session: Session) -> None:
            """Add the second source to the session and flush it, so SQLite checks its index.

            :param session: Session of a transaction that writes.
            :type session: Session
            """
            # A row added directly bypasses the repository, which would report the conflict as a domain error
            session.add(second)
            session.flush()

        with pytest.raises(IntegrityError, match=SQLITE_UNIQUE_FAILED):
            await fx_book_sessions.write(book_record.id, add_and_flush)

    async def test_files_no_import_took_in_may_repeat_the_digest_of_an_imported_source(
        self,
        fx_uow: UnitOfWorkFactory,
        fx_book_sessions: BookSessions,
        fx_owner_id: AccountId,
        fx_databases: BookDatabasesFactory,
    ) -> None:
        """Verify any number of rejected and skipped files share a digest with an imported source.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        :param fx_databases: Function opening a new opener of the databases of the books of the test.
        :type fx_databases: BookDatabasesFactory
        """
        book_record = make_record()
        imported = make_source(name=FIRST_NAME)
        await store_book(fx_uow(), book_record, owner=fx_owner_id, sources=[imported], databases=fx_databases())
        unimported = [
            evolve(
                make_source(name=SECOND_NAME),
                sha256=imported.sha256,
                scan_count=0,
                status=status,
                rejection=RejectionReason.DUPLICATE if status is SourceStatus.REJECTED else None,
            )
            for status in UNIMPORTED_STATUSES
        ]

        def add_sources(session: Session) -> None:
            """Add the files that no import took in, each as a row of its own.

            :param session: Session of a transaction that writes.
            :type session: Session
            """
            session.add_all(SourceMapper().to_row(source) for source in unimported)

        await fx_book_sessions.write(book_record.id, add_sources)

        stored = await fx_book_sessions.read(book_record.id, lambda session: count_rows(session, SourceRow))
        assert stored == 1 + len(UNIMPORTED_STATUSES)
