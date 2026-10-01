"""Building the processing services of a test over in-memory persistence, the local asset store and fake processors.

``ProcessingKit`` holds what the services of one test share, and builds a service, the jobs of the workers or the edit
service over a new unit of work, as a new request or job would get one. The processors are fakes that copy their input,
and the writer of renditions copies the image it is given and writes token files, so no image library is needed to
tell which files a version has and in which format its ``full`` was asked for.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, override

import anyio
from attrs import evolve
from dishka import Provider, Scope, provide

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.api.routing import IIIF_ROOT
from bookreviver.domain.entities import Actor, PageStage
from bookreviver.domain.enums import (
    ImagePolicy,
    Rendition,
    Stage,
    StageState,
    VersionState,
)
from bookreviver.domain.errors import NotFoundError
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import RenditionInfo, Renditions
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.imaging import RenditionWriter
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.ports.runtime import JobQueue
from bookreviver.services.edits import EditService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts, ProcessingRuntime
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from bookreviver.services.stage_runs import StageRuntime
from bookreviver.services.steps import StepRunner
from tests.helpers.builders import (
    EPOCH,
    SPLIT_NONE,
    make_page,
    make_page_version,
    make_project,
    make_scan,
    make_source,
    new_account_id,
)
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.page_services import FakeTiler
from tests.helpers.processors import CleanupProcessor, FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from bookreviver.domain.entities import Job, Page, PageVersion, Project, Scan
    from bookreviver.domain.enums import (
        ColorMode,
    )
    from bookreviver.domain.ids import PageVersionId
    from bookreviver.domain.values import MetadataMap, ProcessorSpec
    from bookreviver.ports.processing import Processor
    from bookreviver.ports.storage import AssetStore

# What the fake writer says every image measures
IMAGE_SIZE_PX: int = 100
IMAGE_CONTENT: bytes = b'image'
PREVIEW_TOKEN: bytes = b'preview'
THUMBNAIL_TOKEN: bytes = b'thumbnail'
RETENTION_DAYS: int = 30
PREVIEW_RETENTION_HOURS: int = 24
PREVIEW_LONG_SIDE_PX: int = 2048
DEFAULTS: DefaultRecipes = DefaultRecipes(
    {
        Stage.PAGE_SPLIT: (RecipeTemplate(name='Whole scan', processor_keys=(SPLIT_NONE.key,)),),
        Stage.GEOMETRY: (RecipeTemplate(name='Fake', processor_keys=(FakeProcessor.spec.key,)),),
        Stage.CLEANUP: (RecipeTemplate(name='Cleanup', processor_keys=(CleanupProcessor.spec.key,)),),
    }
)


class RefusingJobQueue(JobQueue):
    """A queue that refuses every job, as a broker that is down does."""

    @override
    async def enqueue(self, job: Job) -> None:
        """Refuse the job.

        :param job: The job handed to the queue.
        :type job: Job
        :raises RuntimeError: Always.
        """
        err_msg = f'The queue is down, so it cannot take {job.id}.'
        raise RuntimeError(err_msg)


class FakeCatalogue(ProcessorCatalog):
    """A catalogue of the processors it was given."""

    def __init__(self, processors: Sequence[Processor]) -> None:
        """Offer the given processors.

        :param processors: The processors, found by the keys of their specs.
        :type processors: Sequence[Processor]
        """
        self._processors = {processor.spec.key: processor for processor in processors}

    @override
    def get(self, key: str) -> Processor:
        """Return the processor with this key.

        :param key: Key of the processor.
        :type key: str
        :returns: The processor.
        :rtype: Processor
        :raises NotFoundError: If the catalogue has none with this key.
        """
        if (processor := self._processors.get(key)) is None:
            raise NotFoundError(key)
        return processor

    @override
    def specs(self) -> Sequence[ProcessorSpec]:
        """List the specs by key.

        :returns: The specs of the processors.
        :rtype: Sequence[ProcessorSpec]
        """
        return [self._processors[key].spec for key in sorted(self._processors)]


class FakeRenditionWriter(RenditionWriter):
    """A writer that copies the image as ``full`` and writes token files for the other two.

    :ivar calls: The format and the colour of every ``full`` it was asked to write, in order.
    """

    def __init__(self) -> None:
        """Start with nothing written."""
        self.calls: list[tuple[Rendition, ColorMode]] = []

    @override
    async def write(self, image: Path, target_dir: Path, *, full: Rendition, color_mode: ColorMode) -> RenditionInfo:
        """Write the three files of an image.

        :param image: Image to copy.
        :type image: Path
        :param target_dir: Directory to create.
        :type target_dir: Path
        :param full: Format of the ``full`` image.
        :type full: Rendition
        :param color_mode: Colour of the image.
        :type color_mode: ColorMode
        :returns: A fixed size and the format asked for.
        :rtype: RenditionInfo
        """
        self.calls.append((full, color_mode))
        directory = anyio.Path(target_dir)
        await directory.mkdir(parents=True)
        await (directory / full).write_bytes(await anyio.Path(image).read_bytes())
        await (directory / Rendition.PREVIEW).write_bytes(PREVIEW_TOKEN)
        await (directory / Rendition.THUMBNAIL).write_bytes(THUMBNAIL_TOKEN)
        return RenditionInfo(width_px=IMAGE_SIZE_PX, height_px=IMAGE_SIZE_PX, full=full)


class ProcessingKit:
    """What the processing services of one test share, and a builder of the services over a new unit of work.

    :ivar database: In-memory database every unit of work opens over.
    :ivar assets: Asset store over a temporary directory.
    :ivar events: Event bus recording what is published.
    :ivar clock: Clock stopped at the epoch, which a test moves.
    :ivar recording: Queue that records the jobs it is handed.
    :ivar queue: Queue the jobs are handed to, which is the recording one unless a test gave another.
    :ivar fake: The fake geometry processor, which counts its runs and previews.
    :ivar cleanup: The fake cleanup processor.
    :ivar writer: The fake writer of renditions.
    :ivar tiler: The fake tiler.
    """

    def __init__(
        self, assets: AssetStore, *, processors: Sequence[Processor] | None = None, queue: JobQueue | None = None
    ) -> None:
        """Build the kit over an asset store.

        :param assets: Asset store of the test.
        :type assets: AssetStore
        :param processors: The processors of the catalogue, or None for the real ``split.none`` and the two fakes.
        :type processors: Sequence[Processor] | None
        :param queue: The queue the jobs are handed to, or None for one that records them.
        :type queue: JobQueue | None
        """
        self.database = InMemoryDatabase()
        self.assets = assets
        self.events = RecordingEventBus()
        self.clock = FixedClock(EPOCH)
        self.recording = RecordingJobQueue()
        self.queue = queue or self.recording
        self.fake = FakeProcessor()
        self.cleanup = CleanupProcessor()
        self.writer = FakeRenditionWriter()
        self.tiler = FakeTiler()
        self.catalogue = FakeCatalogue(processors or [SplitNone(), self.fake, self.cleanup])
        self._runner = StepRunner(
            assets=assets, catalogue=self.catalogue, renditions=self.writer, tiler=self.tiler, iiif_root=IIIF_ROOT
        )

    def parts(self, uow: InMemoryUnitOfWork) -> ProcessingParts:
        """Build the shared parts over a unit of work.

        :param uow: Unit of work of the request or job.
        :type uow: InMemoryUnitOfWork
        :returns: The parts.
        :rtype: ProcessingParts
        """
        runtime = ProcessingRuntime(publisher=self.events, clock=self.clock, queue=self.queue)
        config = ProcessingConfig(
            version_retention=timedelta(days=RETENTION_DAYS),
            preview_retention=timedelta(hours=PREVIEW_RETENTION_HOURS),
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
        )
        return ProcessingParts.build(uow, self.catalogue, DEFAULTS, runtime, config)

    def service(self) -> ProcessingService:
        """Build the processing service over a new unit of work.

        :returns: The service.
        :rtype: ProcessingService
        """
        uow = InMemoryUnitOfWork(self.database)
        return ProcessingService(uow=uow, catalogue=self.catalogue, parts=self.parts(uow))

    def jobs(self) -> ProcessingJobs:
        """Build the work of the processing jobs over a new unit of work.

        :returns: The jobs.
        :rtype: ProcessingJobs
        """
        uow = InMemoryUnitOfWork(self.database)
        runtime = StageRuntime(
            runner=self._runner,
            catalogue=self.catalogue,
            publisher=self.events,
            clock=self.clock,
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
        )
        return ProcessingJobs(uow=uow, assets=self.assets, runtime=runtime, parts=self.parts(uow))

    def edits(self) -> EditService:
        """Build the edit service over a new unit of work.

        :returns: The service.
        :rtype: EditService
        """
        uow = InMemoryUnitOfWork(self.database)
        return EditService(
            uow=uow, assets=self.assets, catalogue=self.catalogue, records=self.parts(uow).records, clock=self.clock
        )

    def uow(self) -> InMemoryUnitOfWork:
        """Open a unit of work to read what was committed.

        :returns: A new unit of work.
        :rtype: InMemoryUnitOfWork
        """
        return InMemoryUnitOfWork(self.database)

    async def seed_project(self, *, image_policy: ImagePolicy = ImagePolicy.COMPACT) -> tuple[Actor, Project]:
        """Commit a project of a new account.

        :param image_policy: Image policy of the project.
        :type image_policy: ImagePolicy
        :returns: The actor owning the project, and the project.
        :rtype: tuple[Actor, Project]
        """
        owner = new_account_id()
        project = evolve(make_project(owner_id=owner), image_policy=image_policy)
        uow = self.uow()
        await uow.projects.add(project)
        await uow.commit()
        return Actor(account_id=owner), project

    async def seed_scan_page(self, project: Project, *, order_key: str = 'a0') -> tuple[Page, Scan]:
        """Commit a scan whose images are stored and a page that shows it whole.

        :param project: Project owning the page.
        :type project: Project
        :param order_key: Order key of the page.
        :type order_key: str
        :returns: The page and its scan.
        :rtype: tuple[Page, Scan]
        """
        source = make_source(project_id=project.id, name=f'{order_key}.pdf')
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))
        page = make_page(project_id=project.id, order_key=order_key, scan=scan)
        uow = self.uow()
        await uow.sources.add(source)
        await uow.scans.add(scan)
        await uow.pages.add(page)
        await uow.commit()
        keys = ProjectKeys(project.id)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW):
            async with self.assets.writable(keys.scan_rendition(scan, rendition)) as target:
                target.write_bytes(IMAGE_CONTENT)
        return page, scan

    async def seed_base_version(self, page: Page) -> PageVersion:
        """Commit the ready base version of a page, with its files and its stage record.

        :param page: Page that has a scan.
        :type page: Page
        :returns: The base version.
        :rtype: PageVersion
        """
        version = evolve(
            make_page_version(page_id=page.id),
            renditions=Renditions(ready=True),
            state=VersionState.READY,
            tiles_ready=True,
            data={'width_px': IMAGE_SIZE_PX, 'height_px': IMAGE_SIZE_PX},
        )
        keys = ProjectKeys(page.project_id)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL):
            async with self.assets.writable(keys.version_rendition(version, rendition)) as target:
                target.write_bytes(IMAGE_CONTENT)
        uow = self.uow()
        await uow.page_versions.add(version)
        await uow.page_stages.save(
            PageStage(
                page_id=page.id,
                stage=Stage.PAGE_SPLIT,
                head_version_id=version.id,
                state=StageState.FRESH,
                updated_at=EPOCH,
            )
        )
        await uow.commit()
        return version

    async def store_files(self, version: PageVersion) -> None:
        """Write the three files of a version of a project's page, as a run would.

        :param version: Version whose files are written.
        :type version: PageVersion
        """
        project_id = (await self.uow().pages.get(version.page_id)).project_id
        keys = ProjectKeys(project_id)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL):
            async with self.assets.writable(keys.version_rendition(version, rendition)) as target:
                target.write_bytes(IMAGE_CONTENT)

    async def stored_version(self, version_id: PageVersionId) -> PageVersion:
        """Read a version back as committed.

        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version as the database holds it now.
        :rtype: PageVersion
        """
        return await self.uow().page_versions.get(version_id)

    @staticmethod
    def params(**values: object) -> MetadataMap:
        """Build the parameters of a fake step.

        :param values: The parameters by name.
        :type values: object
        :returns: The parameters.
        :rtype: MetadataMap
        """
        return dict(values)


class ProcessingFakesProvider(Provider):
    """Replaces the application's catalogue of processors and its default recipes with the fakes of the tests.

    The imaging adapters stay the application's own, so a run of the fake processors writes real files with libvips.
    """

    scope = Scope.APP

    @provide(override=True)
    def processor_catalog(self) -> ProcessorCatalog:
        """Offer the real ``split.none`` and the two fake processors.

        :returns: The catalogue of the tests.
        :rtype: ProcessorCatalog
        """
        return FakeCatalogue([SplitNone(), FakeProcessor(), CleanupProcessor()])

    @provide(override=True)
    def default_recipes(self) -> DefaultRecipes:
        """Give the recipes the tests' stages start with.

        :returns: The default recipes of the tests.
        :rtype: DefaultRecipes
        """
        return DEFAULTS
