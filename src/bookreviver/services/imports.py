"""Import use cases: accept an upload and put its job on the board, then run the job source by source.

An upload of any number of files becomes one import job. ``ImportService.start_import`` receives the files into the
job's own directory and puts the job on the board, and the request is answered at once. ``ImportService.run_import`` is
the body of the job, which hands the work to an ``ImportRun``. The whole import is one task of the job, so the job is
cancelled between sources and between scans, and not inside one, and its progress counts the files dealt with and the
scans cut.

A run imports every file on its own, so one broken file never cancels the others. In its first phase it groups the
staged files into sources and stores each source, its scans and the pages the scans give the book in one
``Book.change`` of its own, with the enrichment of the book description, so a source is either fully in the book or
not at all. Every file of the upload is a row of ``sources``: an imported one joins the book, one that a check refused
is stored as rejected with its reason, and when the job is cancelled each file it did not reach is stored as skipped.
The rows that are not imported are the report of the import, which the reader reads and then closes. In its second
phase the run cuts the images of every scan of the book that has none, which covers the scans of the sources just
stored and the scans a cancelled or interrupted import left behind. Inspection, rasterising and processing run outside
any transaction, and the work of a transaction holds only the reads that decide and the writes. The staged upload is
removed when the run ends, however it ends.

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

import logging
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING
from uuid import uuid4

import anyio
from attrs import evolve, frozen

from bookreviver.domain.entities import Job, Page, Scan, Source
from bookreviver.domain.enums import (
    FileType,
    JobKind,
    JobState,
    PageOrigin,
    RecipeKind,
    RejectionReason,
    SourceStatus,
    Stage,
    SystemFile,
    UploadProblem,
    VersionState,
)
from bookreviver.domain.errors import (
    ConflictError,
    DomainError,
    JobCancelledError,
    UnsupportedSourceError,
    UploadRejectedError,
)
from bookreviver.domain.ids import JobId, PageId, ScanId, SourceId
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.values import ImportRequest, JobEnd, PageStageKey, Renditions, StageRun, UploadPath
from bookreviver.services.base_versions import SPLIT_NONE, BaseVersions
from bookreviver.services.page_labels import PageLabels
from bookreviver.services.source_labels import SourceLabels
from bookreviver.services.stage_records import StageRecords
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import BookRecord
    from bookreviver.domain.enums import SourceKind
    from bookreviver.domain.values import MetadataSuggestion, UploadedSource
    from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.runtime import JobControl
    from bookreviver.ports.storage import IncomingFile
    from bookreviver.services.book import Book
    from bookreviver.services.processing_parts import ProcessingParts
    from bookreviver.services.steps import StepRunner

IMPORT_ACTIVE: str = 'This book is already importing files. Wait for the import to finish, or cancel it.'
NO_SOURCE_IMPORTED: str = 'None of the uploaded files could be imported.'
# The processor of the page split that decides for each scan, whose recipe an import runs on the new pages
AUTOMATIC_SPLIT: str = 'split.auto'
# Kinds of job that import files, of which a book runs one at a time
IMPORT_JOBS: frozenset[JobKind] = frozenset({JobKind.IMPORT_SOURCE})

logger = logging.getLogger(__name__)


@frozen(kw_only=True)
class ImportLimits:
    """What bounds one upload and one import, read from the settings by the composition root.

    :ivar max_files: Largest number of files in one upload.
    :ivar max_bytes: Largest total size of one upload in bytes.
    :ivar parallel_scans: Largest number of scans cut at the same time.
    """

    max_files: int
    max_bytes: int
    parallel_scans: int


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
    """What an import orders by and is bounded by.

    :ivar order_keys: Builder of the order keys of the new pages.
    :ivar limits: Bounds of an upload and of an import.
    """

    order_keys: OrderKeys
    limits: ImportLimits


@frozen(kw_only=True)
class RunningImport:
    """An import job that the board runs, with the control it reports through.

    :ivar job: The running job, whose request lists the staged files.
    :ivar control: What the job reports its progress through, and learns of its cancellation from.
    """

    job: Job
    control: JobControl


class ImportRun:
    """One execution of an import job, which keeps what the job has done so far.

    The run is the state its steps share: the sources imported, the files dealt with and the progress. Several scans
    are cut at once, and each of them adds to the progress, which is plain counting on the event loop, so no lock is
    needed. The progress counts the files dealt with, and the scans cut against the scans to cut. Every report is
    also the check that the job was not cancelled.

    :ivar imported: Identifiers of the sources the run imported, in book order.
    :ivar book: The open book the job imports into, whose ``Book.change`` stores each source. ``execute`` opens it,
                so it is there for every step that follows and for ``record_skipped``.
    """

    def __init__(
        self,
        *,
        running: RunningImport,
        book: Book,
        imaging: ImportImaging,
        runtime: ImportRuntime,
    ) -> None:
        """Prepare to run a job that is running already.

        :param running: The running job, whose request lists the files to import, and its control.
        :type running: RunningImport
        :param book: The open book the job imports into, whose stores hold the upload, the sources and their scans.
        :type book: Book
        :param imaging: The inspector, the rasterizer, the tiler and the runner of processors.
        :type imaging: ImportImaging
        :param runtime: The order keys and the limits.
        :type runtime: ImportRuntime
        """
        job = running.job
        self.imported: list[SourceId] = []
        self.book = book
        self._job = job
        self._control = running.control
        self._files = {file.name: file for file in (job.request.files if job.request else ())}
        self._sources = book.sources
        self._assets = book.assets
        self._inspector = imaging.inspector
        self._rasterizer = imaging.rasterizer
        self._runner = imaging.runner
        self._records = StageRecords()
        self._labels = PageLabels()
        self._base_versions = BaseVersions(tiler=imaging.tiler)
        self._order_keys = runtime.order_keys
        self._limits = runtime.limits
        self._keys = BookKeys()
        self._handled: set[str] = set()
        self._done = 0
        self._total = len(self._files)

    async def execute(self) -> None:
        """Import the staged files as sources, then cut the images of every scan of the book that has none.

        :raises JobCancelledError: If the job was cancelled, which the first report after it finds out.
        """
        unready = await self.book.read(lambda repos: repos.scans.list_unready())
        await self._report(total=len(unready))
        await self._import_sources(list(self._files))
        await self._cut_scans()

    async def record_skipped(self) -> None:
        """Store each file the run did not reach as a skipped source, which is what a cancelled import leaves.

        A file whose suffix names a type the application accepts keeps that type, and any other has none.
        """
        names = [name for name in self._files if name not in self._handled]
        moment = datetime.now(UTC)
        rows = [
            Source(
                id=SourceId(uuid4()),
                kind=None if (file_type := FileType.from_name(name)) is None else file_type.source_kind,
                file_type=file_type,
                file_name=name,
                files=[self._files[name]],
                size_bytes=self._files[name].size_bytes,
                sha256=self._files[name].sha256,
                scan_count=0,
                status=SourceStatus.SKIPPED,
                imported_at=moment,
            )
            for name in names
        ]
        if rows:
            await self.book.change(lambda repos, _book_record: repos.sources.add_many(rows))

    async def _import_sources(self, names: Sequence[str]) -> None:
        """Group the named staged files into sources and import each one, in the order of the upload.

        A system file of a directory, such as ``Thumbs.db`` or ``._001.tif``, and a file of a type no source can be are
        each rejected on its own, so they do not stop the upload and are listed in its report. The names come in the
        order of the upload, which the inspector keeps, so that order becomes the order of the pages of the book.

        :param names: Relative names of the staged files, in the order of the upload.
        :type names: Sequence[str]
        :raises JobCancelledError: If the job was cancelled.
        """
        supported: list[str] = []
        for name in names:
            if SystemFile.matches(name):
                await self._reject([name], RejectionReason.SYSTEM_FILE, RejectionReason.SYSTEM_FILE.label)
            elif FileType.from_name(name) is None:
                await self._reject([name], RejectionReason.UNSUPPORTED_TYPE, RejectionReason.UNSUPPORTED_TYPE.label)
            else:
                supported.append(name)
        if not supported:
            return
        async with self._sources.staged_files(self._job.id) as paths:
            grouped = await self._inspector.group({name: paths[name] for name in supported})
        for uploaded in grouped:
            await self._import_source(uploaded)

    async def _import_source(self, uploaded: UploadedSource) -> None:
        """Import one source: check it is new and readable, store it with its scans and pages, and promote its files.

        The source, its scans and its pages are stored in one ``Book.change``, which reads the end of the book and the
        sections of the book to place the new pages, and a source whose files cannot be promoted is taken back whole
        in a second one.

        :param uploaded: The staged files that make the source.
        :type uploaded: UploadedSource
        :raises JobCancelledError: If the job was cancelled.
        """
        files = [self._files[name] for name in uploaded.names]
        main = files[0]
        twin = await self.book.read(lambda repos: repos.sources.find_by_sha256(main.sha256))
        if twin is not None:
            await self._reject(
                uploaded.names,
                RejectionReason.DUPLICATE,
                f'The book already has this file as {twin.file_name}.',
                uploaded=uploaded,
            )
            return
        async with self._sources.staged_files(self._job.id) as paths:
            try:
                analysis = await self._inspector.inspect(uploaded.kind, [paths[name] for name in uploaded.names])
            except UnsupportedSourceError as error:
                await self._reject(uploaded.names, RejectionReason.UNREADABLE, str(error), uploaded=uploaded)
                return
        moment = datetime.now(UTC)
        source = Source(
            id=SourceId(uuid4()),
            kind=uploaded.kind,
            file_type=uploaded.file_type,
            file_name=main.name,
            files=files,
            size_bytes=sum(file.size_bytes for file in files),
            sha256=main.sha256,
            scan_count=len(analysis.scans),
            metadata=analysis.file_metadata,
            suggestion=analysis.suggestion,
            imported_at=moment,
        )
        scans = [
            Scan(
                id=ScanId(uuid4()),
                source_id=source.id,
                number=number,
                source_label=analysis.label_of(number),
                facts=facts,
            )
            for number, facts in enumerate(analysis.scans)
        ]

        def store(repos: BookRepositories, book_record: BookRecord) -> list[Page]:
            """Store the source with its scans and pages, and describe the book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it.
            :type book_record: BookRecord
            :returns: The pages as stored.
            :rtype: list[Page]
            """
            order_keys = self._order_keys.spread(lower=repos.pages.last_order_key(), upper=None, count=len(scans))
            pages = [
                Page(
                    id=PageId(uuid4()),
                    order_key=order_key,
                    manual_label=scan.source_label or None,
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
                book_has_sections=bool(repos.pagination_sections.list_all()),
            )
            pages = source_labels.pages
            repos.sources.add(source)
            repos.scans.add_many(scans)
            repos.pages.add_many(pages)
            if source_labels.sections:
                repos.pagination_sections.add_many(source_labels.sections)
            # The new pages may take numbers past what a section of the book can write
            self._labels.settle(repos)
            self._describe_book(repos, book_record, analysis.suggestion)
            return pages

        pages = await self.book.change(store)

        def retract(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Delete the pages and the source, which takes their scans with it.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the retraction does not need.
            :type _book_record: BookRecord
            """
            for page in pages:
                repos.pages.delete(page.id)
            repos.sources.delete(source.id)

        try:
            await self._sources.promote(self._job.id, source.id, names=list(uploaded.names))
        except Exception:
            # The source is stored and its files are not moved, and nothing would repair it once the job ends, so it
            # is taken back whole. Its pages go first, since deleting the source only empties their scan.
            await self.book.change(retract)
            raise
        self.imported.append(source.id)
        self._handled.update(uploaded.names)
        await self._report(done=len(uploaded.names), total=len(scans))

    def _describe_book(self, repos: BookRepositories, book_record: BookRecord, suggestion: MetadataSuggestion) -> None:
        """Fill the empty fields of the book description from what a source suggests, never the title.

        Writes through the repositories of its caller and opens no transaction, so the change joins the one of the
        source, and a source is never stored without the values it gave the book. Sources come in order, so the first
        source that has a value for a field is the one that gives it.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param book_record: The record of the book as the change of the source found it.
        :type book_record: BookRecord
        :param suggestion: Description fields found in the source, empty where nothing was found.
        :type suggestion: MetadataSuggestion
        """
        details = book_record.details.fill_from(suggestion)
        if details != book_record.details:
            repos.book_record.update(evolve(book_record, details=details, updated_at=datetime.now(UTC)))

    async def _cut_scans(self) -> None:
        """Cut the images of every scan of the book that has none, a few at a time, in the order of the book.

        :raises JobCancelledError: If the job was cancelled and no cutter failed.
        :raises DomainError: If cutting one scan failed on a rule of the domain, which stops the others.
        :raises Exception: The first other fault of a cutter, which stops the others.
        :raises BaseExceptionGroup: If a cutter was stopped by a ``BaseException``, such as the death of the worker.
        """

        def read(repos: BookRepositories) -> tuple[Sequence[Scan], dict[SourceId, SourceKind]]:
            """Read the scans without renditions and the kinds of the sources of the book.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The scans to cut in the order of the book, and the kind of each imported source by identifier.
            :rtype: tuple[Sequence[Scan], dict[SourceId, SourceKind]]
            """
            sources = repos.sources.list_all()
            kinds = {source.id: source.kind for source in sources if source.kind is not None}
            return repos.scans.list_unready(), kinds

        scans, kinds = await self.book.read(read)
        pending = iter(scans)

        async def worker() -> None:
            """Take scans from the shared iterator until none is left, each one cut before the next is taken."""
            for scan in pending:
                await self._cut(kinds[scan.source_id], scan)

        try:
            async with anyio.create_task_group() as group:
                for _ in range(min(self._limits.parallel_scans, len(scans))):
                    group.start_soon(worker)
        except BaseExceptionGroup as errors:
            # The group of anyio holds the fault of every cutter, in the order the cutters finished, whether the cutter
            # raised it before the cancellation of the group reached it or while unwinding from it. A cancellation is
            # only the job's own state, so it must not hide a fault, which would end the job as cancelled unlogged.
            _, faults = errors.split(JobCancelledError)
            raise (faults or errors).exceptions[0] from errors

    async def _cut(self, kind: SourceKind, scan: Scan) -> None:
        """Write the four renditions of a scan and the base version of its pages, then mark the scan ready.

        The format of ``full`` is chosen here, from the colour of the scan and the image policy of the book as it is
        now, and recorded with the scan, so a later change of the policy changes no stored path. The base version of a
        page copies that format along with the image. What an earlier attempt left in the directories of the scan and
        of the versions is removed first, since a stored file is never replaced. The files are written outside any
        transaction, and the scan and its versions are marked ready together, in one ``Book.change``. That change reads
        the scan and its pages again. A scan that is gone or ready already, or whose pages are not the ones the
        versions were made for, is left as it is, its files are removed, and the run counts one scan less to cut.

        :param kind: Kind of the source holding the scan.
        :type kind: SourceKind
        :param scan: The scan to cut, whose renditions are not ready.
        :type scan: Scan
        :raises JobCancelledError: If the job was cancelled.
        """

        def start(repos: BookRepositories, book_record: BookRecord) -> tuple[BookRecord, list[Page]]:
            """Read the record of the book and the pages of the scan.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param book_record: The record of the book as the change finds it, whose image policy chooses the format of
                                the scan.
            :type book_record: BookRecord
            :returns: The record of the book, and the pages cut from the scan.
            :rtype: tuple[BookRecord, list[Page]]
            """
            return book_record, list(repos.pages.list_for_scan(scan.id))

        # A cancellation stops the run before the next scan is cut
        await self._report()
        book_record, pages = await self.book.change(start)
        full = book_record.image_policy.full_format(scan.facts.color_mode)
        ready = evolve(scan, renditions=Renditions(ready=True, version=scan.renditions.version, full=full))
        of_scan = partial(self._keys.scan_rendition, ready)
        await self._assets.delete_prefix(self._keys.scan_directory(scan))
        async with (
            self._sources.source_files(scan.source_id) as files,
            self._assets.writable(of_scan(full)) as target,
        ):
            await self._rasterizer.extract(kind, files, scan.number, target, full=full)
        await self._base_versions.derive(of_scan, assets=self._assets, full=full)
        versions = []
        run = StepRun(
            processor_key=SPLIT_NONE.key,
            params={},
            input_data=ready.facts.as_data(),
            image=of_scan(full),
        )
        for page in pages:
            version = BaseVersions.split_none(page=page, scan=ready, state=VersionState.READY, moment=datetime.now(UTC))
            async with self._runner.execute(self.book, run) as result:
                versions.append(
                    await self._runner.store(
                        self.book, version, result.outputs[0], policy=book_record.image_policy, tiles=True
                    )
                )

        def finish(repos: BookRepositories, _book_record: BookRecord) -> bool:
            """Store the versions and mark the scan ready, unless the book changed while the files were cut.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the check does not need.
            :type _book_record: BookRecord
            :returns: Whether the scan and its pages were still the ones the versions were made for.
            :rtype: bool
            """
            # The scan and its pages are read again, since the book may have changed while the files were cut
            unready = {unready_scan.id for unready_scan in repos.scans.list_unready()}
            current = {page.id for page in repos.pages.list_for_scan(scan.id)}
            if scan.id not in unready or current != {page.id for page in pages}:
                return False
            repos.page_versions.add_many(versions)
            for page, version in zip(pages, versions, strict=True):
                self._records.set_head(
                    repos, PageStageKey(page.id, version.stage), head_version_id=version.id, recipe_id=None
                )
            repos.scans.update(ready)
            return True

        fits = await self.book.change(finish)
        if not fits:
            # The scan stays unready for the next run to cut, and the files made for it are of no version or scan
            logger.warning('Scan %s was changed or deleted while its images were cut, so they were not stored', scan.id)
            await self._assets.delete_prefix(self._keys.scan_directory(scan))
            for version in versions:
                await self._assets.delete_prefix(self._keys.version_directory(version))
            await self._report(total=-1)
            return
        await self._report(done=1)

    async def _reject(
        self,
        names: Sequence[str],
        reason: RejectionReason,
        detail: str,
        *,
        uploaded: UploadedSource | None = None,
    ) -> None:
        """Store the files of one source as a rejected source with the reason, and count them as dealt with.

        :param names: Names of the staged files, the main file first.
        :type names: Sequence[str]
        :param reason: Why the files were rejected.
        :type reason: RejectionReason
        :param detail: Text for the user that says more than the reason.
        :type detail: str
        :param uploaded: The files as the inspector grouped them, which tell the kind and the type of the source, or
                         None for a file that no check let get as far as the inspector, which has none.
        :type uploaded: UploadedSource | None
        :raises JobCancelledError: If the job was cancelled.
        """
        files = [self._files[name] for name in names]
        source = Source(
            id=SourceId(uuid4()),
            kind=None if uploaded is None else uploaded.kind,
            file_type=None if uploaded is None else uploaded.file_type,
            file_name=names[0],
            files=files,
            size_bytes=sum(file.size_bytes for file in files),
            sha256=files[0].sha256,
            scan_count=0,
            status=SourceStatus.REJECTED,
            rejection=reason,
            detail=detail,
            imported_at=datetime.now(UTC),
        )
        await self.book.change(lambda repos, _book_record: repos.sources.add(source))
        self._handled.update(names)
        await self._report(done=len(names))

    async def _report(self, *, done: int = 0, total: int = 0) -> None:
        """Add to the progress of the job, announce it, and stop the run when the job was cancelled.

        :param done: Units completed since the last report: files dealt with, or scans cut.
        :type done: int
        :param total: Units added to the total since the last report, which may be negative.
        :type total: int
        :raises JobCancelledError: If the job was cancelled.
        """
        self._done += done
        self._total += total
        await self._control.report(done=self._done, total=self._total)


