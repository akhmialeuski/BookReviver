"""Import of a book source: accepting an upload, and the background job that turns it into pages."""

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
from attrs import evolve, field, fields, fields_dict, frozen, validators

from bookreviver.domain.entities import Job, Page
from bookreviver.domain.enums import FileType, JobKind, JobState, PageAsset, SourceKind, UploadProblem
from bookreviver.domain.errors import ConflictError, DomainError, NotFoundError, UploadRejectedError
from bookreviver.domain.events import JobChanged, PageReady, ProjectChanged
from bookreviver.domain.ids import JobId
from bookreviver.domain.values import BookDetails, MetadataSuggestion, PageAssets, Progress, SliceRequest, SourceSummary

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.entities import Actor
    from bookreviver.domain.ids import ProjectId
    from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
    from bookreviver.ports.storage import AssetStore, IncomingFile, SourceStore

logger = logging.getLogger(__name__)

ACTIVE_STATES: frozenset[JobState] = frozenset({JobState.QUEUED, JobState.RUNNING})
IMPORT_ACTIVE: str = 'An import of this project is already in progress.'
UNEXPECTED_FAILURE: str = 'The import failed because of an internal error, which has been logged.'
# Description fields an import fills from the source when they are empty; the title stays the owner's choice
SUGGESTED_FIELDS: tuple[str, ...] = tuple(
    name for name in fields_dict(MetadataSuggestion) if name != fields(BookDetails).title.name
)


@frozen(kw_only=True)
class ImportLimits:
    """Tunables of the import, taken from the settings by the composition root."""

    max_upload_bytes: int = field(validator=validators.gt(0))
    parallel_pages: int = field(validator=validators.gt(0))


@frozen(kw_only=True)
class ImportStorage:
    """Where an import keeps the uploaded source and the images derived from it."""

    sources: SourceStore
    assets: AssetStore


@frozen(kw_only=True)
class ImportImaging:
    """The imaging adapters an import drives: reading the source, extracting pages, cutting tiles."""

    inspector: SourceInspector
    rasterizer: PageRasterizer
    tiler: Tiler


@frozen(kw_only=True)
class ImportRuntime:
    """How an import is scheduled, reported and timed."""

    queue: JobQueue
    publisher: EventPublisher
    clock: Clock


class _ImportCancelledError(Exception):
    """The job was cancelled while its pages were being produced."""


def source_kind_of(names: Sequence[str]) -> SourceKind:
    """Return the kind of source that files with these names make.

    :raises UploadRejectedError: If the names break an upload rule.
    """
    if not names:
        raise UploadRejectedError(UploadProblem.NO_FILES)
    if not all(name.strip() for name in names):
        raise UploadRejectedError(UploadProblem.EMPTY_NAME)
    # Case-insensitive, because the storage may live on a file system that does not tell the cases apart
    if len({name.casefold() for name in names}) != len(names):
        raise UploadRejectedError(UploadProblem.DUPLICATE_NAME)
    file_types = [FileType.from_name(name) for name in names]
    if None in file_types:
        raise UploadRejectedError(UploadProblem.UNSUPPORTED_TYPE)
    kinds = {file_type.source_kind for file_type in file_types if file_type is not None}
    if kinds == {SourceKind.IMAGES} or (kinds == {SourceKind.PDF} and len(names) == 1):
        return kinds.pop()
    raise UploadRejectedError(UploadProblem.MIXED_TYPES)


def _describe(error: BaseException) -> str:
    """Return what the account holder is told about ``error``, never an internal detail."""
    if isinstance(error, BaseExceptionGroup):
        return _describe(error.exceptions[0])
    return str(error) if isinstance(error, DomainError) else UNEXPECTED_FAILURE


