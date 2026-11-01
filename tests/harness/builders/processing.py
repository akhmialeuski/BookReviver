"""Building the processing services of a test over the test's database, the local asset store and fake processors.

``ProcessingKit`` holds what the services of one test share, and builds a service, the jobs of the workers, the edit
service or the page settings service over a new catalogue database, as a new request or job would get one. The processors
are fakes that copy their input, and the writer of renditions copies the image it is given and writes token files, so
no image library is needed to tell which files a version has and in which format its ``full`` was asked for.
"""

from datetime import timedelta
from typing import TYPE_CHECKING, cast

from attrs import evolve
from dishka import Provider, Scope, provide

from bookreviver.adapters.ordering.fractional import FractionalOrderKeys
from bookreviver.app.recipe_defaults import load_default_recipes
from bookreviver.app.settings import ProcessingSettings
from bookreviver.domain.accounts import Actor
from bookreviver.domain.images import ImagePolicy, Rendition, Renditions
from bookreviver.domain.jobs import JobKind
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.page_steps import PageStepKey
from bookreviver.domain.pages import PageKind, RecipeKind
from bookreviver.domain.recipes import RecipeKey
from bookreviver.domain.slices import SliceRequest
from bookreviver.domain.stages import PageStage, PageStageKey, Stage, StageState
from bookreviver.domain.versions import VersionState
from bookreviver.plugins.split_none import SplitNone
from bookreviver.ports.processing import ProcessorCatalog, StepExecutor
from bookreviver.ports.runtime import BookJobs
from bookreviver.services.book import Book
from bookreviver.services.edits import EditService
from bookreviver.services.page_history import PageHistoryService
from bookreviver.services.page_settings import PageSettingsService
from bookreviver.services.processing import ProcessingService
from bookreviver.services.processing_jobs import ProcessingJobs
from bookreviver.services.processing_parts import ProcessingConfig, ProcessingParts
from bookreviver.services.recipe_profiles import RecipeProfiles
from bookreviver.services.recipes import DefaultRecipes, RecipeTemplate
from bookreviver.services.stage_runs import StageRuntime
from bookreviver.services.stage_summaries import StageSummaries
from bookreviver.services.steps import StepRunner
from tests.harness.builders.domain import (
    EPOCH,
    SPLIT_NONE,
    make_page,
    make_page_version,
    make_record,
    make_scan,
    make_source,
    open_for,
)
from tests.harness.builders.page_services import FakeTiler, make_page_service
from tests.harness.builders.seeding import commit_account, store_book
from tests.harness.fakes.board import FakeJobBoard
from tests.harness.fakes.in_process_executor import InProcessStepExecutor
from tests.harness.fakes.jobs import RecordingEventBus
from tests.harness.fakes.processing import (
    IMAGE_SIZE_PX,
    FakeCatalogue,
    FakeRenditionWriter,
)
from tests.harness.fakes.processors import CleanupProcessor, FakeProcessor
from tests.harness.fakes.recording_tracer import RecordingTracer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bookreviver.adapters.persistence.sqlalchemy.database import SqlDatabase
    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.ids import BookDirectoryName, BookId, JobId, PageVersionId
    from bookreviver.domain.jobs import Job, JobEnd
    from bookreviver.domain.metadata import MetadataMap
    from bookreviver.domain.pages import Page
    from bookreviver.domain.recipes import Recipe, RecipeDraft
    from bookreviver.domain.slices import Slice
    from bookreviver.domain.sources import Scan
    from bookreviver.domain.versions import PageVersion
    from bookreviver.ports.persistence import BookRepositories, CatalogueDatabase, PageVersionRepository
    from bookreviver.ports.processing import Processor
    from bookreviver.ports.runtime import JobControl
    from bookreviver.services.pages import PageService
    from tests.harness.adapters.database import LocatedBookDatabases
    from tests.harness.builders.domain import BookOpener

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
CV_DEFAULTS: DefaultRecipes = load_default_recipes(ProcessingSettings().recipes_file)
# The method of ``ProcessingJobs`` that runs each kind of job it does that takes the book, the job and the control
JOB_METHODS: dict[JobKind, str] = {
    JobKind.RUN_STAGE: 'run_stage',
    JobKind.PREVIEW_STEP: 'preview_step',
    JobKind.CUT_TILES: 'cut_tiles',
    JobKind.COLLECT_VERSIONS: 'collect_versions',
    JobKind.DETECT_CONTENT: 'detect_content',
}


