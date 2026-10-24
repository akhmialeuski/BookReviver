"""Fakes of the import feature: the real imaging adapters watched and steered by a test, and the rig around them.

The import service runs on the application's own adapters, the local stores over a temporary directory and the real
reader and tiler over small generated files, so a test proves the whole path from an upload to the files of a page.
What a test needs beyond that is a way to look at the adapters and to steer them: how many scans are cut at once,
which resource id a pyramid was cut for, and a failure, a cancellation or a crash at a chosen step. Each adapter here
wraps the real one and adds only that. ``WorkerCrashError`` is a ``BaseException``, so no handler of the service
catches it and the job stays running, as it does when a worker process dies.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, NamedTuple, override

import anyio
from attrs import field, frozen

from bookreviver.adapters.imaging import (
    DjvuFormat,
    DjvuLibreTools,
    ImageFormat,
    PdfFormat,
    SourceReader,
    VipsRenditionWriter,
    VipsTiler,
)
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.storage import LocalSourceStore
from bookreviver.app.settings import ImagingSettings
from bookreviver.domain.pagination import Pagination
from bookreviver.domain.values import SliceRequest
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
from bookreviver.services.imports import ImportImaging, ImportLimits, ImportRuntime, ImportService
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts
from bookreviver.services.steps import StepRunner
from tests.adapters.imaging.samples import PdfPage, ScanImage, write_image, write_pdf
from tests.helpers.books_service import make_books_service
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.fakes_jobs import JobFakes, RecordingEventBus
from tests.helpers.in_process_executor import InProcessStepExecutor
from tests.helpers.processing import DEFAULTS, PREVIEW_LONG_SIDE_PX, PREVIEW_RETENTION_HOURS
from tests.helpers.storage import SharedBookDirectories, upload

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from pathlib import Path

    from fastapi import UploadFile

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Job, Page, PaginationSection, Scan, Source
    from bookreviver.domain.enums import Rendition, SourceKind
    from bookreviver.domain.ids import BookDirectoryName, BookId, JobId, SourceId
    from bookreviver.domain.values import JobEnd, SourceAnalysis, UploadedSource
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase
    from bookreviver.ports.processing import Processor
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.recipes import DefaultRecipes
    from tests.helpers.builders import BookOpener
    from tests.helpers.database import CatalogueDatabaseFactory, LocatedBookDatabases

PAGE_WIDTH_PX: int = 200
PAGE_HEIGHT_PX: int = 300
GRAY_MODE: str = 'L'
JPEG_FORMAT: str = 'JPEG'
MAX_FILES: int = 100
MAX_BYTES: int = 10 * 1024 * 1024
DEFAULT_PARALLEL_SCANS: int = 2
# Long enough for two scans cut at once to overlap, short enough to keep a test fast
OVERLAP_SECONDS: float = 0.05
# Bound of an import whose cutters wait on events of the test, so a cutter that never comes fails it instead of hanging
IMPORT_TIMEOUT_SECONDS: float = 30
# A window wide enough for every scan and page of a book a test imports
BOOK_WINDOW: SliceRequest = SliceRequest(limit=5000)


class StoredBook(NamedTuple):
    """The rows an import leaves in a book, read in one snapshot.

    :ivar sources: The sources, in the order they were imported.
    :ivar scans: The scans, source by source.
    :ivar pages: The pages, in book order.
    :ivar sections: The pagination sections, in the order they were made.
    """

    sources: list[Source]
    scans: list[Scan]
    pages: list[Page]
    sections: list[PaginationSection]

    @property
    def labels(self) -> list[str]:
        """The labels the pages show, in book order, computed from the sections as a read of the pages does."""
        labels = Pagination(self.sections, self.pages).labels()
        return [labels[page.id] for page in self.pages]


async def stored_book(book_id: BookId, *, databases: LocatedBookDatabases) -> StoredBook:
    """Read the sources, scans, pages and pagination sections of a book as committed.

    :param book_id: Book owning the rows.
    :type book_id: BookId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The rows, read in one transaction.
    :rtype: StoredBook
    """

    def work(repos: BookRepositories) -> StoredBook:
        """Read the four tables of the book.

        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :returns: The rows of the book.
        :rtype: StoredBook
        """
        return StoredBook(
            sources=list(repos.sources.list_all()),
            scans=list(repos.scans.list_window(BOOK_WINDOW).items),
            pages=list(repos.pages.list_window(BOOK_WINDOW).items),
            sections=list(repos.pagination_sections.list_all()),
        )

    return await databases.open(book_id).read(work)


def pdf_upload(
    directory: Path,
    name: str,
    *,
    pages: int = 2,
    width_px: int = PAGE_WIDTH_PX,
    metadata: Mapping[str, str] | None = None,
) -> UploadFile:
    """Return an upload of a scanned PDF, each page one gray JPEG, whose content follows from its arguments.

    :param directory: Existing directory to build the file in.
    :type directory: Path
    :param name: Name the browser sends for the file, also the name of the built file.
    :type name: str
    :param pages: Number of pages, one scan each.
    :type pages: int
    :param width_px: Width of every page image in pixels, so files of different widths differ in content.
    :type width_px: int
    :param metadata: Document information such as ``title`` and ``author``, or None for none.
    :type metadata: Mapping[str, str] | None
    :returns: The upload, as FastAPI hands it to the source store.
    :rtype: UploadFile
    """
    page = PdfPage(images=[ScanImage(mode=GRAY_MODE, size_px=(width_px, PAGE_HEIGHT_PX), image_format=JPEG_FORMAT)])
    path = write_pdf(directory / name, pages=[page] * pages, metadata=metadata)
    return upload(name, content=path.read_bytes())


def image_upload(directory: Path, name: str, *, width_px: int = PAGE_WIDTH_PX, mode: str = GRAY_MODE) -> UploadFile:
    """Return an upload of a page image, gray unless told otherwise, whose format follows the suffix of its name.

    :param directory: Existing directory to build the file in.
    :type directory: Path
    :param name: Name the browser sends for the file, also the name of the built file.
    :type name: str
    :param width_px: Width of the image in pixels, so files of different widths differ in content.
    :type width_px: int
    :param mode: Pillow mode of the image, such as ``1`` for a bilevel page or ``RGB`` for a colour one.
    :type mode: str
    :returns: The upload, as FastAPI hands it to the source store.
    :rtype: UploadFile
    """
    path = write_image(directory / name, mode=mode, size=(width_px, PAGE_HEIGHT_PX))
    return upload(name, content=path.read_bytes())


def djvu_uploads(files: Sequence[Path]) -> list[UploadFile]:
    """Return an upload of each built DjVu file, named as the file is.

    :param files: Built DjVu files, such as the index and the page files of an indirect document.
    :type files: Sequence[Path]
    :returns: The uploads, as FastAPI hands them to the source store, in the order of ``files``.
    :rtype: list[UploadFile]
    """
    return [upload(path.name, content=path.read_bytes()) for path in files]


class WorkerCrashError(BaseException):
    """A worker that dies in the middle of a job, which the service must not catch since it cannot."""


class WatchedInspector(SourceInspector):
    """The real inspector, with a hook run before every source is inspected.

    :ivar before_inspect: Coroutine function run before each ``inspect``, or None.
    :ivar inspected: The kind and the names of the files of every source inspected, in order.
    """

    def __init__(self, inner: SourceInspector) -> None:
        """Wrap the real inspector.

        :param inner: Inspector that does the work.
        :type inner: SourceInspector
        """
        self._inner = inner
        self.before_inspect: Callable[[], Awaitable[None]] | None = None
        self.inspected: list[tuple[SourceKind, list[str]]] = []

    @override
    async def group(self, files: Mapping[str, Path]) -> Sequence[UploadedSource]:
        """Group the files with the real inspector.

        :param files: Local paths of the staged files by their relative name, in the order of the upload.
        :type files: Mapping[str, Path]
        :returns: The sources the files make.
        :rtype: Sequence[UploadedSource]
        """
        return await self._inner.group(files)

    @override
    async def inspect(self, kind: SourceKind, files: Sequence[Path]) -> SourceAnalysis:
        """Run the hook, then inspect the source with the real inspector.

        :param kind: Kind of the source.
        :type kind: SourceKind
        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :returns: The analysis of the source.
        :rtype: SourceAnalysis
        """
        if self.before_inspect is not None:
            await self.before_inspect()
        self.inspected.append((kind, [path.name for path in files]))
        return await self._inner.inspect(kind, files)


class WatchedRasterizer(PageRasterizer):
    """The real rasterizer, counting how many scans it writes at once and failing on demand.

    :ivar running: Number of scans being written now.
    :ivar peak: Largest number of scans written at once.
    :ivar extracted: Number of every scan written, in the order the writes started.
    :ivar formats: Format asked for every scan written, in the same order.
    :ivar pause: Seconds every write waits before it starts, so writes overlap.
    :ivar failures: Error to raise instead of writing the scan with this number.
    :ivar before_extract: Coroutine function run with the number of a scan before it is written, or None.
    """

    def __init__(self, inner: PageRasterizer) -> None:
        """Wrap the real rasterizer.

        :param inner: Rasterizer that does the work.
        :type inner: PageRasterizer
        """
        self._inner = inner
        self.running = 0
        self.peak = 0
        self.extracted: list[int] = []
        self.formats: list[Rendition] = []
        self.pause = 0.0
        self.failures: dict[int, BaseException] = {}
        self.before_extract: Callable[[int], Awaitable[None]] | None = None

    @override
    async def extract(
        self, kind: SourceKind, files: Sequence[Path], number: int, target: Path, *, full: Rendition
    ) -> None:
        """Write the scan with the real rasterizer, unless the test made this scan fail.

        :param kind: Kind of the source.
        :type kind: SourceKind
        :param files: Local paths of the files of the source.
        :type files: Sequence[Path]
        :param number: Number of the scan in its source.
        :type number: int
        :param target: Path to write the image at.
        :type target: Path
        :param full: Format of the image to write.
        :type full: Rendition
        """
        self.running += 1
        self.peak = max(self.peak, self.running)
        self.extracted.append(number)
        self.formats.append(full)
        try:
            await anyio.sleep(self.pause)
            if self.before_extract is not None:
                await self.before_extract(number)
            if (failure := self.failures.get(number)) is not None:
                raise failure
            await self._inner.extract(kind, files, number, target, full=full)
        finally:
            self.running -= 1


class WatchedTiler(Tiler):
    """The real tiler, remembering the directories it cut pyramids into and crashing on demand.

    :ivar targets: Directory of every pyramid cut, in order.
    :ivar crash_on_preview: Number of the preview call that raises ``WorkerCrashError``, counting from 1, or None.
    """

    def __init__(self, inner: Tiler) -> None:
        """Wrap the real tiler.

        :param inner: Tiler that does the work.
        :type inner: Tiler
        """
        self._inner = inner
        self.targets: list[Path] = []
        self.crash_on_preview: int | None = None
        self._previews = 0

    @override
    async def tile(self, image: Path, target_dir: Path) -> None:
        """Cut the pyramid with the real tiler and remember its directory.

        :param image: Image to cut.
        :type image: Path
        :param target_dir: Directory to create for the pyramid.
        :type target_dir: Path
        """
        self.targets.append(target_dir)
        await self._inner.tile(image, target_dir)

    @override
    async def preview(self, image: Path, target: Path) -> None:
        """Cut the preview with the real tiler, unless this is the call the test made crash.

        :param image: Image to shrink.
        :type image: Path
        :param target: Path to write the preview at.
        :type target: Path
        :raises WorkerCrashError: On the call ``crash_on_preview`` names.
        """
        self._previews += 1
        if self._previews == self.crash_on_preview:
            raise WorkerCrashError
        await self._inner.preview(image, target)

    @override
    async def thumbnail(self, image: Path, target: Path) -> None:
        """Cut the thumbnail with the real tiler.

        :param image: Image to shrink.
        :type image: Path
        :param target: Path to write the thumbnail at.
        :type target: Path
        """
        await self._inner.thumbnail(image, target)


class CrashingBookDirectories(SharedBookDirectories):
    """The local directories of the books, whose next promotion of a source in any book can crash the worker or fail.

    :ivar crash_on_promote: Whether the next ``promote`` raises ``WorkerCrashError``, once.
    :ivar fail_on_promote: Error the next ``promote`` raises, once, leaving the files staged as a refused one does.
    """

    def __init__(self, *, root: Path) -> None:
        """Keep the directories of the books under ``root``, with no fault asked for.

        :param root: Storage root of the directories.
        :type root: Path
        """
        super().__init__(root=root)
        self.crash_on_promote = False
        self.fail_on_promote: Exception | None = None

    @override
    def _new_sources(self, name: BookDirectoryName) -> CrashingSourceStore:
        """Build the source store of a book, which reads the faults of these directories.

        :param name: Name of the directory of the book.
        :type name: BookDirectoryName
        :returns: The source store of the book.
        :rtype: CrashingSourceStore
        """
        return CrashingSourceStore(root=self._files_root, name=name, faults=self)


class CrashingSourceStore(LocalSourceStore):
    """The local source store of one book, whose next promotion can crash the worker or fail with an error."""

    def __init__(self, *, root: Path, name: BookDirectoryName, faults: CrashingBookDirectories) -> None:
        """Keep the uploads and sources of one book under ``root``, failing as ``faults`` asks.

        :param root: Storage root of the stores.
        :type root: Path
        :param name: Name of the directory of the book.
        :type name: BookDirectoryName
        :param faults: The directories that hold the faults a test asks for.
        :type faults: CrashingBookDirectories
        """
        super().__init__(root=root, name=name)
        self._faults = faults

    @override
    async def promote(self, job_id: JobId, source_id: SourceId, *, names: Sequence[str]) -> None:
        """Promote the files, unless the test made this promotion crash.

        :param job_id: Import job whose upload holds the files.
        :type job_id: JobId
        :param source_id: Source the files become.
        :type source_id: SourceId
        :param names: Names of the staged files that make the source.
        :type names: Sequence[str]
        :raises WorkerCrashError: If a crash was asked for, which is then forgotten.
        :raises Exception: The error asked for, which is then forgotten.
        """
        if self._faults.crash_on_promote:
            self._faults.crash_on_promote = False
            raise WorkerCrashError
        if (failure := self._faults.fail_on_promote) is not None:
            self._faults.fail_on_promote = None
            raise failure
        await super().promote(job_id, source_id, names=names)


@frozen(kw_only=True)
class ImportRig:
    """Everything an import test runs on, built over one temporary storage root.

    :ivar database: The SQL database of the test, which stores the account of the actor.
    :ivar new_catalogue: Function opening a catalogue database over ``database``, as a new request or job would get.
    :ivar databases: Opens the databases of the books of the test.
    :ivar fakes: The recording event bus and the board, which the job tests share. The board runs a job
                 through the import service of the rig when a test calls ``run_queued``.
    :ivar events: The event bus of ``fakes``.
    :ivar files: The local directories of the books, whose promotion of a source can crash.
    :ivar inspector: Real inspector, watched.
    :ivar rasterizer: Real rasterizer, watched.
    :ivar tiler: Real tiler, watched.
    :ivar defaults: The recipes a stage starts with, which by default leave the page split to ``split.none``.
    :ivar processors: The processors of the catalogue the recipes are checked against.
    """

    database: SqlDatabase
    new_catalogue: CatalogueDatabaseFactory
    databases: LocatedBookDatabases
    fakes: JobFakes
    events: RecordingEventBus
    files: CrashingBookDirectories
    inspector: WatchedInspector
    rasterizer: WatchedRasterizer
    tiler: WatchedTiler
    defaults: DefaultRecipes = DEFAULTS
    processors: Sequence[Processor] = field(factory=lambda: [SplitNone()])

    @classmethod
    def build(cls, root: Path, database: SqlDatabase, stores: BookOpener) -> ImportRig:
        """Build the rig over the adapters of the application, with the settings' defaults.

        :param root: Storage root of both stores.
        :type root: Path
        :param database: SQL database of the test, such as ``fx_database``.
        :type database: SqlDatabase
        :param stores: Opens the catalogue databases over ``database`` and the databases of the books, such as
                       ``fx_open_book``.
        :type stores: BookOpener
        :returns: The rig with nothing stored.
        :rtype: ImportRig
        """
        imaging = ImagingSettings()
        reader = SourceReader(
            formats=(
                PdfFormat(jpeg_quality=imaging.jpeg_quality),
                ImageFormat(jpeg_quality=imaging.jpeg_quality),
                DjvuFormat(
                    tools=DjvuLibreTools.locate(),
                    jpeg_quality=imaging.jpeg_quality,
                    timeout_s=imaging.djvulibre_timeout_s,
                ),
            )
        )
        tiler = VipsTiler(
            tile_size_px=imaging.tile_size_px,
            preview_long_side_px=imaging.preview_long_side_px,
            thumbnail_long_side_px=imaging.thumbnail_long_side_px,
            jpeg_quality=imaging.jpeg_quality,
        )
        events = RecordingEventBus.current()
        # The board needs the service of the rig to run a job, and the rig needs the board, so the rig is found when a
        # job runs
        built: list[ImportRig] = []

        async def run(job: Job, control: JobControl) -> JobEnd:
            """Run an import job through a service of the rig, as the application runs it.

            :param job: The job the board took from its queue.
            :type job: Job
            :param control: What the job reports through.
            :type control: JobControl
            :returns: How the job ended.
            :rtype: JobEnd
            """
            return await built[0].service().run_import(job, control)

        rig = cls(
            database=database,
            new_catalogue=stores.catalogue,
            databases=stores.databases,
            fakes=JobFakes(events=events, runner=run),
            events=events,
            files=CrashingBookDirectories(root=root),
            inspector=WatchedInspector(reader),
            rasterizer=WatchedRasterizer(reader),
            tiler=WatchedTiler(tiler),
        )
        built.append(rig)
        return rig

    def runner(self) -> StepRunner:
        """Build the runner of ``split.none``, which makes the base version of a page, over the rig's assets and tiler.

        :returns: The runner, with the application's own writer of renditions.
        :rtype: StepRunner
        """
        return StepRunner(
            executor=InProcessStepExecutor(FakeCatalogue([SplitNone()])),
            renditions=VipsRenditionWriter(
                preview_long_side_px=ImagingSettings().preview_long_side_px,
                thumbnail_long_side_px=ImagingSettings().thumbnail_long_side_px,
                jpeg_quality=ImagingSettings().jpeg_quality,
            ),
            tiler=self.tiler,
        )

    def open_catalogue(self) -> CatalogueDatabase:
        """Open a catalogue database over the rig's database, as a new request or job would get.

        :returns: A new catalogue database.
        :rtype: CatalogueDatabase
        """
        return self.new_catalogue()

    def service(
        self,
        *,
        catalogue: CatalogueDatabase | None = None,
        parallel_scans: int = DEFAULT_PARALLEL_SCANS,
        max_files: int = MAX_FILES,
        max_bytes: int = MAX_BYTES,
    ) -> ImportService:
        """Build an import service over a new catalogue database, as a new request or job would get.

        :param catalogue: Catalogue database the service works in, or None for a new one over the rig's database.
        :type catalogue: CatalogueDatabase | None
        :param parallel_scans: Largest number of scans cut at once.
        :type parallel_scans: int
        :param max_files: Largest number of files in an upload.
        :type max_files: int
        :param max_bytes: Largest total size of an upload in bytes.
        :type max_bytes: int
        :returns: The service, sharing the database, the stores and the event bus of the rig.
        :rtype: ImportService
        """
        catalogue = catalogue or self.open_catalogue()
        parts = ProcessingParts.build(
            FakeCatalogue(self.processors),
            self.defaults,
            ProcessingConfig(
                preview_retention=timedelta(hours=PREVIEW_RETENTION_HOURS),
                preview_long_side_px=PREVIEW_LONG_SIDE_PX,
            ),
        )
        return ImportService(
            books=make_books_service(catalogue, databases=self.databases, files=self.files),
            imaging=ImportImaging(
                inspector=self.inspector, rasterizer=self.rasterizer, tiler=self.tiler, runner=self.runner()
            ),
            runtime=ImportRuntime(
                order_keys=FractionalOrderKeys(),
                limits=ImportLimits(max_files=max_files, max_bytes=max_bytes, parallel_scans=parallel_scans),
            ),
            parts=parts,
        )

    def stored_job(self, job: Job) -> Job:
        """Read a job back as the board holds it.

        :param job: Job to read, identified by its identifier.
        :type job: Job
        :returns: The job as the board holds it now.
        :rtype: Job
        """
        return self.fakes.board.get(job.id)
