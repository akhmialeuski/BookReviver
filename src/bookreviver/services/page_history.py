"""The history of a step on a page, and the undo that takes its changes back.

Every change of a layer of a step on a page is in the history, written by the use case that made it. An undo is a use
case of its own: it writes the content a change replaced back into the layer, as a new change whose source is an undo
and which names the change it takes back, so the history only grows and shows what the undo did. Taking a change back
needs the layer to hold what the change left in it, since writing ``before`` over a layer that has changed since would
lose the later change. A change of a batch is taken back with the rest of its batch, on every page the batch reached,
and the undos of one batch share a batch of their own, so the undo of a batch is one action as well.

Taking back marks the stage of each page that changed stale and processes nothing, like the changes it undoes.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import PageStepChange, PageStepState
from bookreviver.domain.enums import ChangeSource
from bookreviver.domain.errors import ConflictError
from bookreviver.domain.history import StepHistory
from bookreviver.domain.ids import ChangeBatchId
from bookreviver.services.projects import owned_page

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.entities import Actor, PageStage
    from bookreviver.domain.ids import PageId, PageStepChangeId, ProjectId
    from bookreviver.domain.values import PageStepKey
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.services.stage_records import StageRecords

CHANGED_SINCE: str = 'The layer {layer} of a step changed after this change, so it cannot be taken back on its own.'


class PageHistoryService:
    """Lists the history of the steps of the acting account's pages and takes its changes back."""

    def __init__(self, *, uow: UnitOfWork, records: StageRecords, clock: Clock) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose commit ends every changing use case.
        :type uow: UnitOfWork
        :param records: Writer of the stage records, which an undo marks stale.
        :type records: StageRecords
        :param clock: Clock stamping the states and the undos.
        :type clock: Clock
        """
        self._uow = uow
        self._records = records
        self._clock = clock

    async def list(self, actor: Actor, project_id: ProjectId, key: PageStepKey) -> StepHistory:
        """Return the history of one step on one page.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The changes of the step, oldest first, with which of them stand.
        :rtype: StepHistory
        :raises NotFoundError: If the actor has no such project, or the project has no such page.
        """
        await owned_page(self._uow, actor, project_id, key.page_id)
        changes = await self._uow.page_step_changes.list_for_page(key.page_id, key.stage)
        return StepHistory(tuple(change for change in changes if change.step_id == key.step_id))

    async def undo(
        self, actor: Actor, project_id: ProjectId, key: PageStepKey, change_id: PageStepChangeId | None
    ) -> Sequence[PageStepChange]:
        """Take back the newest change of a step on a page, or every change back to a chosen one.

        A change of a batch brings the rest of its batch with it, whatever page the rest is on.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :param change_id: The oldest change to take back, which takes back every change after it as well, or None for
                          the newest change alone.
        :type change_id: PageStepChangeId | None
        :returns: The undos that were written, which are none when the step has no change to take back.
        :rtype: Sequence[PageStepChange]
        :raises NotFoundError: If the actor has no such project, the project has no such page, or the step has no
                               standing change with this identifier.
        :raises ConflictError: If a layer changed after the change that is taken back, which a later change of the
                               same page or of a page of the batch did.
        """
        history = await self.list(actor, project_id, key)
        chosen = history.standing[-1:] if change_id is None else history.back_to(change_id)
        targets = await self._with_batches(project_id, chosen)
        if not targets:
            return ()
        moment = self._clock.now()
        batch = ChangeBatchId(uuid4()) if len(targets) > 1 else None
        stored: dict[PageStepKey, PageStepState | None] = {}
        working: dict[PageStepKey, PageStepState] = {}
        written: list[PageStepChange] = []
        for target in targets:
            if target.key not in working:
                stored[target.key] = await self._uow.page_step_states.find(target.key)
                working[target.key] = stored[target.key] or PageStepState(
                    page_id=target.page_id, stage=target.stage, step_id=target.step_id, updated_at=moment
                )
            state = working[target.key]
            if state.layer(target.layer) != target.after:
                raise ConflictError(CHANGED_SINCE.format(layer=target.layer.label))
            reverted = state.with_layer(target.layer, target.before, moment)
            working[target.key] = reverted
            written.append(
                evolve(
                    PageStepChange.between(state, reverted, target.layer, ChangeSource.UNDO),
                    batch_id=batch,
                    undoes=target.id,
                )
            )
        for state_key, final in working.items():
            if not final.is_empty:
                await self._uow.page_step_states.save(final)
            elif stored[state_key] is not None:
                await self._uow.page_step_states.delete(state_key)
        added = await self._uow.page_step_changes.add_many(written)
        stale: list[PageStage] = []
        for page_id, stage in dict.fromkeys((change.page_id, change.stage) for change in added):
            stale.extend(await self._records.mark_stale(page_id, stage))
        await self._uow.commit()
        await self._records.announce(project_id, stale)
        return added

    async def _with_batches(self, project_id: ProjectId, chosen: Sequence[PageStepChange]) -> Sequence[PageStepChange]:
        """Add the rest of the batch of each chosen change, and keep the changes that still stand.

        :param project_id: Identifier of the project, whose pages the batches must all be on.
        :type project_id: ProjectId
        :param chosen: The standing changes of the step that are taken back.
        :type chosen: Sequence[PageStepChange]
        :returns: The changes to take back, newest first within each page.
        :rtype: Sequence[PageStepChange]
        :raises NotFoundError: If a batch reaches a page that is not one of the project.
        """
        members = {change.id: change for change in chosen}
        for batch_id in {change.batch_id for change in chosen if change.batch_id is not None}:
            for member in await self._uow.page_step_changes.list_for_batch(batch_id):
                members.setdefault(member.id, member)
        undone = {undo.undoes for undo in await self._uow.page_step_changes.list_undoing(list(members))}
        standing = [
            change for change in members.values() if change.source is not ChangeSource.UNDO and change.id not in undone
        ]
        pages: set[PageId] = {change.page_id for change in standing}
        if pages:
            await self._uow.pages.list_by_ids(project_id, pages)
        return sorted(standing, key=lambda change: (change.page_id, -change.sequence))
