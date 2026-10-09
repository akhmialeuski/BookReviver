"""One batch of changes to the settings and the edits of several pages, which the history takes back in one action.

A run that takes the work of the pages away, a value set for several pages, a value set for the odd pages, the even
pages or a group, and a carry-over of a shape write the same thing: a layer of a step on many pages, each change in the
history with the same batch and the source that made it. A state with neither a setting nor an edit left is deleted.
The batch commits nothing and marks no stage stale, which the use case that owns the transaction does once the batch is
flushed.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import PageStepChange
from bookreviver.domain.enums import StepLayer
from bookreviver.domain.ids import ChangeBatchId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.entities import PageStepState
    from bookreviver.domain.enums import ChangeSource
    from bookreviver.domain.ids import PageId
    from bookreviver.domain.step_values import StepValues
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork


# The layers a page changes for a step, which a run that drops the work empties, in the order the history of the page
# records them
WORK_LAYERS: tuple[StepLayer, ...] = (StepLayer.SETTINGS, StepLayer.HAND)


class PageBatch:
    """Writes a layer of the state of a step on several pages and collects the changes it makes under one batch.

    :ivar batch_id: The batch every change written through this object belongs to.
    """

    def __init__(self, *, uow: UnitOfWork, source: ChangeSource, moment: datetime) -> None:
        """Write through a unit of work, as one source, at one moment.

        :param uow: Unit of work of the use case, whose commit makes the batch durable.
        :type uow: UnitOfWork
        :param source: What makes the changes of the batch.
        :type source: ChangeSource
        :param moment: When the layers are set, which stamps the states and the changes.
        :type moment: datetime
        """
        self.batch_id = ChangeBatchId(uuid4())
        self._uow = uow
        self._source = source
        self._moment = moment
        self._changes: list[PageStepChange] = []

    async def write(self, state: PageStepState, layer: StepLayer, content: MetadataMap | None) -> PageStepState:
        """Set a layer of the state of a step on a page, storing the state and keeping the change.

        A use case that sets several layers of one state passes each call the state the call before returned.

        :param state: The state as it is stored, or an empty one for a page that has none yet.
        :type state: PageStepState
        :param layer: The layer to set.
        :type layer: StepLayer
        :param content: The content the layer has from now on, or None to empty it.
        :type content: MetadataMap | None
        :returns: The state with the layer set, which is ``state`` itself when the layer already held the content and
                  nothing is written. A state with nothing left is deleted, and is returned empty.
        :rtype: PageStepState
        :raises ConflictError: For the layer of what the automatic run found, which no state keeps yet.
        """
        changed = state.with_layer(layer, content, self._moment)
        if changed.layer(layer) == state.layer(layer):
            return state
        if changed.is_empty:
            await self._uow.page_step_states.delete(state.key)
        else:
            await self._uow.page_step_states.save(changed)
        self._changes.append(
            evolve(PageStepChange.between(state, changed, layer, self._source), batch_id=self.batch_id)
        )
        return changed

    async def write_values(self, values: StepValues, params: MetadataMap, pages: Sequence[PageId]) -> StepValues:
        """Set the fields the odd pages, the even pages or a group change for a step, and keep the change for each page.

        The values are stored once, and the history of each page that takes them gets a change, so the page shows the
        change and an undo from any of them takes it back.

        :param values: The values as they are stored, or empty ones for a part of the pages that has none yet.
        :type values: StepValues
        :param params: The fields the part changes from now on, which are none to take them all back.
        :type params: MetadataMap
        :param pages: The pages whose history keeps the change, which are those the change reaches.
        :type pages: Sequence[PageId]
        :returns: The values after the change, which are ``values`` itself when they already held ``params`` and nothing
                  is written. Values with no field left are deleted, and are returned empty.
        :rtype: StepValues
        """
        if params == values.params:
            return values
        changed = evolve(values, params=params, updated_at=self._moment)
        if changed.is_empty:
            await self._uow.step_values.delete(values.key)
        else:
            await self._uow.step_values.save(changed)
        self._changes.extend(
            evolve(PageStepChange.of_values(page_id, values, changed, self._source), batch_id=self.batch_id)
            for page_id in pages
        )
        return changed

    async def clear_work(self, state: PageStepState) -> PageStepState:
        """Empty the settings and the manual edit of the state, which is the work of the page for the step.

        :param state: The state as it is stored.
        :type state: PageStepState
        :returns: The state with both layers empty, which is deleted when nothing else is left in it.
        :rtype: PageStepState
        """
        for layer in WORK_LAYERS:
            state = await self.write(state, layer, None)
        return state

    async def flush(self) -> Sequence[PageStepChange]:
        """Add the changes of the batch to the history, which numbers them, and forget them.

        :returns: The changes as stored, in the order they were written.
        :rtype: Sequence[PageStepChange]
        """
        added = await self._uow.page_step_changes.add_many(self._changes)
        self._changes = []
        return added
