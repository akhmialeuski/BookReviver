"""Import use cases: accept an upload and enqueue its job, then run the job source by source.

An upload of any number of files becomes one import job. ``ImportService.start_import`` receives the files into the
job's own directory, records the job with the list of the files and enqueues it, and the request is answered at once.
``ImportService.run_import`` is the body of the background task and hands the work to an ``ImportRun``.

A run imports every file on its own, so one broken file never cancels the others. In its first phase it groups the
staged files into sources and commits each source, its scans and the pages the scans give the book in a transaction of
its own, with the progress of the job and the enrichment of the book description, so a source is either fully in the
project or not at all. In its second phase it cuts the images of every scan of the project that has none, which
covers the scans of the sources just committed, the scans a cancelled import left behind, and the scans of a delivery
that crashed. A job delivered again after a crash therefore continues where it stopped, skipping the sources it
already committed and writing the directories of unready scans again. The job cancels between steps: every write of
its progress is guarded by its state, and the run stops at the first one that finds it cancelled.

The base version of a page, ``split.none``, holds the page's own copy of its scan's ``full`` image and the renditions
cut from that copy, so the page stands on its own once its scan is deleted. Until the plugin framework exists this
module writes it, and the ``split.none`` processor replaces this code.

A file is named by its relative path in the upload, such as ``vol1/001.tif``, and the order of the upload is the order
of the book: the job lists its files as the user gave them, sources are created in that order, and the pages of their
scans join the end of the book in it, with nothing sorted on the way. A system file of a directory is rejected by name
like a file of an unsupported type.

Services touch no file: names and sizes come from the staged files the store reported, and every image is read and
written through the imaging and asset ports.
"""

import asyncio
import logging
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import Job, Page, PageVersion, Scan, Source
from bookreviver.domain.enums import (
    FileType,
    JobKind,
    JobState,
    PageChange,
    PageOrigin,
    RejectionReason,
    Rendition,
    Stage,
    SystemFile,
    UploadProblem,
    VersionState,
)
from bookreviver.domain.errors import (
    ConflictError,
    DomainError,
    NotFoundError,
    UnsupportedSourceError,
    UploadRejectedError,
)
from bookreviver.domain.events import JobChanged, PagesChanged, ProjectChanged, ScanReady, SourceImported
from bookreviver.domain.ids import JobId, PageId, ScanId, SourceId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import (
    ImportRequest,
    ImportResult,
    ProcessorRef,
    Progress,
    RejectedFile,
    Renditions,
    UploadPath,
)
from bookreviver.services.projects import owned_project

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bookreviver.domain.entities import Actor
    from bookreviver.domain.ids import ProjectId, StorageKey
    from bookreviver.domain.values import MetadataSuggestion, UploadedSource
    from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
    from bookreviver.ports.storage import AssetStore, IncomingFile, SourceStore

IMPORT_ACTIVE: str = 'This project is already importing files. Wait for the import to finish, or cancel it.'
NO_SOURCE_IMPORTED: str = 'None of the uploaded files could be imported.'
NOT_QUEUED: str = 'The import could not be queued. Upload the files again.'
UNEXPECTED_FAILURE: str = 'The import stopped because of an unexpected error. It has been logged.'
# Kinds of job that import files, of which a project runs one at a time
IMPORT_JOBS: frozenset[JobKind] = frozenset(
    {JobKind.IMPORT_SOURCE}
)  # The step that gives a page its base version while the page split is skipped, until its processor exists
SPLIT_NONE: ProcessorRef = ProcessorRef(key='split.none', version='1')

logger = logging.getLogger(__name__)


class ImportCancelledError(Exception):
    """The account holder cancelled the job, which the run learns from the first guarded write that finds it so."""


