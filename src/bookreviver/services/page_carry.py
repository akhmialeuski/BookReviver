"""Carrying the shape a page has set by hand for a step over to other pages.

The user sets the shape of a step by hand on one page, and the pages after it, the pages the user selected or every
page the step processes should have the same. A carry-over takes the manual edit of the source page, the whole shape and
not a part of it, and writes it as the manual edit of each target page, which touches the settings of the page not at
all. A page that has a shape of its own set by hand is skipped and counted, unless the user asked to write over it,
since that shape was set for that page on purpose. A page that has the shape already is left as it is and is not
counted as skipped. An edit that is a mask, such as the strokes of an eraser, belongs to the picture of one page and is
not carried.

The pages of the same kind are those of the kind of the page the shape is taken from, which are processed by the same
recipe. A page that shows a leaf the program drew passes every step unchanged, so a shape there would do nothing. The
pages the user selected are written as they are chosen.

All the changes are one batch of the history, from a carry-over, so one undo takes the shape back from every page. The
carry-over marks the stage of each page that changed stale and processes nothing, like any change of an edit.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.entities import PageStepState
from bookreviver.domain.enums import CarryScope, ChangeSource, PageOrigin, StepLayer
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.history import CarryOver
from bookreviver.services.page_batches import PageBatch
from bookreviver.services.projects import book_pages, owned_page
from bookreviver.services.recipes import find_step

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Page, PageStage
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.values import CarryRequest
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.stage_records import StageRecords

SELECTED_NEEDS_PAGES: str = 'A carry-over to the selected pages names at least one page.'
MASK_NOT_CARRIED: str = 'The edit of this step is a mask painted on one page, which no other page can take.'


class CarryOverService:
    """Carries the shape a page has set by hand over to the following, the selected or the same kind pages."""

    def __init__(self, *, uow: UnitOfWork, records: StageRecords, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends the carry-over.
        :type uow: UnitOfWork
        :param records: Writer of the stage records, which a changed edit marks stale.
        :type records: StageRecords
        :param clock: Clock stamping the states and the changes.
        :type clock: Clock
        """
        self._uow = uow
        self._records = records
        self._clock = clock

    async def carry(self, actor: Actor, project_id: ProjectId, request: CarryRequest) -> CarryOver:
        """Write the shape a page has set by hand for a step to other pages, as one batch.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The source page, the stage and the step, and the pages the shape goes to.
        :type request: CarryRequest
        :returns: The batch, the changes written and the pages skipped for a shape of their own.
        :rtype: CarryOver
        :raises NotFoundError: If the actor has no such project, the project has no such page, no recipe of the stage
                               has the step, or the source page has no shape set by hand.
        :raises InvalidParametersError: If the scope is the selected pages and none is named, or the edit is a mask.
        """
        key = request.key
        source = await owned_page(self._uow, actor, project_id, key.page_id)
        await find_step(self._uow.recipes, project_id, key)
        stored_source = await self._uow.page_step_states.find(key)
        if stored_source is None or stored_source.edit is None:
            raise NotFoundError(key)
        if stored_source.edit.mask_key is not None:
            raise InvalidParametersError(MASK_NOT_CARRIED)
        shape = stored_source.layer(StepLayer.HAND)
        targets = await self._targets(project_id, request, source)
        stored = {
            state.page_id: state
            for state in await self._uow.page_step_states.list_for_step(
                [page.id for page in targets], key.stage, key.step_id
            )
        }
        moment = self._clock.now()
        carried: list[PageStepState] = []
        skipped: list[PageId] = []
        for page in targets:
            state = stored.get(page.id) or PageStepState(
                page_id=page.id, stage=key.stage, step_id=key.step_id, updated_at=moment
            )
            if state.layer(StepLayer.HAND) == shape:
                continue
            if state.edit is not None and not request.overwrite:
                skipped.append(page.id)
                continue
            carried.append(state)
        batch = PageBatch(uow=self._uow, source=ChangeSource.CARRY_OVER, moment=moment)
        for state in carried:
            await batch.write(state, StepLayer.HAND, shape)
        changes = await batch.flush()
        stale: list[PageStage] = []
        for change in changes:
            stale.extend(await self._records.mark_stale(change.page_id, change.stage))
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return CarryOver(batch_id=batch.batch_id, changes=tuple(changes), skipped=tuple(skipped))

    async def _targets(self, project_id: ProjectId, request: CarryRequest, source: Page) -> Sequence[Page]:
        """Choose the pages a carry-over goes to, which are never the source page and never a placeholder.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The carry-over, whose source page is left out and whose scope says which pages are chosen.
        :type request: CarryRequest
        :param source: The page the shape is taken from, whose kind says which pages the step processes for it.
        :type source: Page
        :returns: The pages in book order for the following pages and the pages of the kind, and as read for the
                  selected ones.
        :rtype: Sequence[Page]
        :raises NotFoundError: If a selected page is not one of the project.
        :raises InvalidParametersError: If the scope is the selected pages and none is named.
        """
        key = request.key
        if request.scope is CarryScope.SELECTED:
            if not request.page_ids:
                raise InvalidParametersError(SELECTED_NEEDS_PAGES)
            candidates = await self._uow.pages.list_by_ids(project_id, request.page_ids)
        else:
            book = await book_pages(self._uow.pages, project_id)
            if request.scope is CarryScope.FOLLOWING:
                book = book[next((index for index, page in enumerate(book) if page.id == key.page_id), -1) + 1 :]
            candidates = [page for page in book if not page.is_leaf and page.recipe_kind is source.recipe_kind]
        return [page for page in candidates if page.id != key.page_id and page.origin is not PageOrigin.PLACEHOLDER]
