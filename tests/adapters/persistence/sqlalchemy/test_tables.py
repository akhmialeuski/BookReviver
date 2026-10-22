"""Tests for the SQLAlchemy tables: the file of a book ties its rows to its project, and the library ties it to an owner."""

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
    make_project,
    make_source,
    make_step_values,
    new_account_id,
)
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.adapters.persistence.sqlalchemy.tables import ProjectRow, ScanRow
    from bookreviver.domain.ids import AccountId
    from tests.helpers.database import BookSessions, UnitOfWorkFactory

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


def count_rows(session: Session, table: type[PageRow | ProjectRow | ScanRow | SourceRow | StepChangeRow]) -> int:
    """Count the rows of a table in the transaction of the session.

    :param session: Session of the transaction.
    :type session: Session
    :param table: Table to count.
    :type table: type[PageRow | ProjectRow | ScanRow | SourceRow | StepChangeRow]
    :returns: Number of rows the transaction sees.
    :rtype: int
    """
    return session.scalar(select(func.count()).select_from(table)) or 0


class TestProjectRow:
    """Tests for ProjectRow together with the foreign keys and relationships of its pages."""

    async def test_update_keeps_pages(
        self, fx_uow: UnitOfWorkFactory, fx_book_sessions: BookSessions, fx_owner_id: AccountId
    ) -> None:
        """Verify updating a project leaves its pages, which the relationships must not merge away.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project()
        uow = fx_uow()
        await store_project(uow, project, make_page(), owner=fx_owner_id)

        renamed = evolve(project, details=evolve(project.details, title='Renamed'))
        await uow.change_book(project.id, lambda repos, _project: repos.projects.update(renamed))

        assert await fx_book_sessions.read(project.id, lambda session: count_rows(session, PageRow)) == 1


class TestStepChangeRow:
    """Tests for StepChangeRow, which the histories of the pages it reached name."""

    async def test_a_deleted_page_takes_its_own_change_and_leaves_one_another_page_names(
        self, fx_uow: UnitOfWorkFactory, fx_book_sessions: BookSessions, fx_owner_id: AccountId
    ) -> None:
        """Verify deleting a page removes the change of its own layer, and keeps a change of values another page has.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project, deleted, kept = make_project(), make_page(order_key='a0'), make_page(order_key='b0')
        uow = fx_uow()
        await store_project(uow, project, deleted, kept, owner=fx_owner_id)
        values = make_step_values(scope=ValueScope.ODD)
        shared = PageHistoryEntry.of_values([deleted.id, kept.id], values, values, ChangeSource.USER)
        own = make_page_history_entry(page_id=deleted.id)
        await uow.change_book(project.id, lambda repos, _project: repos.page_history.add_many([*shared, own]))

        await uow.change_book(project.id, lambda repos, _project: repos.pages.delete(deleted.id))

        ids = await fx_book_sessions.read(project.id, lambda session: session.scalars(select(StepChangeRow.id)).all())
        assert ids == [shared[0].id]


class TestLibraryRow:
    """Tests for the library of the catalogue, whose key to the owner keeps an account while it owns a book."""

    async def test_owner_with_a_book_cannot_be_deleted(
        self, fx_database: SqlDatabase, fx_uow: UnitOfWorkFactory, fx_owner_id: AccountId
    ) -> None:
        """Verify the owner key restricts: an account is deleted only after its books, whose files a cascade keeps.

        :param fx_database: Catalogue of the test, from which the account is deleted with plain SQL.
        :type fx_database: SqlDatabase
        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_owner_id: Committed account owning the book.
        :type fx_owner_id: AccountId
        """
        await store_project(fx_uow(), make_project(), owner=fx_owner_id)
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
        entry = LibraryEntry(project_id=make_project().id, owner_id=owner_id, added_at=EPOCH)
        with pytest.raises(NotFoundError, match=str(owner_id)):
            await fx_uow().change(lambda repos: repos.library.add(entry))


class TestSourceRow:
    """Tests for the partial unique index that keeps the digest of an imported source unique in its project."""

    async def test_database_refuses_a_second_imported_source_with_one_digest(
        self, fx_uow: UnitOfWorkFactory, fx_book_sessions: BookSessions, fx_owner_id: AccountId
    ) -> None:
        """Verify the database itself refuses a second imported source with a digest, whatever a service checked first.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project()
        first = make_source(name=FIRST_NAME)
        await store_project(fx_uow(), project, owner=fx_owner_id, sources=[first])
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
            await fx_book_sessions.write(project.id, add_and_flush)

    async def test_files_no_import_took_in_may_repeat_the_digest_of_an_imported_source(
        self, fx_uow: UnitOfWorkFactory, fx_book_sessions: BookSessions, fx_owner_id: AccountId
    ) -> None:
        """Verify any number of rejected and skipped files share a digest with an imported source.

        :param fx_uow: Function opening a unit of work over the catalogue and the books of the test.
        :type fx_uow: UnitOfWorkFactory
        :param fx_book_sessions: Sessions of the files of the books of the test.
        :type fx_book_sessions: BookSessions
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project()
        imported = make_source(name=FIRST_NAME)
        await store_project(fx_uow(), project, owner=fx_owner_id, sources=[imported])
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

        await fx_book_sessions.write(project.id, add_sources)

        stored = await fx_book_sessions.read(project.id, lambda session: count_rows(session, SourceRow))
        assert stored == 1 + len(UNIMPORTED_STATUSES)
