"""Tests for the SQLAlchemy tables: the database itself keeps pages and jobs tied to their project."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError

from bookreviver.adapters.persistence.sqlalchemy.mappers import PageMapper
from bookreviver.adapters.persistence.sqlalchemy.tables import JobRow, PageRow, ProjectRow
from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from tests.helpers.builders import make_job, make_page, make_project, new_account_id

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase

pytestmark = pytest.mark.anyio


class TestProjectRow:
    """Tests for the foreign keys pointing at ProjectRow."""

    async def test_database_deletes_pages_and_jobs_with_their_project(self, fx_database: SqlDatabase) -> None:
        """Verify a plain SQL delete of a project row removes its pages and jobs through the foreign keys."""
        project = make_project(owner_id=new_account_id())
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            await uow.projects.add(project)
            await uow.pages.replace_for_project(project.id, [make_page(project_id=project.id, index=0)])
            await uow.jobs.add(make_job(project_id=project.id))
            await uow.commit()
        async with fx_database.sessions() as session:
            # A bulk statement bypasses the ORM, so only the database can remove the dependent rows
            await session.execute(delete(ProjectRow).where(ProjectRow.id == project.id))
            await session.commit()
            expect(await session.scalar(select(func.count()).select_from(PageRow)) == 0)
            expect(await session.scalar(select(func.count()).select_from(JobRow)) == 0)
        assert_expectations()

    async def test_sqlite_rejects_a_page_of_a_missing_project(self, fx_database: SqlDatabase) -> None:
        """Verify SQLite enforces the foreign key, which it ignores unless the connection turns it on."""
        orphan = PageMapper().to_row(make_page(project_id=make_project(owner_id=new_account_id()).id, index=0))
        async with fx_database.sessions() as session:
            session.add(orphan)
            with pytest.raises(IntegrityError, match='FOREIGN KEY constraint failed'):
                await session.flush()
