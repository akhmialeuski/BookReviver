"""Fakes of the storage, imaging and event ports the import feature drives, and a provider injecting them."""

import math
import shutil
from collections import defaultdict
from contextlib import asynccontextmanager
from functools import partial
from operator import attrgetter
from pathlib import Path
from typing import TYPE_CHECKING, override

import anyio
import anyio.lowlevel
from attrs import define, field, frozen
from dishka import AnyOf, Provider, Scope, provide

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.domain.enums import ColorMode, UploadProblem
from bookreviver.domain.errors import NotFoundError, UploadRejectedError
from bookreviver.domain.events import DomainEvent
from bookreviver.domain.values import MetadataSuggestion, PageFacts, SourceAnalysis
from bookreviver.ports.imaging import PageRasterizer, SourceInspector, Tiler
from bookreviver.ports.runtime import EventPublisher, EventStream
from bookreviver.ports.storage import AssetStore, IncomingFile, SourceStore
from bookreviver.services.imports import ImportImaging, ImportLimits, ImportRuntime, ImportService, ImportStorage
from bookreviver.services.jobs import JobService
from tests.helpers.builders import EPOCH, PAGE_HEIGHT_PX, PAGE_WIDTH_PX

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence

    from anyio.streams.memory import MemoryObjectSendStream

    from bookreviver.domain.entities import Job, Project
    from bookreviver.domain.enums import SourceKind
    from bookreviver.domain.ids import ProjectId, StorageKey

type PageHook = Callable[[int], Awaitable[None]]

READ_CHUNK_BYTES: int = 4
DEFAULT_PAGE_COUNT: int = 3
DEFAULT_LIMITS: ImportLimits = ImportLimits(max_upload_bytes=1_024, parallel_pages=2)
FAKE_JPEG: bytes = b'\xff\xd8 fake jpeg \xff\xd9'
INCOMING_DIR: str = 'incoming'
SOURCE_DIR: str = 'source'
TILE_INFO: str = 'info.json'


@define
class FakeUpload(IncomingFile):
    """An uploaded file read in small chunks, as a streamed upload would be."""

    filename: str | None
    content: bytes = b'%PDF-1.7 fake'
    _offset: int = 0

    @override
    async def read(self, size: int = -1) -> bytes:
        end = len(self.content) if size < 0 else min(len(self.content), self._offset + size)
        chunk = self.content[self._offset : end]
        self._offset = end
        await anyio.lowlevel.checkpoint()
        return chunk