class ImportService:
    """Accepts the upload of a book source and runs the job that turns it into pages with their images."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        storage: ImportStorage,
        imaging: ImportImaging,
        runtime: ImportRuntime,
        limits: ImportLimits,
    ) -> None:
        self._uow = uow
        self._sources = storage.sources
        self._assets = storage.assets
        self._imaging = imaging
        self._queue = runtime.queue
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._limits = limits
        # Pages are produced concurrently, but the unit of work takes one change at a time
        self._page_slots = asyncio.Semaphore(limits.parallel_pages)
        self._writing = asyncio.Lock()

    async def start_import(self, actor: Actor, project_id: ProjectId, files: Sequence[IncomingFile]) -> Job:
        """Stage an upload as the project's next source and enqueue the job that imports it.

        :raises NotFoundError:       If the project does not exist or belongs to another account.
        :raises UploadRejectedError: If the file set breaks an upload rule or is too large.
        :raises ConflictError:       If an import of the project is queued or running.
        """
        project = await self._uow.projects.get(project_id)
        if not project.is_owned_by(actor):
            raise NotFoundError(project_id)
        source_kind_of([file.filename or '' for file in files])
        active = await self._uow.jobs.list_for_project(project_id, ACTIVE_STATES)
        if any(job.kind is JobKind.IMPORT_SOURCE for job in active):
            raise ConflictError(IMPORT_ACTIVE)
        job = Job(id=JobId(uuid4()), project_id=project_id, kind=JobKind.IMPORT_SOURCE, created_at=self._clock.now())
        try:
            await self._sources.stage(project_id, files, max_bytes=self._limits.max_upload_bytes)
            await self._uow.jobs.add(job)
            await self._uow.commit()
        except BaseException:
            await self._sources.discard(project_id)
            raise
        await self._publisher.publish(JobChanged(project_id=project_id, job=job))
        try:
            await self._queue.enqueue(job)
        except Exception:
            # A queued job no worker will run would block every later import of the project
            await self._sources.discard(project_id)
            await self._finish(job.id, JobState.FAILED, UNEXPECTED_FAILURE)
            raise
        return job

    async def run_import(self, job_id: JobId) -> None:
        """Make the staged upload the project's source, then produce the images of every page.

        A job cancelled before it started does nothing. Any failure marks the job failed with a readable error, and
        a failure before the upload is promoted also discards it, so the previous source stays in place.
        """
        job = await self._uow.jobs.get(job_id)
        if job.state is not JobState.QUEUED:
            return
        await self._save(evolve(job, state=JobState.RUNNING, started_at=self._clock.now()))
        try:
            pages = await self._replace_source(job)
            completed = pages is not None and await self._produce_assets(job_id, pages)
        except Exception as error:
            logger.exception('Import job %s failed', job_id)
            await self._finish(job_id, JobState.FAILED, _describe(error))
            return
        if completed:
            await self._finish(job_id, JobState.SUCCEEDED)

    async def _replace_source(self, job: Job) -> Sequence[Page] | None:
        """Inspect and promote the staged upload, and replace the pages and the source summary of the project.

        :return: The new pages, or None when the job was cancelled before the upload was promoted.
        """
        project_id = job.project_id
        try:
            async with self._sources.staged_files(project_id) as files:
                names = [path.name for path in files]
                analysis = await self._imaging.inspector.inspect(source_kind_of(names), files)
                size_bytes = sum([(await anyio.Path(path).stat()).st_size for path in files])
            cancelled = (await self._fresh_job(job.id)).state.is_final
        except BaseException:
            await self._sources.discard(project_id)
            raise
        if cancelled:
            await self._sources.discard(project_id)
            return None
        await self._sources.promote(project_id)

        # New pages get a newer asset version than any old page, so no cached image of the old source is reused
        previous = await self._uow.pages.list_for_project(project_id, SliceRequest(limit=1))
        if previous.total:
            previous = await self._uow.pages.list_for_project(project_id, SliceRequest(limit=previous.total))
        version = max((page.assets.version + 1 for page in previous.items), default=0)
        pages = [
            Page(project_id=project_id, index=index, facts=facts, assets=PageAssets(version=version))
            for index, facts in enumerate(analysis.pages)
        ]
        await self._uow.pages.replace_for_project(project_id, pages)

        project = await self._uow.projects.get(project_id)
        now = self._clock.now()
        suggested = {
            name: value
            for name in SUGGESTED_FIELDS
            if not getattr(project.details, name) and (value := getattr(analysis.suggestion, name))
        }
        source = SourceSummary(
            kind=analysis.kind,
            name=names[0],
            size_bytes=size_bytes,
            metadata=analysis.file_metadata,
            imported_at=now,
        )
        await self._uow.projects.update(
            evolve(project, details=evolve(project.details, **suggested), source=source, updated_at=now)
        )
        await self._uow.commit()
        await self._publisher.publish(ProjectChanged(project_id=project_id))
        return pages

    async def _produce_assets(self, job_id: JobId, pages: Sequence[Page]) -> bool:
        """Produce the images of every page, at most ``parallel_pages`` at a time.

        :return: Whether every page was produced; False when the job was cancelled on the way.
        """
        job = await self._fresh_job(job_id)
        if job.state.is_final:
            return False
        await self._save(evolve(job, progress=Progress(total=len(pages))))
        cancelled = False
        try:
            async with self._sources.source_files(job.project_id) as files, asyncio.TaskGroup() as group:
                kind = source_kind_of([path.name for path in files])
                for page in pages:
                    group.create_task(self._produce_page(job_id, kind, files, page))
        except* _ImportCancelledError:
            cancelled = True
        return not cancelled

    async def _produce_page(self, job_id: JobId, kind: SourceKind, files: Sequence[Path], page: Page) -> None:
        """Write the native image, the tile pyramid and the thumbnail of a page, then mark it ready.

        :raises _ImportCancelledError: If the job was cancelled meanwhile.
        """
        async with self._page_slots, self._assets.writable(page.asset_key(PageAsset.FULL)) as full:
            await self._imaging.rasterizer.extract(kind, files, page.index, full)
            async with (
                self._assets.writable(page.asset_key(PageAsset.TILES)) as tiles,
                self._assets.writable(page.asset_key(PageAsset.THUMBNAIL)) as thumbnail,
            ):
                await self._imaging.tiler.tile(full, tiles)
                await self._imaging.tiler.thumbnail(full, thumbnail)
        async with self._writing:
            job = await self._fresh_job(job_id)
            if job.state.is_final:
                raise _ImportCancelledError(job_id)
            ready = await self._uow.pages.update(evolve(page, assets=evolve(page.assets, ready=True)))
            progressed = await self._uow.jobs.update(
                evolve(job, progress=evolve(job.progress, done=job.progress.done + 1))
            )
            await self._uow.commit()
            await self._publisher.publish(PageReady(project_id=page.project_id, page=ready))
            await self._publisher.publish(JobChanged(project_id=job.project_id, job=progressed))

    async def _fresh_job(self, job_id: JobId) -> Job:
        """Read the job as committed, so a cancellation made by another request is seen."""
        # Right after a commit a rollback discards nothing: it starts a transaction that sees other commits
        await self._uow.rollback()
        return await self._uow.jobs.get(job_id)

    async def _finish(self, job_id: JobId, state: JobState, error: str = '') -> None:
        """Move the job to a final state, unless it was cancelled meanwhile."""
        job = await self._fresh_job(job_id)
        if not job.state.is_final:
            await self._save(evolve(job, state=state, error=error, finished_at=self._clock.now()))

    async def _save(self, job: Job) -> None:
        """Commit the job and tell the project's listeners about it."""
        await self._uow.jobs.update(job)
        await self._uow.commit()
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))
