"""Tests for the SQLAlchemy tables: the database itself keeps the rows of a book tied to their project and owner."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from bookreviver.adapters.persistence.sqlalchemy.accounts import AccountTable
from bookreviver.adapters.persistence.sqlalchemy.mappers import JobMapper, PageMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import JobRow, PageRow, ProjectRow, ScanRow, SourceRow
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import NotFoundError
from tests.helpers.builders import make_job, make_page, make_project, make_scan, make_source, new_account_id
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
# The states of the jobs that finished or have not begun, which may stand beside a running import
OTHER_STATES: tuple[JobState, ...] = (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.QUEUED)


def count_rows(session: Session, table: type[JobRow | PageRow | ProjectRow | ScanRow | SourceRow]) -> int:
    """Count the rows of a table in the transaction of the session.

    :param session: Session of the transaction.
    :type session: Session
    :param table: Table to count.
    :type table: type[JobRow | PageRow | ProjectRow | ScanRow | SourceRow]
    :returns: Number of rows the transaction sees.
    :rtype: int
    """
    return session.scalar(select(func.count()).select_from(table)) or 0


class TestProjectRow:
    """Tests for ProjectRow together with the foreign keys and relationships of its pages and jobs."""

    async def test_database_deletes_pages_and_jobs_with_their_project(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify a plain SQL delete of a project row removes its pages and jobs through the foreign keys.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project, make_page(project_id=project.id))
        await uow.change(lambda repos: repos.jobs.add(make_job(project_id=project.id)))

        # A bulk statement bypasses the ORM, so only the database can remove the dependent rows
        await uow.change_session(lambda session: session.execute(delete(ProjectRow).where(ProjectRow.id == project.id)))

        pages, jobs = await uow.read_session(
            lambda session: (count_rows(session, PageRow), count_rows(session, JobRow))
        )
        expect(pages == 0)
        expect(jobs == 0)
        assert_expectations()

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

    async def test_update_keeps_pages_and_jobs(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify updating a project leaves its pages and jobs, which the relationships must not merge away.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await store_project(uow, project, make_page(project_id=project.id))
        await uow.change(lambda repos: repos.jobs.add(make_job(project_id=project.id)))

        renamed = evolve(project, details=evolve(project.details, title='Renamed'))
        await uow.change(lambda repos: repos.projects.update(renamed))

        pages, jobs = await uow.read_session(
            lambda session: (count_rows(session, PageRow), count_rows(session, JobRow))
        )
        expect(pages == 1)
        expect(jobs == 1)
        assert_expectations()

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


class TestJobRow:
    """Tests for the partial unique index that keeps a project to one active import."""

    async def test_database_refuses_a_second_active_import_of_a_project(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify the database itself refuses a queued import beside a running one, whatever a service checked first.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await uow.change(lambda repos: repos.projects.add(project))
        running = JobMapper().to_row(make_job(project_id=project.id, state=JobState.RUNNING))
        await uow.change_session(lambda session: session.add(running))
        queued = JobMapper().to_row(make_job(project_id=project.id, state=JobState.QUEUED))

        def add_and_flush(session: Session) -> None:
            """Add the queued job to the session and flush it, so SQLite checks its index.

            :param session: Session of a transaction that writes.
            :type session: Session
            """
            # A row added directly bypasses the repository, which would report the conflict as a domain error
            session.add(queued)
            session.flush()

        with pytest.raises(IntegrityError, match=SQLITE_UNIQUE_FAILED):
            await uow.change_session(add_and_flush)

    async def test_finished_imports_do_not_count(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify any number of finished imports may share a project with an active one.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await uow.change(lambda repos: repos.projects.add(project))

        def add_jobs(session: Session) -> None:
            """Add a job of the project in each of the states that may stand beside a running import.

            :param session: Session of a transaction that writes.
            :type session: Session
            """
            session.add_all(JobMapper().to_row(make_job(project_id=project.id, state=state)) for state in OTHER_STATES)

        await uow.change_session(add_jobs)

        assert await uow.read_session(lambda session: count_rows(session, JobRow)) == len(OTHER_STATES)