class FakeSourceStore(SourceStore):
    """Sources in plain directories under ``root``, so the service can read real file sizes."""

    def __init__(self, root: Path) -> None:
        self._root = anyio.Path(root)
        self.discarded: list[ProjectId] = []

    @override
    async def stage(self, project_id: ProjectId, files: Sequence[IncomingFile], *, max_bytes: int) -> int:
        incoming = self._dir(project_id, INCOMING_DIR)
        await self._remove(incoming)
        await incoming.mkdir(parents=True)
        total = 0
        for file in files:
            target = incoming / (file.filename or '')
            while chunk := await file.read(READ_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    await self._remove(incoming)
                    raise UploadRejectedError(UploadProblem.TOO_LARGE)
                async with await target.open('ab') as stream:
                    await stream.write(chunk)
        return total

    @override
    async def promote(self, project_id: ProjectId) -> None:
        source = self._dir(project_id, SOURCE_DIR)
        await self._remove(source)
        await self._dir(project_id, INCOMING_DIR).rename(source)

    @override
    async def discard(self, project_id: ProjectId) -> None:
        self.discarded.append(project_id)
        await self._remove(self._dir(project_id, INCOMING_DIR))

    @override
    @asynccontextmanager
    async def staged_files(self, project_id: ProjectId) -> AsyncIterator[Sequence[Path]]:
        yield await self.names_in(project_id, INCOMING_DIR)

    @override
    @asynccontextmanager
    async def source_files(self, project_id: ProjectId) -> AsyncIterator[Sequence[Path]]:
        yield await self.names_in(project_id, SOURCE_DIR)

    @override
    async def delete_project(self, project_id: ProjectId) -> None:
        await self._remove(self._root / str(project_id))

    async def names_in(self, project_id: ProjectId, kind: str) -> list[Path]:
        """Return the files of the staged upload (``INCOMING_DIR``) or of the source (``SOURCE_DIR``) by name."""
        directory = self._dir(project_id, kind)
        if not await directory.exists():
            return []
        return sorted([Path(entry) async for entry in directory.iterdir()], key=attrgetter('name'))

    def _dir(self, project_id: ProjectId, kind: str) -> anyio.Path:
        """Return the directory holding one kind of files of a project."""
        return self._root / str(project_id) / kind

    @staticmethod
    async def _remove(directory: anyio.Path) -> None:
        """Remove a directory tree if it exists."""
        await anyio.to_thread.run_sync(partial(shutil.rmtree, directory, ignore_errors=True))


class FakeAssetStore(AssetStore):
    """Derived files under ``root``, recording the key of everything written."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self.published: list[StorageKey] = []

    @override
    @asynccontextmanager
    async def writable(self, key: StorageKey) -> AsyncIterator[Path]:
        path = self._root / key
        await anyio.Path(path.parent).mkdir(parents=True, exist_ok=True)
        try:
            yield path
        finally:
            if await anyio.Path(path).exists():
                self.published.append(key)

    @override
    @asynccontextmanager
    async def readable(self, key: StorageKey) -> AsyncIterator[Path]:
        if key not in self.published:
            raise NotFoundError(key)
        yield self._root / key

    @override
    async def delete_prefix(self, prefix: StorageKey) -> None:
        self.published = [key for key in self.published if not key.startswith(prefix)]


class FakeJobQueue(RecordingJobQueue):
    """Records enqueued jobs, or fails with ``error`` as an unreachable broker would."""

    def __init__(self) -> None:
        super().__init__()
        self.error: Exception | None = None

    @override
    async def enqueue(self, job: Job) -> None:
        if self.error is not None:
            raise self.error
        await super().enqueue(job)


@define(kw_only=True)
class FakeSourceInspector(SourceInspector):
    """Describes any source as ``page_count`` grayscale pages, or fails with ``error``."""

    page_count: int = DEFAULT_PAGE_COUNT
    suggestion: MetadataSuggestion = field(factory=MetadataSuggestion)
    error: Exception | None = None

    @override
    async def inspect(self, kind: SourceKind, files: Sequence[Path]) -> SourceAnalysis:
        await anyio.lowlevel.checkpoint()
        if self.error is not None:
            raise self.error
        facts = PageFacts(width_px=PAGE_WIDTH_PX, height_px=PAGE_HEIGHT_PX, color_mode=ColorMode.GRAY)
        return SourceAnalysis(kind=kind, pages=[facts] * self.page_count, suggestion=self.suggestion)


@define(kw_only=True)
class FakePageRasterizer(PageRasterizer):
    """Writes a tiny JPEG per page, counting how many pages it works on at once.

    ``before_extract`` runs first for every page, so a test can act while pages are in flight. With
    ``hold_until_active`` set, no page finishes before that many pages are in flight together, which makes the
    concurrency deterministic. ``error`` is raised for the page at ``failing_index``.
    """

    before_extract: PageHook | None = None
    hold_until_active: int = 0
    failing_index: int | None = None
    error: Exception = field(factory=lambda: OSError('disk full'))
    active: int = 0
    max_active: int = 0
    extracted: list[int] = field(factory=list)
    _enough_active: anyio.Event = field(factory=anyio.Event)

    @override
    async def extract(self, kind: SourceKind, files: Sequence[Path], index: int, target: Path) -> None:
        if self.before_extract is not None:
            await self.before_extract(index)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        if self.active >= self.hold_until_active:
            self._enough_active.set()
        try:
            await self._enough_active.wait()
            if index == self.failing_index:
                raise self.error
            await anyio.Path(target).write_bytes(FAKE_JPEG)
            self.extracted.append(index)
        finally:
            self.active -= 1


class FakeTiler(Tiler):
    """Writes a one-file pyramid and a copy of the image as its thumbnail."""

    @override
    async def tile(self, image: Path, target_dir: Path) -> None:
        await anyio.Path(target_dir).mkdir(parents=True, exist_ok=True)
        await anyio.Path(target_dir / TILE_INFO).write_text('{}')

    @override
    async def thumbnail(self, image: Path, target: Path) -> None:
        await anyio.Path(target).write_bytes(await anyio.Path(image).read_bytes())


class FakeEventBus(EventPublisher, EventStream):
    """Records every published event, and subscribes eagerly so a test knows when a listener is in place."""

    def __init__(self) -> None:
        self.published: list[DomainEvent] = []
        self.subscribed = anyio.Event()
        self._subscribers: defaultdict[ProjectId, list[MemoryObjectSendStream[DomainEvent]]] = defaultdict(list)

    @override
    async def publish(self, event: DomainEvent) -> None:
        self.published.append(event)
        for stream in self._subscribers[event.project_id]:
            await stream.send(event)

    @override
    def subscribe(self, project_id: ProjectId) -> AsyncIterator[DomainEvent]:
        sender, receiver = anyio.create_memory_object_stream[DomainEvent](max_buffer_size=math.inf)
        self._subscribers[project_id].append(sender)
        self.subscribed.set()
        return receiver


@frozen(kw_only=True)
class ImportFakes:
    """Every fake the import feature runs against, shared by a test and the services it builds."""

    database: InMemoryDatabase = field(factory=InMemoryDatabase)
    sources: FakeSourceStore
    assets: FakeAssetStore
    inspector: FakeSourceInspector = field(factory=FakeSourceInspector)
    rasterizer: FakePageRasterizer = field(factory=FakePageRasterizer)
    tiler: FakeTiler = field(factory=FakeTiler)
    queue: FakeJobQueue = field(factory=FakeJobQueue)
    events: FakeEventBus = field(factory=FakeEventBus)
    clock: FixedClock = field(factory=lambda: FixedClock(EPOCH))

    @classmethod
    def under(cls, root: Path) -> ImportFakes:
        """Build fakes whose files live under ``root``."""
        return cls(sources=FakeSourceStore(root / 'sources'), assets=FakeAssetStore(root / 'assets'))

    def import_service(self, limits: ImportLimits = DEFAULT_LIMITS) -> ImportService:
        """Build an import service over a new unit of work, as a new request or job would get."""
        return ImportService(
            uow=InMemoryUnitOfWork(self.database),
            storage=ImportStorage(sources=self.sources, assets=self.assets),
            imaging=ImportImaging(inspector=self.inspector, rasterizer=self.rasterizer, tiler=self.tiler),
            runtime=ImportRuntime(queue=self.queue, publisher=self.events, clock=self.clock),
            limits=limits,
        )

    def job_service(self) -> JobService:
        """Build a job service over a new unit of work."""
        return JobService(
            uow=InMemoryUnitOfWork(self.database), publisher=self.events, stream=self.events, clock=self.clock
        )

    async def store(self, project: Project) -> None:
        """Commit a project to the fake database."""
        uow = InMemoryUnitOfWork(self.database)
        await uow.projects.add(project)
        await uow.commit()


class ImportFakesProvider(Provider):
    """Overrides the database, storage, imaging and event adapters of the application with the given fakes."""

    scope = Scope.APP

    def __init__(self, fakes: ImportFakes) -> None:
        super().__init__()
        self._fakes = fakes

    @provide(override=True)
    def database(self) -> InMemoryDatabase:
        """Provide the fakes' in-memory database, so a test can store projects the application sees."""
        return self._fakes.database

    @provide(override=True)
    def source_store(self) -> SourceStore:
        """Provide the fake source store."""
        return self._fakes.sources

    @provide(override=True)
    def asset_store(self) -> AssetStore:
        """Provide the fake asset store."""
        return self._fakes.assets

    @provide(override=True)
    def inspector(self) -> SourceInspector:
        """Provide the fake source inspector."""
        return self._fakes.inspector

    @provide(override=True)
    def rasterizer(self) -> PageRasterizer:
        """Provide the fake page rasterizer."""
        return self._fakes.rasterizer

    @provide(override=True)
    def tiler(self) -> Tiler:
        """Provide the fake tiler."""
        return self._fakes.tiler

    @provide(override=True)
    def event_bus(self) -> AnyOf[EventPublisher, EventStream]:
        """Provide the fake event bus as both publisher and stream."""
        return self._fakes.events
