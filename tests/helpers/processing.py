"""Building the processing services of a test over in-memory persistence, the local asset store and fake processors.

``ProcessingKit`` holds what the services of one test share, and builds a service, the jobs of the workers, the edit
service or the page settings service over a new unit of work, as a new request or job would get one. The processors
are fakes that copy their input, and the writer of renditions copies the image it is given and writes token files, so
no image library is needed to tell which files a version has and in which format its ``full`` was asked for.
"""

from datetime import timedelta
from typing import TYPE_CHECKING

from attrs import evolve
from dishka import Provider, Scope, provide

from bookreviver.adapters.clock.system import FixedClock
from bookreviver.adapters.jobs.recording import RecordingJobQueue
from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.adapters.persistence.memory import InMemoryDatabase, InMemoryUnitOfWork
from bookreviver.api.routing import IIIF_ROOT
from bookreviver.domain.entities import Actor, PageStage
from bookreviver.domain.enums import (
    ImagePolicy,
    JobKind,
    PageKind,
    RecipeKind,
    Rendition,
    Stage,
    StageState,
    VersionState,
)
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageStepKey, RecipeKey, Renditions, SliceRequest
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.processing import ProcessorCatalog
from bookreviver.services.edits import EditService
from bookreviver.services.page_history import PageHistoryService
from bookreviver.services.page_settings import PageSettingsService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts, ProcessingRuntime
from bookreviver.services.recipe_profiles import RecipeProfiles
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from bookreviver.services.stage_runs import StageRuntime
from bookreviver.services.stage_summaries import StageSummaries
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
from tests.helpers.fake_processing import (
    IMAGE_SIZE_PX,
    FakeCatalogue,
    FakeRenditionWriter,
)
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.page_services import FakeTiler
from tests.helpers.processors import CleanupProcessor, FakeProcessor

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Page, PageVersion, Project, Recipe, Scan
    from bookreviver.domain.ids import PageVersionId
    from bookreviver.domain.values import MetadataMap, RecipeDraft
    from bookreviver.ports.processing import Processor
    from bookreviver.ports.runtime import JobQueue
    from bookreviver.ports.storage import AssetStore

IMAGE_CONTENT: bytes = b'image'
PREVIEW_RETENTION_HOURS: int = 24
PREVIEW_LONG_SIDE_PX: int = 2048
DEFAULTS: DefaultRecipes = DefaultRecipes(
    {
        Stage.PAGE_SPLIT: dict.fromkeys(RecipeKind, RecipeTemplate(processor_keys=(SPLIT_NONE.key,))),
        Stage.GEOMETRY: dict.fromkeys(RecipeKind, RecipeTemplate(processor_keys=(FakeProcessor.spec.key,))),
        Stage.CLEANUP: dict.fromkeys(RecipeKind, RecipeTemplate(processor_keys=(CleanupProcessor.spec.key,))),
    }
)

