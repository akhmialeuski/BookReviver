"""Import use cases: accept an upload and enqueue its job, then run the job source by source.

An upload of any number of files becomes one import job. ``ImportService.start_import`` receives the files into the
job's own directory, records the job with the list of the files and enqueues it, and the request is answered at once.
``ImportService.run_import`` is the body of the background task and hands the work to an ``ImportRun``.

A run imports every file on its own, so one broken file never cancels the others. In its first phase it groups the
staged files into sources and stores each source, its scans and the pages the scans give the book in a ``change_book``
block of its own, with the progress of the job and the enrichment of the book description, so a source is either fully
in the project or not at all. In its second phase it cuts the images of every scan of the project that has none, which
covers the scans of the sources just stored, the scans a cancelled import left behind, and the scans of a delivery
that crashed. A job delivered again after a crash therefore continues where it stopped, skipping the sources it
already stored and writing the directories of unready scans again. The job cancels between steps: every write of
its progress is guarded by its state, and the run stops at the first block whose write finds it cancelled, which
rolls the block back. Inspection, rasterising and processing run outside any block, and a block holds only the reads
that decide and the writes.

The base version of a page, ``split.none``, holds the page's own copy of its scan's ``full`` image and the renditions
cut from that copy, so the page stands on its own once its scan is deleted. Until the plugin framework exists
``BaseVersions`` writes it, as it does when a scan is bound to a placeholder, and the ``split.none`` processor replaces
that code.

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

from bookreviver.domain.entities import Job, Page, Scan, Source
from bookreviver.domain.enums import (
    FileType,
    JobKind,
    JobState,
    PageChange,
    PageOrigin,
    RecipeKind,
    RejectionReason,
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
    PageStageKey,
    Progress,
    RejectedFile,
    Renditions,
    StageRun,
    UploadPath,
)
from bookreviver.services.base_versions import SPLIT_NONE, BaseVersions
from bookreviver.services.page_labels import PageLabels
from bookreviver.services.projects import owned_project
from bookreviver.services.source_labels import SourceLabels
from bookreviver.services.stage_records import StageRecords
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, PageStage, Project
    from bookreviver.domain.ids import ProjectId
    from bookreviver.domain.values import MetadataSuggestion, UploadedSource
    from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher, JobQueue
    from bookreviver.ports.storage import AssetStore, IncomingFile, SourceStore
    from bookreviver.services.processing_parts import ProcessingParts
    from bookreviver.services.steps import StepRunner

IMPORT_ACTIVE: str = 'This project is already importing files. Wait for the import to finish, or cancel it.'
NO_SOURCE_IMPORTED: str = 'None of the uploaded files could be imported.'
NOT_QUEUED: str = 'The import could not be queued. Upload the files again.'
UNEXPECTED_FAILURE: str = 'The import stopped because of an unexpected error. It has been logged.'
# The processor of the page split that decides for each scan, whose recipe an import runs on the new pages
AUTOMATIC_SPLIT: str = 'split.auto'
# Kinds of job that import files, of which a project runs one at a time
IMPORT_JOBS: frozenset[JobKind] = frozenset({JobKind.IMPORT_SOURCE})

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
    :ivar runner: Runner of the processor ``split.none``, which makes the base version of a page from its scan.
    """

    inspector: SourceInspector
    rasterizer: PageRasterizer
    tiler: Tiler
    runner: StepRunner


@frozen(kw_only=True)
class ImportRuntime:
    """What an import reports through and is bounded by.

    :ivar publisher: Publisher of the events the browser follows.
    :ivar clock: Clock stamping jobs and what they create.
    :ivar order_keys: Builder of the order keys of the new pages.
    :ivar limits: Bounds of an upload and of an import.
    :ivar queue: Queue handing the import job to the workers.
    """

    publisher: EventPublisher
    clock: Clock
    order_keys: OrderKeys
    limits: ImportLimits
    queue: JobQueue


