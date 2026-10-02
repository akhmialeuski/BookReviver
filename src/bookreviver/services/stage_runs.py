"""Running the steps of a recipe on one page, for a stage run and for a preview.

A run reads the current version of the nearest earlier stage of the page, or the scan itself for the page split, and
makes one version for each step of the recipe, each reading the one before. Before a step is run its identifier is
worked out from everything it depends on, so a version that was made already is found and used again, and only a step
whose parameters, edit or input changed is computed. A version that is made is stored in its own directory and never
changed, and one that fails is stored as failed with its reason, so a repeated run makes it again under the same
identifier.

``RecipeRun`` ends a page's run by making the last version the current one of the stage, which marks the later stages of
the page stale, and by cutting its tile pyramid. If the earlier stage of the page is stale, it is run again first by its
own recipe, which finds its versions in the cache when nothing changed. ``PreviewRun`` runs the steps of a form on the
previews of the images and never changes what is current.

Both classes read and write through one unit of work and commit after each version, so the viewer shows the first
results while the rest are made.
"""

import logging
from typing import TYPE_CHECKING, override

from attrs import evolve, frozen

from bookreviver.domain.entities import Page, PageVersion, VersionInputs
from bookreviver.domain.enums import (
    PageOrigin,
    ProcessorScope,
    Rendition,
    RunOutcome,
    Stage,
    StageState,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, DomainError, NotFoundError
from bookreviver.domain.events import PageVersionReady
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageEditKey, PageSize, PageStageKey
from bookreviver.services.spread_splits import SpreadSplit
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Project, Recipe
    from bookreviver.domain.ids import PageVersionId, StorageKey
    from bookreviver.domain.values import MetadataMap, Step
    from bookreviver.ports.ordering import OrderKeys
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock, EventPublisher
    from bookreviver.services.recipes import RecipeBook
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.steps import StepRunner

UNEXPECTED_FAILURE: str = 'The step failed because of an unexpected error. It has been logged.'
SPLIT_NOT_AVAILABLE: str = 'The step {key} splits a scan, so it is the only step of its recipe and cannot be previewed.'
WRONG_OUTPUTS: str = 'The step {key} made {count} outputs, and a step of one page makes one.'
EARLIER_STAGE_FAILED: str = (
    'The {stage} stage of the page is out of date and could not be run again, so a later stage cannot read it.'
)
NO_PREVIEW_INPUT: str = 'The page has no image to preview a step on.'
NO_STEP_TO_PREVIEW: str = 'Every step up to this one is switched off, so there is nothing to preview.'

logger = logging.getLogger(__name__)


@frozen(kw_only=True)
class StepSource:
    """What a step reads: the version before it, or the scan, and the image of it.

    :ivar version_id: Version the step reads, or None when it reads a scan.
    :ivar image: Key of the image, the ``full`` or the ``preview`` of the version or the scan.
    :ivar data: ``data`` of the version, or the facts of the scan.
    :ivar ratio: Size of the image over the size of the full image, 1 for a full run.
    """

    version_id: PageVersionId | None
    image: StorageKey
    data: MetadataMap
    ratio: float = 1.0


@frozen(kw_only=True)
class StageRuntime:
    """What a run of a stage works with, apart from the project and the unit of work of the job.

    :ivar runner: Runner of processors and writer of their files.
    :ivar catalogue: The processors the application can run.
    :ivar publisher: Publisher of the events the browser follows.
    :ivar clock: Clock stamping new versions.
    :ivar order_keys: Builder of the order key of the right half of a spread, which a split inserts after the left.
    :ivar preview_long_side_px: Longer side of a preview in pixels, from which the ratio of a preview is worked out.
    """

    runner: StepRunner
    catalogue: ProcessorCatalog
    publisher: EventPublisher
    clock: Clock
    order_keys: OrderKeys
    preview_long_side_px: int


class StageWork:
    """What a run of a recipe and a preview have in common: reading the input of a stage and making one version."""

    def __init__(self, *, project: Project, uow: UnitOfWork, runtime: StageRuntime) -> None:
        """Work over the ports of one job.

        :param project: Project owning the pages, whose image policy chooses the format of a version's ``full``.
        :type project: Project
        :param uow: Unit of work, committed after each version.
        :type uow: UnitOfWork
        :param runtime: The runner, the catalogue, the publisher, the clock and the size of a preview.
        :type runtime: StageRuntime
        """
        self._project = project
        self._uow = uow
        self._runner = runtime.runner
        self._catalogue = runtime.catalogue
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._preview_long_side_px = runtime.preview_long_side_px
        self._keys = ProjectKeys(project.id)

    async def _refresh(self, page: Page, stage: Stage) -> None:
        """Bring a stale earlier stage of a page up to date before a step reads it; a preview leaves it as it is.

        :param page: Page whose earlier stage is stale.
        :type page: Page
        :param stage: The stale stage.
        :type stage: Stage
        """

    async def _source(self, page: Page, stage: Stage, scale: VersionScale) -> StepSource | None:
        """Find what the first step of a stage reads: the scan for the page split, else the nearest earlier version.

        :param page: Page being processed.
        :type page: Page
        :param stage: Stage of the recipe.
        :type stage: Stage
        :param scale: Whether the full images or the previews are read.
        :type scale: VersionScale
        :returns: The source, or None when the page has no image to process, such as a placeholder.
        :rtype: StepSource | None
        :raises ConflictError: If an earlier stage of the page is stale and running it again failed.
        """
        if stage is Stage.PAGE_SPLIT:
            return await self._scan_source(page, scale)
        for earlier in sorted((s for s in Stage if s.position < stage.position), key=lambda s: -s.position):
            record = await self._uow.page_stages.find(PageStageKey(page.id, earlier))
            if record is None or record.head_version_id is None:
                continue
            if record.state is StageState.STALE:
                await self._refresh(page, earlier)
                record = await self._uow.page_stages.get(PageStageKey(page.id, earlier))
                if record.state is StageState.FAILED:
                    raise ConflictError(EARLIER_STAGE_FAILED.format(stage=earlier.label))
            if record.head_version_id is None:
                continue
            head = await self._uow.page_versions.get(record.head_version_id)
            if head.renditions is not None and head.renditions.ready:
                return self._version_source(head, scale)
        bases = [
            version
            for version in await self._uow.page_versions.list_base_versions([page.id])
            if version.state is VersionState.READY and version.renditions is not None and version.renditions.ready
        ]
        return self._version_source(bases[-1], scale) if bases else None

    async def _scan_source(self, page: Page, scale: VersionScale) -> StepSource | None:
        """Find the scan a page was cut from, whose image the page split reads.

        :param page: Page being processed.
        :type page: Page
        :param scale: Whether the full image or the preview is read.
        :type scale: VersionScale
        :returns: The source, or None for a page that has no scan, or whose scan is not cut yet.
        :rtype: StepSource | None
        """
        if page.origin is not PageOrigin.SCAN or page.scan_id is None:
            return None
        scan = await self._uow.scans.get(page.scan_id)
        if not scan.renditions.ready:
            return None
        facts = scan.facts
        data = facts.as_data()
        if scale is VersionScale.PREVIEW:
            return StepSource(
                version_id=None,
                image=self._keys.scan_rendition(scan, Rendition.PREVIEW),
                data=data,
                ratio=self._ratio(facts.width_px, facts.height_px),
            )
        return StepSource(version_id=None, image=self._keys.scan_rendition(scan, scan.renditions.full), data=data)

    def _version_source(self, version: PageVersion, scale: VersionScale) -> StepSource:
        """Make a source of the files of a version that has an image.

        :param version: Ready version with an image.
        :type version: PageVersion
        :param scale: Whether the ``full`` image or the preview is read.
        :type scale: VersionScale
        :returns: The source.
        :rtype: StepSource
        :raises ValueError: If the version has no image.
        """
        if version.renditions is None:
            err_msg = f'The version {version.id} has no image.'
            raise ValueError(err_msg)
        if scale is VersionScale.PREVIEW:
            size = PageSize.from_data(version.data)
            ratio = 1.0 if size is None else self._ratio(size.width_px, size.height_px)
            return StepSource(
                version_id=version.id,
                image=self._keys.version_rendition(version, Rendition.PREVIEW),
                data=version.data,
                ratio=ratio,
            )
        return StepSource(
            version_id=version.id,
            image=self._keys.version_rendition(version, version.renditions.full),
            data=version.data,
        )

    def _ratio(self, width_px: int, height_px: int) -> float:
        """Work out the size of the preview of an image over its full size.

        :param width_px: Width of the full image.
        :type width_px: int
        :param height_px: Height of the full image.
        :type height_px: int
        :returns: The ratio, at most 1, since a preview is never larger than the image.
        :rtype: float
        """
        return min(1.0, self._preview_long_side_px / max(width_px, height_px))

    async def _execute(self, version: PageVersion, run: StepRun) -> PageVersion:
        """Run the step of a version and store its files.

        :param version: The version being made, in the state running.
        :type version: PageVersion
        :param run: What the step reads.
        :type run: StepRun
        :returns: The version as ready.
        :rtype: PageVersion
        :raises ConflictError: If the step makes more or fewer than one output.
        :raises DomainError: If the step cannot run on its input.
        """
        async with self._runner.execute(run) as result:
            if len(result.outputs) != 1:
                raise ConflictError(WRONG_OUTPUTS.format(key=run.processor_key, count=len(result.outputs)))
            return await self._runner.store(
                self._keys, version, result.outputs[0], policy=self._project.image_policy, tiles=False
            )

    async def _make_version(
        self, page: Page, stage: Stage, step: Step, source: StepSource, scale: VersionScale
    ) -> PageVersion:
        """Make the version a step gives on a source, or find the one an earlier run made.

        A version that is ready is returned as it is. One that is new, or failed or left running by a crash, is run, and
        returned as ready or failed.

        :param page: Page being processed.
        :type page: Page
        :param stage: Stage of the recipe.
        :type stage: Stage
        :param step: The step to run.
        :type step: Step
        :param source: What the step reads.
        :type source: StepSource
        :param scale: Whether the step runs on the full image or on the preview.
        :type scale: VersionScale
        :returns: The version, in the state ready or failed.
        :rtype: PageVersion
        :raises NotFoundError: If the processor is not in the catalogue.
        :raises InvalidParametersError: If the parameters do not fit the processor.
        :raises ConflictError: If the step is one that splits a scan, which this run cannot apply.
        """
        processor = self._catalogue.get(step.processor_key)
        if processor.spec.scope is ProcessorScope.SPLIT:
            raise ConflictError(SPLIT_NOT_AVAILABLE.format(key=step.processor_key))
        params = processor.validate_params(step.params)
        edit = await self._uow.page_edits.find(PageEditKey(page.id, stage, step.processor_key))
        inputs = VersionInputs(
            page_id=page.id,
            processor=processor.spec.ref,
            params=params,
            input_id=source.version_id,
            edit_hash='' if edit is None else edit.edit_hash,
            scale=scale,
        )
        existing = await self._uow.page_versions.find(inputs.identify())
        if existing is not None and existing.state is VersionState.READY:
            return existing
        version = PageVersion(
            id=inputs.identify(),
            page_id=page.id,
            stage=stage,
            processor=processor.spec.ref,
            input_id=source.version_id,
            params=params,
            renditions=None,
            state=VersionState.RUNNING,
            scale=scale,
            edit_hash=inputs.edit_hash,
            created_at=self._clock.now() if existing is None else existing.created_at,
        )
        await (self._uow.page_versions.add(version) if existing is None else self._uow.page_versions.update(version))
        await self._uow.commit()
        run = StepRun(
            processor_key=step.processor_key,
            params=params,
            input_data=source.data,
            image=source.image,
            edit=edit,
            scale=scale,
            ratio=source.ratio,
        )
        try:
            made = await self._execute(version, run)
        except DomainError as error:
            made = evolve(version, state=VersionState.FAILED, data={VersionData.ERROR: str(error)})
        except Exception:
            logger.exception('The step %s on page %s failed', step.processor_key, page.id)
            made = evolve(version, state=VersionState.FAILED, data={VersionData.ERROR: UNEXPECTED_FAILURE})
        await self._uow.page_versions.update(made)
        await self._uow.commit()
        if made.state is VersionState.READY:
            await self._publisher.publish(PageVersionReady(project_id=page.project_id, version=made))
        return made


class RecipeRun(StageWork):
    """Runs a recipe on one page and makes its last version the current one of the stage."""

    def __init__(
        self, *, project: Project, uow: UnitOfWork, runtime: StageRuntime, recipes: RecipeBook, records: StageRecords
    ) -> None:
        """Work over the ports of one job.

        :param project: Project owning the pages.
        :type project: Project
        :param uow: Unit of work, committed after each version.
        :type uow: UnitOfWork
        :param runtime: The runner, the catalogue, the publisher, the clock and the size of a preview.
        :type runtime: StageRuntime
        :param recipes: The recipes of the project, which an earlier stage that is stale is run again by.
        :type recipes: RecipeBook
        :param records: Writer of the stage records.
        :type records: StageRecords
        """
        super().__init__(project=project, uow=uow, runtime=runtime)
        self._recipes = recipes
        self._records = records
        self._splits = SpreadSplit(project=project, uow=uow, runtime=runtime, records=records)

    async def run(self, page: Page, recipe: Recipe, *, confirmed: bool = False) -> RunOutcome:
        """Run the steps of the recipe on the page, and make the version of the last one current.

        The right half of a split spread is made by the run of its left half, so a run of the page split leaves it out.

        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe of the stage to run.
        :type recipe: Recipe
        :param confirmed: Whether the user confirmed that undoing a split deletes the right half of a spread.
        :type confirmed: bool
        :returns: Whether the page was processed, skipped for lack of an image, or failed.
        :rtype: RunOutcome
        """
        stage = recipe.stage
        if stage is Stage.PAGE_SPLIT and page.slot > Page.LEFT_HALF:
            return RunOutcome.SKIPPED
        try:
            if (source := await self._source(page, stage, VersionScale.FULL)) is None:
                return RunOutcome.SKIPPED
            if self._splits.splits(recipe):
                return (
                    RunOutcome.DONE
                    if await self._splits.split(page, recipe, source, confirmed=confirmed)
                    else await self._fail(page, recipe)
                )
            undoing = await self._splits.undoing(page, recipe, confirmed=confirmed)
            version = await self._run_steps(page, recipe, source)
        except DomainError:
            await self._uow.rollback()
            return await self._fail(page, recipe)
        if version is None:
            return await self._fail(page, recipe)
        changed = await self._records.set_head(page.id, stage, head_version_id=version.id, recipe_id=recipe.id)
        if undoing is not None:
            await self._splits.unsplit(undoing)
        await self._uow.commit()
        if undoing is not None:
            await self._splits.finish_unsplit(undoing)
        await self._records.announce(page.project_id, changed)
        return RunOutcome.DONE

    async def _run_steps(self, page: Page, recipe: Recipe, source: StepSource) -> PageVersion | None:
        """Make the version of each step of the recipe that is switched on, and cut the pyramid of the last one.

        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe of the stage to run.
        :type recipe: Recipe
        :param source: What the first step reads.
        :type source: StepSource
        :returns: The version of the last step, or None when a step failed.
        :rtype: PageVersion | None
        :raises DomainError: If a processor is missing, its parameters do not fit, or the pyramid cannot be cut.
        """
        version: PageVersion | None = None
        for step in recipe.enabled_steps:
            version = await self._make_version(page, recipe.stage, step, source, VersionScale.FULL)
            if version.state is not VersionState.READY:
                return None
            source = self._version_source(version, VersionScale.FULL)
        if version is not None and version.renditions is not None and not version.tiles_ready:
            version = await self._runner.cut_tiles(self._keys, version)
            await self._uow.page_versions.update(version)
        return version

    @override
    async def _refresh(self, page: Page, stage: Stage) -> None:
        """Run the recipe that made an earlier stage again, which finds its versions in the cache when nothing changed.

        :param page: Page whose earlier stage is stale.
        :type page: Page
        :param stage: The stale stage.
        :type stage: Stage
        """
        record = await self._uow.page_stages.get(PageStageKey(page.id, stage))
        try:
            recipe = (
                await self._uow.recipes.get(record.recipe_id)
                if record.recipe_id is not None
                else await self._recipes.active(page.project_id, stage)
            )
        except NotFoundError:
            # A stage without a recipe, such as the page order, has nothing to run again
            return
        await self.run(page, recipe)

    async def _fail(self, page: Page, recipe: Recipe) -> RunOutcome:
        """Record that the stage failed on the page.

        :param page: Page whose stage failed.
        :type page: Page
        :param recipe: Recipe that failed.
        :type recipe: Recipe
        :returns: The outcome failed.
        :rtype: RunOutcome
        """
        record = await self._records.mark_failed(page.id, recipe.stage, recipe_id=recipe.id)
        await self._uow.commit()
        await self._records.announce(page.project_id, [record])
        return RunOutcome.FAILED


class PreviewRun(StageWork):
    """Runs the steps of a form on the previews of a page's images, and changes nothing that is current."""

    async def run(self, page: Page, stage: Stage, steps: Sequence[Step], step_index: int) -> PageVersion:
        """Run the steps up to one of them, each on the preview the one before made.

        :param page: Page to preview on.
        :type page: Page
        :param stage: Stage of the steps.
        :type stage: Stage
        :param steps: The steps of the form, whose parameters are not saved in a recipe. A step that is switched off is
                      left out, as in a run.
        :type steps: Sequence[Step]
        :param step_index: Index of the last step to run.
        :type step_index: int
        :returns: The preview version of the last step that is on.
        :rtype: PageVersion
        :raises ConflictError: If the page has no image to preview on, every step up to the index is off, or a step
                               failed.
        """
        source = await self._source(page, stage, VersionScale.PREVIEW)
        if source is None:
            raise ConflictError(NO_PREVIEW_INPUT)
        version: PageVersion | None = None
        for step in (step for step in steps[: step_index + 1] if step.enabled):
            version = await self._make_version(page, stage, step, source, VersionScale.PREVIEW)
            if version.state is not VersionState.READY:
                raise ConflictError(version.data[VersionData.ERROR])
            source = self._version_source(version, VersionScale.PREVIEW)
        if version is None:
            raise ConflictError(NO_STEP_TO_PREVIEW)
        return version
