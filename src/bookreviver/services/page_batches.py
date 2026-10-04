"""One batch of changes to a layer of the state of several pages, which the history takes back in one action.

A run that takes the work of the pages away, a carry-over of a setting to other pages and a reset to the defaults write
the same thing: a layer of the state of a step on many pages, each change in the history with the same batch and the
source that made it. A state with neither a setting nor an edit left is deleted. The batch commits nothing and marks no
stage stale, which the use case that owns the transaction does once the batch is flushed.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import PageStepChange
from bookreviver.domain.ids import ChangeBatchId

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.entities import PageStepState
    from bookreviver.domain.enums import ChangeSource, StepLayer
    from bookreviver.domain.values import MetadataMap
    from bookreviver.ports.persistence import UnitOfWork


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

    async def flush(self) -> Sequence[PageStepChange]:
        """Add the changes of the batch to the history, which numbers them, and forget them.

        :returns: The changes as stored, in the order they were written.
        :rtype: Sequence[PageStepChange]
        """
        added = await self._uow.page_step_changes.add_many(self._changes)
        self._changes = []
        return added