@frozen(kw_only=True)
class ImportLimits:
    """What bounds one upload and one import, read from the settings by the composition root.

    :ivar max_files: Largest number of files in one upload.
    :ivar max_bytes: Largest total size of one upload in bytes.
    :ivar parallel_scans: Largest number of scans cut at the same time.
    :ivar iiif_root: Path the IIIF routes are mounted at, which a pyramid's ``info.json`` names as its address.
    """

    max_files: int
    max_bytes: int
    parallel_scans: int
    iiif_root: str


@frozen(kw_only=True)
class ImportStorage:
    """The two stores an import reads and writes.

    :ivar sources: Store of the uploads being received and of the sources.
    :ivar assets: Store of the derived files.
    """

    sources: SourceStore
    assets: AssetStore


@frozen(kw_only=True)
class ImportImaging:
    """The imaging ports an import reads its sources and cuts its scans with.

    :ivar inspector: Port grouping an upload into sources and describing each source.
    :ivar rasterizer: Port writing one scan as an image.
    :ivar tiler: Port cutting the pyramid, the preview and the thumbnail of an image.
    """

    inspector: SourceInspector
    rasterizer: PageRasterizer
    tiler: Tiler


@frozen(kw_only=True)
class ImportRuntime:
    """What an import reports through and is bounded by.

    :ivar publisher: Publisher of the events the browser follows.
    :ivar clock: Clock stamping jobs and what they create.
    :ivar order_keys: Builder of the order keys of the new pages.
    :ivar limits: Bounds of an upload and of an import.
    """

    publisher: EventPublisher
    clock: Clock
    order_keys: OrderKeys
    limits: ImportLimits


