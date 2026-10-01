"""Splitting a scan into the two pages of a spread, and undoing the split.

A processor of the scope ``split`` reads the scan of a page and makes one output for each part of it, the left half and
then the right half. The page that runs the step keeps its place and becomes the left half, slot 1, and the right half
is a page of its own, slot 2, inserted into the book right after it. Each half has a base version of the stage, which
holds the copy of its part of the scan, so the page stands on its own as any other. The identifier of a base version
comes from ``VersionInputs`` like that of every version, with the manual cut line of the step as the edit, so a changed
line gives new versions of both halves and the old line finds the old ones again.

The right half is made once. Splitting again finds its page by the scan and the slot and gives it new versions, and a
stage run that goes by the pages of a book leaves the right halves to the run of their left halves.

Undoing a split is running a step of the scope ``page`` on the stage, such as ``split.none``, on the left half. It makes
the left half the whole scan again and deletes the right half, with its versions and files, and so the work on it. A
run that was not confirmed by the user leaves the page failed with the reason and deletes nothing.

The class reads and writes through the unit of work of the run, which commits after each step of its own, and it
announces what changed after the commit.
"""

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import Page, PageVersion, VersionInputs
from bookreviver.domain.enums import PageChange, PageOrigin, Side, Stage, VersionData, VersionScale, VersionState
from bookreviver.domain.errors import ConflictError, DomainError
from bookreviver.domain.events import PagesChanged, PageVersionReady
from bookreviver.domain.ids import PageId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import PageEditKey
from bookreviver.services.steps import StepRun

if TYPE_CHECKING:
    from bookreviver.domain.entities import PageEdit, Project
    from bookreviver.domain.ids import RecipeId
    from bookreviver.domain.values import MetadataMap, Step
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import Processor, StepOutput
    from bookreviver.services.stage_records import StageRecords
    from bookreviver.services.stage_runs import StageRuntime, StepSource

UNSPLIT_NOT_CONFIRMED: str = (
    'Undoing the split deletes page {page_id}, the right half of the spread, with its versions. Confirm it to go on.'
)
WRONG_HALVES: str = 'The step {key} made {count} outputs, and a split into the halves of a spread makes two.'
UNEXPECTED_FAILURE: str = 'The split failed because of an unexpected error. It has been logged.'

logger = logging.getLogger(__name__)


