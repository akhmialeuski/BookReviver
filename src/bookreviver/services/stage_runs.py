"""Running the steps of a recipe on one page, for a stage run and for a preview.

A run reads the current version of the nearest earlier stage of the page, or the scan itself for the page split, and
makes one version for each step of the recipe, each reading the one before. Before a step is run its identifier is
worked out from everything it depends on, so a version that was made already is found and used again, and only a step
whose parameters, edit or input changed is computed. A version that is made is stored in its own directory and never
changed, and one that fails is stored as failed with its reason, so a repeated run makes it again under the same
identifier.

``RecipeRun`` ends a page's run by making the last version the current one of the stage, which marks the later stages of
the page stale, and by cutting its tile pyramid. A run may stop at one step of the recipe, and the page is then recorded
as stopped there. If the earlier stage of the page is stale, or stopped short of its last step, it is run again first by
its own recipe, which finds its versions in the cache when nothing changed. ``PreviewRun`` runs the steps of a form on
the previews of the images and never changes what is current.

Both classes read and write through one unit of work and commit after each version, so the viewer shows the first
results while the rest are made.
"""

import logging
from typing import TYPE_CHECKING, override

from attrs import evolve, frozen

from bookreviver.domain.entities import Page, PageVersion, VersionInputs
from bookreviver.domain.enums import (
    PageOrigin,
    PageSide,
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
from bookreviver.domain.values import PageEditKey, PageSize, PageStageKey, Step
from bookreviver.services.spread_splits import SpreadSplit
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Project, Recipe
    from bookreviver.domain.ids import PageVersionId, StorageKey
    from bookreviver.domain.values import MetadataMap
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
REMAKE_CHANGED: str = (
    'The result cannot be made again as it was: the processor, the manual edit or the page side it depends on has '
    'changed since.'
)
REMAKE_INPUT_CHANGED: str = (
    'The result cannot be made again as it was: the earlier stage of the page has another current result than the one '
    'this result was made from.'
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
    :ivar scale: Whether the image is the full one, which a full run reads, or the preview a preview reads.
    """

    version_id: PageVersionId | None
    image: StorageKey
    data: MetadataMap
    ratio: float = 1.0
    scale: VersionScale = VersionScale.FULL


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
            # A page run through some of the steps only is not ready to be read, and is run through the rest first
            if record.state is StageState.STALE or record.through_step is not None:
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
                scale=VersionScale.PREVIEW,
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
                scale=VersionScale.PREVIEW,
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
        self,
        page: Page,
        stage: Stage,
        step: Step,
        source: StepSource,
        *,
        expected: PageVersionId | None = None,
    ) -> PageVersion:
        """Make the version a step gives on a source, or find the one an earlier run made.

        A version that is ready and has its files is returned as it is. One that is new, or failed or left running by a
        crash, or whose files a collection removed, is run, and returned as ready or failed. The one whose files were
        removed keeps its row, its identifier and its creation time.

        :param page: Page being processed.
        :type page: Page
        :param stage: Stage of the recipe.
        :type stage: Stage
        :param step: The step to run.
        :type step: Step
        :param source: What the step reads, which also says whether it runs on the full image or on the preview.
        :type source: StepSource
        :param expected: Identifier the version must have, when the step makes a version again, or None.
        :type expected: PageVersionId | None
        :returns: The version, in the state ready or failed.
        :rtype: PageVersion
        :raises NotFoundError: If the processor is not in the catalogue.
        :raises InvalidParametersError: If the parameters do not fit the processor.
        :raises ConflictError: If the step is one that splits a scan, which this run cannot apply, or what it depends on
                               gives another identifier than ``expected``, so the same version cannot be made again.
        """
        scale = source.scale
        processor = self._catalogue.get(step.processor_key)
        if processor.spec.scope is ProcessorScope.SPLIT:
            raise ConflictError(SPLIT_NOT_AVAILABLE.format(key=step.processor_key))
        params = processor.validate_params(step.params)
        edit = await self._uow.page_edits.find(PageEditKey(page.id, stage, step.processor_key))
        # The side is part of what a step depends on, so a page moved to the other side of the book is made again
        side = (
            PageSide.of_position(await self._uow.pages.count_before(page) + 1) if processor.spec.by_page_side else None
        )
        inputs = VersionInputs(
            page_id=page.id,
            processor=processor.spec.ref,
            params=params,
            input_id=source.version_id,
            edit_hash='' if edit is None else edit.edit_hash,
            scale=scale,
            side=side,
        )
        if expected is not None and inputs.identify() != expected:
            raise ConflictError(REMAKE_CHANGED)
        existing = await self._uow.page_versions.find(inputs.identify())
        if existing is not None and existing.state is VersionState.READY and not existing.files_removed:
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
            side=side,
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

    async def run(
        self,
        page: Page,
        recipe: Recipe,
        *,
        confirmed: bool = False,
        pin: bool | None = None,
        through_step: int | None = None,
    ) -> RunOutcome:
        """Run the steps of the recipe on the page, and make the version of the last one current.

        The right half of a split spread is made by the run of its left half, so a run of the page split leaves it out.
        A run through a step makes the steps up to it, finding those before in the cache when their inputs did not
        change, and records the page as stopped there, so the stage after it does not read the page until the rest is
        run.

        :param page: Page to process.
        :type page: Page
        :param recipe: Recipe of the stage to run.
        :type recipe: Recipe
        :param confirmed: Whether the user confirmed that undoing a split deletes the right half of a spread.
        :type confirmed: bool
        :param pin: Whether the recipe is pinned to the page, or None to keep the pin the page has.
        :type pin: bool | None
        :param through_step: Index in the recipe of the last step to run, or None for every step that is on.
        :type through_step: int | None
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
                    else await self._fail(page, recipe, pin=pin)
                )
            undoing = await self._splits.undoing(page, recipe, confirmed=confirmed)
            version = await self._run_steps(
                page, stage, [step for _, step in recipe.indexed_steps_through(through_step)], source
            )
        except DomainError:
            await self._uow.rollback()
            return await self._fail(page, recipe, pin=pin)
        if version is None:
            return await self._fail(page, recipe, pin=pin)
        changed = await self._records.set_head(
            PageStageKey(page.id, stage),
            head_version_id=version.id,
            recipe_id=recipe.id,
            pin=pin,
            through_step=recipe.stopped_at(through_step),
        )
        if undoing is not None:
            await self._splits.unsplit(undoing)
        await self._uow.commit()
        if undoing is not None:
            await self._splits.finish_unsplit(undoing)
        await self._records.announce(page.project_id, changed)
        return RunOutcome.DONE

    async def remake(self, page: Page, version_id: PageVersionId) -> RunOutcome:
        """Make a version again whose files a collection removed, and make it the current one of its stage.

        The version is made by the steps its chain of inputs inside the stage stored, from the first of them, over the
        current version of the earlier stage. Every version of the chain is found under the identifier it has and given
        its files, so no row is added. The chain must read what the earlier stage has now, and its processors, edits
        and page side must be what they were, since otherwise the identifier would differ and another result would be
        made in its place.

        :param page: Page owning the version.
        :type page: Page
        :param version_id: Version whose files are made again.
        :type version_id: PageVersionId
        :returns: Whether the version was made, skipped for lack of an image to read, or failed.
        :rtype: RunOutcome
        :raises NotFoundError: If the page has no such version.
        :raises ConflictError: If the version cannot be made again as it was, or its step splits a scan.
        """
        target = await self._uow.page_versions.get(version_id)
        if target.page_id != page.id:
            raise NotFoundError(version_id)
        chain = [target]
        while (input_id := chain[-1].input_id) is not None:
            earlier = await self._uow.page_versions.find(input_id)
            if earlier is None or earlier.stage is not target.stage:
                break
            chain.append(earlier)
        chain.reverse()
        try:
            source = await self._source(page, target.stage, VersionScale.FULL)
            if source is None:
                return RunOutcome.SKIPPED
            if source.version_id != chain[0].input_id:
                raise ConflictError(REMAKE_INPUT_CHANGED)
            version = await self._run_steps(
                page,
                target.stage,
                [Step(processor_key=made.processor.key, params=made.params) for made in chain],
                source,
                expected=[made.id for made in chain],
            )
        except DomainError:
            await self._uow.rollback()
            raise
        if version is None:
            return RunOutcome.FAILED
        previous = await self._uow.page_stages.find(PageStageKey(page.id, target.stage))
        changed = await self._records.set_head(
            PageStageKey(page.id, target.stage),
            head_version_id=version.id,
            recipe_id=None if previous is None else previous.recipe_id,
        )
        await self._uow.commit()
        await self._records.announce(page.project_id, changed)
        return RunOutcome.DONE

    async def _run_steps(
        self,
        page: Page,
        stage: Stage,
        steps: Sequence[Step],
        source: StepSource,
        *,
        expected: Sequence[PageVersionId] | None = None,
    ) -> PageVersion | None:
        """Make the version of each step, and cut the pyramid of the last.

        :param page: Page to process.
        :type page: Page
        :param stage: Stage of the steps.
        :type stage: Stage
        :param steps: The steps to run, in order, each of them on.
        :type steps: Sequence[Step]
        :param source: What the first step reads.
        :type source: StepSource
        :param expected: The identifier each version must have, in the order of the steps, when versions are made
                         again, or None.
        :type expected: Sequence[PageVersionId] | None
        :returns: The version of the last step run, or None when a step failed or there is no step.
        :rtype: PageVersion | None
        :raises DomainError: If a processor is missing, its parameters do not fit, the identifier is not the expected
                             one, or the pyramid cannot be cut.
        """
        version: PageVersion | None = None
        for index, step in enumerate(steps):
            version = await self._make_version(
                page, stage, step, source, expected=None if expected is None else expected[index]
            )
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

    async def _fail(self, page: Page, recipe: Recipe, *, pin: bool | None) -> RunOutcome:
        """Record that the stage failed on the page.

        :param page: Page whose stage failed.
        :type page: Page
        :param recipe: Recipe that failed.
        :type recipe: Recipe
        :param pin: Whether the recipe is pinned to the page, or None to keep the pin the page has.
        :type pin: bool | None
        :returns: The outcome failed.
        :rtype: RunOutcome
        """
        record = await self._records.mark_failed(page.id, recipe.stage, recipe_id=recipe.id, pin=pin)
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
            version = await self._make_version(page, stage, step, source)
            if version.state is not VersionState.READY:
                raise ConflictError(version.data[VersionData.ERROR])
            source = self._version_source(version, VersionScale.PREVIEW)
        if version is None:
            raise ConflictError(NO_STEP_TO_PREVIEW)
        return version
