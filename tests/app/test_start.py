"""Tests for the start of the application: it begins no job, and removes the uploads no job can use any more."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.ids import JobId, ProjectId, SourceId
from tests.helpers.fakes_jobs import FakeBoardProvider, JobFakes, JobFakesProvider
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from collections.abc import Sequence

    from dishka import Provider

    from bookreviver.app.settings import Settings

pytestmark = pytest.mark.anyio

UPLOAD_LIMIT_BYTES: int = 1024
UPLOAD_NAME: str = 'book.pdf'
PROMOTED_NAME: str = 'part.pdf'


class Uploads:
    """The uploads and the source the test left on disk before the application started.

    :ivar store: Source store over the storage root of the application.
    :ivar interrupted: The projects with an upload an interrupted import left staged, each with the job it belonged to.
    :ivar kept: The project and the source whose files an earlier import had promoted.
    """

    def __init__(self, store: LocalSourceStore) -> None:
        """Start with nothing on disk.

        :param store: Source store over the storage root of the application.
        :type store: LocalSourceStore
        """
        self.store = store
        self.interrupted: list[tuple[ProjectId, JobId]] = []
        self.kept: tuple[ProjectId, SourceId] | None = None

    async def leave(self) -> None:
        """Stage an upload in each of two projects, and promote the files of a source in a third."""
        for _ in range(2):
            project_id, job_id = ProjectId(uuid4()), JobId(uuid4())
            await self.store.stage(project_id, job_id, [upload(UPLOAD_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
            self.interrupted.append((project_id, job_id))
        project_id, job_id, source_id = ProjectId(uuid4()), JobId(uuid4()), SourceId(uuid4())
        await self.store.stage(project_id, job_id, [upload(PROMOTED_NAME)], max_bytes=UPLOAD_LIMIT_BYTES)
        await self.store.promote(project_id, job_id, source_id, names=[PROMOTED_NAME])
        self.kept = (project_id, source_id)


@pytest.fixture
def fx_fakes() -> JobFakes:
    """Build the adapters the application runs on, with an empty database and a board that runs nothing.

    :returns: Fakes with nothing stored and no job on the board.
    :rtype: JobFakes
    """
    return JobFakes()


@pytest.fixture
async def fx_uploads(fx_settings: Settings) -> Uploads:
    """Leave the uploads on disk before the application starts.

    :param fx_settings: Settings of the application, which name the storage root.
    :type fx_settings: Settings
    :returns: The uploads left on disk.
    :rtype: Uploads
    """
    uploads = Uploads(LocalSourceStore(root=fx_settings.storage_root))
    await uploads.leave()
    return uploads


@pytest.fixture
def fx_extra_providers(fx_fakes: JobFakes, fx_uploads: Uploads) -> Sequence[Provider]:
    """Give the application the fakes and the fake board, once the uploads are on disk.

    :param fx_fakes: Adapters of the test.
    :type fx_fakes: JobFakes
    :param fx_uploads: The uploads left on disk, which the fixture is ordered after.
    :type fx_uploads: Uploads
    :returns: The providers of the fakes and of the board.
    :rtype: Sequence[Provider]
    """
    del fx_uploads
    return (JobFakesProvider(fx_fakes), FakeBoardProvider(fx_fakes.board))


class TestStart:
    """Tests for what the lifespan of the application does before it serves a request."""

    @pytest.mark.usefixtures('fx_app')
    async def test_removes_the_staged_uploads_and_keeps_the_sources(self, fx_uploads: Uploads) -> None:
        """Verify the upload an interrupted import left in each project is gone, and a promoted source stays.

        The running application is only started, and no request is made, so the removal can only come from the start.

        :param fx_uploads: The uploads left on disk before the start.
        :type fx_uploads: Uploads
        """
        gone: list[bool] = []
        for project_id, job_id in fx_uploads.interrupted:
            try:
                async with fx_uploads.store.staged_files(project_id, job_id):
                    gone.append(False)
            except NotFoundError:
                gone.append(True)
        assert fx_uploads.kept is not None
        async with fx_uploads.store.source_files(*fx_uploads.kept) as files:
            kept = [file.name for file in files]
        expect(gone == [True, True])
        expect(kept == [PROMOTED_NAME])
        assert_expectations()

    @pytest.mark.usefixtures('fx_app')
    async def test_queues_no_job(self, fx_fakes: JobFakes) -> None:
        """Verify nothing is put on the board by the start, so no job begins before the user asks for one.

        :param fx_fakes: Adapters of the test, whose board the application puts its jobs on.
        :type fx_fakes: JobFakes
        """
        expect(fx_fakes.board.put_jobs == [])
        expect(fx_fakes.events.published == [])
        assert_expectations()
