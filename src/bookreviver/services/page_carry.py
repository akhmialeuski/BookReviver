"""Carrying a setting one page changed for a step over to other pages.

The user changes a field of a step on one page, and the pages after it, the pages the user selected or every page the
step processes should have the same value. A carry-over takes the value the source page has for the field and writes it
as the setting of each target page, which touches no other field of the page. A page that has a value of its own for the
field is skipped and counted, unless the user asked to write over it, since that value was set for that page on purpose.
A page that has the value already is left as it is and is not counted as skipped.

The pages of the condition of the step are those the step processes (``AppliesTo``), and a page that the condition
leaves out passes the step unchanged, so a setting there would do nothing. The two conditions that depend on the colour
of the image, the pictures in colour and the pictures in black and white, are met by every picture here, since the
colour is known only to a run, and a setting on a picture of the other colour waits unused. The pages the user selected
are written as they are chosen.

All the changes are one batch of the history, from a carry-over, so one undo takes the value back from every page. The
carry-over marks the stage of each page that changed stale and processes nothing, like any change of a setting.
"""

from typing import TYPE_CHECKING

from bookreviver.domain.entities import PageStepState
from bookreviver.domain.enums import CarryScope, ChangeSource, ColorMode, PageOrigin, StepLayer
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.history import CarryOver
from bookreviver.services.page_batches import PageBatch
from bookreviver.services.projects import book_pages, owned_page
from bookreviver.services.recipes import find_step

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, Page, PageStage, Step
    from bookreviver.domain.ids import PageId, ProjectId
    from bookreviver.domain.values import CarryRequest, MetadataMap
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.processing import ProcessorCatalog
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.stage_records import StageRecords

SELECTED_NEEDS_PAGES: str = 'A carry-over to the selected pages names at least one page.'
# The colours a picture may have, over which a step for pictures of one colour is taken to meet every picture
PICTURE_COLORS: tuple[ColorMode, ...] = (ColorMode.COLOR, ColorMode.GRAY)


class CarryOverService:
    """Carries a setting of a page over to the following, the selected or the condition pages of its step."""

    def __init__(self, *, uow: UnitOfWork, catalogue: ProcessorCatalog, records: StageRecords, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends the carry-over.
        :type uow: UnitOfWork
        :param catalogue: The processors the application can run, which check the value on each target page.
        :type catalogue: ProcessorCatalog
        :param records: Writer of the stage records, which a changed setting marks stale.
        :type records: StageRecords
        :param clock: Clock stamping the states and the changes.
        :type clock: Clock
        """
        self._uow = uow
        self._catalogue = catalogue
        self._records = records
        self._clock = clock

    async def carry(self, actor: Actor, project_id: ProjectId, request: CarryRequest) -> CarryOver:
        """Write the value a page has for one field of a step as the setting of other pages, as one batch.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The source page, the stage, the step and the field, and the pages the value goes to.
        :type request: CarryRequest
        :returns: The batch, the changes written and the pages skipped for a value of their own.
        :rtype: CarryOver
        :raises NotFoundError: If the actor has no such project, the project has no such page, no recipe of the stage
                               has the step, its processor is not installed, or the source page does not change the
                               field.
        :raises InvalidParametersError: If the scope is the selected pages and none is named, or the value is out of
                                        range for the other fields of one of the target pages.
        """
        key, name = request.key, request.name
        await owned_page(self._uow, actor, project_id, key.page_id)
        step = await find_step(self._uow.recipes, project_id, key)
        source = await self._uow.page_step_states.find(key)
        if source is None or name not in source.params:
            raise NotFoundError(name)
        value = source.params[name]
        targets = await self._targets(project_id, request, step)
        stored = {
            state.page_id: state
            for state in await self._uow.page_step_states.list_for_step(
                [page.id for page in targets], key.stage, key.step_id
            )
        }
        moment = self._clock.now()
        processor = self._catalogue.get(step.processor_key)
        carried: list[tuple[PageStepState, MetadataMap]] = []
        skipped: list[PageId] = []
        for page in targets:
            state = stored.get(page.id) or PageStepState(
                page_id=page.id, stage=key.stage, step_id=key.step_id, updated_at=moment
            )
            own = name in state.params
            if own and state.params[name] == value:
                continue
            if own and not request.overwrite:
                skipped.append(page.id)
                continue
            processor.validate_params({**state.apply_to(step.params), name: value})
            carried.append((state, {**state.params, name: value}))
        batch = PageBatch(uow=self._uow, source=ChangeSource.CARRY_OVER, moment=moment)
        for state, params in carried:
            await batch.write(state, StepLayer.SETTINGS, params)
        changes = await batch.flush()
        stale: list[PageStage] = []
        for change in changes:
            stale.extend(await self._records.mark_stale(change.page_id, change.stage))
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return CarryOver(batch_id=batch.batch_id, changes=tuple(changes), skipped=tuple(skipped))

    async def _targets(self, project_id: ProjectId, request: CarryRequest, step: Step) -> Sequence[Page]:
        """Choose the pages a carry-over goes to, which are never the source page and never a placeholder.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param request: The carry-over, whose source page is left out and whose scope says which pages are chosen.
        :type request: CarryRequest
        :param step: The step, whose condition says which pages it processes.
        :type step: Step
        :returns: The pages in book order for the following pages and the condition pages, and as read for the selected
                  ones.
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
            candidates = [
                page
                for page in book
                if not page.is_leaf and any(step.applies_to.matches(page.kind, color) for color in PICTURE_COLORS)
            ]
        return [page for page in candidates if page.id != key.page_id and page.origin is not PageOrigin.PLACEHOLDER]