class ProcessingKit:
    """What the processing services of one test share, and a builder of the services over a new catalogue database.

    :ivar database: Catalogue of the test, which stores the account of the actor and every catalogue database opens over.
    :ivar stores: Opens the catalogue databases, the databases and the file stores of the books of the test.
    :ivar databases: Opens the databases of the books of the test.
    :ivar files: Opens the source store and the asset store of a book of the test.
    :ivar events: Event bus recording what is published.
    :ivar board: Board the jobs are put on, which records them and runs them when a test calls ``work_queue``.
    :ivar fake: The fake geometry processor, which counts its runs and previews.
    :ivar cleanup: The fake cleanup processor.
    :ivar processors: The catalogue of the processors the services can run.
    :ivar writer: The fake writer of renditions.
    :ivar tiler: The fake tiler.
    :ivar tracer: Tracer of the runs of the kit, which keeps the spans of the pages and steps they run.
    """

    def __init__(
        self,
        database: SqlDatabase,
        stores: BookOpener,
        *,
        processors: Sequence[Processor] | None = None,
        defaults: DefaultRecipes = DEFAULTS,
    ) -> None:
        """Build the kit over the catalogue, the books and the files of the test.

        A second kit over the opener of a first stands for the same process after a restart: its board is new and
        empty, and its databases and files are the ones the first left.

        :param database: Catalogue of the test, such as ``fx_database``.
        :type database: SqlDatabase
        :param stores: Opens the catalogue databases, the databases and the file stores of the books of the test, such as
                       ``fx_open_book``.
        :type stores: BookOpener
        :param processors: The processors of the catalogue, or None for the real ``split.none`` and the two fakes.
        :type processors: Sequence[Processor] | None
        :param defaults: The recipes the stages start with.
        :type defaults: DefaultRecipes
        """
        self._defaults = defaults
        self.database = database
        self.stores = stores
        self._new_catalogue = stores.catalogue
        self.databases = stores.databases
        self.files = stores.files
        self.events = RecordingEventBus.current()
        self.board = FakeJobBoard(runner=self._run_job)
        BookJobs.register(self.board.open)
        self.fake = FakeProcessor()
        self.cleanup = CleanupProcessor()
        self.writer = FakeRenditionWriter()
        self.tiler = FakeTiler()
        self.tracer = RecordingTracer()
        self.processors = FakeCatalogue(processors or [SplitNone(), self.fake, self.cleanup])
        self._runner = StepRunner(
            executor=InProcessStepExecutor(self.processors),
            renditions=self.writer,
            tiler=self.tiler,
        )

    def parts(self) -> ProcessingParts:
        """Build the shared parts of one request or job.

        :returns: The parts.
        :rtype: ProcessingParts
        """
        config = ProcessingConfig(
            preview_retention=timedelta(hours=PREVIEW_RETENTION_HOURS),
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
        )
        return ProcessingParts.build(self.processors, self._defaults, config)

    def service(self) -> ProcessingService:
        """Build the processing service over a new catalogue database.

        :returns: The service.
        :rtype: ProcessingService
        """
        return ProcessingService(processors=self.processors, parts=self.parts())

    def profiles(self) -> RecipeProfiles:
        """Build the service of the recipe profiles.

        :returns: The service, which applies a profile through a processing service.
        :rtype: RecipeProfiles
        """
        parts = self.parts()
        processing = ProcessingService(processors=self.processors, parts=parts)
        return RecipeProfiles(recipes=parts.recipes, processing=processing)

    async def recipe_of(
        self, actor: Actor, book_id: BookId, stage: Stage, kind: RecipeKind = RecipeKind.TEXT
    ) -> Recipe:
        """Read the recipe of one kind of a stage of a book, which the first read creates with the others.

        :param actor: Account owning the book.
        :type actor: Actor
        :param book_id: Identifier of the book whose recipe is read.
        :type book_id: BookId
        :param stage: The stage.
        :type stage: Stage
        :param kind: The kind of page, text unless given.
        :type kind: RecipeKind
        :returns: The recipe.
        :rtype: Recipe
        """
        listed = await self.service().recipes(
            open_for(actor, book_id),
            stage,
            SliceRequest(limit=len(RecipeKind)),
        )
        return next(recipe for recipe in listed.items if recipe.kind is kind)

    async def edit_recipe(
        self,
        actor: Actor,
        book_id: BookId,
        stage: Stage,
        draft: RecipeDraft,
        kind: RecipeKind = RecipeKind.TEXT,
    ) -> Recipe:
        """Replace the steps of the recipe of one kind of a stage, which the first read of the stage creates.

        :param actor: Account owning the book.
        :type actor: Actor
        :param book_id: Identifier of the book whose recipe is changed.
        :type book_id: BookId
        :param stage: The stage.
        :type stage: Stage
        :param draft: The new steps.
        :type draft: RecipeDraft
        :param kind: The kind of page, text unless given.
        :type kind: RecipeKind
        :returns: The recipe as stored.
        :rtype: Recipe
        """
        recipe = await self.recipe_of(actor, book_id, stage, kind)
        return await self.service().save_recipe(
            open_for(actor, book_id),
            RecipeKey(stage, recipe.id),
            draft,
        )

    def stages(self) -> StageSummaries:
        """Build the sums of the stages of books, which read through the repositories their caller hands them.

        :returns: The stage summaries, which offer the processors of the kit.
        :rtype: StageSummaries
        """
        return StageSummaries(processors=self.processors)

    def jobs(self) -> ProcessingJobs:
        """Build the work of the processing jobs over a new catalogue database.

        :returns: The jobs.
        :rtype: ProcessingJobs
        """
        runtime = StageRuntime(
            runner=self._runner,
            processors=self.processors,
            order_keys=FractionalOrderKeys(),
            preview_long_side_px=PREVIEW_LONG_SIDE_PX,
            tracer=self.tracer,
        )
        return ProcessingJobs(
            runtime=runtime,
            parts=self.parts(),
            pages=self._pages(),
        )

    async def work_queue(self) -> None:
        """Let the board run every job put on it that has not run, in the order they were put.

        A book processes one thing at a time, so its jobs run one after the other, and a job that a running
        job puts, such as the collection a run queues when it ends, runs too.
        """
        await self.board.run_queued()

    async def _run_job(self, dir_name: BookDirectoryName, job: Job, control: JobControl) -> JobEnd:
        """Run a job through the entry of its kind, over services built on a new catalogue database, as the application does.

        :param dir_name: Name of the directory of the book whose queue the job was taken from.
        :type dir_name: BookDirectoryName
        :param job: The job the board took from its queue.
        :type job: Job
        :param control: What the job reports through.
        :type control: JobControl
        :returns: How the job ended.
        :rtype: JobEnd
        """
        book = Book(dir_name)
        if job.kind is JobKind.PREPARE_PAGES:
            return await self._pages().prepare_images(book, job, control)
        if job.kind is JobKind.MEASURE_BOOK:
            return await self.jobs().measure_book(book, control)
        return cast('JobEnd', await getattr(self.jobs(), JOB_METHODS[job.kind])(book, job, control))

    def _pages(self) -> PageService:
        """Build the page service over a new catalogue database, which prepares the images and queues the detection.

        :returns: The service.
        :rtype: PageService
        """
        return make_page_service(self.stores, (self.events, self.board))

    def edits(self) -> EditService:
        """Build the edit service over a new catalogue database.

        :returns: The service.
        :rtype: EditService
        """
        return EditService(processors=self.processors, records=self.parts().records)

    def page_settings(self) -> PageSettingsService:
        """Build the page settings service over a new catalogue database.

        :returns: The service.
        :rtype: PageSettingsService
        """
        return PageSettingsService(processors=self.processors, records=self.parts().records)

    def page_history(self) -> PageHistoryService:
        """Build the page history service over a new catalogue database.

        :returns: The service.
        :rtype: PageHistoryService
        """
        parts = self.parts()
        return PageHistoryService(records=parts.records, starter=parts.starter)

    async def edit_key(self, book_id: BookId, page: Page, stage: Stage, processor_key: str) -> PageStepKey:
        """Give the key of the edit of a step of the recipe of the kind of a page in a stage, found by its processor.

        :param book_id: Identifier of the book of the page.

        :type book_id: BookId

        :param page: Page the edit belongs to.
        :type page: Page
        :param stage: Stage of the recipe.
        :type stage: Stage
        :param processor_key: Key of the processor of the step, whose first step in the recipe is the one named.
        :type processor_key: str
        :returns: The key of the edit of that step.
        :rtype: PageStepKey
        """
        recipe = await self.parts().recipes.of_kind(await open_book(self, book_id), stage, page.recipe_kind)
        step = next(step for step in recipe.steps if step.processor_key == processor_key)
        return PageStepKey(page.id, stage, step.step_id)

    def catalogue(self) -> CatalogueDatabase:
        """Open a catalogue database over the kit's catalogue and books, as a new request or job would get.

        :returns: A new catalogue database.
        :rtype: CatalogueDatabase
        """
        return self._new_catalogue()

    async def seed_book(self, *, image_policy: ImagePolicy = ImagePolicy.COMPACT) -> tuple[Actor, BookId]:
        """Commit a new account and a book owned by it.

        :param image_policy: Image policy of the book.
        :type image_policy: ImagePolicy
        :returns: The actor owning the book, and the identifier of the book.
        :rtype: tuple[Actor, BookId]
        """
        owner = await commit_account(self.database)
        book_id = await store_book(
            self.catalogue(), evolve(make_record(), image_policy=image_policy), owner=owner, databases=self.databases
        )
        return Actor(account_id=owner), book_id

    async def seed_scan_page(
        self,
        book_id: BookId,
        *,
        order_key: str = 'a0',
        image: bytes = IMAGE_CONTENT,
        kind: PageKind = PageKind.TEXT,
    ) -> tuple[Page, Scan]:
        """Commit a scan whose images are stored and a page that shows it whole.

        :param book_id: Identifier of the book owning the page.
        :type book_id: BookId
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

        def add_scan_page(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the source, its scan and the page that shows the scan.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
            """
            repos.sources.add(source)
            repos.scans.add(scan)
            repos.pages.add(page)

        await self.databases.open(book_id).change(add_scan_page)
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW):
            async with self.files.assets(book_id).writable(BookKeys.scan_rendition(scan, rendition)) as target:
                target.write_bytes(image)
        return page, scan

    async def seed_base_version(self, book_id: BookId, page: Page, *, image: bytes = IMAGE_CONTENT) -> PageVersion:
        """Commit the ready base version of a page, with its files and its stage record.

        :param book_id: Identifier of the book of the page.

        :type book_id: BookId

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
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL):
            async with self.files.assets(book_id).writable(BookKeys.version_rendition(version, rendition)) as target:
                target.write_bytes(image)

        def add_base_version(repos: BookRepositories, _book_record: BookRecord) -> None:
            """Add the version and the record of the page split stage that names it as current.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: Record of the book, which the work does not need.
            :type _book_record: BookRecord
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

        await self.databases.open(book_id).change(add_base_version)
        return version

    async def store_files(self, version: PageVersion, book_id: BookId) -> None:
        """Write the three files of a version of a book's page, as a run would.

        :param version: Version whose files are written.
        :type version: PageVersion
        :param book_id: Book owning the page.
        :type book_id: BookId
        """
        for rendition in (Rendition.FULL_JPEG, Rendition.PREVIEW, Rendition.THUMBNAIL):
            async with self.files.assets(book_id).writable(BookKeys.version_rendition(version, rendition)) as target:
                target.write_bytes(IMAGE_CONTENT)

    async def stored_version(self, book_id: BookId, version_id: PageVersionId) -> PageVersion:
        """Read a version back as committed.

        :param book_id: Book that holds the version.
        :type book_id: BookId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version as its book holds it now.
        :rtype: PageVersion
        :raises NotFoundError: If the book holds no such version.
        """
        return await self.databases.open(book_id).read(lambda repos: repos.page_versions.get(version_id))

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