class ImportRun:
    """One execution of an import job, which keeps what the job has done so far and reports it as a result.

    The run is the state its steps share: the latest job as stored, the sources imported, the files rejected and the
    files dealt with. Several scans are cut at once, and the unit of work of one job is one database session, so every
    database step of a scan runs under one lock while the files are cut outside it.

    :ivar job: The job as last stored by this run, which carries its progress.
    """

    def __init__(
        self, *, job: Job, uow: UnitOfWork, storage: ImportStorage, imaging: ImportImaging, runtime: ImportRuntime
    ) -> None:
        """Prepare to run a job that is running already.

        :param job: The job, in the running state, whose request lists the files to import.
        :type job: Job
        :param uow: Unit of work of the job, committed step by step.
        :type uow: UnitOfWork
        :param storage: The source store and the asset store.
        :type storage: ImportStorage
        :param imaging: The inspector, the rasterizer and the tiler.
        :type imaging: ImportImaging
        :param runtime: The publisher, the clock, the order keys and the limits.
        :type runtime: ImportRuntime
        """
        self.job = job
        self._files = {file.name: file for file in (job.request.files if job.request else ())}
        self._uow = uow
        self._sources = storage.sources
        self._assets = storage.assets
        self._inspector = imaging.inspector
        self._rasterizer = imaging.rasterizer
        self._tiler = imaging.tiler
        self._order_keys = runtime.order_keys
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._limits = runtime.limits
        self._keys = ProjectKeys(job.project_id)
        self._lock = asyncio.Lock()
        self._imported: list[SourceId] = []
        self._rejected: list[RejectedFile] = []
        self._handled: set[str] = set()
        self._uncommitted: Job | None = None

    @property
    def result(self) -> ImportResult:
        """What the run did with the files: the sources, the rejected files and the files it never reached."""
        skipped = [name for name in self._files if name not in self._handled]
        return ImportResult(imported=tuple(self._imported), rejected=tuple(self._rejected), skipped=tuple(skipped))

    async def execute(self) -> None:
        """Import the staged files as sources, then cut the images of every scan of the project that has none.

        :raises ImportCancelledError: If the job was cancelled, which the first guarded write that finds it so reports.
        """
        staged = await self._resume()
        await self._import_sources([name for name in self._files if name in staged and name not in self._handled])
        await self._cut_scans()

    async def _resume(self) -> set[str]:
        """Take back what an earlier delivery of the job committed, and total the scans still to cut.

        A source committed by the job is complete apart from its files, which a crash between its commit and its
        promotion left staged, so they are promoted now. Sources are committed before their files are promoted,
        because a failed commit after a promotion would lose the upload.

        :returns: Names of the files still staged.
        :rtype: set[str]
        """
        try:
            async with self._sources.staged_files(self.job.project_id, self.job.id) as paths:
                staged = set(paths)
        except NotFoundError:
            staged = set()
        for source in await self._uow.sources.list_for_project(self.job.project_id):
            if source.import_job_id != self.job.id:
                continue
            self._imported.append(source.id)
            self._handled.update(file.name for file in source.files)
            if source.file_name in staged:
                await self._sources.promote(
                    source.project_id, self.job.id, source.id, names=[file.name for file in source.files]
                )
        unready = await self._uow.scans.list_unready(self.job.project_id)
        await self._record_progress(total=self.job.progress.done + len(unready) - self.job.progress.total)
        await self._commit()
        return staged

    async def _import_sources(self, names: Sequence[str]) -> None:
        """Group the named staged files into sources and import each one, in the order of the upload.

        A system file of a directory, such as ``Thumbs.db`` or ``._001.tif``, and a file of a type no source can be are
        each rejected on its own, so they do not stop the upload and are named in its result. The names come in the
        order of the upload, which the inspector keeps, so that order becomes the order of the pages of the book.

        :param names: Relative names of the staged files this run has not dealt with yet, in the order of the upload.
        :type names: Sequence[str]
        :raises ImportCancelledError: If the job was cancelled.
        """
        supported: list[str] = []
        for name in names:
            if SystemFile.matches(name):
                self._reject([name], RejectionReason.SYSTEM_FILE, RejectionReason.SYSTEM_FILE.label)
            elif FileType.from_name(name) is None:
                self._reject([name], RejectionReason.UNSUPPORTED_TYPE, RejectionReason.UNSUPPORTED_TYPE.label)
            else:
                supported.append(name)
        if not supported:
            return
        async with self._sources.staged_files(self.job.project_id, self.job.id) as paths:
            grouped = await self._inspector.group({name: paths[name] for name in supported})
        for uploaded in grouped:
            await self._import_source(uploaded)

    async def _import_source(self, uploaded: UploadedSource) -> None:
        """Import one source: check it is new and readable, commit it with its scans and pages, and promote its files.

        :param uploaded: The staged files that make the source.
        :type uploaded: UploadedSource
        :raises ImportCancelledError: If the job was cancelled.
        """
        files = [self._files[name] for name in uploaded.names]
        main = files[0]
        if (twin := await self._uow.sources.find_by_sha256(self.job.project_id, main.sha256)) is not None:
            self._reject(
                uploaded.names, RejectionReason.DUPLICATE, f'The project already has this file as {twin.file_name}.'
            )
            return
        async with self._sources.staged_files(self.job.project_id, self.job.id) as paths:
            try:
                analysis = await self._inspector.inspect(uploaded.kind, [paths[name] for name in uploaded.names])
            except UnsupportedSourceError as error:
                self._reject(uploaded.names, RejectionReason.UNREADABLE, str(error))
                return
        moment = self._clock.now()
        source = Source(
            id=SourceId(uuid4()),
            project_id=self.job.project_id,
            kind=uploaded.kind,
            file_type=uploaded.file_type,
            file_name=main.name,
            files=files,
            size_bytes=sum(file.size_bytes for file in files),
            sha256=main.sha256,
            scan_count=len(analysis.scans),
            metadata=analysis.file_metadata,
            suggestion=analysis.suggestion,
            import_job_id=self.job.id,
            imported_at=moment,
        )
        scans = [
            Scan(id=ScanId(uuid4()), project_id=source.project_id, source_id=source.id, number=number, facts=facts)
            for number, facts in enumerate(analysis.scans)
        ]
        order_keys = self._order_keys.spread(
            lower=await self._uow.pages.last_order_key(source.project_id), upper=None, count=len(scans)
        )
        pages = [
            Page(
                id=PageId(uuid4()),
                project_id=source.project_id,
                order_key=order_key,
                label=scan.source_label,
                origin=PageOrigin.SCAN,
                scan_id=scan.id,
                created_at=moment,
                updated_at=moment,
            )
            for scan, order_key in zip(scans, order_keys, strict=True)
        ]
        await self._record_progress(total=len(scans))
        await self._uow.sources.add(source)
        await self._uow.scans.add_many(scans)
        await self._uow.pages.add_many(pages)
        described = await self._describe_book(analysis.suggestion)
        await self._commit()
        try:
            await self._sources.promote(source.project_id, self.job.id, source.id, names=list(uploaded.names))
        except Exception:
            # The source is committed and its files are not moved, and nothing would repair it once the job ends, so
            # it is taken back whole. Its pages go first, since deleting the source only empties their scan.
            for page in pages:
                await self._uow.pages.delete(page.id)
            await self._uow.sources.delete(source.id)
            await self._record_progress(total=-len(scans))
            await self._commit()
            raise
        self._imported.append(source.id)
        self._handled.update(uploaded.names)
        await self._publisher.publish(SourceImported(project_id=source.project_id, source=source))
        await self._publisher.publish(
            PagesChanged(project_id=source.project_id, page_ids=[page.id for page in pages], change=PageChange.ADDED)
        )
        if described:
            await self._publisher.publish(ProjectChanged(project_id=source.project_id))

    async def _describe_book(self, suggestion: MetadataSuggestion) -> bool:
        """Fill the empty fields of the book description from what a source suggests, never the title.

        The change joins the transaction of the source, so a source is never committed without the values it gave the
        book. Sources come in order, so the first source that has a value for a field is the one that gives it.

        :param suggestion: Description fields found in the source, empty where nothing was found.
        :type suggestion: MetadataSuggestion
        :returns: Whether the description changed.
        :rtype: bool
        """
        project = await self._uow.projects.get(self.job.project_id)
        details = project.details.fill_from(suggestion)
        if details == project.details:
            return False
        await self._uow.projects.update(evolve(project, details=details, updated_at=self._clock.now()))
        return True

    async def _cut_scans(self) -> None:
        """Cut the images of every scan of the project that has none, a few at a time, in the order of the book.

        :raises ImportCancelledError: If the job was cancelled.
        :raises ExceptionGroup: If cutting one scan failed, which stops the others.
        """
        scans = await self._uow.scans.list_unready(self.job.project_id)
        sources = {source.id: source for source in await self._uow.sources.list_for_project(self.job.project_id)}
        pending = iter(scans)

        async def worker() -> None:
            """Take scans from the shared iterator until none is left, each one cut before the next is taken."""
            for scan in pending:
                await self._cut(sources[scan.source_id], scan)

        try:
            async with asyncio.TaskGroup() as group:
                for _ in range(min(self._limits.parallel_scans, len(scans))):
                    group.create_task(worker())
        except ExceptionGroup as errors:
            # Workers that stop in the same moment are all in the group, in no meaningful order. A cancellation is
            # only the job's own state, so it must not hide a fault, which would end the job as cancelled unlogged.
            faults = [error for error in errors.exceptions if not isinstance(error, ImportCancelledError)]
            raise (faults or errors.exceptions)[0] from errors

    async def _cut(self, source: Source, scan: Scan) -> None:
        """Write the four renditions of a scan and the base version of its pages, then mark the scan ready.

        The format of ``full`` is chosen here, from the colour of the scan and the image policy of the project as it is
        now, and recorded with the scan, so a later change of the policy changes no stored path. The base version of a
        page copies that format along with the image. What an earlier attempt left in the directories of the scan and
        of the versions is removed first, since a stored file is never replaced. The scan and its versions are marked
        ready together, in one transaction with the progress of the job.

        :param source: Source holding the scan.
        :type source: Source
        :param scan: The scan to cut, whose renditions are not ready.
        :type scan: Scan
        :raises ImportCancelledError: If the job was cancelled.
        """
        async with self._lock:
            # The job is read as last committed before every scan, so a cancellation stops the run before the next one
            await self._record_progress()
            await self._commit()
            pages = await self._uow.pages.list_for_scan(scan.id)
            project = await self._uow.projects.get(scan.project_id)
        full = project.image_policy.full_format(scan.facts.color_mode)
        ready = evolve(scan, renditions=Renditions(ready=True, version=scan.renditions.version, full=full))
        of_scan = partial(self._keys.scan_rendition, ready)
        await self._assets.delete_prefix(self._keys.scan_directory(scan))
        async with (
            self._sources.source_files(scan.project_id, scan.source_id) as files,
            self._assets.writable(of_scan(full)) as target,
        ):
            await self._rasterizer.extract(source.kind, files, scan.number, target, full=full)
        await self._derive(of_scan, full=full)
        versions = []
        for page in pages:
            version = PageVersion(
                id=PageVersion.identify(page_id=page.id, processor=SPLIT_NONE),
                page_id=page.id,
                stage=Stage.PAGE_SPLIT,
                processor=SPLIT_NONE,
                renditions=Renditions(ready=True, full=full),
                state=VersionState.READY,
                created_at=self._clock.now(),
            )
            of_version = partial(self._keys.version_rendition, version)
            await self._assets.delete_prefix(self._keys.version_directory(version))
            await self._assets.copy(of_scan(full), of_version(full))
            await self._derive(of_version, full=full)
            versions.append(version)
        async with self._lock:
            await self._record_progress(done=1)
            await self._uow.page_versions.add_many(versions)
            await self._uow.scans.update(ready)
            await self._commit()
            await self._publisher.publish(ScanReady(project_id=scan.project_id, scan=ready))

    async def _derive(self, key: Callable[[Rendition], StorageKey], *, full: Rendition) -> None:
        """Cut the preview, the thumbnail and the tile pyramid from the ``full`` image stored under the keys.

        :param key: Function giving the storage key of each rendition of one scan or one page version.
        :type key: Callable[[Rendition], StorageKey]
        :param full: Format the ``full`` image was written in, which names the file to cut from.
        :type full: Rendition
        """
        async with self._assets.readable(key(full)) as image:
            async with self._assets.writable(key(Rendition.PREVIEW)) as target:
                await self._tiler.preview(image, target)
            async with self._assets.writable(key(Rendition.THUMBNAIL)) as target:
                await self._tiler.thumbnail(image, target)
            tiles = key(Rendition.TILES)
            async with self._assets.writable(tiles) as target:
                await self._tiler.tile(image, target, resource_id=f'{self._limits.iiif_root}/{tiles}')

    def _reject(self, names: Sequence[str], reason: RejectionReason, detail: str) -> None:
        """Record that the files of one source were not imported, and why.

        :param names: Names of the staged files, the main file first.
        :type names: Sequence[str]
        :param reason: Why the files were rejected.
        :type reason: RejectionReason
        :param detail: Text for the user that says more than the reason.
        :type detail: str
        """
        self._rejected.append(RejectedFile(file_name=names[0], reason=reason, detail=detail))
        self._handled.update(names)

    async def _record_progress(self, *, done: int = 0, total: int = 0) -> None:
        """Add to the progress of the job, only while the job is running, in the transaction still open.

        The write is guarded by the state, so it is also the check that the job was not cancelled, and it reads the
        job as last committed by anyone.

        :param done: Steps completed since the last write.
        :type done: int
        :param total: Steps added to the total since the last write, which may be negative.
        :type total: int
        :raises ImportCancelledError: If the stored job is not running any more.
        """
        progress = Progress(done=self.job.progress.done + done, total=self.job.progress.total + total)
        saved = await self._uow.jobs.update_if_state(evolve(self.job, progress=progress), expected=(JobState.RUNNING,))
        if saved is None:
            raise ImportCancelledError
        # ``job`` is only what was committed, since a job that fails after this write is stored with its own copy of
        # the progress, and a step that never committed must not be counted in it. A write that changes nothing only
        # checks the state, and is not worth an event.
        self._uncommitted = saved if done or total else None

    async def _commit(self) -> None:
        """Commit the open transaction, take on the progress it wrote and announce it.

        :raises ImportCancelledError: If the job was cancelled and committed so since the run wrote its progress,
                                      which an adapter without locks reports as a conflict.
        """
        uncommitted, self._uncommitted = self._uncommitted, None
        try:
            await self._uow.commit()
        except ConflictError as error:
            raise ImportCancelledError from error
        if uncommitted is not None:
            self.job = uncommitted
            await self._publisher.publish(JobChanged(project_id=self.job.project_id, job=self.job))


