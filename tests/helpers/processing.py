"""Building the processing services of a test over the test's database, the local asset store and fake processors.

``ProcessingKit`` holds what the services of one test share, and builds a service, the jobs of the workers, the edit
service or the page settings service over a new unit of work, as a new request or job would get one. The processors
are fakes that copy their input, and the writer of renditions copies the image it is given and writes token files, so
no image library is needed to tell which files a version has and in which format its ``full`` was asked for.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, cast

from attrs import evolve
from dishka import Provider, Scope, provide

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
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
from bookreviver.domain.values import PageStageKey, PageStepKey, RecipeKey, Renditions, SliceRequest
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.processing import ProcessorCatalog, StepExecutor
from bookreviver.services.edits import EditService
from bookreviver.services.library import Library
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
)
from tests.helpers.clock import FixedClock
from tests.helpers.fake_board import FakeJobBoard
from tests.helpers.fake_processing import (
    IMAGE_SIZE_PX,
    FakeCatalogue,
    FakeRenditionWriter,
)
from tests.helpers.fakes_jobs import RecordingEventBus
from tests.helpers.in_process_executor import InProcessStepExecutor
from tests.helpers.page_services import FakeTiler, make_page_service
from tests.helpers.processors import CleanupProcessor, FakeProcessor
from tests.helpers.recording_tracer import RecordingTracer
from tests.helpers.seeding import commit_account, store_project

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.entities import Job, Page, PageVersion, Project, Recipe, Scan
    from bookreviver.domain.ids import JobId, PageVersionId, ProjectId
    from bookreviver.domain.values import JobEnd, MetadataMap, RecipeDraft, Slice
    from bookreviver.ports.persistence import BookRepositories, PageVersionRepository, UnitOfWork
    from bookreviver.ports.processing import Processor
    from bookreviver.ports.runtime import JobControl
    from bookreviver.ports.storage import AssetStore
    from bookreviver.services.pages import PageService
    from tests.helpers.database import UnitOfWorkFactory

IMAGE_CONTENT: bytes = b'image'
PREVIEW_RETENTION_HOURS: int = 24
PREVIEW_LONG_SIDE_PX: int = 2048
# How many versions of one stage of a page the helper that reads them returns, which is more than any test makes
VERSIONS_WINDOW: int = 100
DEFAULTS: DefaultRecipes = DefaultRecipes(
    {
        Stage.PAGE_SPLIT: dict.fromkeys(RecipeKind, RecipeTemplate(processor_keys=(SPLIT_NONE.key,))),
        Stage.GEOMETRY: dict.fromkeys(RecipeKind, RecipeTemplate(processor_keys=(FakeProcessor.spec.key,))),
        Stage.CLEANUP: dict.fromkeys(RecipeKind, RecipeTemplate(processor_keys=(CleanupProcessor.spec.key,))),
    }
)

# The recipes of a kit with the OpenCV plugins, which are the ones the application starts with
CV_DEFAULTS: DefaultRecipes = DefaultRecipes()
# The method of ``ProcessingJobs`` that runs each kind of job it does
JOB_METHODS: dict[JobKind, str] = {
    JobKind.RUN_STAGE: 'run_stage',
    JobKind.PREVIEW_STEP: 'preview_step',
    JobKind.CUT_TILES: 'cut_tiles',
    JobKind.COLLECT_VERSIONS: 'collect_versions',
    JobKind.MEASURE_BOOK: 'measure_book',
    JobKind.DETECT_CONTENT: 'detect_content',
}


class ProcessingKit:
    """What the processing services of one test share, and a builder of the services over a new unit of work.

    :ivar database: Catalogue of the test, which stores the account of the actor and every unit of work opens over.
    :ivar assets: Asset store over a temporary directory.
    :ivar events: Event bus recording what is published.
    :ivar clock: Clock stopped at the epoch, which a test moves.
    :ivar board: Board the jobs are put on, which records them and runs them when a test calls ``work_queue``.
    :ivar fake: The fake geometry processor, which counts its runs and previews.
    :ivar cleanup: The fake cleanup processor.
    :ivar catalogue: The catalogue of the processors the services can run.
    :ivar writer: The fake writer of renditions.
    :ivar tiler: The fake tiler.
    :ivar tracer: Tracer of the runs of the kit, which keeps the spans of the pages and steps they run.
    """

    def __init__(
        self,
        database: SqlDatabase,
        new_uow: UnitOfWorkFactory,
        assets: AssetStore,
        *,
        processors: Sequence[Processor] | None = None,
        defaults: DefaultRecipes = DEFAULTS,
    ) -> None:
        """Build the kit over the catalogue and the books of the test and an asset store.

        A second kit over the databases and the assets of a first stands for the same process after a restart: its
        board is new and empty, and its databases and files are the ones the first left.

        :param database: Catalogue of the test, such as ``fx_database``.
        :type database: SqlDatabase
        :param new_uow: Function opening a unit of work over the catalogue and the books of the test, such as
                        ``fx_uow``.
        :type new_uow: UnitOfWorkFactory
        :param assets: Asset store of the test.
        :type assets: AssetStore
        :param processors: The processors of the catalogue, or None for the real ``split.none`` and the two fakes.
        :type processors: Sequence[Processor] | None
        :param defaults: The recipes the stages start with.
        :type defaults: DefaultRecipes
        """
        self._defaults = defaults
        self.database = database
        self._new_uow = new_uow
        self.assets = assets
        self.events = RecordingEventBus()
        self.clock = FixedClock(EPOCH)
        self.board = FakeJobBoard(runner=self._run_job, publisher=self.events, clock=self.clock)
        self.fake = FakeProcessor()
        self.cleanup = CleanupProcessor()
        self.writer = FakeRenditionWriter()
        self.tiler = FakeTiler()
        self.tracer = RecordingTracer()
        self.catalogue = FakeCatalogue(processors or [SplitNone(), self.fake, self.cleanup])
        self._runner = StepRunner(
            assets=assets,
            executor=InProcessStepExecutor(self.catalogue),
            renditions=self.writer,
            tiler=self.tiler,
            iiif_root=IIIF_ROOT,
        )

    def parts(self, uow: UnitOfWork) -> ProcessingParts:
        """Build the shared parts over a unit of work.

        :param uow: Unit of work of the request or job.
        :type uow: UnitOfWork
        :returns: The parts.
        :rtype: ProcessingParts
        """
        runtime = ProcessingRuntime(publisher=self.events, clock=self.clock, board=self.board, tracer=self.tracer)
        config = ProcessingConfig(
            preview_retention=timedelta(hours=PREVIEW_RETENTION_HOURS),
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
        )
        return ProcessingParts.build(uow, self.catalogue, self._defaults, runtime, config)

    def service(self, uow: UnitOfWork | None = None) -> ProcessingService:
        """Build the processing service over a unit of work.

        :param uow: The unit of work to run on, such as one that counts its reads, or None for a new one.
        :type uow: UnitOfWork | None
        :returns: The service.
        :rtype: ProcessingService
        """
        if uow is None:
            uow = self.uow()
        return ProcessingService(
            uow=uow, library=Library(uow=uow), assets=self.assets, catalogue=self.catalogue, parts=self.parts(uow)
        )

    def profiles(self) -> RecipeProfiles:
        """Build the service of the recipe profiles over a new unit of work.

        :returns: The service, which applies a profile through a processing service of the same unit of work.
        :rtype: RecipeProfiles
        """
        uow = self.uow()
        parts = self.parts(uow)
        processing = ProcessingService(
            uow=uow, library=Library(uow=uow), assets=self.assets, catalogue=self.catalogue, parts=parts
        )
        return RecipeProfiles(
            uow=uow, library=Library(uow=uow), recipes=parts.recipes, processing=processing, clock=self.clock
        )

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
        """Build the sums of the stages of books, which read through the repositories their caller hands them.

        :returns: The stage summaries, which offer the processors of the kit.
        :rtype: StageSummaries
        """
        return StageSummaries(catalogue=self.catalogue, board=self.board)

    def jobs(self) -> ProcessingJobs:
        """Build the work of the processing jobs over a new unit of work.

        :returns: The jobs.
        :rtype: ProcessingJobs
        """
        uow = self.uow()
        runtime = StageRuntime(
            runner=self._runner,
            catalogue=self.catalogue,
            publisher=self.events,
            clock=self.clock,
            order_keys=FractionalOrderKeys(),
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
            tracer=self.tracer,
        )
        return ProcessingJobs(uow=uow, assets=self.assets, runtime=runtime, parts=self.parts(uow), pages=self._pages())

    async def work_queue(self) -> None:
        """Let the board run every job put on it that has not run, in the order they were put.

        A project processes one thing at a time, so the jobs of a book run one after the other, and a job that a running
        job puts, such as the collection a run queues when it ends, runs too.
        """
        await self.board.run_queued()

    async def _run_job(self, job: Job, control: JobControl) -> JobEnd:
        """Run a job through the entry of its kind, over services built on a new unit of work, as the application does.

        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How the job ended.
        :rtype: JobEnd
        """
        if job.kind is JobKind.PREPARE_PAGES:
            return await self._pages().prepare_images(job, control)
        return cast('JobEnd', await getattr(self.jobs(), JOB_METHODS[job.kind])(job, control))

    def _pages(self) -> PageService:
        """Build the page service over a new unit of work, which prepares the images and queues the detection.

        :returns: The service.
        :rtype: PageService
        """
        return make_page_service(self.uow(), self.assets, (self.events, self.clock, self.board))

    def edits(self) -> EditService:
        """Build the edit service over a new unit of work.

        :returns: The service.
        :rtype: EditService
        """
        uow = self.uow()
        return EditService(
            uow=uow, assets=self.assets, catalogue=self.catalogue, records=self.parts(uow).records, clock=self.clock
        )

    def page_settings(self) -> PageSettingsService:
        """Build the page settings service over a new unit of work.

        :returns: The service.
        :rtype: PageSettingsService
        """
        uow = self.uow()
        return PageSettingsService(
            uow=uow,
            library=Library(uow=uow),
            catalogue=self.catalogue,
            records=self.parts(uow).records,
            clock=self.clock,
        )

    def page_history(self) -> PageHistoryService:
        """Build the page history service over a new unit of work.

        :returns: The service.
        :rtype: PageHistoryService
        """
        uow = self.uow()
        parts = self.parts(uow)
        return PageHistoryService(
            uow=uow, records=parts.records, starter=parts.starter, assets=self.assets, clock=self.clock
        )

    async def edit_key(self, project_id: ProjectId, page: Page, stage: Stage, processor_key: str) -> PageStepKey:
        """Give the key of the edit of a step of the recipe of the kind of a page in a stage, found by its processor.

        :param project_id: Identifier of the project of the page.

        :type project_id: ProjectId

        :param page: Page the edit belongs to.
        :type page: Page
        :param stage: Stage of the recipe.
        :type stage: Stage
        :param processor_key: Key of the processor of the step, whose first step in the recipe is the one named.
        :type processor_key: str
        :returns: The key of the edit of that step.
        :rtype: PageStepKey
        """
        recipe = await self.parts(self.uow()).recipes.of_kind(project_id, stage, page.recipe_kind)
        step = next(step for step in recipe.steps if step.processor_key == processor_key)
        return PageStepKey(page.id, stage, step.step_id)

    def uow(self) -> UnitOfWork:
        """Open a unit of work over the kit's catalogue and books, as a new request or job would get.

        :returns: A new unit of work.
        :rtype: UnitOfWork
        """
        return self._new_uow()

    async def seed_project(self, *, image_policy: ImagePolicy = ImagePolicy.COMPACT) -> tuple[Actor, Project]:
        """Commit a new account and a project of it.

        :param image_policy: Image policy of the project.
        :type image_policy: ImagePolicy
        :returns: The actor owning the project, and the project.
        :rtype: tuple[Actor, Project]
        """
        owner = await commit_account(self.database)
        project = evolve(make_project(), image_policy=image_policy)
        await store_project(self.uow(), project, owner=owner)
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
        source = make_source(name=f'{order_key}.pdf')
        scan = evolve(make_scan(source=source, number=0), renditions=Renditions(ready=True))
        page = make_page(order_key=order_key, scan=scan, kind=kind)

        def add_scan_page(repos: BookRepositories, _project: Project) -> None:
            """Add the source, its scan and the page that shows the scan.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.sources.add(source)
            repos.scans.add(scan)
            repos.pages.add(page)

        await self.uow().change_book(project.id, add_scan_page)
        keys = ProjectKeys(project.id)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW):
            async with self.assets.writable(keys.scan_rendition(scan, rendition)) as target:
                target.write_bytes(image)
        return page, scan

    async def seed_base_version(
        self, project_id: ProjectId, page: Page, *, image: bytes = IMAGE_CONTENT
    ) -> PageVersion:
        """Commit the ready base version of a page, with its files and its stage record.

        :param project_id: Identifier of the project of the page.

        :type project_id: ProjectId

        :param page: Page that has a scan.
        :type page: Page
        :param image: Content of the ``full``, the preview and the thumbnail file of the version, which a step of the
                      geometry stage reads.
        :type image: bytes
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
        keys = ProjectKeys(project_id)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL):
            async with self.assets.writable(keys.version_rendition(version, rendition)) as target:
                target.write_bytes(image)

        def add_base_version(repos: BookRepositories, _project: Project) -> None:
            """Add the version and the record of the page split stage that names it as current.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _project: Project of the book, which the work does not need.
            :type _project: Project
            """
            repos.page_versions.add(version)
            repos.page_stages.save(
                PageStage(
                    page_id=page.id,
                    stage=Stage.PAGE_SPLIT,
                    head_version_id=version.id,
                    state=StageState.FRESH,
                    updated_at=EPOCH,
                )
            )

        await self.uow().change_book(project_id, add_base_version)
        return version

    async def store_files(self, version: PageVersion, project_id: ProjectId) -> None:
        """Write the three files of a version of a project's page, as a run would.

        :param version: Version whose files are written.
        :type version: PageVersion
        :param project_id: Project owning the page.
        :type project_id: ProjectId
        """
        keys = ProjectKeys(project_id)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL):
            async with self.assets.writable(keys.version_rendition(version, rendition)) as target:
                target.write_bytes(IMAGE_CONTENT)

    async def stored_version(self, project_id: ProjectId, version_id: PageVersionId) -> PageVersion:
        """Read a version back as committed.

        :param project_id: Project whose book holds the version.
        :type project_id: ProjectId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version as its book holds it now.
        :rtype: PageVersion
        :raises NotFoundError: If the book holds no such version.
        """
        return await self.uow().read_book(project_id, lambda repos: repos.page_versions.get(version_id))

    @staticmethod
    def params(**values: object) -> MetadataMap:
        """Build the parameters of a fake step.

        :param values: The parameters by name.
        :type values: object
        :returns: The parameters.
        :rtype: MetadataMap
        """
        return dict(values)


def stored_job(kit: ProcessingKit, job_id: JobId) -> Job:
    """Read a job back as the board holds it.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param job_id: Identifier of the job.
    :type job_id: JobId
    :returns: The job as the board holds it now.
    :rtype: Job
    """
    return kit.board.get(job_id)


async def change_versions(
    kit: ProcessingKit, project_id: ProjectId, work: Callable[[PageVersionRepository], object]
) -> None:
    """Change the versions of a book in a change of its own, as another request would.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_id: Project whose book is changed.
    :type project_id: ProjectId
    :param work: What is done to the version repository of the change.
    :type work: Callable[[PageVersionRepository], object]
    """
    await kit.uow().change_book(project_id, lambda repos, _project: work(repos.page_versions))


async def found_version(kit: ProcessingKit, project_id: ProjectId, version_id: PageVersionId) -> PageVersion | None:
    """Read a version as committed, which is None for a version that the book does not hold.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_id: Project whose book is read.
    :type project_id: ProjectId
    :param version_id: Identifier of the version.
    :type version_id: PageVersionId
    :returns: The version, or None.
    :rtype: PageVersion | None
    """
    return await kit.uow().read_book(project_id, lambda repos: repos.page_versions.find(version_id))


async def found_stage(kit: ProcessingKit, project_id: ProjectId, page: Page, stage: Stage) -> PageStage | None:
    """Read the record of a stage of a page as committed, which is None for a stage that has not run on the page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_id: Identifier of the project of the page.
    :type project_id: ProjectId
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :returns: The record, or None.
    :rtype: PageStage | None
    """
    return await kit.uow().read_book(project_id, lambda repos: repos.page_stages.find(PageStageKey(page.id, stage)))


async def stage_versions(
    kit: ProcessingKit, project_id: ProjectId, page: Page, stage: Stage | None
) -> Slice[PageVersion]:
    """Read the versions a page has made in a stage or in all, as committed, the earliest first.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project_id: Identifier of the project of the page.
    :type project_id: ProjectId
    :param page: The page.
    :type page: Page
    :param stage: The stage, or None for every stage.
    :type stage: Stage | None
    :returns: The versions of every scale and mark, and their number.
    :rtype: Slice[PageVersion]
    """
    return await kit.uow().read_book(
        project_id,
        lambda repos: repos.page_versions.list_for_stage(page.id, stage, None, SliceRequest(limit=VERSIONS_WINDOW)),
    )


class ProcessingFakesProvider(Provider):
    """Replaces the application's catalogue of processors and its default recipes with the fakes of the tests.

    The imaging adapters stay the application's own, so a run of the fake processors writes real files with libvips.
    The steps run in threads over this catalogue through ``InProcessExecutorProvider``, which ``fx_app`` adds first.
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


class InProcessExecutorProvider(Provider):
    """Replaces only the application's pool of step processes with a thread executor over its own catalogue.

    The lifespan starts the pool, and every process of it builds the catalogue of all the plugins, which takes seconds
    that the tests of the API, which run no step of the real plugins, would pay one by one.
    """

    scope = Scope.APP

    @provide(override=True)
    def step_executor(self, catalogue: ProcessorCatalog) -> StepExecutor:
        """Run the steps in a thread over the catalogue of the application.

        :param catalogue: The catalogue of the application.
        :type catalogue: ProcessorCatalog
        :returns: The executor that calls the processors in this process.
        :rtype: StepExecutor
        """
        return InProcessStepExecutor(catalogue)
