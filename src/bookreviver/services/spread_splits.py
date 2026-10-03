"""Splitting a scan into the two pages of a spread, and undoing the split.

A processor of the scope ``split`` reads the scan of a page and makes one output for each part of it, the left half and
then the right half. The page that runs the step keeps its place and becomes the left half, slot 1, and the right half
is a page of its own, slot 2, inserted into the book right after it. Each half has a base version of the stage, which
holds the copy of its part of the scan, so the page stands on its own as any other. The identifier of a base version
comes from ``VersionInputs`` like that of every version, with the manual cut line of the step as the edit, so a changed
line gives new versions of both halves and the old line finds the old ones again.

Nothing of the book changes until the halves exist. The step runs first and its files are written, and then the new
page, the change of the left page, the versions of both halves and their heads are committed together, so a split that
fails leaves the book as it was, with a failed version on the page that ran it. The right half is made once. Splitting
again finds its page by the scan and the slot and gives it new versions, and a stage run that goes by the pages of a
book leaves the right halves to the run of their left halves.

A step of the scope ``split`` may decide for each scan, and keep it whole: it then makes one output, the page that runs
the step becomes the whole scan, and a split made earlier is undone as described below, with the same confirmation.

Undoing a split is running a step of the scope ``page`` on the stage, such as ``split.none``, on the left half. It
makes the left half the whole scan again and deletes the right half, with its versions and files, and so the work on
it. A run that was not confirmed by the user leaves the page failed with the reason and deletes nothing. The deletion
is committed with the new current version of the page, so a replacement that fails leaves the right half in place, and
its files are removed after the commit.

The class reads and writes through the unit of work of the run, and announces what changed after the commit.
"""

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve, frozen

from bookreviver.domain.entities import Page, PageVersion, VersionInputs
from bookreviver.domain.enums import (
    PageChange,
    PageOrigin,
    ProcessorScope,
    Side,
    Stage,
    VersionData,
    VersionScale,
    VersionState,
)
from bookreviver.domain.errors import ConflictError, DomainError
from bookreviver.domain.events import PagesChanged, PageVersionReady
from bookreviver.domain.geometry import SplitChoice
from bookreviver.domain.ids import PageId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageStageKey, PageStepKey
from bookreviver.services.page_labels import PageLabels
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import PageEdit, PageStage, Project, Recipe
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import Processor, StepOutput
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.stage_runs import StageRuntime, StepSource

UNSPLIT_NOT_CONFIRMED: str = (
    'Undoing the split deletes page {page_id}, the right half of the spread, with its versions. Confirm it to go on.'
)
WRONG_HALVES: str = 'The step {key} made {count} outputs, and a split into the halves of a spread makes two.'
UNEXPECTED_FAILURE: str = 'The split failed because of an unexpected error. It has been logged.'
NO_SCAN: str = 'The page {page_id} has no scan to split.'

logger = logging.getLogger(__name__)


@frozen(kw_only=True)
class Halves:
    """The two pages of a spread as a split will leave them, before any of them is stored.

    :ivar left: The page that runs the step, as the left half.
    :ivar right: The right half, which is a new page when the scan was not split before.
    :ivar left_changes: Whether the left page changes, since it was the whole scan.
    :ivar right_is_new: Whether the right half is a page to add.
    """

    left: Page
    right: Page
    left_changes: bool
    right_is_new: bool


@frozen(kw_only=True)
class Unsplit:
    """A split that is undone: the left half that becomes the whole scan, and the right halves it deletes.

    :ivar page: The left half, as read.
    :ivar rights: The pages of the scan that come after it, which are deleted.
    """

    page: Page
    rights: Sequence[Page]