class ImportService:
    """Accepts uploads for the owner of a book, and runs the import jobs they become."""

    def __init__(
        self,
        *,
        imaging: ImportImaging,
        runtime: ImportRuntime,
        parts: ProcessingParts,
    ) -> None:
        """Build the service over the ports of one request or job.

        :param imaging: The inspector, the rasterizer and the tiler.
        :type imaging: ImportImaging
        :param runtime: The order keys and the limits.
        :type runtime: ImportRuntime
        :param parts: The recipes and the job starter, with which the split of the new pages is queued.
        :type parts: ProcessingParts
        """
        self._imaging = imaging
        self._runtime = runtime
        self._recipes = parts.recipes
        self._starter = parts.starter
        self._limits = runtime.limits

    async def authorize_upload(self, book: Book) -> None:
        """Refuse an upload the book cannot take now, before any file is read.

        The API calls this before it parses the body of the request, so a refused caller costs the server no upload
        to spool, and ``start_import`` repeats it, since a caller of the service needs no API.

        :param book: The open book.
        :type book: Book
        :raises ConflictError: If the book already has an import queued or running.
        """
        self._refuse_active_import(book)

    def _refuse_active_import(self, book: Book) -> None:
        """Refuse a book that has an import queued or running.

        Only reads the board, so it is called for an early answer from ``authorize_upload``, and again right before
        the job is put, with no ``await`` in between, which gives the answer that holds.

        :param book: Book to import into.
        :type book: Book
        :raises ConflictError: If the book already has an import queued or running.
        """
        if any(job.kind in IMPORT_JOBS for job in book.jobs.active()):
            raise ConflictError(IMPORT_ACTIVE)

    async def start_import(self, book: Book, files: Sequence[IncomingFile]) -> Job:
        """Receive an upload into the directory of a new import job and put the job on the board.

        The rules that need no file are checked before anything is received, so an upload that cannot be imported is
        not streamed first. The one-import rule is checked again right before the job is put, with no ``await`` between
        the check and the put, so two uploads cannot both pass it. The upload is removed again when that check fails.

        :param book: The open book to import into.
        :type book: Book
        :param files: The uploaded files.
        :type files: Sequence[IncomingFile]
        :returns: The queued job, whose request lists the staged files.
        :rtype: Job
        :raises UploadRejectedError: If there are no files or too many, a file has no usable name or a path that leaves
                                     its folder, two files share a path, or the upload is larger than allowed.
        :raises ConflictError: If the book already has an import queued or running.
        """
        await self.authorize_upload(book)
        if not files:
            raise UploadRejectedError(UploadProblem.NO_FILES)
        if len(files) > self._limits.max_files:
            raise UploadRejectedError(UploadProblem.TOO_MANY_FILES)
        # A path that cannot be stored is found before the first file is streamed, not after hundreds of megabytes
        for file in files:
            UploadPath.parse(file.filename or '')
        job_id = JobId(uuid4())
        staged = await book.sources.stage(job_id, files, max_bytes=self._limits.max_bytes)
        try:
            self._refuse_active_import(book)
            return book.jobs.put(
                Job(
                    id=job_id,
                    kind=JobKind.IMPORT_SOURCE,
                    request=ImportRequest(files=staged),
                    created_at=datetime.now(UTC),
                )
            )
        except BaseException:
            await book.sources.discard(job_id)
            raise

    async def run_import(self, book: Book, job: Job, control: JobControl) -> JobEnd:
        """Run an import job the board took from its queue, and remove its upload however it ends while its book exists.

        The import is one task. A cancellation stores every file the run did not reach as a skipped source and goes
        on as ``JobCancelledError``. An import that took in at least one source succeeds and queues the split of its
        pages, and one that took in none fails.

        :param book: The book of the queue the job was taken from.
        :type book: Book
        :param job: The running job, whose request lists the staged files.
        :type job: Job
        :param control: What the job reports its progress through, and learns of its cancellation from.
        :type control: JobControl
        :returns: How the job ended: failed when no source could be imported or a rule of the domain stopped it.
        :rtype: JobEnd
        :raises JobCancelledError: If the job was cancelled.
        """
        run = ImportRun(
            running=RunningImport(job=job, control=control), book=book, imaging=self._imaging, runtime=self._runtime
        )
        try:
            await run.execute()
        except JobCancelledError:
            await run.record_skipped()
            raise
        except DomainError as error:
            return JobEnd(state=JobState.FAILED, error=str(error))
        finally:
            await book.sources.discard(job.id)
        if not run.imported:
            return JobEnd(state=JobState.FAILED, error=NO_SOURCE_IMPORTED)
        await self._queue_split(job, run)
        return JobEnd(state=JobState.SUCCEEDED)

    async def _queue_split(self, job: Job, run: ImportRun) -> None:
        """Queue a run of the page split on the pages an import made, when the recipe of text pages decides it.

        The book gets its pages with no action of the user, even with the tab closed. The pages of an import are text
        until their content is detected. A book whose recipe was chosen by hand is left as it is, and so is a book that
        is processing something already, since the user can run
        the stage. Neither is an error of the import, which has succeeded.

        :param job: The import job that ended.
        :type job: Job
        :param run: The run that ended, which lists the sources it imported.
        :type run: ImportRun
        """
        try:
            steps = (await self._recipes.of_kind(run.book, Stage.PAGE_SPLIT, RecipeKind.TEXT)).enabled_steps
            if not steps or steps[0].processor_key != AUTOMATIC_SPLIT:
                return
            pages = await run.book.read(
                lambda repos: [
                    page.id for source_id in run.imported for page in repos.pages.list_for_source(source_id)
                ],
            )
            if pages:
                await self._starter.enqueue(
                    run.book, JobKind.RUN_STAGE, StageRun(stage=Stage.PAGE_SPLIT, page_ids=tuple(pages)).to_map()
                )
        except DomainError:
            logger.warning('The split of the pages of import job %s was not queued', job.id, exc_info=True)