class SpreadSplit:
    """Applies a step that splits a scan to the page of the scan, and undoes the split."""

    def __init__(self, *, project: Project, uow: UnitOfWork, runtime: StageRuntime, records: StageRecords) -> None:
        """Work over the ports of one job.

        :param project: Project owning the pages, whose image policy chooses the format of a version's ``full``.
        :type project: Project
        :param uow: Unit of work, committed after each step.
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
        self._keys = ProjectKeys(project.id)

    async def split(self, page: Page, recipe_id: RecipeId, step: Step, source: StepSource) -> PageVersion | None:
        """Split the scan of a page into two pages, and make the base version of each half current.

        :param page: The page that runs the step, which becomes the left half.
        :type page: Page
        :param recipe_id: Recipe the step belongs to, which both halves record as the one that made their heads.
        :type recipe_id: RecipeId
        :param step: The step that splits the scan.
        :type step: Step
        :param source: The scan the step reads.
        :type source: StepSource
        :returns: The base version of the left half, which the caller makes current, or None when the step failed.
        :rtype: PageVersion | None
        :raises NotFoundError: If the processor is not in the catalogue.
        :raises InvalidParametersError: If the parameters do not fit the processor.
        """
        processor = self._catalogue.get(step.processor_key)
        params = processor.validate_params(step.params)
        edit = await self._uow.page_edits.find(PageEditKey(page.id, Stage.PAGE_SPLIT, step.processor_key))
        left, right = await self._halves(page)
        halves = await self._base_versions(
            processor,
            params,
            edit,
            (left, right),
            StepRun(
                processor_key=step.processor_key,
                params=params,
                input_data=source.data,
                image=source.image,
                edit=edit,
            ),
        )
        if halves is None:
            return None
        made = [await self._tiled(version) for version in halves]
        changed = await self._records.set_head(
            right.id, Stage.PAGE_SPLIT, head_version_id=made[1].id, recipe_id=recipe_id
        )
        await self._uow.commit()
        await self._records.announce(self._project.id, changed)
        return made[0]

    async def _tiled(self, version: PageVersion) -> PageVersion:
        """Cut the tile pyramid of a current version, unless it has been cut.

        :param version: A ready version of a half.
        :type version: PageVersion
        :returns: The version with its pyramid cut and stored.
        :rtype: PageVersion
        """
        if version.tiles_ready:
            return version
        cut = await self._runner.cut_tiles(self._keys, version)
        await self._uow.page_versions.update(cut)
        return cut

    async def unsplit(self, page: Page, *, confirmed: bool) -> Page:
        """Make a left half the whole scan again, deleting the right half, which the user has to confirm.

        :param page: The page that runs a step that does not split the scan.
        :type page: Page
        :param confirmed: Whether the user confirmed that the right half is deleted.
        :type confirmed: bool
        :returns: The page as the whole scan, or the page unchanged when it is no left half of a split spread.
        :rtype: Page
        :raises ConflictError: If a right half is to be deleted and the user did not confirm it.
        """
        if page.slot != Page.LEFT_HALF or page.scan_id is None:
            return page
        rights = [sibling for sibling in await self._uow.pages.list_for_scan(page.scan_id) if sibling.slot > page.slot]
        if rights and not confirmed:
            raise ConflictError(UNSPLIT_NOT_CONFIRMED.format(page_id=rights[0].id))
        for right in rights:
            await self._uow.pages.delete(right.id)
        whole = evolve(page, slot=Page.WHOLE_SCAN, updated_at=self._clock.now())
        await self._uow.pages.update(whole)
        await self._uow.commit()
        for right in rights:
            await self._runner.discard(self._keys.page(right.id))
        if rights:
            await self._publisher.publish(
                PagesChanged(
                    project_id=self._project.id, page_ids=[right.id for right in rights], change=PageChange.REMOVED
                )
            )
        return whole

    async def _halves(self, page: Page) -> tuple[Page, Page]:
        """Find the two pages of a spread, making the left half of the page and the right half if it has none.

        :param page: The page that runs the step.
        :type page: Page
        :returns: The left half and the right half, both stored.
        :rtype: tuple[Page, Page]
        :raises ConflictError: If the page has no scan.
        """
        if page.scan_id is None:
            raise ConflictError(page.id)
        left = page
        if page.slot == Page.WHOLE_SCAN:
            left = evolve(page, slot=Page.LEFT_HALF, updated_at=self._clock.now())
            await self._uow.pages.update(left)
        siblings = await self._uow.pages.list_for_scan(page.scan_id)
        if (right := next((sibling for sibling in siblings if sibling.slot == Page.RIGHT_HALF), None)) is not None:
            return left, right
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
        await self._uow.pages.add(right)
        await self._uow.commit()
        await self._publisher.publish(
            PagesChanged(project_id=self._project.id, page_ids=[right.id], change=PageChange.ADDED)
        )
        return left, right

    async def _base_versions(
        self,
        processor: Processor,
        params: MetadataMap,
        edit: PageEdit | None,
        halves: tuple[Page, Page],
        run: StepRun,
    ) -> tuple[PageVersion, PageVersion] | None:
        """Find the base versions of the two halves, or run the step and store them.

        :param processor: The processor that splits the scan.
        :type processor: Processor
        :param params: Parameters of the step after the check.
        :type params: MetadataMap
        :param edit: Manual cut line of the step, or None.
        :type edit: PageEdit | None
        :param halves: The left half and the right half.
        :type halves: tuple[Page, Page]
        :param run: What the step reads.
        :type run: StepRun
        :returns: The versions of the halves as ready, or None when the step failed.
        :rtype: tuple[PageVersion, PageVersion] | None
        """
        edit_hash = '' if edit is None else edit.edit_hash
        pending: list[PageVersion] = []
        for half in halves:
            inputs = VersionInputs(
                page_id=half.id, processor=processor.spec.ref, params=params, input_id=None, edit_hash=edit_hash
            )
            existing = await self._uow.page_versions.find(inputs.identify())
            if existing is not None and existing.state is VersionState.READY:
                pending.append(existing)
                continue
            version = PageVersion(
                id=inputs.identify(),
                page_id=half.id,
                stage=Stage.PAGE_SPLIT,
                processor=processor.spec.ref,
                params=params,
                renditions=None,
                state=VersionState.RUNNING,
                scale=VersionScale.FULL,
                edit_hash=edit_hash,
                created_at=self._clock.now() if existing is None else existing.created_at,
            )
            await (
                self._uow.page_versions.add(version) if existing is None else self._uow.page_versions.update(version)
            )
            pending.append(version)
        if all(version.state is VersionState.READY for version in pending):
            return pending[0], pending[1]
        await self._uow.commit()
        try:
            made = await self._execute(pending, run)
        except DomainError as error:
            made = [
                evolve(version, state=VersionState.FAILED, data={VersionData.ERROR: str(error)}) for version in pending
            ]
        except Exception:
            logger.exception('The split %s of page %s failed', run.processor_key, halves[0].id)
            made = [
                evolve(version, state=VersionState.FAILED, data={VersionData.ERROR: UNEXPECTED_FAILURE})
                for version in pending
            ]
        for version in made:
            await self._uow.page_versions.update(version)
        await self._uow.commit()
        if any(version.state is not VersionState.READY for version in made):
            return None
        for version in made:
            await self._publisher.publish(PageVersionReady(project_id=self._project.id, version=version))
        return made[0], made[1]

    async def _execute(self, pending: list[PageVersion], run: StepRun) -> list[PageVersion]:
        """Run the step once and store each half under the version that waits for it.

        A half that was found ready is kept as it is, and only the others are stored.

        :param pending: The versions of the left and the right half, some of them ready.
        :type pending: list[PageVersion]
        :param run: What the step reads.
        :type run: StepRun
        :returns: The versions as ready.
        :rtype: list[PageVersion]
        :raises ConflictError: If the step makes other than two outputs.
        :raises DomainError: If the step cannot run on this input.
        """
        async with self._runner.execute(run) as result:
            if len(result.outputs) != len(pending):
                raise ConflictError(WRONG_HALVES.format(key=run.processor_key, count=len(result.outputs)))
            outputs: list[StepOutput] = list(result.outputs)
            return [
                version
                if version.state is VersionState.READY
                else await self._runner.store(
                    self._keys, version, output, policy=self._project.image_policy, tiles=False
                )
                for version, output in zip(pending, outputs, strict=True)
            ]
