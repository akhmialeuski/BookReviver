"""Tests for the SQLAlchemy tables: the database itself keeps the rows of a book tied to their project and owner."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.mappers import PageMapper, SourceMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import PageRow, ProjectRow, ScanRow, SourceRow
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import RejectionReason, SourceStatus
from bookreviver.domain.errors import NotFoundError
from tests.helpers.builders import make_page, make_project, make_scan, make_source, new_account_id
from tests.helpers.seeding import store_project

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

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


def count_rows(session: Session, table: type[PageRow | ProjectRow | ScanRow | SourceRow]) -> int:
    """Count the rows of a table in the transaction of the session.

    :param session: Session of the transaction.
    :type session: Session
    :param table: Table to count.
    :type table: type[PageRow | ProjectRow | ScanRow | SourceRow]
    :returns: Number of rows the transaction sees.
    :rtype: int
    """
    return session.scalar(select(func.count()).select_from(table)) or 0


class TestProjectRow:
    """Tests for ProjectRow together with the foreign keys and relationships of its pages."""

    async def test_database_deletes_pages_with_their_project(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a plain SQL delete of a project row removes its pages through the foreign keys.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project, make_page(project_id=project.id))

        # A bulk statement bypasses the ORM, so only the database can remove the dependent rows
        await uow.change_session(lambda session: session.execute(delete(ProjectRow).where(ProjectRow.id == project.id)))

        assert await uow.read_session(lambda session: count_rows(session, PageRow)) == 0

    async def test_database_deletes_sources_and_scans_with_their_project(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a plain SQL delete of a project row removes its sources and their scans through the foreign keys.

        A source belongs to the project and a scan to both its source and the project, so the scans go with either.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        source = make_source(project_id=project.id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(
            uow,
            project,
            sources=[source],
            scans=[make_scan(source=source, number=number) for number in range(SCAN_COUNT)],
        )

        # A bulk statement bypasses the ORM, so only the database can remove the dependent rows
        await uow.change_session(lambda session: session.execute(delete(ProjectRow).where(ProjectRow.id == project.id)))

        sources, scans = await uow.read_session(
            lambda session: (count_rows(session, SourceRow), count_rows(session, ScanRow))
        )
        expect(sources == 0)
        expect(scans == 0)
        assert_expectations()

    async def test_database_deletes_a_project_with_a_cover(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a plain SQL delete of a project whose cover is set removes it and its pages, despite the cycle.

        The project refers to its cover page and the page to its project, so the cascade to the pages empties the
        cover of the row being deleted.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        cover = make_page(project_id=project.id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project, cover)
        await uow.change(lambda repos: repos.projects.update(evolve(project, cover_page_id=cover.id)))

        await uow.change_session(lambda session: session.execute(delete(ProjectRow).where(ProjectRow.id == project.id)))

        projects, pages = await uow.read_session(
            lambda session: (count_rows(session, ProjectRow), count_rows(session, PageRow))
        )
        expect(projects == 0)
        expect(pages == 0)
        assert_expectations()

    async def test_update_keeps_pages(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify updating a project leaves its pages, which the relationships must not merge away.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project, make_page(project_id=project.id))

        renamed = evolve(project, details=evolve(project.details, title='Renamed'))
        await uow.change(lambda repos: repos.projects.update(renamed))

        assert await uow.read_session(lambda session: count_rows(session, PageRow)) == 1

    async def test_sqlite_rejects_a_page_of_a_missing_project(self, fx_database: SqlDatabase) -> None:
        """Verify SQLite enforces the foreign key, which it ignores unless the connection turns it on.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        orphan = PageMapper().to_row(make_page(project_id=make_project(owner_id=new_account_id()).id))

        def add_and_flush(session: Session) -> None:
            """Add the page to the session and flush it, so SQLite checks its key.

            :param session: Session of a transaction that writes.
            :type session: Session
            """
            session.add(orphan)
            session.flush()

        with pytest.raises(IntegrityError, match=SQLITE_FOREIGN_KEY_FAILED):
            await SqlAlchemyUnitOfWork(fx_database).change_session(add_and_flush)

    async def test_owner_with_a_project_cannot_be_deleted(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify the owner key restricts: an account is deleted only after its projects, whose files a cascade keeps.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        uow = SqlAlchemyUnitOfWork(fx_database)
        await uow.change(lambda repos: repos.projects.add(make_project(owner_id=fx_owner_id)))
        with pytest.raises(IntegrityError, match=SQLITE_FOREIGN_KEY_FAILED):
            await uow.change_session(lambda session: session.execute(delete(AccountTable).filter_by(id=fx_owner_id)))

    async def test_project_of_a_missing_owner_raises_not_found(self, fx_database: SqlDatabase) -> None:
        """Verify a project cannot be stored for an account that does not exist, reported by the account's identifier.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        owner_id = new_account_id()
        with pytest.raises(NotFoundError, match=str(owner_id)):
            await SqlAlchemyUnitOfWork(fx_database).change(
                lambda repos: repos.projects.add(make_project(owner_id=owner_id))
            )


class TestSourceRow:
    """Tests for the partial unique index that keeps the digest of an imported source unique in its project."""

    async def test_database_refuses_a_second_imported_source_with_one_digest(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify the database itself refuses a second imported source with a digest, whatever a service checked first.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        first = make_source(project_id=project.id, name=FIRST_NAME)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project, sources=[first])
        second = SourceMapper().to_row(
            evolve(make_source(project_id=project.id, name=SECOND_NAME), sha256=first.sha256)
        )

        def add_and_flush(session: Session) -> None:
            """Add the second source to the session and flush it, so SQLite checks its index.

            :param session: Session of a transaction that writes.
            :type session: Session
            """
            # A row added directly bypasses the repository, which would report the conflict as a domain error
            session.add(second)
            session.flush()

        with pytest.raises(IntegrityError, match=SQLITE_UNIQUE_FAILED):
            await uow.change_session(add_and_flush)

    async def test_files_no_import_took_in_may_repeat_the_digest_of_an_imported_source(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify any number of rejected and skipped files share a digest with an imported source.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        imported = make_source(project_id=project.id, name=FIRST_NAME)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project, sources=[imported])
        unimported = [
            evolve(
                make_source(project_id=project.id, name=SECOND_NAME),
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

        await uow.change_session(add_sources)

        assert await uow.read_session(lambda session: count_rows(session, SourceRow)) == 1 + len(UNIMPORTED_STATUSES)
