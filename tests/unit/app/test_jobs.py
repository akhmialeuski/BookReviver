"""Tests for the workers of the background jobs: every kind of job has one, which is one method of a service."""

from unittest.mock import Mock

import pytest
from delayed_assert import assert_expectations, expect

from bookreviver.app.jobs import JobWorkers, job_workers
from bookreviver.domain.jobs import JobKind
from bookreviver.services.imports import ImportService
from bookreviver.services.page_history import PageHistoryService
from bookreviver.services.pages import PageService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs


class TestJobWorkers:
    """Tests for job_workers and JobWorkers.of."""

    def test_every_kind_of_job_has_a_worker(self) -> None:
        """Verify no kind of job is left without a worker, so the runner refuses none the application can put."""
        workers = job_workers(
            Mock(spec=ImportService),
            Mock(spec=PageService),
            Mock(spec=ProcessingJobs),
            Mock(spec=ProcessingService),
            Mock(spec=PageHistoryService),
        )
        assert set(workers.by_kind) == set(JobKind)

    def test_a_worker_is_the_method_of_the_service_that_does_the_job(self) -> None:
        """Verify each kind is done by its own service: the import, the page service, the processing jobs or services."""
        imports, pages, processing = Mock(spec=ImportService), Mock(spec=PageService), Mock(spec=ProcessingJobs)
        choices, history = Mock(spec=ProcessingService), Mock(spec=PageHistoryService)
        workers = job_workers(imports, pages, processing, choices, history)
        expect(workers.of(JobKind.IMPORT_SOURCE) is imports.run_import)
        expect(workers.of(JobKind.PREPARE_PAGES) is pages.prepare_images)
        expect(workers.of(JobKind.RUN_STAGE) is processing.run_stage)
        expect(workers.of(JobKind.MEASURE_BOOK) is processing.measure_book)
        expect(workers.of(JobKind.CHOOSE_VERSION) is choices.make_current)
        expect(workers.of(JobKind.CLEAR_STEP) is history.clear_step)
        assert_expectations()

    def test_a_kind_without_a_worker_is_refused(self) -> None:
        """Verify the runner learns at once of a kind that no worker is named for."""
        with pytest.raises(KeyError):
            JobWorkers({}).of(JobKind.RUN_STAGE)