async def open_book(kit: ProcessingKit, book_id: BookId) -> Book:
    """Open a book of the library with its database, as a job opens the book it runs on.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Identifier of the book.
    :type book_id: BookId
    :returns: The open book.
    :rtype: Book
    :raises NotFoundError: If the library has no such book.
    """
    catalogue = kit.catalogue()
    return Book((await catalogue.read(lambda repos: repos.library.get(book_id))).directory)


async def change_versions(
    book_id: BookId, work: Callable[[PageVersionRepository], object], *, databases: LocatedBookDatabases
) -> None:
    """Change the versions of a book in a change of its own, as another request would.

    :param book_id: Book that is changed.
    :type book_id: BookId
    :param work: What is done to the version repository of the change.
    :type work: Callable[[PageVersionRepository], object]
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    """
    await databases.open(book_id).change(lambda repos, _book_record: work(repos.page_versions))


async def found_version(
    book_id: BookId, version_id: PageVersionId, *, databases: LocatedBookDatabases
) -> PageVersion | None:
    """Read a version as committed, which is None for a version that the book does not hold.

    :param book_id: Book that is read.
    :type book_id: BookId
    :param version_id: Identifier of the version.
    :type version_id: PageVersionId
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The version, or None.
    :rtype: PageVersion | None
    """
    return await databases.open(book_id).read(lambda repos: repos.page_versions.find(version_id))