class SpreadSplit:
    """Applies a step that splits a scan to the page of the scan, and undoes the split."""

    def __init__(self, *, project: Project, uow: UnitOfWork, runtime: StageRuntime, records: StageRecords) -> None:
        """Work over the ports of one job.

        :param project: Project owning the pages, whose image policy chooses the format of a version's ``full``.
        :type project: Project
        :param uow: Unit of work, committed once a split or an undoing is complete.
        :type uow: UnitOfWork
        :param runtime: The runner, the catalogue, the publisher, the clock and the order keys.
        :type runtime: StageRuntime
        :param records: Writer of the stage records.
        :type records: StageRecords
        """
        self._project = project
        self._uow = uow
        self._runner = runtime.runner
        self._catalogue = runtime.catalogue
        self._publisher = runtime.publisher
        self._clock = runtime.clock
        self._order_keys = runtime.order_keys
        self._records = records
        self._labels = PageLabels(uow=uow, publisher=runtime.publisher, clock=runtime.clock)
        self._keys = ProjectKeys(project.id)

    def splits(self, recipe: Recipe) -> bool:
        """Tell whether a recipe splits scans: it is a recipe of the page split whose step makes a part for each half.

        :param recipe: The recipe of the stage.
        :type recipe: Recipe
        :returns: True when the first step of the recipe that is switched on is of the scope ``split``.
        :rtype: bool
        :raises NotFoundError: If the processor of the step is not in the catalogue.
        """
        steps = recipe.enabled_steps
        if recipe.stage is not Stage.PAGE_SPLIT or not steps:
            return False
        return self._catalogue.get(steps[0].processor_key).spec.scope is ProcessorScope.SPLIT

    async def split(self, page: Page, recipe: Recipe, source: StepSource, *, confirmed: bool = False) -> bool:
        """Split the scan of a page into two pages, and make the base version of each half the current one.

        A step that decides for each scan, such as ``split.auto``, may keep the scan whole and make one output. The
        page then is the whole scan, and a spread it was split into before is undone as by a step of the scope ``page``:
        the right half is deleted, which needs the confirmation of the user.

        :param page: The page that runs the step, which becomes the left half.
        :type page: Page
        :param recipe: The recipe whose first step splits the scan, which both halves record as the one that made them.
        :type recipe: Recipe
        :param source: The scan the step reads.
        :type source: StepSource
        :param confirmed: Whether the user confirmed that keeping the scan whole deletes the right half of a spread.
        :type confirmed: bool
        :returns: True when the pages are made and current, and False when the step failed, which leaves the book as it
                  was and a failed version on the page.
        :rtype: bool
        :raises NotFoundError: If the processor is not in the catalogue.
        :raises InvalidParametersError: If the parameters do not fit the processor.
        :raises ConflictError: If the page has no scan.
        """
        step = recipe.enabled_steps[0]
        processor = self._catalogue.get(step.processor_key)
        state = await self._uow.page_step_states.find(PageStepKey(page.id, Stage.PAGE_SPLIT, step.step_id))
        params = processor.validate_params(step.params if state is None else state.apply_to(step.params))
        edit = None if state is None else state.edit
        halves = await self._halves(page)
        templates = [self._template(half, processor, params, edit) for half in (halves.left, halves.right)]
        stored = [await self._uow.page_versions.find(template.id) for template in templates]
        run = StepRun(
            processor_key=step.processor_key, params=params, input_data=source.data, image=source.image, edit=edit
        )
        try:
            made = await self._cached(stored)
            if made is None:
                made = await self._make(templates, run)
            undoing = await self.undoing(page, recipe, confirmed=confirmed) if len(made) == 1 else None
        except DomainError as error:
            await self._fail(templates[0], stored[0], str(error))
            return False
        except Exception:
            logger.exception('The split %s of page %s failed', run.processor_key, page.id)
            await self._fail(templates[0], stored[0], UNEXPECTED_FAILURE)
            return False
        changed = await self._commit(halves, made, stored, recipe, undoing)
        await self._announce(halves, made, stored, changed)
        if undoing is not None:
            await self.finish_unsplit(undoing)
        return True

    async def undoing(self, page: Page, recipe: Recipe, *, confirmed: bool) -> Unsplit | None:
        """Find what undoing a split deletes, when a recipe that does not split runs on a left half.

        Nothing is written. The deletion is made by ``unsplit``, with the version that replaces the split.

        :param page: The page that runs the recipe.
        :type page: Page
        :param recipe: The recipe of the stage.
        :type recipe: Recipe
        :param confirmed: Whether the user confirmed that the right half is deleted.
        :type confirmed: bool
        :returns: The undoing, or None when the recipe is of another stage, splits scans, or the page is no left half.
        :rtype: Unsplit | None
        :raises ConflictError: If a right half is to be deleted and the user did not confirm it.
        """
        if recipe.stage is not Stage.PAGE_SPLIT or page.slot != Page.LEFT_HALF or page.scan_id is None:
            return None
        rights = [sibling for sibling in await self._uow.pages.list_for_scan(page.scan_id) if sibling.slot > page.slot]
        if rights and not confirmed:
            raise ConflictError(UNSPLIT_NOT_CONFIRMED.format(page_id=rights[0].id))
        return Unsplit(page=page, rights=rights)

    async def unsplit(self, undoing: Unsplit) -> None:
        """Delete the right halves and make the left half the whole scan, in the transaction of the caller.

        :param undoing: What the undoing deletes.
        :type undoing: Unsplit
        """
        await self._labels.recompute(self._project.id, leaving=[right.id for right in undoing.rights])
        for right in undoing.rights:
            await self._uow.pages.delete(right.id)
        await self._uow.pages.update(evolve(undoing.page, slot=Page.WHOLE_SCAN, updated_at=self._clock.now()))

    async def finish_unsplit(self, undoing: Unsplit) -> None:
        """Remove the files of the deleted pages and tell the browser, after the caller committed the undoing.

        :param undoing: What the undoing deleted.
        :type undoing: Unsplit
        """
        for right in undoing.rights:
            await self._runner.discard(self._keys.page(right.id))
        if undoing.rights:
            await self._publisher.publish(
                PagesChanged(
                    project_id=self._project.id,
                    page_ids=[right.id for right in undoing.rights],
                    change=PageChange.REMOVED,
                )
            )
        await self._labels.announce(self._project.id)

    async def _halves(self, page: Page) -> Halves:
        """Work out the two pages of the spread, without storing any of them.

        :param page: The page that runs the step.
        :type page: Page
        :returns: The left half and the right half, the right one new when the scan has none yet.
        :rtype: Halves
        :raises ConflictError: If the page has no scan.
        """
        if page.scan_id is None:
            raise ConflictError(NO_SCAN.format(page_id=page.id))
        left = page if page.slot == Page.LEFT_HALF else evolve(page, slot=Page.LEFT_HALF, updated_at=self._clock.now())
        siblings = await self._uow.pages.list_for_scan(page.scan_id)
        if (right := next((sibling for sibling in siblings if sibling.slot == Page.RIGHT_HALF), None)) is not None:
            return Halves(left=left, right=right, left_changes=left != page, right_is_new=False)
        upper = await self._uow.pages.neighbour_key(self._project.id, left.order_key, Side.AFTER)
        moment = self._clock.now()
        right = Page(
            id=PageId(uuid4()),
            project_id=self._project.id,
            order_key=self._order_keys.between(lower=left.order_key, upper=upper),
            origin=PageOrigin.SCAN,
            scan_id=page.scan_id,
            slot=Page.RIGHT_HALF,
            created_at=moment,
            updated_at=moment,
        )
        return Halves(left=left, right=right, left_changes=left != page, right_is_new=True)

    def _template(self, half: Page, processor: Processor, params: MetadataMap, edit: PageEdit | None) -> PageVersion:
        """Build the version a half would have, which is running and has no files yet.

        :param half: The page of the half.
        :type half: Page
        :param processor: The processor that splits the scan.
        :type processor: Processor
        :param params: Parameters of the step after the check.
        :type params: MetadataMap
        :param edit: Manual cut line of the step, or None.
        :type edit: PageEdit | None
        :returns: The version, whose identifier follows from what it depends on.
        :rtype: PageVersion
        """
        edit_hash = '' if edit is None else edit.edit_hash
        inputs = VersionInputs(
            page_id=half.id, processor=processor.spec.ref, params=params, input_id=None, edit_hash=edit_hash
        )
        return PageVersion(
            id=inputs.identify(),
            page_id=half.id,
            stage=Stage.PAGE_SPLIT,
            processor=processor.spec.ref,
            params=params,
            renditions=None,
            state=VersionState.RUNNING,
            scale=VersionScale.FULL,
            edit_hash=edit_hash,
            created_at=self._clock.now(),
        )

    async def _cached(self, stored: Sequence[PageVersion | None]) -> list[PageVersion] | None:
        """Find the versions an earlier run made of the same scan, step, parameters and edit.

        A run that kept the scan whole made one version, so the right half has none and the run is still complete.

        :param stored: The version of each half's identifier an earlier run stored, or None.
        :type stored: Sequence[PageVersion | None]
        :returns: The versions, with the pyramid of each cut, or None when the step has to run.
        :rtype: list[PageVersion] | None
        """
        left = stored[0]
        if left is None or left.state is not VersionState.READY:
            return None
        count = SplitChoice.ONE_PAGE if left.data.get(VersionData.PAGES) == SplitChoice.ONE_PAGE else len(stored)
        wanted = [version for version in stored[:count] if version is not None]
        if len(wanted) != count or any(version.state is not VersionState.READY for version in wanted):
            return None
        return [await self._tiled(version) for version in wanted]

    async def _make(self, templates: Sequence[PageVersion], run: StepRun) -> list[PageVersion]:
        """Run the step once and store the files of each page it made, with the tile pyramid.

        :param templates: The versions of the left and the right half.
        :type templates: Sequence[PageVersion]
        :param run: What the step reads.
        :type run: StepRun
        :returns: The versions as ready, two for a scan that was split and one for a scan kept whole.
        :rtype: list[PageVersion]
        :raises ConflictError: If the step makes neither one output nor two.
        :raises DomainError: If the step cannot run on this input.
        """
        async with self._runner.execute(run) as result:
            outputs: list[StepOutput] = list(result.outputs)
            if not 0 < len(outputs) <= len(templates):
                raise ConflictError(WRONG_HALVES.format(key=run.processor_key, count=len(outputs)))
            return [
                await self._runner.store(self._keys, template, output, policy=self._project.image_policy, tiles=True)
                for template, output in zip(templates, outputs, strict=False)
            ]

    async def _fail(self, template: PageVersion, stored: PageVersion | None, reason: str) -> None:
        """Store a failed version on the page that ran the step, which tells the user why, and nothing else.

        :param template: The version the left half would have.
        :type template: PageVersion
        :param stored: The version of that identifier an earlier run stored, or None.
        :type stored: PageVersion | None
        :param reason: Why the step failed.
        :type reason: str
        """
        failed = evolve(
            template if stored is None else stored, state=VersionState.FAILED, data={VersionData.ERROR: reason}
        )
        await (self._uow.page_versions.add(failed) if stored is None else self._uow.page_versions.update(failed))
        await self._uow.commit()

    async def _tiled(self, version: PageVersion) -> PageVersion:
        """Cut the tile pyramid of a half that an earlier run made, if it was never cut.

        :param version: A ready version of a half.
        :type version: PageVersion
        :returns: The version with its pyramid cut.
        :rtype: PageVersion
        """
        return version if version.tiles_ready else await self._runner.cut_tiles(self._keys, version)

    async def _commit(
        self,
        halves: Halves,
        made: Sequence[PageVersion],
        stored: Sequence[PageVersion | None],
        recipe: Recipe,
        undoing: Unsplit | None,
    ) -> list[PageStage]:
        """Store the pages, the versions and the heads of a split in one commit.

        A scan that was split gets the new page, the change of the left page, and the versions and heads of both halves.
        A scan kept whole gets the version and head of its page, and the undoing of an earlier split.

        :param halves: The two pages of the spread.
        :type halves: Halves
        :param made: The versions of the left and the right half, ready, or of the whole page alone.
        :type made: Sequence[PageVersion]
        :param stored: The version of each identifier an earlier run stored, or None, which is replaced.
        :type stored: Sequence[PageVersion | None]
        :param recipe: The recipe that made the pages.
        :type recipe: Recipe
        :param undoing: The earlier split that keeping the scan whole undoes, or None.
        :type undoing: Unsplit | None
        :returns: The stage records that changed, which the caller announces.
        :rtype: list[PageStage]
        """
        pages = (halves.left, halves.right)[: len(made)]
        if len(made) > 1 and halves.right_is_new:
            await self._uow.pages.add(halves.right)
        if len(made) > 1 and halves.left_changes:
            await self._uow.pages.update(halves.left)
        if len(made) > 1 and halves.right_is_new:
            # The new half takes its place in the numbering, which moves the pages after it on
            await self._labels.recompute(self._project.id)
        for version, earlier in zip(made, stored, strict=False):
            await (self._uow.page_versions.add(version) if earlier is None else self._uow.page_versions.update(version))
        changed = [
            record
            for page, version in zip(pages, made, strict=True)
            for record in await self._records.set_head(
                PageStageKey(page.id, Stage.PAGE_SPLIT), head_version_id=version.id, recipe_id=recipe.id
            )
        ]
        if undoing is not None:
            await self.unsplit(undoing)
        await self._uow.commit()
        return changed

    async def _announce(
        self,
        halves: Halves,
        made: Sequence[PageVersion],
        stored: Sequence[PageVersion | None],
        changed: Sequence[PageStage],
    ) -> None:
        """Tell the browser what the split changed, after the commit.

        :param halves: The two pages of the spread.
        :type halves: Halves
        :param made: The versions of the left and the right half, ready, or of the whole page alone.
        :type made: Sequence[PageVersion]
        :param stored: The version of each identifier an earlier run stored, which were not made again when ready.
        :type stored: Sequence[PageVersion | None]
        :param changed: The stage records the split changed.
        :type changed: Sequence[PageStage]
        """
        if len(made) > 1 and halves.right_is_new:
            await self._publisher.publish(
                PagesChanged(project_id=self._project.id, page_ids=[halves.right.id], change=PageChange.ADDED)
            )
        for version, earlier in zip(made, stored, strict=False):
            if earlier is None or earlier.state is not VersionState.READY:
                await self._publisher.publish(PageVersionReady(project_id=self._project.id, version=version))
        await self._records.announce(self._project.id, changed)
        await self._labels.announce(self._project.id)
