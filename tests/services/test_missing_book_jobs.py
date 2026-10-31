"""Tests for the jobs of a book that is gone by the time the job runs, which the board ends as failed.

A job opens its book once. A missing book is a ``NotFoundError`` on that first step: the entry points that answer a
domain error with a failed end do so, and the ones that let it pass leave it to the board, which fails the job.
"""

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from bookreviver.domain.accounts import Actor
from bookreviver.domain.ids import BookId, PageId, PageVersionId
from bookreviver.domain.jobs import (
    ContentDetection,
    JobKind,
    JobState,
    StageRun,
    StepPreview,
    TileCut,
    VersionCollection,
)
from bookreviver.domain.recipes import Step
from bookreviver.domain.stages import Stage
from tests.helpers.builders import EPOCH, new_account_id, open_for
from tests.helpers.processing import ProcessingKit, stored_job
from tests.helpers.processors import FakeContentProbe, FakeProcessor

if TYPE_CHECKING:
    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.services.book import Book
    from tests.helpers.builders import BookOpener

pytestmark = pytest.mark.anyio

JOB_ARG: str = 'job'
# The jobs that need no book to be valid, by the kind and the parameters their entry point reads
MISSING_BOOK_JOBS: list[tuple[JobKind, MetadataMap]] = [
    (JobKind.RUN_STAGE, StageRun(stage=Stage.GEOMETRY).to_map()),
    (
        JobKind.PREVIEW_STEP,
        StepPreview(
            page_id=PageId(uuid4()),
            stage=Stage.GEOMETRY,
            steps=(Step(processor_key=FakeProcessor.spec.key),),
            step_index=0,
        ).to_map(),
    ),
    (JobKind.COLLECT_VERSIONS, VersionCollection(previews_older_than=EPOCH + timedelta(days=1)).to_map()),
    (JobKind.MEASURE_BOOK, {}),
    (JobKind.CUT_TILES, TileCut(version_ids=(PageVersionId('0123456789abcdef'),)).to_map()),
    (JobKind.PREPARE_PAGES, {}),
]
MISSING_BOOK_IDS: list[str] = [kind for kind, _ in MISSING_BOOK_JOBS]


def missing_book() -> Book:
    """Open a book the library does not list, which the starter queues jobs of like any other.

    :returns: The book of a fresh identifier.
    :rtype: Book
    """
    return open_for(Actor(account_id=new_account_id()), BookId(uuid4()))


class TestJobsOfAMissingBook:
    """Tests for the end of each job whose book is not in the library."""

    @pytest.mark.parametrize(JOB_ARG, MISSING_BOOK_JOBS, ids=MISSING_BOOK_IDS)
    async def test_job_ends_failed(self, fx_kit: ProcessingKit, job: tuple[JobKind, MetadataMap]) -> None:
        """Verify a job of a book that is gone ends as failed, and the board holds it so.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param job: The kind of the job and the parameters its entry point reads.
        :type job: tuple[JobKind, MetadataMap]
        """
        kind, params = job
        queued = await fx_kit.parts().starter.enqueue(missing_book(), kind, params)

        await fx_kit.work_queue()

        assert stored_job(fx_kit, queued.id).state is JobState.FAILED

    async def test_detection_ends_failed_where_the_processor_is_installed(
        self,
        fx_database: SqlDatabase,
        fx_open_book: BookOpener,
    ) -> None:
        """Verify a detection of a book that is gone fails when the processor that reads the pages is installed.

        :param fx_database: Database of the test.
        :type fx_database: SqlDatabase
        :param fx_open_book: Opens the catalogue databases and the databases of the books of the test.
        :type fx_open_book: BookOpener
        """
        kit = ProcessingKit(fx_database, fx_open_book, processors=[FakeContentProbe([])])
        queued = await kit.parts().starter.enqueue(missing_book(), JobKind.DETECT_CONTENT, ContentDetection().to_map())

        await kit.work_queue()

        assert stored_job(kit, queued.id).state is JobState.FAILED

    async def test_detection_succeeds_where_the_processor_is_not_installed(self, fx_kit: ProcessingKit) -> None:
        """Verify a detection reads nothing and so opens nothing where no processor reads the pages, a book gone or not.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        queued = await fx_kit.parts().starter.enqueue(
            missing_book(), JobKind.DETECT_CONTENT, ContentDetection().to_map()
        )

        await fx_kit.work_queue()

        assert stored_job(fx_kit, queued.id).state is JobState.SUCCEEDED
