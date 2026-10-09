"""Tests for the SQLAlchemy tables: the database itself keeps the rows of a book tied to their project and owner."""

from typing import TYPE_CHECKING

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect
from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql
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
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

# The message SQLite gives for every violated foreign key
SQLITE_FOREIGN_KEY_FAILED: str = 'FOREIGN KEY constraint failed'
# The message SQLite gives for every violated unique index
SQLITE_UNIQUE_FAILED: str = 'UNIQUE constraint failed'
SCAN_COUNT: int = 3


class TestPageRow:
    """Tests for the columns of PageRow."""

    def test_order_keys_compare_byte_by_byte_on_postgresql(self) -> None:
        """Verify the order key column takes the C collation on PostgreSQL, whose default collation ignores case."""
        column_type = PageRow.__table__.c.order_key.type
        assert column_type.compile(dialect=postgresql.dialect()) == 'VARCHAR COLLATE "C"'


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
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await store_project(uow, project, make_page(project_id=project.id))
            async with uow.change():
                await uow.jobs.add(make_job(project_id=project.id))
        async with fx_database.sessions() as session:
            # A bulk statement bypasses the ORM, so only the database can remove the dependent rows
            await session.execute(delete(ProjectRow).where(ProjectRow.id == project.id))
            await session.commit()
            expect(await session.scalar(select(func.count()).select_from(PageRow)) == 0)
            expect(await session.scalar(select(func.count()).select_from(JobRow)) == 0)
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
        async with fx_database.sessions() as session:
            await store_project(
                SqlAlchemyUnitOfWork(session),
                project,
                sources=[source],
                scans=[make_scan(source=source, number=number) for number in range(SCAN_COUNT)],
            )
        async with fx_database.sessions() as session:
            # A bulk statement bypasses the ORM, so only the database can remove the dependent rows
            await session.execute(delete(ProjectRow).where(ProjectRow.id == project.id))
            await session.commit()
            expect(await session.scalar(select(func.count()).select_from(SourceRow)) == 0)
            expect(await session.scalar(select(func.count()).select_from(ScanRow)) == 0)
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
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await store_project(uow, project, cover)
            async with uow.change():
                await uow.projects.update(evolve(project, cover_page_id=cover.id))
        async with fx_database.sessions() as session:
            await session.execute(delete(ProjectRow).where(ProjectRow.id == project.id))
            await session.commit()
            expect(await session.scalar(select(func.count()).select_from(ProjectRow)) == 0)
            expect(await session.scalar(select(func.count()).select_from(PageRow)) == 0)
        assert_expectations()

    async def test_update_keeps_pages_and_jobs(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify updating a project leaves its pages and jobs, which the relationships must not merge away.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await store_project(uow, project, make_page(project_id=project.id))
            async with uow.change():
                await uow.jobs.add(make_job(project_id=project.id))
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change():
                await uow.projects.update(evolve(project, details=evolve(project.details, title='Renamed')))
            expect(await session.scalar(select(func.count()).select_from(PageRow)) == 1)
            expect(await session.scalar(select(func.count()).select_from(JobRow)) == 1)
        assert_expectations()

    async def test_sqlite_rejects_a_page_of_a_missing_project(self, fx_database: SqlDatabase) -> None:
        """Verify SQLite enforces the foreign key, which it ignores unless the connection turns it on.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        orphan = PageMapper().to_row(make_page(project_id=make_project(owner_id=new_account_id()).id))
        async with fx_database.sessions() as session:
            session.add(orphan)
            with pytest.raises(IntegrityError, match=SQLITE_FOREIGN_KEY_FAILED):
                await session.flush()

    async def test_owner_with_a_project_cannot_be_deleted(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId
    ) -> None:
        """Verify the owner key restricts: an account is deleted only after its projects, whose files a cascade keeps.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change():
                await uow.projects.add(make_project(owner_id=fx_owner_id))
        async with fx_database.sessions() as session:
            with pytest.raises(IntegrityError, match=SQLITE_FOREIGN_KEY_FAILED):
                await session.execute(delete(AccountTable).filter_by(id=fx_owner_id))

    async def test_project_of_a_missing_owner_raises_not_found(self, fx_database: SqlDatabase) -> None:
        """Verify a project cannot be stored for an account that does not exist, reported by the account's identifier.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        """
        owner_id = new_account_id()
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            with pytest.raises(NotFoundError, match=str(owner_id)):
                async with uow.change():
                    await uow.projects.add(make_project(owner_id=owner_id))


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
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change():
                await uow.projects.add(project)
                session.add(JobMapper().to_row(make_job(project_id=project.id, state=JobState.RUNNING)))
        async with fx_database.sessions() as session:
            # A row added directly bypasses the repository, which would report the conflict as a domain error
            session.add(JobMapper().to_row(make_job(project_id=project.id, state=JobState.QUEUED)))
            with pytest.raises(IntegrityError, match=SQLITE_UNIQUE_FAILED):
                await session.flush()

    async def test_finished_imports_do_not_count(self, fx_database: SqlDatabase, fx_owner_id: AccountId) -> None:
        """Verify any number of finished imports may share a project with an active one.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        """
        project = make_project(owner_id=fx_owner_id)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change():
                await uow.projects.add(project)
                for state in (JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELLED, JobState.QUEUED):
                    session.add(JobMapper().to_row(make_job(project_id=project.id, state=state)))
            assert await session.scalar(select(func.count()).select_from(JobRow)) == len(JobState) - 1
