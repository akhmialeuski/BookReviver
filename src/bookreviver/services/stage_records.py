"""The records of the current version of each stage of a page, and the rule that a changed stage makes later ones stale.

A page version never knows whether it is current: the ``PageStage`` record of its stage does. When the current version
of a stage changes, the later stages of the same page were computed from the old one, so their records are marked
stale and their versions stay, which lets the interface show the old result with a mark until the user computes it
again. A record that already failed stays failed, since a stale mark would hide the failure.

The class writes the records through the unit of work of its caller, which commits, and then announces them, so a
browser reads the stages it shows again.
"""

from typing import TYPE_CHECKING

from attrs import evolve

from bookreviver.domain.entities import PageStage
from bookreviver.domain.enums import Stage, StageState, ValueScope
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.values import PageStageKey

if TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from bookreviver.domain.ids import PageId, PageVersionId, ProjectId, RecipeId
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock, EventPublisher


class StageRecords:
    """Writes the stage records of pages, and announces the ones that changed."""

    def __init__(self, *, uow: UnitOfWork, publisher: EventPublisher, clock: Clock) -> None:
        """Write records through the unit of work of the use case.

        :param uow: Unit of work whose commit ends the use case.
        :type uow: UnitOfWork
        :param publisher: Publisher of the events the browser follows.
        :type publisher: EventPublisher
        :param clock: Clock stamping the records.
        :type clock: Clock
        """
        self._uow = uow
        self._publisher = publisher
        self._clock = clock

    async def set_head(
        self,
        key: PageStageKey,
        *,
        head_version_id: PageVersionId,
        recipe_id: RecipeId | None,
        through_step: int | None = None,
    ) -> Sequence[PageStage]:
        """Make a version the current one of a stage, and mark the later stages of the page stale if it changed.

        :param key: Page and stage whose record changes.
        :type key: PageStageKey
        :param head_version_id: Version that becomes the current one.
        :type head_version_id: PageVersionId
        :param recipe_id: Recipe the page was processed by, or None for a version no recipe made.
        :type recipe_id: RecipeId | None
        :param through_step: Index in the recipe of the step the version is the result of when the run stopped before
                             the last step that is on, or None when it went through all of them.
        :type through_step: int | None
        :returns: The records that changed, the stage first, then the later stages that became stale.
        :rtype: Sequence[PageStage]
        """
        previous = await self._uow.page_stages.find(key)
        record = PageStage(
            page_id=key.page_id,
            stage=key.stage,
            recipe_id=recipe_id,
            head_version_id=head_version_id,
            state=StageState.FRESH,
            through_step=through_step,
            updated_at=self._clock.now(),
        )
        await self._uow.page_stages.save(record)
        changed = [record]
        if previous is None or previous.head_version_id != head_version_id:
            changed.extend(await self._mark_later_stale(key.page_id, key.stage))
        return changed

    async def clear(self, key: PageStageKey) -> list[PageStage]:
        """Remove the record of a stage of a page that has no current version of its own any longer.

        The later stages of the page were computed from the version the record named, so they are marked stale, and the
        record of a stage the page never had is nothing to remove.

        :param key: Page and stage whose record is removed.
        :type key: PageStageKey
        :returns: The later stages that became stale.
        :rtype: list[PageStage]
        """
        if await self._uow.page_stages.find(key) is None:
            return []
        await self._uow.page_stages.delete(key)
        return await self._mark_later_stale(key.page_id, key.stage)

    async def mark_stale(self, page_id: PageId, stage: Stage) -> list[PageStage]:
        """Mark one stage of a page stale, because its recipe or a manual edit it reads changed.

        :param page_id: Page whose stage is marked.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :returns: The record if it changed, and none for a stage that has not run or is stale or failed already.
        :rtype: list[PageStage]
        """
        record = await self._uow.page_stages.find(PageStageKey(page_id, stage))
        if record is None or record.state is not StageState.FRESH:
            return []
        stale = evolve(record, state=StageState.STALE, updated_at=self._clock.now())
        await self._uow.page_stages.save(stale)
        return [stale]

    async def mark_group_stale(
        self, project_id: ProjectId, page_id: PageId, labels: Collection[str]
    ) -> list[PageStage]:
        """Mark the stages of a page stale in which a group it joined or left has values for a step.

        A page takes the values of its group over those of its side, so a page that changes its group runs with other
        parameters wherever one of the two groups has a value, and in no other stage.

        :param project_id: Project owning the page and the values.
        :type project_id: ProjectId
        :param page_id: Page whose group changed.
        :type page_id: PageId
        :param labels: The group the page was in and the one it is in now, either of which may be empty for none.
        :type labels: Collection[str]
        :returns: The records that became stale, none for a page that has not been through such a stage.
        :rtype: list[PageStage]
        """
        stale: list[PageStage] = []
        for record in await self._uow.page_stages.list_for_page(page_id):
            valued = await self._uow.step_values.list_for_stage(project_id, record.stage)
            if any(values.scope is ValueScope.GROUP and values.group_label in labels for values in valued):
                stale.extend(await self.mark_stale(page_id, record.stage))
        return stale

    async def mark_content_stale(self, page_id: PageId) -> list[PageStage]:
        """Mark every stage of a page after the page order stale, because what the page shows changed.

        A page is processed by the recipe of its kind, so a page whose kind changed is processed by other steps than the
        ones that made its current versions.

        :param page_id: Page whose kind changed.
        :type page_id: PageId
        :returns: The records that became stale, none for a page that has not been through such a stage or whose stages
                  are stale or failed already.
        :rtype: list[PageStage]
        """
        stale: list[PageStage] = []
        for record in await self._uow.page_stages.list_for_page(page_id):
            if record.stage.position > Stage.PAGE_ORDER.position:
                stale.extend(await self.mark_stale(page_id, record.stage))
        return stale

    async def mark_recipe_stale(self, recipe_id: RecipeId) -> list[PageStage]:
        """Mark the stage of every page a recipe processed stale, because the recipe changed.

        :param recipe_id: Recipe that changed.
        :type recipe_id: RecipeId
        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        stale: list[PageStage] = []
        for record in await self._uow.page_stages.list_for_recipe(recipe_id):
            stale.extend(await self.mark_stale(record.page_id, record.stage))
        return stale

    async def mark_failed(self, page_id: PageId, stage: Stage, *, recipe_id: RecipeId | None) -> PageStage:
        """Record that a stage failed on a page, keeping the version that was current before.

        :param page_id: Page whose stage failed.
        :type page_id: PageId
        :param stage: The stage.
        :type stage: Stage
        :param recipe_id: Recipe that failed.
        :type recipe_id: RecipeId | None
        :returns: The record in its new state.
        :rtype: PageStage
        """
        previous = await self._uow.page_stages.find(PageStageKey(page_id, stage))
        record = PageStage(
            page_id=page_id,
            stage=stage,
            recipe_id=recipe_id,
            head_version_id=None if previous is None else previous.head_version_id,
            state=StageState.FAILED,
            through_step=None if previous is None else previous.through_step,
            updated_at=self._clock.now(),
        )
        await self._uow.page_stages.save(record)
        return record

    async def announce(self, project_id: ProjectId, records: Sequence[PageStage]) -> None:
        """Publish one event for each record that changed, after the transaction that wrote them committed.

        :param project_id: Project owning the pages.
        :type project_id: ProjectId
        :param records: The records that changed.
        :type records: Sequence[PageStage]
        """
        for record in records:
            await self._publisher.publish(PageStageChanged(project_id=project_id, stage=record))

    async def _mark_later_stale(self, page_id: PageId, stage: Stage) -> list[PageStage]:
        """Mark every up-to-date stage after ``stage`` stale.

        :param page_id: Page whose later stages are marked.
        :type page_id: PageId
        :param stage: The stage that changed.
        :type stage: Stage
        :returns: The records that became stale.
        :rtype: list[PageStage]
        """
        stale = [
            evolve(later, state=StageState.STALE, updated_at=self._clock.now())
            for later in await self._uow.page_stages.list_for_page(page_id)
            if later.stage.position > stage.position and later.state is StageState.FRESH
        ]
        for record in stale:
            await self._uow.page_stages.save(record)
        return stale