# The recipes of a kit with the OpenCV plugins, which are the ones the application starts with
CV_DEFAULTS: DefaultRecipes = DefaultRecipes()


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
    :ivar catalogue: The catalogue of the processors the services can run.
    :ivar writer: The fake writer of renditions.
    :ivar tiler: The fake tiler.
    """

    def __init__(
        self,
        assets: AssetStore,
        *,
        processors: Sequence[Processor] | None = None,
        queue: JobQueue | None = None,
        defaults: DefaultRecipes = DEFAULTS,
    ) -> None:
        """Build the kit over an asset store.

        :param assets: Asset store of the test.
        :type assets: AssetStore
        :param processors: The processors of the catalogue, or None for the real ``split.none`` and the two fakes.
        :type processors: Sequence[Processor] | None
        :param queue: The queue the jobs are handed to, or None for one that records them.
        :type queue: JobQueue | None
        :param defaults: The recipes the stages start with.
        :type defaults: DefaultRecipes
        """
        self._defaults = defaults
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
            preview_retention=timedelta(hours=PREVIEW_RETENTION_HOURS),
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
        )
        return ProcessingParts.build(uow, self.catalogue, self._defaults, runtime, config)

    def service(self) -> ProcessingService:
        """Build the processing service over a new unit of work.

        :returns: The service.
        :rtype: ProcessingService
        """
        uow = InMemoryUnitOfWork(self.database)
        return ProcessingService(uow=uow, assets=self.assets, catalogue=self.catalogue, parts=self.parts(uow))

    def profiles(self) -> RecipeProfiles:
        """Build the service of the recipe profiles over a new unit of work.

        :returns: The service, which applies a profile through a processing service of the same unit of work.
        :rtype: RecipeProfiles
        """
        uow = InMemoryUnitOfWork(self.database)
        parts = self.parts(uow)
        processing = ProcessingService(uow=uow, assets=self.assets, catalogue=self.catalogue, parts=parts)
        return RecipeProfiles(uow=uow, recipes=parts.recipes, processing=processing, clock=self.clock)

    async def recipe_of(
        self, actor: Actor, project: Project, stage: Stage, kind: RecipeKind = RecipeKind.TEXT
    ) -> Recipe:
        """Read the recipe of one kind of a stage of a project, which the first read creates with the others.

        :param actor: Account owning the project.
        :type actor: Actor
        :param project: Project whose recipe is read.
        :type project: Project
        :param stage: The stage.
        :type stage: Stage
        :param kind: The kind of page, text unless given.
        :type kind: RecipeKind
        :returns: The recipe.
        :rtype: Recipe
        """
        listed = await self.service().recipes(actor, project.id, stage, SliceRequest(limit=len(RecipeKind)))
        return next(recipe for recipe in listed.items if recipe.kind is kind)

    async def edit_recipe(
        self, actor: Actor, project: Project, stage: Stage, draft: RecipeDraft, kind: RecipeKind = RecipeKind.TEXT
    ) -> Recipe:
        """Replace the steps of the recipe of one kind of a stage, which the first read of the stage creates.

        :param actor: Account owning the project.
        :type actor: Actor
        :param project: Project whose recipe is changed.
        :type project: Project
        :param stage: The stage.
        :type stage: Stage
        :param draft: The new steps.
        :type draft: RecipeDraft
        :param kind: The kind of page, text unless given.
        :type kind: RecipeKind
        :returns: The recipe as stored.
        :rtype: Recipe
        """
        recipe = await self.recipe_of(actor, project, stage, kind)
        return await self.service().save_recipe(actor, project.id, RecipeKey(stage, recipe.id), draft)

    def stages(self) -> StageSummaries:
        """Build the sums of the stages of books over a new unit of work.

        :returns: The stage summaries, which offer the processors of the kit.
        :rtype: StageSummaries
        """
        return StageSummaries(uow=InMemoryUnitOfWork(self.database), catalogue=self.catalogue)

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
            order_keys=FractionalOrderKeys(),
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
        )
        return ProcessingJobs(uow=uow, assets=self.assets, runtime=runtime, parts=self.parts(uow))

    async def work_queue(self) -> None:
        """Let a worker take every job the recording queue holds that has not finished, in the order they were queued.

        A project processes one thing at a time, so a test that starts a second job first lets the worker finish the
        collection that a run queues when it ends.
        """
        methods = {
            JobKind.RUN_STAGE: 'run_stage',
            JobKind.PREVIEW_STEP: 'preview_step',
            JobKind.CUT_TILES: 'cut_tiles',
            JobKind.COLLECT_VERSIONS: 'collect_versions',
            JobKind.MEASURE_BOOK: 'measure_book',
            JobKind.DETECT_CONTENT: 'detect_content',
        }
        for queued in list(self.recording.enqueued):
            if not (await self.uow().jobs.get(queued.id)).state.is_final:
                await getattr(self.jobs(), methods[queued.kind])(queued.id)

    def edits(self) -> EditService:
        """Build the edit service over a new unit of work.

        :returns: The service.
        :rtype: EditService
        """
        uow = InMemoryUnitOfWork(self.database)
        return EditService(
            uow=uow, assets=self.assets, catalogue=self.catalogue, records=self.parts(uow).records, clock=self.clock
        )

    def page_settings(self) -> PageSettingsService:
        """Build the page settings service over a new unit of work.

        :returns: The service.
        :rtype: PageSettingsService
        """
        uow = InMemoryUnitOfWork(self.database)
        return PageSettingsService(uow=uow, catalogue=self.catalogue, records=self.parts(uow).records, clock=self.clock)

    def page_history(self) -> PageHistoryService:
        """Build the page history service over a new unit of work.

        :returns: The service.
        :rtype: PageHistoryService
        """
        uow = InMemoryUnitOfWork(self.database)
        parts = self.parts(uow)
        return PageHistoryService(
            uow=uow, records=parts.records, starter=parts.starter, assets=self.assets, clock=self.clock
        )

    async def edit_key(self, page: Page, stage: Stage, processor_key: str) -> PageStepKey:
        """Give the key of the edit of a step of the recipe of the kind of a page in a stage, found by its processor.

        :param page: Page the edit belongs to.
        :type page: Page
        :param stage: Stage of the recipe.
        :type stage: Stage
        :param processor_key: Key of the processor of the step, whose first step in the recipe is the one named.
        :type processor_key: str
        :returns: The key of the edit of that step.
        :rtype: PageStepKey
        """
        recipe = await self.parts(self.uow()).recipes.of_kind(page.project_id, stage, page.recipe_kind)
        step = next(step for step in recipe.steps if step.processor_key == processor_key)
        return PageStepKey(page.id, stage, step.step_id)

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

    async def seed_scan_page(
        self, project: Project, *, order_key: str = 'a0', image: bytes = IMAGE_CONTENT, kind: PageKind = PageKind.TEXT
    ) -> tuple[Page, Scan]:
        """Commit a scan whose images are stored and a page that shows it whole.

        :param project: Project owning the page.
        :type project: Project
        :param order_key: Order key of the page.
        :type order_key: str
        :param image: Content of the ``full`` and the preview image of the scan.
        :type image: bytes
        :param kind: Role of the page in the book, a text page unless given.
        :type kind: PageKind
        :returns: The page and its scan.
        :rtype: tuple[Page, Scan]
        """
        source = make_source(project_id=project.id, name=f'{order_key}.pdf')
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))
        page = make_page(project_id=project.id, order_key=order_key, scan=scan, kind=kind)
        uow = self.uow()
        await uow.sources.add(source)
        await uow.scans.add(scan)
        await uow.pages.add(page)
        await uow.commit()
        keys = ProjectKeys(project.id)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW):
            async with self.assets.writable(keys.scan_rendition(scan, rendition)) as target:
                target.write_bytes(image)
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
