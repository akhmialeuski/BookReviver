"""Tests for the import service: upload rules, ownership, the conflict rule and the import job."""

from typing import TYPE_CHECKING, NamedTuple

import anyio
import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import JobState, PageAsset, SourceKind, UploadProblem
from bookreviver.domain.errors import ConflictError, NotFoundError, UnsupportedSourceError, UploadRejectedError
from bookreviver.domain.events import JobChanged, PageReady, ProjectChanged
from bookreviver.domain.values import BookDetails, MetadataSuggestion, SliceRequest
from bookreviver.services.imports import UNEXPECTED_FAILURE, ImportLimits, source_kind_of
from tests.helpers.builders import make_job, make_project, new_account_id
from tests.helpers.fakes_imports import DEFAULT_LIMITS, INCOMING_DIR, SOURCE_DIR, FakeUpload, ImportFakes

if TYPE_CHECKING:
    from pathlib import Path

    from bookreviver.domain.entities import Job, Project

pytestmark = pytest.mark.anyio

OLD_PDF: str = 'old.pdf'
NEW_PDF: str = 'new.pdf'
PAGE_PNG: str = 'p1.png'
PAGE_COUNT: int = 5
HOLD_TIMEOUT_SECONDS: float = 10
BROKEN_SOURCE: str = 'The PDF file cannot be read.'
SUGGESTION: MetadataSuggestion = MetadataSuggestion(
    title='Suggested title', authors='Suggested author', publisher='Suggested publisher', language='be'
)
KEPT_PUBLISHER: str = 'Publisher set by the owner'


class RejectedNames(NamedTuple):
    """A file set breaking an upload rule and the rule it breaks."""

    names: list[str]
    problem: UploadProblem


@pytest.fixture
def fx_fakes(tmp_path: Path) -> ImportFakes:
    """Build the fakes of the import ports with their files under the test's directory."""
    return ImportFakes.under(tmp_path)


@pytest.fixture
def fx_owner() -> Actor:
    """Build the account that owns the project."""
    return Actor(account_id=new_account_id())


@pytest.fixture
async def fx_project(fx_fakes: ImportFakes, fx_owner: Actor) -> Project:
    """Store a project of ``fx_owner`` whose publisher is already filled in."""
    project = make_project(owner_id=fx_owner.account_id)
    project = evolve(project, details=evolve(project.details, publisher=KEPT_PUBLISHER))
    await fx_fakes.store(project)
    return project


async def _import(fakes: ImportFakes, owner: Actor, project: Project, name: str) -> Job:
    """Upload one file as the project's source and run the import job to its end."""
    job = await fakes.import_service().start_import(owner, project.id, [FakeUpload(name)])
    await fakes.import_service().run_import(job.id)
    return await InMemoryUnitOfWork(fakes.database).jobs.get(job.id)


class TestSourceKindOf:
    """Tests for source_kind_of()."""

    @pytest.mark.parametrize(
        ('names', 'kind'),
        [(['BOOK.PDF'], SourceKind.PDF), (['p1.tif', 'p2.JPEG', 'p3.png'], SourceKind.IMAGES)],
        ids=['one-pdf', 'images'],
    )
    def test_accepted_file_set_gives_its_kind(self, names: list[str], kind: SourceKind) -> None:
        """Verify one PDF, or images of any accepted type, make a source of the matching kind."""
        assert source_kind_of(names) == kind

    @pytest.mark.parametrize(
        'case',
        [
            RejectedNames([], UploadProblem.NO_FILES),
            RejectedNames([NEW_PDF, '  '], UploadProblem.EMPTY_NAME),
            RejectedNames([PAGE_PNG, PAGE_PNG.upper()], UploadProblem.DUPLICATE_NAME),
            RejectedNames(['notes.txt'], UploadProblem.UNSUPPORTED_TYPE),
            RejectedNames(['README'], UploadProblem.UNSUPPORTED_TYPE),
            RejectedNames([NEW_PDF, PAGE_PNG], UploadProblem.MIXED_TYPES),
            RejectedNames([OLD_PDF, NEW_PDF], UploadProblem.MIXED_TYPES),
        ],
        ids=['none', 'empty-name', 'duplicate-ignoring-case', 'text', 'suffixless', 'pdf-and-image', 'two-pdfs'],
    )
    def test_broken_rule_is_rejected(self, case: RejectedNames) -> None:
        """Verify each upload rule rejects the file set with its own problem."""
        with pytest.raises(UploadRejectedError) as caught:
            source_kind_of(case.names)
        assert caught.value.problem is case.problem