class ImportService:
    """Accepts uploads for the owner of a project, and runs the import jobs they become."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        storage: ImportStorage,
        imaging: ImportImaging,
        runtime: ImportRuntime,
        queue: JobQueue,
    ) -> None:
        """Build the service over the ports of one request or job.

        :param uow: Unit of work of the current request or job.
        :type uow: UnitOfWork
        :param storage: The source store and the asset store.
        :type storage: ImportStorage
        :param imaging: The inspector, the rasterizer and the tiler.
        :type imaging: ImportImaging
        :param runtime: The publisher, the clock, the order keys and the limits.
        :type runtime: ImportRuntime
        :param queue: Queue handing the job to the workers.
        :type queue: JobQueue
        """
        self._uow = uow
        self._storage = storage
        self._imaging = imaging
        self._runtime = runtime
        self._queue = queue
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._limits = runtime.limits

    async def authorize_upload(self, actor: Actor, project_id: ProjectId) -> None:
        """Refuse an upload the actor may not make, or the project cannot take now, before any file is read.

        The API calls this before it parses the body of the request, so a refused caller costs the server no upload
        to spool, and ``start_import`` repeats it, since a caller of the service needs no API.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Project to import into.
        :type project_id: ProjectId
        :raises NotFoundError: If the actor has no such project.
        :raises ConflictError: If the project already has an import queued or running.
        """
        await owned_project(self._uow.projects, actor, project_id)
        active = await self._uow.jobs.list_for_project(project_id, JobState.active())
        if any(job.kind in IMPORT_JOBS for job in active):
            raise ConflictError(IMPORT_ACTIVE)

    async def start_import(self, actor: Actor, project_id: ProjectId, files: Sequence[IncomingFile]) -> Job:
        """Receive an upload into the directory of a new import job, record the job and enqueue it.

        The rules that need no file are checked before anything is received, so an upload that cannot be imported is
        not streamed first. The one-import rule is checked again by the database when the job is stored, since two
        uploads can pass the check before either commits.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Project to import into.
        :type project_id: ProjectId
        :param files: The uploaded files.
        :type files: Sequence[IncomingFile]
        :returns: The queued job, whose request lists the staged files.
        :rtype: Job
        :raises NotFoundError: If the actor has no such project.
        :raises UploadRejectedError: If there are no files or too many, a file has no usable name or a path that leaves
                                     its folder, two files share a path, or the upload is larger than allowed.
        :raises ConflictError: If the project already has an import queued or running.
        """
        await self.authorize_upload(actor, project_id)
        if not files:
            raise UploadRejectedError(UploadProblem.NO_FILES)
        if len(files) > self._limits.max_files:
            raise UploadRejectedError(UploadProblem.TOO_MANY_FILES)
        # A path that cannot be stored is found before the first file is streamed, not after hundreds of megabytes
        for file in files:
            UploadPath.parse(file.filename or '')
        job_id = JobId(uuid4())
        staged = await self._storage.sources.stage(project_id, job_id, files, max_bytes=self._limits.max_bytes)
        job = Job(
            id=job_id,
            project_id=project_id,
            kind=JobKind.IMPORT_SOURCE,
            request=ImportRequest(files=staged),
            created_at=self._clock.now(),
        )
        try:
            await self._uow.jobs.add(job)
            await self._uow.commit()
        except ConflictError as error:
            await self._storage.sources.discard(project_id, job_id)
            raise ConflictError(IMPORT_ACTIVE) from error
        except BaseException:
            await self._storage.sources.discard(project_id, job_id)
            raise
        await self._publisher.publish(JobChanged(project_id=project_id, job=job))
        try:
            await self._queue.enqueue(job)
        except Exception:
            # A job that is queued in the database but never in the queue would keep the project from importing
            await self._uow.jobs.update_if_state(
                evolve(job, state=JobState.FAILED, error=NOT_QUEUED, finished_at=self._clock.now()),
                expected=(JobState.QUEUED,),
            )
            await self._uow.commit()
            await self._storage.sources.discard(project_id, job_id)
            raise
        return job

    async def run_import(self, job_id: JobId) -> None:
        """Run an import job that a worker took from the queue, and leave it in a final state.

        A job that has finished, such as one cancelled while it was queued, only has its upload removed. A job found
        running is a delivery repeated after a crash and continues where the earlier one stopped.

        :param job_id: Identifier of the job.
        :type job_id: JobId
        :raises NotFoundError: If there is no such job.
        """
        job = await self._uow.jobs.get(job_id)
        if job.state.is_final:
            await self._storage.sources.discard(job.project_id, job.id)
            return
        if job.state is JobState.QUEUED:
            started = evolve(job, state=JobState.RUNNING, started_at=self._clock.now())
            if (running := await self._uow.jobs.update_if_state(started, expected=(JobState.QUEUED,))) is None:
                # The job left the queue since it was read: another delivery started it, whose upload this one must
                # leave, or it was cancelled, which nothing delivers again, so its upload is removed here
                await self._uow.rollback()
                if (await self._uow.jobs.get(job.id)).state.is_final:
                    await self._storage.sources.discard(job.project_id, job.id)
                return
            job = running
            await self._uow.commit()
        await self._publisher.publish(JobChanged(project_id=job.project_id, job=job))
        run = ImportRun(job=job, uow=self._uow, storage=self._storage, imaging=self._imaging, runtime=self._runtime)
        try:
            await run.execute()
        except ImportCancelledError:
            await self._conclude(run, JobState.CANCELLED)
        except DomainError as error:
            await self._conclude(run, JobState.FAILED, error=str(error))
        except Exception:
            logger.exception('Import job %s failed', job_id)
            await self._conclude(run, JobState.FAILED, error=UNEXPECTED_FAILURE)
        else:
            if run.result.imported:
                await self._conclude(run, JobState.SUCCEEDED)
            else:
                await self._conclude(run, JobState.FAILED, error=NO_SOURCE_IMPORTED)

    async def _conclude(self, run: ImportRun, state: JobState, *, error: str = '') -> None:
        """Store the final state and the result of a job, remove what is left of its upload, and announce it.

        Whatever the run left uncommitted is discarded first, and the job is read again as last committed. A job that
        the account holder cancelled meanwhile stays cancelled and only gains its result.

        :param run: The run that ended, which holds the latest job and its result.
        :type run: ImportRun
        :param state: State to store, unless the job was cancelled.
        :type state: JobState
        :param error: Why the job failed, or empty.
        :type error: str
        """
        await self._uow.rollback()
        final = evolve(run.job, state=state, error=error, result=run.result, finished_at=self._clock.now())
        if (stored := await self._uow.jobs.update_if_state(final, expected=(JobState.RUNNING,))) is None:
            cancelled = await self._uow.jobs.get(run.job.id)
            stored = await self._uow.jobs.update_if_state(
                evolve(cancelled, result=run.result), expected=(JobState.CANCELLED,)
            )
        await self._uow.commit()
        await self._storage.sources.discard(run.job.project_id, run.job.id)
        if stored is not None:
            await self._publisher.publish(JobChanged(project_id=stored.project_id, job=stored))
