"""Tests of what a failed import leaves in the SQL database, whose foreign keys refuse a row without its parent."""

from typing import TYPE_CHECKING

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState
from bookreviver.domain.values import JobEnd, SliceRequest
from tests.helpers.builders import make_project
from tests.helpers.fakes_imports import pdf_upload

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId
    from tests.helpers.fakes_imports import ImportRig

pytestmark = pytest.mark.anyio

PAGES: int = 3
EVERY_SLICE: SliceRequest = SliceRequest(limit=100)


class TestFailedImportOnSqlAlchemy:
    """Tests for ImportService.run_import() against the database, when a source's files cannot be promoted."""

    async def test_source_whose_files_cannot_be_promoted_leaves_no_row_of_it(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, fx_import_rig: Callable[..., ImportRig], tmp_path: Path
    ) -> None:
        """Verify the withdrawal removes the source, its scans and its pages, and the failed job counts none of them.

        The pages hold a foreign key to their scan and the scans to their source, so deleting the source alone would
        leave the pages of the book without a scan, and a source that was committed without its files. The withdrawn
        source is no rejected one either, since the job failed and nobody reads a report of it.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param fx_import_rig: Function building the import rig whose board runs an import on the database.
        :type fx_import_rig: Callable[..., ImportRig]
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        """
        rig = fx_import_rig()
        samples = tmp_path / 'samples'
        samples.mkdir()
        project = make_project(owner_id=fx_owner_id)
        uow = SqlAlchemyUnitOfWork(fx_database)
        await uow.change(lambda repos: repos.projects.add(project))
        job = await rig.service(uow=uow).start_import(
            Actor(account_id=fx_owner_id), project.id, [pdf_upload(samples, 'a.pdf', pages=PAGES)]
        )
        rig.sources.fail_on_promote = OSError('disk full')

        await rig.fakes.board.run_queued()

        sources, unimported, scans, pages = await uow.read(
            lambda repos: (
                repos.sources.list_for_project(project.id),
                repos.sources.list_unimported(project.id),
                repos.scans.list_for_project(project.id, EVERY_SLICE),
                repos.pages.list_for_project(project.id, EVERY_SLICE),
            )
        )
        stored = rig.stored_job(job)
        expect(sources == [])
        expect(unimported == [])
        expect(scans.total == 0)
        expect(pages.total == 0)
        expect((stored.state, stored.error) == (JobState.FAILED, JobEnd.UNEXPECTED_FAILURE))
        # The progress counts the one file of the upload, and none of its scans
        expect((stored.progress.done, stored.progress.total) == (0, 1))
        assert_expectations()