class TestStartImport:
    """Tests for ImportService.start_import()."""

    async def test_upload_is_staged_recorded_and_enqueued(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify an accepted upload is staged, recorded as a queued job, announced and enqueued."""
        job = await fx_fakes.import_service().start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])
        staged = await fx_fakes.sources.names_in(fx_project.id, INCOMING_DIR)
        expect(job.state is JobState.QUEUED)
        expect(await InMemoryUnitOfWork(fx_fakes.database).jobs.get(job.id) == job)
        expect(fx_fakes.queue.enqueued == [job])
        expect(fx_fakes.events.published == [JobChanged(project_id=fx_project.id, job=job)])
        expect([path.name for path in staged] == [NEW_PDF])
        assert_expectations()

    async def test_broken_rule_rejects_before_staging(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a file set breaking a rule records no job and stages nothing."""
        files = [FakeUpload(NEW_PDF), FakeUpload(PAGE_PNG)]
        with pytest.raises(UploadRejectedError):
            await fx_fakes.import_service().start_import(fx_owner, fx_project.id, files)
        expect(fx_fakes.queue.enqueued == [])
        expect(await fx_fakes.sources.names_in(fx_project.id, INCOMING_DIR) == [])
        assert_expectations()

    async def test_too_large_upload_is_discarded(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify an upload over the size limit is rejected as too large, discarded, and records no job."""
        service = fx_fakes.import_service(ImportLimits(max_upload_bytes=4, parallel_pages=1))
        with pytest.raises(UploadRejectedError) as caught:
            await service.start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])
        jobs = await InMemoryUnitOfWork(fx_fakes.database).jobs.list_for_project(fx_project.id, frozenset(JobState))
        expect(caught.value.problem is UploadProblem.TOO_LARGE)
        expect(fx_fakes.sources.discarded == [fx_project.id])
        expect(await fx_fakes.sources.names_in(fx_project.id, INCOMING_DIR) == [])
        expect(jobs == [])
        assert_expectations()

    async def test_unreachable_queue_fails_job_and_frees_project(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a job that cannot be enqueued is marked failed and discarded, so a later upload is accepted."""
        fx_fakes.queue.error = ConnectionError('broker down')
        with pytest.raises(ConnectionError):
            await fx_fakes.import_service().start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])
        jobs = await InMemoryUnitOfWork(fx_fakes.database).jobs.list_for_project(fx_project.id, frozenset(JobState))
        expect([(job.state, job.error) for job in jobs] == [(JobState.FAILED, UNEXPECTED_FAILURE)])
        expect(await fx_fakes.sources.names_in(fx_project.id, INCOMING_DIR) == [])
        assert_expectations()
        fx_fakes.queue.error = None
        retried = await fx_fakes.import_service().start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])
        assert fx_fakes.queue.enqueued == [retried]

    async def test_project_of_another_account_is_not_found(self, fx_fakes: ImportFakes, fx_project: Project) -> None:
        """Verify an account cannot import into, or learn about, a project it does not own."""
        stranger = Actor(account_id=new_account_id())
        with pytest.raises(NotFoundError):
            await fx_fakes.import_service().start_import(stranger, fx_project.id, [FakeUpload(NEW_PDF)])
        assert fx_fakes.queue.enqueued == []

    @pytest.mark.parametrize(
        ('state', 'accepted'),
        [
            (JobState.QUEUED, False),
            (JobState.RUNNING, False),
            (JobState.SUCCEEDED, True),
            (JobState.FAILED, True),
            (JobState.CANCELLED, True),
        ],
    )
    async def test_active_import_blocks_another(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project, *, state: JobState, accepted: bool
    ) -> None:
        """Verify a queued or running import refuses a new upload and a finished one does not."""
        uow = InMemoryUnitOfWork(fx_fakes.database)
        await uow.jobs.add(make_job(project_id=fx_project.id, state=state))
        await uow.commit()
        service = fx_fakes.import_service()
        if accepted:
            await service.start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])
        else:
            with pytest.raises(ConflictError):
                await service.start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])
        assert len(fx_fakes.queue.enqueued) == int(accepted)


