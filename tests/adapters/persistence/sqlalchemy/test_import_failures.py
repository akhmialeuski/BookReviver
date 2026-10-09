"""Tests of what a failed import leaves in the SQL database, whose foreign keys the in-memory adapter only mirrors."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState
from bookreviver.domain.values import SliceRequest
from bookreviver.services.imports import UNEXPECTED_FAILURE
from tests.helpers.builders import make_project
from tests.helpers.fakes_imports import ImportRig, pdf_upload

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId

pytestmark = pytest.mark.anyio

PAGES: int = 3
EVERY_SLICE: SliceRequest = SliceRequest(limit=100)


class TestFailedImportOnSqlAlchemy:
    """Tests for ImportService.run_import() against the database, when a source's files cannot be promoted."""

    async def test_source_whose_files_cannot_be_promoted_leaves_no_row_of_it(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, tmp_path: Path
    ) -> None:
        """Verify the withdrawal removes the source, its scans and its pages, and the failed job counts none of them.

        The pages hold a foreign key to their scan and the scans to their source, so deleting the source alone would
        leave the pages of the book without a scan, and a source that was committed without its files.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        """
        rig = ImportRig.build(tmp_path / 'storage')
        samples = tmp_path / 'samples'
        samples.mkdir()
        project = make_project(owner_id=fx_owner_id)
        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            async with uow.change():
                await uow.projects.add(project)
        async with fx_database.sessions() as session:
            job = await rig.service(uow=SqlAlchemyUnitOfWork(session)).start_import(
                Actor(account_id=fx_owner_id), project.id, [pdf_upload(samples, 'a.pdf', pages=PAGES)]
            )
        rig.sources.fail_on_promote = OSError('disk full')

        async with fx_database.sessions() as session:
            await rig.service(uow=SqlAlchemyUnitOfWork(session)).run_import(job.id)

        async with fx_database.sessions() as session:
            uow = SqlAlchemyUnitOfWork(session)
            stored = await uow.jobs.get(job.id)
            expect(await uow.sources.list_for_project(project.id) == [])
            expect((await uow.scans.list_for_project(project.id, EVERY_SLICE)).total == 0)
            expect((await uow.pages.list_for_project(project.id, EVERY_SLICE)).total == 0)
            expect((stored.state, stored.error) == (JobState.FAILED, UNEXPECTED_FAILURE))
            expect((stored.progress.done, stored.progress.total) == (0, 0))
        assert_expectations()
