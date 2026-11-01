"""Use cases of the processing framework a request makes: recipes, runs, previews and page versions.

A stage has one recipe for each kind of page, and a page is processed by the recipe of its kind. The use cases here
change a recipe or the current version of a stage, which writes rows in one ``Book.change`` and marks the pages
they affect stale, or start heavy work, a run, a preview, a cut of tiles, a collection or a measure of the book, which
records a job with its parameters, queues it and answers at once, since a request handler never blocks on CPU-bound
work. A use case that starts heavy work holds no transaction itself: it finds the recipes, which opens a ``Book.change``
of its own when the stage has none, and then the job starter records the job in a ``change`` of its own. Neither runs
a processor. The workers do, through ``ProcessingJobs``, and the files of a step are written by ``StepRunner``.

Editing a recipe does not process any page again. It marks the stage stale on every page the recipe processed, and the
user starts the run. The one recipe of each kind of a stage is kept by the database with a unique key.

Versions are not deleted when they stop being current, so going back to earlier parameters stays possible. The job
``collect-versions``, which a run queues when it ends and the owner of the book starts by hand, deletes the ones that
are no longer current and that nothing needs, with their files and the rows that describe them, so no row is left
without its picture. A preview goes only once it is old.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.errors import ConflictError, InvalidParametersError, NotFoundError
from bookreviver.domain.jobs import JobKind, StepPreview, TileCut
from bookreviver.domain.keys import BookKeys
from bookreviver.domain.recipes import OrderMode
from bookreviver.domain.runs import RunMode
from bookreviver.domain.slices import Slice
from bookreviver.domain.stages import PageStageKey, Stage
from bookreviver.domain.version_chains import stage_depths, step_places, versions_of_step
from bookreviver.domain.versions import CollectionReport, VersionScale, VersionState
from bookreviver.services.processing_parts import BOOK_BUSY
from bookreviver.services.run_plans import RunPlan

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.books import BookRecord
    from bookreviver.domain.geometry import Point
    from bookreviver.domain.ids import PageId, PageVersionId
    from bookreviver.domain.jobs import Job, StageRun
    from bookreviver.domain.pages import Page
    from bookreviver.domain.processors import ProcessorSpec
    from bookreviver.domain.recipes import Recipe, RecipeDraft, RecipeKey
    from bookreviver.domain.slices import SliceRequest
    from bookreviver.domain.stages import PageStage
    from bookreviver.domain.versions import PageVersion, VersionFilter
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.services.book import Book
    from bookreviver.services.processing_parts import ProcessingParts

NOT_CHOOSABLE: str = 'The version {version_id} cannot be made the current one: {reason}.'
NOT_READY: str = 'it is not ready'
NOT_FULL: str = 'it is a preview'
OTHER_STAGE: str = 'it belongs to another stage'
NO_IMAGE: str = 'The version {version_id} has no image.'
NEEDS_CONFIRMATION: str = (
    'The mode {mode} takes the work of {pages} pages away, which the run does only when it is confirmed.'
)
STEP_NEEDS_STAGE: str = 'The versions of a step are listed with the stage of the step.'
NO_STEP_TO_RUN_THROUGH: str = (
    'Step {index} of the recipe for {name} does not exist, or it and every step before it are switched off.'
)


class ProcessingService:
    """Recipes, runs, previews and page versions of the acting account's books."""

    def __init__(
        self,
        *,
        processors: ProcessorCatalog,
        parts: ProcessingParts,
    ) -> None:
        """Work over the ports of one request.

        :param processors: The processors the application can run.
        :type processors: ProcessorCatalog
        :param parts: The recipes, the stage records and the job starter, which work on the book of each request.
        :type parts: ProcessingParts
        """
        self._processors = processors
        self._recipes = parts.recipes
        self._records = parts.records
        self._starter = parts.starter

    def processors(self) -> Sequence[ProcessorSpec]:
        """List what every processor of the catalogue says about itself.

        :returns: The specs, by key.
        :rtype: Sequence[ProcessorSpec]
        """
        return self._processors.specs()

    async def recipes(self, book: Book, stage: Stage, request: SliceRequest) -> Slice[Recipe]:
        """List the recipes of a stage, one for each kind of page, creating them the first time the stage is asked for.

        :param book: The open book.
        :type book: Book
        :param stage: The stage.
        :type stage: Stage
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The recipes of the window in the order of the kinds, and the number of all the stage's recipes.
        :rtype: Slice[Recipe]
        :raises NotFoundError: If the stage has no recipe.
        """
        recipes = await self._recipes.recipes(book, stage)
        return Slice(items=recipes[request.offset : request.offset + request.limit], total=len(recipes))

    async def save_recipe(self, book: Book, key: RecipeKey, draft: RecipeDraft) -> Recipe:
        """Replace the steps of a recipe of a stage, and mark the pages it processed stale, in one transaction.

        It opens a ``Book.change`` of its own, in which the ownership, the recipe and the stale marks are read and
        written together. The browser is told after the transaction.

        :param book: The open book.
        :type book: Book
        :param key: The stage the request names and the identifier of the recipe, which must belong to that stage.
        :type key: RecipeKey
        :param draft: New steps, which are checked against their processors and their order.
        :type draft: RecipeDraft
        :returns: The recipe as stored.
        :rtype: Recipe
        :raises NotFoundError: If the book has no such recipe of the stage.
        :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                        usual order.
        """

        def work(repos: BookRepositories, _book_record: BookRecord) -> Recipe:
            """Store the new steps of the recipe, and mark the pages it processed stale.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it.
            :type _book_record: BookRecord
            :returns: The recipe as stored.
            :rtype: Recipe
            :raises NotFoundError: If the book has no such recipe of the stage.
            :raises InvalidParametersError: If a step does not fit its processor, or stands off a required place in the
                                            usual order.
            """
            recipe = self._recipes.get(repos, key.recipe_id, stage=key.stage)
            changed = self._recipes.rewrite(repos, recipe, draft)
            self._records.mark_recipe_stale(repos, recipe.id)
            return changed

        return await book.change(work)

    async def start_run(self, book: Book, stage: Stage, run: StageRun) -> Job:
        """Record a job that runs a stage over some pages, each by the recipe of its kind, and queue it.

        :param book: The open book.
        :type book: Book
        :param stage: The stage.
        :type stage: Stage
        :param run: The pages and the mode, with the stage the request names.
        :type run: StageRun
        :returns: The queued job; a preview of the book that is queued or running is cancelled for it.
        :rtype: Job
        :raises NotFoundError: If the book has no such page, or the stage has no recipe.
        :raises ConflictError: If a run or a measure of the book is queued or running, or the run goes through a
                               step that the recipe of a page has no such step for, or whose steps up to it are all
                               switched off, or its mode takes the work of pages away and it is not confirmed.
        """

        def check(repos: BookRepositories) -> None:
            """Require, for a run over chosen pages, every one of the pages in the book.

            :param repos: Repositories of the transaction that reads the recipes.
            :type repos: BookRepositories
            :raises NotFoundError: If a page is not in the book.
            """
            if (page_ids := run.page_ids) is not None:
                repos.pages.list_by_ids(page_ids)

        await self._recipes.recipes(book, stage, checking=check)
        if run.through_step is not None:
            planned = {recipe.id: recipe for recipe in (await self._plan(book, run).recipes()).values()}
            for recipe in planned.values():
                if run.through_step >= len(recipe.steps) or not recipe.indexed_steps_through(run.through_step):
                    raise ConflictError(
                        NO_STEP_TO_RUN_THROUGH.format(index=run.through_step + 1, name=recipe.kind.label)
                    )
        if run.mode is not RunMode.KEEP and not run.confirm_overwrite:
            affected = (await self._plan(book, run).impact()).affected
            if affected:
                raise ConflictError(NEEDS_CONFIRMATION.format(mode=run.mode.label, pages=affected))
        return await self._starter.enqueue(book, JobKind.RUN_STAGE, run.to_map())

    def _plan(self, book: Book, run: StageRun) -> RunPlan:
        """Plan a run over the book.

        :param book: The open book.
        :type book: Book
        :param run: The run.
        :type run: StageRun
        :returns: The pages and recipes of the run.
        :rtype: RunPlan
        """
        return RunPlan(recipes=self._recipes, book=book, run=run)

    async def start_measure(self, book: Book) -> Job:
        """Record a job that measures the book and writes the medians into the normalize step, and queue it.

        :param book: The open book.
        :type book: Book
        :returns: The queued job; a preview of the book that is queued or running is cancelled for it.
        :rtype: Job
        :raises NotFoundError: If the Geometry stage has no recipe.
        :raises ConflictError: If a run or a measure of the book is queued or running, which may be writing the
                               versions that are measured or the recipe.
        """
        await self._recipes.recipes(book, Stage.GEOMETRY)
        return await self._starter.enqueue(book, JobKind.MEASURE_BOOK, {})

    async def start_preview(self, book: Book, preview: StepPreview) -> Job:
        """Record a job that previews the steps of a form on a page, and queue it.

        :param book: The open book.
        :type book: Book
        :param preview: The page, the stage, the steps of the form and the step the result is wanted of.
        :type preview: StepPreview
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the book has no such page.
        :raises InvalidParametersError: If a step does not fit its processor.
        :raises ConflictError: If a run, a preview, a tile cutting, a collection or a measure of the book is queued
                               or running.
        """
        await book.read(lambda repos: repos.pages.get(preview.page_id))
        checked = StepPreview(
            page_id=preview.page_id,
            stage=preview.stage,
            # A preview only draws, so an order the user may still be arranging is not refused
            steps=self._recipes.check(preview.stage, preview.steps, order=OrderMode.FREE),
            step_index=preview.step_index,
        )
        return await self._starter.enqueue(book, JobKind.PREVIEW_STEP, checked.to_map())

    async def page_stages(self, book: Book, page_id: PageId, request: SliceRequest) -> Slice[PageStage]:
        """List the current version and the state of every stage a page has been through, in pipeline order.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The records of the window and the number of all the page's records.
        :rtype: Slice[PageStage]
        :raises NotFoundError: If the book has no such page.
        """

        def work(repos: BookRepositories) -> Sequence[PageStage]:
            """Read the records of the page from one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: Every record of the page.
            :rtype: Sequence[PageStage]
            :raises NotFoundError: If the book has no such page.
            """
            repos.pages.get(page_id)
            return repos.page_stages.list_for_page(page_id)

        records = await book.read(work)
        return Slice(items=records[request.offset : request.offset + request.limit], total=len(records))

    async def choose_version(self, book: Book, page_id: PageId, stage: Stage, version_id: PageVersionId) -> PageStage:
        """Make a version the current one of a stage of a page, and mark the later stages stale.

        The version must be ready, made by a full run of this page in this stage. If its tile pyramid is not cut yet, a
        job is queued to cut it, since the viewer opens the current version.

        A preview of the book, queued or running, is cancelled first, since the reader asked for the choice and a
        preview is disposable: the editor of a step asks for one by itself, so a choice made a moment later would
        otherwise be refused by a job the reader never started. A request for a version that cannot be chosen is
        refused before any preview is cancelled. The version is checked again inside the transaction that makes it
        current, since the cancelling is a transaction of its own, and a version that stopped being choosable meanwhile
        is refused with nothing written.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The record of the stage in its new state.
        :rtype: PageStage
        :raises NotFoundError: If the book has no such page, or the page has no such version.
        :raises ConflictError: If the version is not ready, is a preview, or belongs to another stage, or a run, a
                               measure of the book, a tile cutting or a collection of the book is queued or
                               running, which may be reading or deleting the versions the choice depends on.
        """
        await book.read(lambda repos: self._choosable(repos, repos.pages.get(page_id), stage, version_id))
        if await self._starter.free_of_previews(book):
            raise ConflictError(BOOK_BUSY)
        key = PageStageKey(page_id, stage)

        def work(repos: BookRepositories, _book_record: BookRecord) -> tuple[PageVersion, PageStage]:
            """Check the version again, and make it the current one of the stage.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :param _book_record: The record of the book as the change finds it, which the check of the version reads
                                 again.
            :type _book_record: BookRecord
            :returns: The version, and the record of the stage in its new state.
            :rtype: tuple[PageVersion, PageStage]
            :raises NotFoundError: If the book has no such page, or the page has no
                                   such version.
            :raises ConflictError: If the version is not ready, is a preview, or belongs to another stage.
            """
            version = self._choosable(repos, repos.pages.get(page_id), stage, version_id)
            previous = repos.page_stages.find(key)
            return version, self._records.set_head(
                repos, key, head_version_id=version_id, recipe_id=None if previous is None else previous.recipe_id
            )

        version, record = await book.change(work)
        if version.renditions is not None and not version.tiles_ready:
            await self._starter.enqueue_tiles(book, [version_id])
        return record

    async def versions(
        self,
        book: Book,
        page_id: PageId,
        version_filter: VersionFilter,
        request: SliceRequest,
    ) -> Slice[PageVersion]:
        """List the versions of a page, the earliest first, of one stage, step, scale and mark or of all.

        The versions of a step are the ones its place in a recipe of the stage gives: every step that is on stores one
        version that reads the one before, so a version belongs to the step whose processor made it and whose place
        among the steps that are on is the number of versions of its stage the version reads. A step that is switched
        off in every recipe has none.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_filter: The stage, the step, the scale and the mark to list, each of them or all.
        :type version_filter: VersionFilter
        :param request: Offset and limit of the window.
        :type request: SliceRequest
        :returns: The versions of the window and the number of all that match.
        :rtype: Slice[PageVersion]
        :raises NotFoundError: If the book has no such page, or no recipe of the stage has the step.
        :raises InvalidParametersError: If a step is asked for without its stage.
        """

        def work(repos: BookRepositories) -> Slice[PageVersion]:
            """Read the versions of the page from one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The versions of the window and the number of all that match.
            :rtype: Slice[PageVersion]
            :raises NotFoundError: If the book has no such page, or no recipe of the
                                   stage has the step.
            :raises InvalidParametersError: If a step is asked for without its stage.
            """
            repos.pages.get(page_id)
            step_id = version_filter.step_id
            if step_id is None:
                return repos.page_versions.list_for_stage(
                    page_id, version_filter.stage, version_filter.scale, request, version_filter.mark
                )
            if version_filter.stage is None:
                raise InvalidParametersError(STEP_NEEDS_STAGE)
            recipes = repos.recipes.list_for_stage(version_filter.stage)
            if not any(step.step_id == step_id for recipe in recipes for step in recipe.steps):
                raise NotFoundError(step_id)
            found = repos.page_versions.list_for_page(page_id)
            made = versions_of_step(found, version_filter.stage, step_places(recipes, step_id), stage_depths(found))
            matching = [
                version
                for version in found
                if version.id in made
                and (version_filter.scale is None or version.scale is version_filter.scale)
                and (version_filter.mark is None or version.mark is version_filter.mark)
            ]
            return Slice(items=matching[request.offset : request.offset + request.limit], total=len(matching))

        return await book.read(work)

    async def version(self, book: Book, page_id: PageId, version_id: PageVersionId) -> PageVersion:
        """Return one version of a page.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the book has no such page, or the page has no such version.
        """

        def work(repos: BookRepositories) -> PageVersion:
            """Read the version of the page.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The version.
            :rtype: PageVersion
            :raises NotFoundError: If the book has no such page, or the page has no
                                   such version.
            """
            repos.pages.get(page_id)
            return self._version_of(repos, page_id, version_id)

        return await book.read(work)

    async def start_tiles(self, book: Book, page_id: PageId, version_id: PageVersionId) -> Job:
        """Record a job that cuts the tile pyramid of a version, which the viewer asks for when it opens one.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The queued job.
        :rtype: Job
        :raises NotFoundError: If the book has no such page, or the page has no such version.
        :raises ConflictError: If the version is not ready, has no image, or is a preview, or a run, a preview, a tile
                               cutting or a collection of the book is queued or running.
        """
        version = await self.version(book, page_id, version_id)
        if version.renditions is None:
            raise ConflictError(NO_IMAGE.format(version_id=version_id))
        if version.state is not VersionState.READY or version.scale is not VersionScale.FULL:
            raise ConflictError(NOT_CHOOSABLE.format(version_id=version_id, reason=NOT_READY))
        return await self._starter.enqueue(book, JobKind.CUT_TILES, TileCut(version_ids=(version_id,)).to_map())

    async def collection_report(self, book: Book) -> CollectionReport:
        """Count the versions a collection would delete now and the size of their files, which it changes nothing of.

        :param book: The open book.
        :type book: Book
        :returns: The number of versions and the bytes their files take.
        :rtype: CollectionReport
        """
        collection = self._starter.collection()

        def work(repos: BookRepositories) -> Sequence[PageVersion]:
            """Read the versions a collection would delete from one snapshot.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The versions nothing needs.
            :rtype: Sequence[PageVersion]
            """
            return repos.page_versions.collectable(collection.previews_older_than)

        old = await book.read(work)
        sizes = [await book.assets.size_of(BookKeys.version_directory(version)) for version in old]
        return CollectionReport(versions=len(old), size_bytes=sum(sizes))

    async def start_collection(self, book: Book) -> Job:
        """Record a job that deletes the old versions nothing needs, with their files, and queue it.

        :param book: The open book.
        :type book: Book
        :returns: The queued job, or the collection that is queued or running already.
        :rtype: Job
        :raises ConflictError: If a run, a measure of the book or a tile cutting of the book is queued or running.
        """
        if (job := await self._starter.enqueue_collection(book)) is None:
            raise ConflictError(BOOK_BUSY)
        return job

    async def map_to_scan(
        self,
        book: Book,
        page_id: PageId,
        version_id: PageVersionId,
        points: Sequence[Point],
    ) -> Sequence[Point]:
        """Map points of the image of a version back to the pixels of the scan, along the chain of its transforms.

        Each version maps a point of its output to its input, and a base version maps it to the scan, which is its
        input.

        :param book: The open book.
        :type book: Book
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version the points lie on.
        :type version_id: PageVersionId
        :param points: Points in the pixels of the image of the version.
        :type points: Sequence[Point]
        :returns: The points in the pixels of the scan, in the same order.
        :rtype: Sequence[Point]
        :raises NotFoundError: If the book has no such page, the page has no such
                               version, or a version of the chain was deleted.
        :raises UnsupportedTransformError: If a version of the chain follows a mesh.
        """

        def work(repos: BookRepositories) -> Sequence[Point]:
            """Follow the chain of the versions from one snapshot, which maps the points through each of them.

            :param repos: Repositories of the transaction.
            :type repos: BookRepositories
            :returns: The points in the pixels of the scan.
            :rtype: Sequence[Point]
            :raises NotFoundError: If the book has no such page, the page has no such
                                   version, or a version of the chain was deleted.
            :raises UnsupportedTransformError: If a version of the chain follows a mesh.
            """
            repos.pages.get(page_id)
            version = self._version_of(repos, page_id, version_id)
            mapped = list(points)
            while True:
                mapped = [version.transform.to_input(point) for point in mapped]
                if version.input_id is None:
                    return mapped
                version = self._version_of(repos, page_id, version.input_id)

        return await book.read(work)

    def _choosable(self, repos: BookRepositories, page: Page, stage: Stage, version_id: PageVersionId) -> PageVersion:
        """Return a version of a page, if it can be made the current one of a stage.

        Reads only, so it is called before the transaction that makes the version current, where it refuses a request
        early, and inside that transaction, where it decides.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page: Page of the book, which the version must belong to.
        :type page: Page
        :param stage: The stage.
        :type stage: Stage
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the page has no such version.
        :raises ConflictError: If the version is not ready, is a preview, or belongs to another stage.
        """
        version = self._version_of(repos, page.id, version_id)
        if (reason := self._why_not_choosable(version, stage)) is not None:
            raise ConflictError(NOT_CHOOSABLE.format(version_id=version_id, reason=reason))
        return version

    @staticmethod
    def _version_of(repos: BookRepositories, page_id: PageId, version_id: PageVersionId) -> PageVersion:
        """Return a version of a page, in the transaction of the caller.

        :param repos: Repositories of the transaction the caller runs in.
        :type repos: BookRepositories
        :param page_id: Identifier of the page.
        :type page_id: PageId
        :param version_id: Identifier of the version.
        :type version_id: PageVersionId
        :returns: The version.
        :rtype: PageVersion
        :raises NotFoundError: If the version does not exist or belongs to another page.
        """
        version = repos.page_versions.get(version_id)
        if version.page_id != page_id:
            raise NotFoundError(version_id)
        return version

    @staticmethod
    def _why_not_choosable(version: PageVersion, stage: Stage) -> str | None:
        """Say why a version cannot become the current one of a stage, or None when it can.

        :param version: The version.
        :type version: PageVersion
        :param stage: The stage.
        :type stage: Stage
        :returns: The reason, or None.
        :rtype: str | None
        """
        if version.stage is not stage:
            return OTHER_STAGE
        if version.state is not VersionState.READY:
            return NOT_READY
        if version.scale is not VersionScale.FULL:
            return NOT_FULL
        return None