class TestRunImport:
    """Tests for ImportService.run_import()."""

    async def test_import_produces_ready_pages_and_events_in_order(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a full import promotes the source, fills empty fields, produces every page and reports it all."""
        fx_fakes.inspector.page_count = PAGE_COUNT
        fx_fakes.inspector.suggestion = SUGGESTION
        job = await _import(fx_fakes, fx_owner, fx_project, NEW_PDF)
        uow = InMemoryUnitOfWork(fx_fakes.database)
        project = await uow.projects.get(fx_project.id)
        pages = (await uow.pages.list_for_project(fx_project.id, SliceRequest())).items
        events = fx_fakes.events.published
        job_events = [event.job for event in events if isinstance(event, JobChanged)]
        ready_events = [event.page for event in events if isinstance(event, PageReady)]
        source_names = [path.name for path in await fx_fakes.sources.names_in(fx_project.id, SOURCE_DIR)]

        expect((job.state, job.progress.done, job.progress.total) == (JobState.SUCCEEDED, PAGE_COUNT, PAGE_COUNT))
        expect(source_names == [NEW_PDF])
        expect(project.source is not None and (project.source.kind, project.source.name) == (SourceKind.PDF, NEW_PDF))
        expect(project.source is not None and project.source.size_bytes == len(FakeUpload(NEW_PDF).content))
        expect(
            project.details
            == evolve(
                BookDetails(title=fx_project.details.title),
                publisher=KEPT_PUBLISHER,
                authors=SUGGESTION.authors,
                language=SUGGESTION.language,
            )
        )
        expect([(page.index, page.assets.ready) for page in pages] == [(index, True) for index in range(PAGE_COUNT)])
        expect(set(fx_fakes.assets.published) == {page.asset_key(asset) for page in pages for asset in PageAsset})
        # Queued, running, the page total, one progress step per page, succeeded
        running = [JobState.RUNNING] * (PAGE_COUNT + 2)
        expect([event.state for event in job_events] == [JobState.QUEUED, *running, JobState.SUCCEEDED])
        expect([event.progress.done for event in job_events[2:]] == [*range(PAGE_COUNT + 1), PAGE_COUNT])
        expect(sorted(page.index for page in ready_events) == list(range(PAGE_COUNT)))
        expect(all(page.assets.ready for page in ready_events))
        expect(type(events[2]) is ProjectChanged)
        assert_expectations()

    async def test_pages_run_at_most_parallel_pages_at_a_time(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify page images are produced concurrently, but never more than the limit at once."""
        fx_fakes.inspector.page_count = PAGE_COUNT
        fx_fakes.rasterizer.hold_until_active = DEFAULT_LIMITS.parallel_pages
        # Pages that could never run together would wait for each other forever
        with anyio.fail_after(HOLD_TIMEOUT_SECONDS):
            job = await _import(fx_fakes, fx_owner, fx_project, NEW_PDF)
        expect(job.state is JobState.SUCCEEDED)
        expect(fx_fakes.rasterizer.max_active == DEFAULT_LIMITS.parallel_pages)
        assert_expectations()

    async def test_reimport_moves_pages_to_a_new_asset_version(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a second import gives its pages new asset keys, so cached images of the old source are not reused."""
        await _import(fx_fakes, fx_owner, fx_project, OLD_PDF)
        await _import(fx_fakes, fx_owner, fx_project, NEW_PDF)
        pages = (
            await InMemoryUnitOfWork(fx_fakes.database).pages.list_for_project(fx_project.id, SliceRequest())
        ).items
        assert {page.assets.version for page in pages} == {1}

    async def test_unsupported_source_fails_and_keeps_previous_source(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify an unreadable upload fails the job with its reason, is discarded, and leaves the old book intact."""
        await _import(fx_fakes, fx_owner, fx_project, OLD_PDF)
        before = InMemoryUnitOfWork(fx_fakes.database)
        old_project = await before.projects.get(fx_project.id)
        old_pages = await before.pages.list_for_project(fx_project.id, SliceRequest())
        fx_fakes.inspector.error = UnsupportedSourceError(BROKEN_SOURCE)

        job = await _import(fx_fakes, fx_owner, fx_project, NEW_PDF)
        after = InMemoryUnitOfWork(fx_fakes.database)
        last_event = fx_fakes.events.published[-1]
        expect((job.state, job.error) == (JobState.FAILED, BROKEN_SOURCE))
        expect(isinstance(last_event, JobChanged) and last_event.job == job)
        expect([path.name for path in await fx_fakes.sources.names_in(fx_project.id, SOURCE_DIR)] == [OLD_PDF])
        expect(await fx_fakes.sources.names_in(fx_project.id, INCOMING_DIR) == [])
        expect(await after.projects.get(fx_project.id) == old_project)
        expect(await after.pages.list_for_project(fx_project.id, SliceRequest()) == old_pages)
        assert_expectations()

    async def test_page_failure_fails_job_without_internal_detail(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify an unexpected error while producing a page fails the job with a message that hides internals."""
        fx_fakes.rasterizer.failing_index = 1
        job = await _import(fx_fakes, fx_owner, fx_project, NEW_PDF)
        expect((job.state, job.error) == (JobState.FAILED, UNEXPECTED_FAILURE))
        expect(job.finished_at == fx_fakes.clock.now())
        assert_expectations()

    async def test_job_cancelled_before_start_does_nothing(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a job cancelled while queued never touches the project's source or pages."""
        job = await fx_fakes.import_service().start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])
        await fx_fakes.job_service().cancel(fx_owner, job.id)
        await fx_fakes.import_service().run_import(job.id)
        uow = InMemoryUnitOfWork(fx_fakes.database)
        expect((await uow.jobs.get(job.id)).state is JobState.CANCELLED)
        expect((await uow.projects.get(fx_project.id)).source is None)
        expect(await fx_fakes.sources.names_in(fx_project.id, SOURCE_DIR) == [])
        expect(fx_fakes.rasterizer.extracted == [])
        assert_expectations()

    async def test_job_cancelled_while_running_stops_producing_pages(
        self, fx_fakes: ImportFakes, fx_owner: Actor, fx_project: Project
    ) -> None:
        """Verify a cancellation made by another request stops the remaining pages and is not overwritten."""
        fx_fakes.inspector.page_count = PAGE_COUNT
        job = await fx_fakes.import_service().start_import(fx_owner, fx_project.id, [FakeUpload(NEW_PDF)])

        async def cancel_at_second_page(index: int) -> None:
            if index == 1:
                await fx_fakes.job_service().cancel(fx_owner, job.id)

        fx_fakes.rasterizer.before_extract = cancel_at_second_page
        await fx_fakes.import_service().run_import(job.id)
        stored = await InMemoryUnitOfWork(fx_fakes.database).jobs.get(job.id)
        states = [event.job.state for event in fx_fakes.events.published if isinstance(event, JobChanged)]
        expect(stored.state is JobState.CANCELLED)
        expect(stored.progress.done < PAGE_COUNT)
        expect(JobState.SUCCEEDED not in states)
        # Every page commit re-reads the job first, so nothing is written after the cancellation
        expect(states[-1] is JobState.CANCELLED)
        assert_expectations()
