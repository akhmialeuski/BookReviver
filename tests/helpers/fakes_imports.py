"""Fakes of the import feature: the real imaging adapters watched and steered by a test, and the rig around them.

The import service runs on the application's own adapters, the local stores over a temporary directory and the real
reader and tiler over small generated files, so a test proves the whole path from an upload to the files of a page.
What a test needs beyond that is a way to look at the adapters and to steer them: how many scans are cut at once,
which resource id a pyramid was cut for, and a failure, a cancellation or a crash at a chosen step. Each adapter here
wraps the real one and adds only that. ``WorkerCrashError`` is a ``BaseException``, so no handler of the service
catches it and the job stays running, as it does when a worker process dies.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, override

import anyio
from attrs import field, frozen

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.imaging import (
    DjvuFormat,
    DjvuLibreTools,
    ImageFormat,
    PdfFormat,
    SourceReader,
    VipsRenditionWriter,
    VipsTiler,
)
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryUnitOfWork
from bookreviver.adapters.storage import LocalAssetStore, LocalSourceStore
from bookreviver.app.settings import ImagingSettings
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
from bookreviver.services.imports import ImportImaging, ImportLimits, ImportRuntime, ImportService, ImportStorage
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts, ProcessingRuntime
from bookreviver.services.steps import StepRunner
from tests.adapters.imaging.samples import PdfPage, ScanImage, write_image, write_pdf
from tests.helpers.builders import EPOCH
from tests.helpers.fake_processing import FakeCatalogue
from tests.helpers.fakes_jobs import JobFakes, RecordingEventBus
from tests.helpers.processing import DEFAULTS, PREVIEW_LONG_SIDE_PX, PREVIEW_RETENTION_HOURS
from tests.helpers.storage import upload

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from datetime import datetime
    from pathlib import Path

    from fastapi import UploadFile

    from bookreviver.adapters.persistence.memory import InMemoryDatabase
    from bookreviver.domain.entities import Job
    from bookreviver.domain.enums import Rendition, SourceKind
    from bookreviver.domain.events import DomainEvent
    from bookreviver.domain.ids import JobId, ProjectId, SourceId
    from bookreviver.domain.values import SourceAnalysis, UploadedSource
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import Processor
    from bookreviver.ports.storage import SourceStore
    from bookreviver.services.recipes import DefaultRecipes

TICK: timedelta = timedelta(seconds=1)
PAGE_WIDTH_PX: int = 200
PAGE_HEIGHT_PX: int = 300
GRAY_MODE: str = 'L'
JPEG_FORMAT: str = 'JPEG'
IIIF_ROOT: str = '/api/v1/iiif'
MAX_FILES: int = 100
MAX_BYTES: int = 10 * 1024 * 1024
DEFAULT_PARALLEL_SCANS: int = 2
# Long enough for two scans cut at once to overlap, short enough to keep a test fast
OVERLAP_SECONDS: float = 0.05


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


class TickingClock(FixedClock):
    """A clock that moves a second forward every time it is read, so what an import creates has distinct times.

    Sources are listed in the order of their import times, and a stopped clock would leave the order to their random
    identifiers.
    """

    @override
    def now(self) -> datetime:
        """Return the current time of the clock, then move it a second forward.

        :returns: The moment the clock showed before it moved.
        :rtype: datetime
        """
        moment = self.moment
        self.moment += TICK
        return moment


class HookedEventBus(RecordingEventBus):
    """The recording event bus, with a hook run after each event is delivered.

    :ivar after_publish: Coroutine function run with each event once it was delivered, or None.
    """

    def __init__(self) -> None:
        """Start with no hook."""
        super().__init__()
        self.after_publish: Callable[[DomainEvent], Awaitable[None]] | None = None

    @override
    async def publish(self, event: DomainEvent) -> None:
        """Deliver the event as the recording bus does, then run the hook.

        :param event: Event to deliver, carrying the project it belongs to.
        :type event: DomainEvent
        """
        await super().publish(event)
        if self.after_publish is not None:
            await self.after_publish(event)


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
    """The real tiler, remembering the resource ids it cut pyramids for and crashing on demand.

    :ivar resource_ids: Resource id of every pyramid cut, in order.
    :ivar crash_on_preview: Number of the preview call that raises ``WorkerCrashError``, counting from 1, or None.
    """

    def __init__(self, inner: Tiler) -> None:
        """Wrap the real tiler.

        :param inner: Tiler that does the work.
        :type inner: Tiler
        """
        self._inner = inner
        self.resource_ids: list[str] = []
        self.crash_on_preview: int | None = None
        self._previews = 0

    @override
    async def tile(self, image: Path, target_dir: Path, *, resource_id: str) -> None:
        """Cut the pyramid with the real tiler and remember its resource id.

        :param image: Image to cut.
        :type image: Path
        :param target_dir: Directory to create for the pyramid.
        :type target_dir: Path
        :param resource_id: Path the pyramid is served from.
        :type resource_id: str
        """
        self.resource_ids.append(resource_id)
        await self._inner.tile(image, target_dir, resource_id=resource_id)

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


class CrashingSourceStore(LocalSourceStore):
    """The local source store, whose next promotion can crash the worker or fail with an error.

    :ivar crash_on_promote: Whether the next ``promote`` raises ``WorkerCrashError``, once.
    :ivar fail_on_promote: Error the next ``promote`` raises, once, leaving the files staged as a refused one does.
    """

    crash_on_promote: bool = False
    fail_on_promote: Exception | None = None

    @override
    async def promote(self, project_id: ProjectId, job_id: JobId, source_id: SourceId, *, names: Sequence[str]) -> None:
        """Promote the files, unless the test made this promotion crash.

        :param project_id: Project owning the upload and the source.
        :type project_id: ProjectId
        :param job_id: Import job whose upload holds the files.
        :type job_id: JobId
        :param source_id: Source the files become.
        :type source_id: SourceId
        :param names: Names of the staged files that make the source.
        :type names: Sequence[str]
        :raises WorkerCrashError: If a crash was asked for, which is then forgotten.
        :raises Exception: The error asked for, which is then forgotten.
        """
        if self.crash_on_promote:
            self.crash_on_promote = False
            raise WorkerCrashError
        if (failure := self.fail_on_promote) is not None:
            self.fail_on_promote = None
            raise failure
        await super().promote(project_id, job_id, source_id, names=names)


@frozen(kw_only=True)
class ImportRig:
    """Everything an import test runs on, built over one temporary storage root.

    :ivar fakes: The database, the recording event bus and the clock, which the job tests share.
    :ivar events: The event bus of ``fakes``, seen as the hooked bus it is.
    :ivar queue: Queue recording the jobs enqueued instead of running them.
    :ivar sources: Local source store, whose promotion can crash.
    :ivar assets: Local asset store.
    :ivar inspector: Real inspector, watched.
    :ivar rasterizer: Real rasterizer, watched.
    :ivar tiler: Real tiler, watched.
    :ivar defaults: The recipes a stage starts with, which by default leave the page split to ``split.none``.
    :ivar processors: The processors of the catalogue the recipes are checked against.
    """

    fakes: JobFakes
    events: HookedEventBus
    queue: RecordingJobQueue
    sources: CrashingSourceStore
    assets: LocalAssetStore
    inspector: WatchedInspector
    rasterizer: WatchedRasterizer
    tiler: WatchedTiler
    defaults: DefaultRecipes = DEFAULTS
    processors: Sequence[Processor] = field(factory=lambda: [SplitNone()])

    @classmethod
    def build(cls, root: Path) -> ImportRig:
        """Build the rig over the adapters of the application, with the settings' defaults.

        :param root: Storage root of both stores.
        :type root: Path
        :returns: The rig with an empty database and nothing stored.
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
        events = HookedEventBus()
        return cls(
            fakes=JobFakes(events=events, clock=TickingClock(EPOCH)),
            events=events,
            queue=RecordingJobQueue(),
            sources=CrashingSourceStore(root=root),
            assets=LocalAssetStore(root=root),
            inspector=WatchedInspector(reader),
            rasterizer=WatchedRasterizer(reader),
            tiler=WatchedTiler(tiler),
        )

    @property
    def database(self) -> InMemoryDatabase:
        """The database every unit of work of the rig opens over."""
        return self.fakes.database

    def runner(self) -> StepRunner:
        """Build the runner of ``split.none``, which makes the base version of a page, over the rig's assets and tiler.

        :returns: The runner, with the application's own writer of renditions.
        :rtype: StepRunner
        """
        return StepRunner(
            assets=self.assets,
            catalogue=FakeCatalogue([SplitNone()]),
            renditions=VipsRenditionWriter(
                preview_long_side_px=ImagingSettings().preview_long_side_px,
                thumbnail_long_side_px=ImagingSettings().thumbnail_long_side_px,
                jpeg_quality=ImagingSettings().jpeg_quality,
            ),
            tiler=self.tiler,
            iiif_root=IIIF_ROOT,
        )

    def open_uow(self) -> UnitOfWork:
        """Open a unit of work, as a new request or job would.

        :returns: A unit of work over the rig's database.
        :rtype: UnitOfWork
        """
        return InMemoryUnitOfWork(self.database)

    def service(
        self,
        *,
        uow: UnitOfWork | None = None,
        sources: SourceStore | None = None,
        parallel_scans: int = DEFAULT_PARALLEL_SCANS,
        max_files: int = MAX_FILES,
        max_bytes: int = MAX_BYTES,
    ) -> ImportService:
        """Build an import service over a new unit of work, as a new request or job would get.

        :param uow: Unit of work the service works in, or None for a new in-memory one over the rig's database.
        :type uow: UnitOfWork | None
        :param sources: Source store the service works with, or None for the rig's own.
        :type sources: SourceStore | None
        :param parallel_scans: Largest number of scans cut at once.
        :type parallel_scans: int
        :param max_files: Largest number of files in an upload.
        :type max_files: int
        :param max_bytes: Largest total size of an upload in bytes.
        :type max_bytes: int
        :returns: The service, sharing the database, the stores and the event bus of the rig.
        :rtype: ImportService
        """
        uow = uow or self.open_uow()
        parts = ProcessingParts.build(
            uow,
            FakeCatalogue(self.processors),
            self.defaults,
            ProcessingRuntime(publisher=self.fakes.events, clock=self.fakes.clock, queue=self.queue),
            ProcessingConfig(
                preview_retention=timedelta(hours=PREVIEW_RETENTION_HOURS),
                preview_long_side_px=PREVIEW_LONG_SIDE_PX,
            ),
        )
        return ImportService(
            uow=uow,
            storage=ImportStorage(sources=sources or self.sources, assets=self.assets),
            imaging=ImportImaging(
                inspector=self.inspector, rasterizer=self.rasterizer, tiler=self.tiler, runner=self.runner()
            ),
            runtime=ImportRuntime(
                publisher=self.fakes.events,
                clock=self.fakes.clock,
                order_keys=FractionalOrderKeys(),
                limits=ImportLimits(
                    max_files=max_files, max_bytes=max_bytes, parallel_scans=parallel_scans, iiif_root=IIIF_ROOT
                ),
                queue=self.queue,
            ),
            parts=parts,
        )

    async def stored_job(self, job: Job) -> Job:
        """Read a job back as committed.

        :param job: Job to read, identified by its identifier.
        :type job: Job
        :returns: The job as the database holds it now.
        :rtype: Job
        """
        return await self.fakes.stored_job(job)