async def found_stage(
    book_id: BookId, page: Page, stage: Stage, *, databases: LocatedBookDatabases
) -> PageStage | None:
    """Read the record of a stage of a page as committed, which is None for a stage that has not run on the page.

    :param book_id: Identifier of the book of the page.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :param stage: The stage.
    :type stage: Stage
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The record, or None.
    :rtype: PageStage | None
    """
    return await databases.open(book_id).read(lambda repos: repos.page_stages.find(PageStageKey(page.id, stage)))


async def stage_versions(
    book_id: BookId, page: Page, stage: Stage | None, *, databases: LocatedBookDatabases
) -> Slice[PageVersion]:
    """Read the versions a page has made in a stage or in all, as committed, the earliest first.

    :param book_id: Identifier of the book of the page.
    :type book_id: BookId
    :param page: The page.
    :type page: Page
    :param stage: The stage, or None for every stage.
    :type stage: Stage | None
    :param databases: Opens the databases of the books of the test.
    :type databases: LocatedBookDatabases
    :returns: The versions of every scale and mark, and their number.
    :rtype: Slice[PageVersion]
    """
    return await databases.open(book_id).read(
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
    def step_executor(self, processors: ProcessorCatalog) -> StepExecutor:
        """Run the steps in a thread over the catalogue of the application.

        :param processors: The catalogue of the application.
        :type processors: ProcessorCatalog
        :returns: The executor that calls the processors in this process.
        :rtype: StepExecutor
        """
        return InProcessStepExecutor(processors)