class ImportRun:
    """One execution of an import job, which keeps what the job has done so far and reports it as a result.

    The run is the state its steps share: the latest job as stored, the sources imported, the files rejected and the
    files dealt with. Several scans are cut at once, and the unit of work of one job is one database session that holds
    one block at a time and refuses a second as nested, so the blocks of the scans take one lock in turn while the
    files are cut outside it. The port serialises the changes of a book between units of work, and this lock the
    blocks of tasks that share one.

    :ivar job: The job as last stored by this run, which carries its progress.
    """

    def __init__(
        self, *, job: Job, uow: UnitOfWork, storage: ImportStorage, imaging: ImportImaging, runtime: ImportRuntime
    ) -> None:
        """Prepare to run a job that is running already.

        :param job: The job, in the running state, whose request lists the files to import.
        :type job: Job
        :param uow: Unit of work of the job, whose ``change_book`` blocks store the job step by step.
        :type uow: UnitOfWork
        :param storage: The source store and the asset store.
        :type storage: ImportStorage
        :param imaging: The inspector, the rasterizer, the tiler and the runner of processors.
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
        self._runner = imaging.runner
        self._records = StageRecords(uow=uow, publisher=runtime.publisher, clock=runtime.clock)
        self._labels = PageLabels(uow=uow, publisher=runtime.publisher, clock=runtime.clock)
        self._base_versions = BaseVersions(
            assets=storage.assets, tiler=imaging.tiler, iiif_root=runtime.limits.iiif_root
        )
        self._order_keys = runtime.order_keys
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._limits = runtime.limits
        self._keys = ProjectKeys(job.project_id)
        self._lock = asyncio.Lock()
        self._imported: list[SourceId] = []
        self._rejected: list[RejectedFile] = []
        self._handled: set[str] = set()

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
        because a failed block after a promotion would lose the upload. The promotion is a file operation and runs
        before the block, which totals the scans still to cut.

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
        async with self._uow.change_book(self.job.project_id):
            unready = await self._uow.scans.list_unready(self.job.project_id)
            saved = await self._record_progress(total=self.job.progress.done + len(unready) - self.job.progress.total)
        await self._take_on(saved)
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
        """Import one source: check it is new and readable, store it with its scans and pages, and promote its files.

        The source, its scans and its pages are stored in one ``change_book`` block, which reads the end of the book
        and the sections of the book to place the new pages, and a source whose files cannot be promoted is taken
        back whole in a second block.

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
            Scan(
                id=ScanId(uuid4()),
                project_id=source.project_id,
                source_id=source.id,
                number=number,
                source_label=analysis.label_of(number),
                facts=facts,
            )
            for number, facts in enumerate(analysis.scans)
        ]
        async with self._uow.change_book(source.project_id) as project:
            order_keys = self._order_keys.spread(
                lower=await self._uow.pages.last_order_key(source.project_id), upper=None, count=len(scans)
            )
            pages = [
                Page(
                    id=PageId(uuid4()),
                    project_id=source.project_id,
                    order_key=order_key,
                    label=scan.source_label,
                    label_manual=bool(scan.source_label),
                    origin=PageOrigin.SCAN,
                    scan_id=scan.id,
                    created_at=moment,
                    updated_at=moment,
                )
                for scan, order_key in zip(scans, order_keys, strict=True)
            ]
            # The label rules of the file become sections, and only a label they do not give stays an exception. A
            # source without rules is kept out of the numbering of a book that has sections, instead of continuing it
            source_labels = SourceLabels(
                rules=analysis.label_rules,
                pages=pages,
                moment=moment,
                book_has_sections=bool(await self._uow.pagination_sections.list_for_project(source.project_id)),
            )
            pages = source_labels.pages
            saved = await self._record_progress(total=len(scans))
            await self._uow.sources.add(source)
            await self._uow.scans.add_many(scans)
            await self._uow.pages.add_many(pages)
            if source_labels.sections:
                await self._uow.pagination_sections.add_many(source_labels.sections)
            # A page without the label of its source takes the number its section gives it
            await self._labels.recompute(source.project_id)
            described = await self._describe_book(project, analysis.suggestion)
        await self._take_on(saved)
        try:
            await self._sources.promote(source.project_id, self.job.id, source.id, names=list(uploaded.names))
        except Exception:
            # The source is stored and its files are not moved, and nothing would repair it once the job ends, so it
            # is taken back whole. Its pages go first, since deleting the source only empties their scan.
            async with self._uow.change_book(source.project_id):
                for page in pages:
                    await self._uow.pages.delete(page.id)
                await self._uow.sources.delete(source.id)
                saved = await self._record_progress(total=-len(scans))
            await self._take_on(saved)
            raise
        self._imported.append(source.id)
        self._handled.update(uploaded.names)
        await self._publisher.publish(SourceImported(project_id=source.project_id, source=source))
        await self._publisher.publish(
            PagesChanged(project_id=source.project_id, page_ids=[page.id for page in pages], change=PageChange.ADDED)
        )
        await self._labels.announce(source.project_id)
        if described:
            await self._publisher.publish(ProjectChanged(project_id=source.project_id))

    async def _describe_book(self, project: Project, suggestion: MetadataSuggestion) -> bool:
        """Fill the empty fields of the book description from what a source suggests, never the title.

        Writes inside the block of its caller and never opens one, so the change joins the block of the source, and a
        source is never stored without the values it gave the book. Sources come in order, so the first source that has
        a value for a field is the one that gives it.

        :param project: The project as the block of the source yielded it.
        :type project: Project
        :param suggestion: Description fields found in the source, empty where nothing was found.
        :type suggestion: MetadataSuggestion
        :returns: Whether the description changed.
        :rtype: bool
        """
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
        of the versions is removed first, since a stored file is never replaced. The files are written outside any
        block, and the scan and its versions are marked ready together, in one ``change_book`` block with the progress
        of the job. That block reads the scan and its pages again. A scan that is gone or ready already, or whose pages
        are not the ones the versions were made for, is left as it is, its files are removed, and the run counts one
        scan less to cut.

        :param source: Source holding the scan.
        :type source: Source
        :param scan: The scan to cut, whose renditions are not ready.
        :type scan: Scan
        :raises ImportCancelledError: If the job was cancelled.
        """
        async with self._lock:
            async with self._uow.change_book(scan.project_id) as project:
                # The job is read as last stored before every scan, so a cancellation stops the run before the next one
                saved = await self._record_progress()
                pages = await self._uow.pages.list_for_scan(scan.id)
            await self._take_on(saved)
        full = project.image_policy.full_format(scan.facts.color_mode)
        ready = evolve(scan, renditions=Renditions(ready=True, version=scan.renditions.version, full=full))
        of_scan = partial(self._keys.scan_rendition, ready)
        await self._assets.delete_prefix(self._keys.scan_directory(scan))
        async with (
            self._sources.source_files(scan.project_id, scan.source_id) as files,
            self._assets.writable(of_scan(full)) as target,
        ):
            await self._rasterizer.extract(source.kind, files, scan.number, target, full=full)
        await self._base_versions.derive(of_scan, full=full)
        versions = []
        run = StepRun(
            processor_key=SPLIT_NONE.key,
            params={},
            input_data=ready.facts.as_data(),
            image=of_scan(full),
        )
        for page in pages:
            version = BaseVersions.split_none(page=page, scan=ready, state=VersionState.READY, moment=self._clock.now())
            async with self._runner.execute(run) as result:
                versions.append(
                    await self._runner.store(
                        self._keys, version, result.outputs[0], policy=project.image_policy, tiles=True
                    )
                )
        changed: list[PageStage] = []
        async with self._lock:
            async with self._uow.change_book(scan.project_id):
                # The scan and its pages are read again, since the book may have changed while the files were cut
                unready = {unready_scan.id for unready_scan in await self._uow.scans.list_unready(scan.project_id)}
                current = {page.id for page in await self._uow.pages.list_for_scan(scan.id)}
                if fits := scan.id in unready and current == {page.id for page in pages}:
                    saved = await self._record_progress(done=1)
                    await self._uow.page_versions.add_many(versions)
                    for page, version in zip(pages, versions, strict=True):
                        changed += await self._records.set_head(
                            PageStageKey(page.id, version.stage), head_version_id=version.id, recipe_id=None
                        )
                    await self._uow.scans.update(ready)
                else:
                    saved = await self._record_progress(total=-1)
            await self._take_on(saved)
        if not fits:
            # The scan stays unready for the next run to cut, and the files made for it are of no version or scan
            logger.warning('Scan %s was changed or deleted while its images were cut, so they were not stored', scan.id)
            await self._assets.delete_prefix(self._keys.scan_directory(scan))
            for version in versions:
                await self._assets.delete_prefix(self._keys.version_directory(version))
            return
        await self._publisher.publish(ScanReady(project_id=scan.project_id, scan=ready))
        await self._records.announce(scan.project_id, changed)

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

    async def _record_progress(self, *, done: int = 0, total: int = 0) -> Job:
        """Add to the progress of the job, only while the job is running, in the block of the caller.

        Writes inside the block of its caller and never opens one. The write is guarded by the state, so it is also the
        check that the job was not cancelled, and it reads the job as last stored by anyone. A block that this raises
        in is rolled back, so no step the run did not store is counted in the progress.

        :param done: Steps completed since the last write.
        :type done: int
        :param total: Steps added to the total since the last write, which may be negative.
        :type total: int
        :returns: The job as written, which the caller hands to ``_take_on`` once its block has committed.
        :rtype: Job
        :raises ImportCancelledError: If the stored job is not running any more.
        """
        progress = Progress(done=self.job.progress.done + done, total=self.job.progress.total + total)
        saved = await self._uow.jobs.update_if_state(evolve(self.job, progress=progress), expected=(JobState.RUNNING,))
        if saved is None:
            raise ImportCancelledError
        return saved

    async def _take_on(self, saved: Job) -> None:
        """Keep the job a committed block wrote as the latest one, and announce it when its progress changed.

        ``job`` is only what was committed, since a job that fails after a write is stored with its own copy of the
        progress, and a step that never committed must not be counted in it. A write that changes nothing only checks
        the state, and is not worth an event.

        :param saved: The job as ``_record_progress`` returned it, from a block that has committed.
        :type saved: Job
        """
        changed = saved.progress != self.job.progress
        self.job = saved
        if changed:
            await self._publisher.publish(JobChanged(project_id=saved.project_id, job=saved))


class ImportService:
    """Accepts uploads for the owner of a project, and runs the import jobs they become."""

    def __init__(
        self,
        *,
        uow: UnitOfWork,
        storage: ImportStorage,
        imaging: ImportImaging,
        runtime: ImportRuntime,
        parts: ProcessingParts,
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
        :param parts: The recipes and the job starter, with which the split of the new pages is queued, and the job
                      tracker, which moves a queued import job to running.
        :type parts: ProcessingParts
        """
        self._uow = uow
        self._storage = storage
        self._imaging = imaging
        self._runtime = runtime
        self._queue = runtime.queue
        self._recipes = parts.recipes
        self._starter = parts.starter
        self._tracker = parts.tracker
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
        await self._refuse_active_import(project_id)

    async def _refuse_active_import(self, project_id: ProjectId) -> None:
        """Refuse a project that has an import queued or running.

        Only reads, so it is called outside any block, which gives an early answer, and inside the ``change`` block that
        stores the job, which gives the answer that holds.

        :param project_id: Project to import into.
        :type project_id: ProjectId
        :raises ConflictError: If the project already has an import queued or running.
        """
        active = await self._uow.jobs.list_for_project(project_id, JobState.active())
        if any(job.kind in IMPORT_JOBS for job in active):
            raise ConflictError(IMPORT_ACTIVE)

    async def start_import(self, actor: Actor, project_id: ProjectId, files: Sequence[IncomingFile]) -> Job:
        """Receive an upload into the directory of a new import job, record the job and enqueue it.

        The rules that need no file are checked before anything is received, so an upload that cannot be imported is
        not streamed first. The one-import rule is checked again in the ``change`` block that stores the job, so two
        uploads cannot both pass it, and the database keeps it with a unique index as the last line of defence. The
        upload is removed again when that block fails.

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
            async with self._uow.change():
                await self._refuse_active_import(project_id)
                await self._uow.jobs.add(job)
        except BaseException:
            await self._storage.sources.discard(project_id, job_id)
            raise
        await self._publisher.publish(JobChanged(project_id=project_id, job=job))
        try:
            await self._queue.enqueue(job)
        except Exception:
            # A job that is queued in the database but never in the queue would keep the project from importing
            async with self._uow.change():
                await self._uow.jobs.update_if_state(
                    evolve(job, state=JobState.FAILED, error=NOT_QUEUED, finished_at=self._clock.now()),
                    expected=(JobState.QUEUED,),
                )
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
        if (job := await self._tracker.start(job_id)) is None:
            # The job has finished already, or it left the queue since it was read: another delivery started it, whose
            # upload this one must leave, or it was cancelled, which nothing delivers again, so its upload is removed
            if (stored := await self._uow.jobs.get(job_id)).state.is_final:
                await self._storage.sources.discard(stored.project_id, stored.id)
            return
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
                await self._queue_split(run)
            else:
                await self._conclude(run, JobState.FAILED, error=NO_SOURCE_IMPORTED)

    async def _conclude(self, run: ImportRun, state: JobState, *, error: str = '') -> None:
        """Store the final state and the result of a job, remove what is left of its upload, and announce it.

        Opens its own ``change`` block, so it is called outside any block, and the job is read again inside it as last
        stored. A job that the account holder cancelled meanwhile stays cancelled and only gains its result.

        :param run: The run that ended, which holds the latest job and its result.
        :type run: ImportRun
        :param state: State to store, unless the job was cancelled.
        :type state: JobState
        :param error: Why the job failed, or empty.
        :type error: str
        """
        final = evolve(run.job, state=state, error=error, result=run.result, finished_at=self._clock.now())
        async with self._uow.change():
            if (stored := await self._uow.jobs.update_if_state(final, expected=(JobState.RUNNING,))) is None:
                cancelled = await self._uow.jobs.get(run.job.id)
                stored = await self._uow.jobs.update_if_state(
                    evolve(cancelled, result=run.result), expected=(JobState.CANCELLED,)
                )
        await self._storage.sources.discard(run.job.project_id, run.job.id)
        if stored is not None:
            await self._publisher.publish(JobChanged(project_id=stored.project_id, job=stored))

    async def _queue_split(self, run: ImportRun) -> None:
        """Queue a run of the page split on the pages an import made, when the recipe of text pages decides it.

        The book gets its pages with no action of the user, even with the tab closed. The pages of an import are text
        until their content is detected. A book whose recipe was chosen by hand is left as it is, and so is a book whose
        project is processing something already, since the user can run
        the stage. Neither is an error of the import, which has succeeded.

        :param run: The run that ended, whose result lists the sources it imported.
        :type run: ImportRun
        """
        project_id = run.job.project_id
        try:
            steps = (await self._recipes.of_kind(project_id, Stage.PAGE_SPLIT, RecipeKind.TEXT)).enabled_steps
            if not steps or steps[0].processor_key != AUTOMATIC_SPLIT:
                return
            pages = [
                page.id
                for source_id in run.result.imported
                for page in await self._uow.pages.list_for_source(project_id, source_id)
            ]
            if pages:
                await self._starter.enqueue(
                    project_id, JobKind.RUN_STAGE, StageRun(stage=Stage.PAGE_SPLIT, page_ids=tuple(pages)).to_map()
                )
        except DomainError:
            logger.warning('The split of the pages of import job %s was not queued', run.job.id, exc_info=True)
