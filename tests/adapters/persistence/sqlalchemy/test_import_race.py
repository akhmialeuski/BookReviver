"""Test of two uploads to one project that race on SQLAlchemy, where only the database can keep one of them out."""

from typing import TYPE_CHECKING, override

import anyio
import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.sqlalchemy.unit_of_work import SqlAlchemyUnitOfWork
from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.domain.entities import Actor, Job
from bookreviver.domain.enums import JobState
from bookreviver.domain.errors import ConflictError
from bookreviver.services.imports import IMPORT_ACTIVE
from tests.helpers.builders import make_project
from tests.helpers.fakes_imports import ImportRig, image_upload

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.ids import AccountId, JobId, ProjectId
    from bookreviver.domain.values import SourceFile
    from bookreviver.ports.storage import IncomingFile

pytestmark = pytest.mark.anyio

UPLOADS: int = 2


class MeetingSourceStore(LocalSourceStore):
    """The local source store, which holds every upload after it is received until all the racing uploads are.

    An upload receives its files after the service checked that the project is idle, so holding it there lets both
    uploads pass the check before either stores its job, which is the race the partial unique index closes.
    """

    def __init__(self, *, root: Path) -> None:
        """Keep the files under ``root`` and wait for ``UPLOADS`` uploads.

        :param root: Storage root, created on the first write.
        :type root: Path
        """
        super().__init__(root=root)
        self._arrived = 0
        self._all_arrived = anyio.Event()

    @override
    async def stage(
        self, project_id: ProjectId, job_id: JobId, files: Sequence[IncomingFile], *, max_bytes: int
    ) -> Sequence[SourceFile]:
        """Receive the upload, then wait until every racing upload has been received.

        :param project_id: Project receiving the upload.
        :type project_id: ProjectId
        :param job_id: Import job owning the upload.
        :type job_id: JobId
        :param files: Uploaded files.
        :type files: Sequence[IncomingFile]
        :param max_bytes: Largest total size of the upload in bytes.
        :type max_bytes: int
        :returns: Name, size and SHA-256 digest of every staged file.
        :rtype: Sequence[SourceFile]
        """
        staged = await super().stage(project_id, job_id, files, max_bytes=max_bytes)
        self._arrived += 1
        if self._arrived == UPLOADS:
            self._all_arrived.set()
        await self._all_arrived.wait()
        return staged


class TestStartImportOnSqlAlchemy:
    """Tests for ImportService.start_import() against the database that enforces one import per project."""

    async def test_two_uploads_that_race_store_one_job_and_the_other_gets_a_conflict(
        self, fx_database: SqlDatabase, fx_owner_id: AccountId, tmp_path: Path
    ) -> None:
        """Verify two uploads that both passed the check leave one job, one upload and a 409-bound conflict.

        :param fx_database: Fresh SQLite database with every table created.
        :type fx_database: SqlDatabase
        :param fx_owner_id: Committed account owning the project.
        :type fx_owner_id: AccountId
        :param tmp_path: Temporary directory of the test, holding the storage root.
        :type tmp_path: Path
        """
        rig = ImportRig.build(tmp_path / 'storage')
        sources = MeetingSourceStore(root=tmp_path / 'storage')
        samples = tmp_path / 'samples'
        samples.mkdir()
        project = make_project(owner_id=fx_owner_id)
        async with fx_database.sessions() as session:
            await SqlAlchemyUnitOfWork(session).projects.add(project)
            await session.commit()
        outcomes: list[Job | ConflictError] = []

        async def upload(name: str) -> None:
            """Upload one file to the project in a session of its own, as a request would, and record the outcome.

            :param name: Name of the uploaded file.
            :type name: str
            """
            async with fx_database.sessions() as session:
                service = rig.service(uow=SqlAlchemyUnitOfWork(session), sources=sources)
                try:
                    outcomes.append(
                        await service.start_import(
                            Actor(account_id=fx_owner_id), project.id, [image_upload(samples, name)]
                        )
                    )
                except ConflictError as error:
                    outcomes.append(error)

        async with anyio.create_task_group() as group:
            group.start_soon(upload, 'first.jpg')
            group.start_soon(upload, 'second.jpg')

        async with fx_database.sessions() as session:
            stored = await SqlAlchemyUnitOfWork(session).jobs.list_for_project(project.id, set(JobState))
        winners = [outcome for outcome in outcomes if isinstance(outcome, Job)]
        losers = [outcome for outcome in outcomes if isinstance(outcome, ConflictError)]
        expect(len(winners) == len(losers) == 1)
        expect([job.id for job in stored] == [winners[0].id])
        expect(str(losers[0]) == IMPORT_ACTIVE)
        expect([path.parent.name for path in (tmp_path / 'storage').rglob('*.jpg')] == [str(winners[0].id)])
        assert_expectations()
