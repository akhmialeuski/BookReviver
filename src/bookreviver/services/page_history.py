"""The history of a step on a page, and the undo that takes its changes back.

Every change of a layer of a step on a page is in the history, written by the use case that made it. An undo is a use
case of its own: it writes the content a change replaced back into the layer, as a new change whose source is an undo
and which names the change it takes back, so the history only grows and shows what the undo did. Taking a change back
needs the layer to hold what the change left in it, since writing ``before`` over a layer that has changed since would
lose the later change. A change of a batch is taken back with the rest of its batch, on every page the batch reached,
and the undos of one batch share a batch of their own, so the undo of a batch is one action as well.

Taking back marks the stage of each page that changed stale and processes nothing, like the changes it undoes.

A clear is the one way a history shrinks: it takes the settings and the edit of a step away from one page, deletes that
step's history on that page, and deletes the results of the step on the page with every result that read them, so the
step is back to the state it had before it first ran or changed there, and nothing of it can be undone any more. Other
pages and the steps that did not read the cleared one are untouched.
"""

import logging
from typing import TYPE_CHECKING
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.entities import PageStepChange, PageStepState
from bookreviver.domain.enums import ChangeSource, ValueScope
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.history import ClearedStep, StepHistory
from bookreviver.domain.ids import ChangeBatchId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.step_values import StepValues, StepValuesKey
from bookreviver.domain.version_chains import StepVersions, step_places
from bookreviver.services.processing_parts import PROJECT_BUSY
from bookreviver.services.projects import book_pages, owned_page

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

    from bookreviver.domain.entities import Actor, PageStage, Recipe
    from bookreviver.domain.ids import PageId, PageStepChangeId, PageVersionId, ProjectId
    from bookreviver.domain.values import PageStepKey
    from bookreviver.ports.persistence import UnitOfWork
    from bookreviver.ports.runtime import Clock
    from bookreviver.ports.storage import AssetStore
    from bookreviver.services.processing_parts import JobStarter
    from bookreviver.services.stage_records import StageRecords

logger = logging.getLogger(__name__)

CHANGED_SINCE: str = 'The layer {layer} of a step changed after this change, so it cannot be taken back on its own.'


class PageHistoryService:
    """Lists the history of the steps of the acting account's pages, takes its changes back, and clears a step."""

    def __init__(
        self, *, uow: UnitOfWork, records: StageRecords, starter: JobStarter, assets: AssetStore, clock: Clock
    ) -> None:
        """Work over the ports of one request.

        :param uow: Unit of work of the request, whose blocks end every changing use case.
        :type uow: UnitOfWork
        :param records: Writer of the stage records, which an undo marks stale.
        :type records: StageRecords
        :param starter: Queuer of the jobs, which cuts the pyramid of the version a clear makes current.
        :type starter: JobStarter
        :param assets: Store of the derived files, which the versions a clear deletes leave.
        :type assets: AssetStore
        :param clock: Clock stamping the states and the undos.
        :type clock: Clock
        """
        self._uow = uow
        self._records = records
        self._starter = starter
        self._assets = assets
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
        async with self._uow.change_book(project_id):
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
                if target.scope is not ValueScope.PAGES:
                    continue
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
            undone, parts_stale = await self._take_parts_back(project_id, targets, moment, batch)
            written.extend(undone)
            added = await self._uow.page_step_changes.add_many(written)
            stale = [*parts_stale]
            for page_id, stage in dict.fromkeys((change.page_id, change.stage) for change in added):
                stale.extend(await self._records.mark_stale(page_id, stage))
        await self._records.announce(project_id, stale)
        return added

    async def clear(self, actor: Actor, project_id: ProjectId, key: PageStepKey) -> ClearedStep:
        """Return a step to its initial state on a page: delete its history, its settings, its edit and its results.

        The results of the step on the page go with the versions that read them, directly or through a chain, whatever
        their stage, and with the marks and comments of those versions. The stage of the page then stands on the version
        the step read, marked stale, or has no current version when the step is the first of its recipe. A later stage
        whose current version went has no record any longer, and the later stages of the page are marked stale. The
        versions of other pages, of steps that did not read the step, and the changes of a batch that reached other
        pages stay, and an undo of such a batch goes on working, since it takes back only the changes it finds.

        The version the stage stands on was made by a step in the middle of the recipe, which cuts no pyramid, and the
        page is shown by the pyramid of the current version of its stage. So the cutting of that pyramid is queued once
        the block of the clear has ended, which a project busy with another tile cutting or collection leaves undone.

        A preview of the project, queued or running, is cancelled first, since the reader asked for the clear and a
        preview is disposable: the editor of a step asks for one by itself when the step is opened, so a clear a moment
        later would otherwise be refused by a job the reader never started. It writes versions of the preview scale
        only, and its last write finds the job cancelled and changes nothing, as for a run.

        The cancelling and the queuing of the tile cutting are blocks of their own, so this use case opens one block for
        the rows and is called outside a block. The files of the deleted versions are removed after that block ended,
        so a store that fails to remove them leaves them without a row for the next collection and does not fail the
        clear.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The number of changes deleted and the versions deleted, none of which when the step had nothing on the
                  page.
        :rtype: ClearedStep
        :raises NotFoundError: If the actor has no such project, the project has no such page, or no recipe of the stage
                               has the step.
        :raises ConflictError: If a run, a measure of the book, a tile cutting or a collection of the project is queued
                               or running, which may be reading or deleting the versions the clear deletes.
        """
        # The step is checked before the previews are cancelled, so a request the block would refuse cancels nothing
        await self._recipes_with_step(actor, project_id, key)
        if await self._starter.free_of_previews(project_id):
            raise ConflictError(PROJECT_BUSY)
        async with self._uow.change_book(project_id):
            recipes = await self._recipes_with_step(actor, project_id, key)
            stored = await self._uow.page_step_states.find(key)
            if stored is not None:
                await self._uow.page_step_states.delete(key)
            changes = await self._uow.page_step_changes.delete_for_step(key)
            versions = await self._uow.page_versions.list_for_page(key.page_id)
            chain = StepVersions.of(versions, key.stage, step_places(recipes, key.step_id))
            if stored is None and not changes and not chain.doomed:
                return ClearedStep(changes=0, versions=())
            stale: list[PageStage] = []
            untiled: list[PageVersionId] = []
            for record in await self._uow.page_stages.list_for_page(key.page_id):
                if record.head_version_id is None or record.head_version_id not in chain.doomed:
                    continue
                head = chain.input_of(record.head_version_id) if record.stage is key.stage else None
                if head is None:
                    stale.extend(await self._records.clear(record.key))
                    continue
                if (kept := chain.versions[head]).renditions is not None and not kept.tiles_ready:
                    untiled.append(head)
                # The new head is the result of the step at its depth in the recipe the page was run by, and the page
                # was run through that step only, which is before the last step that is on
                recipe = next((recipe for recipe in recipes if recipe.id == record.recipe_id), None)
                enabled = () if recipe is None else recipe.indexed_steps_through(None)
                place = chain.depths[head]
                through_step = None if recipe is None or place >= len(enabled) else recipe.stopped_at(enabled[place][0])
                # The record is marked stale below, so only the later stages that the new head makes stale are
                # announced
                _, *later = await self._records.set_head(
                    record.key, head_version_id=head, recipe_id=record.recipe_id, through_step=through_step
                )
                stale.extend(later)
            stale.extend(await self._records.mark_stale(key.page_id, key.stage))
            deleted = tuple(version for version in versions if version.id in chain.doomed)
            await self._uow.page_versions.delete_many([version.id for version in deleted])
        await self._records.announce(project_id, stale)
        await self._starter.enqueue_tiles(project_id, untiled)
        for version in deleted:
            try:
                await self._assets.delete_prefix(ProjectKeys(project_id).version_directory(version))
            except OSError:
                logger.exception('The files of the version %s were not removed after its page was cleared', version.id)
        return ClearedStep(changes=changes, versions=deleted)

    async def _recipes_with_step(self, actor: Actor, project_id: ProjectId, key: PageStepKey) -> Sequence[Recipe]:
        """Read the recipes of the stage of a page of the actor's project, which must include the step.

        It only reads, so it is called outside a block, to refuse a request before anything is cancelled, and inside the
        block, to decide on what the block holds.

        :param actor: Account acting in the current request.
        :type actor: Actor
        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        :returns: The recipes of the stage.
        :rtype: Sequence[Recipe]
        :raises NotFoundError: If the actor has no such project, the project has no such page, or no recipe of the stage
                               has the step.
        """
        await owned_page(self._uow, actor, project_id, key.page_id)
        recipes = await self._uow.recipes.list_for_stage(project_id, key.stage)
        if not any(step.step_id == key.step_id for recipe in recipes for step in recipe.steps):
            raise NotFoundError(key.step_id)
        return recipes

    async def _take_parts_back(
        self, project_id: ProjectId, targets: Sequence[PageStepChange], moment: datetime, batch: ChangeBatchId | None
    ) -> tuple[Sequence[PageStepChange], Sequence[PageStage]]:
        """Write back the values of the odd pages, the even pages and the groups that the targets changed.

        The values of a part of the pages are stored once, and every page they reached has a change of them in its
        history, the changes of one batch alike. The batches of one part are taken back from the newest, which is the
        one that left the values as they are now, to the oldest, and the values are written once. Every change is then
        taken back in the history of its page.

        The pages the part covers now are marked stale in the stage of the step, which are not the pages its changes
        name, since a page added, moved or put into the group after the change takes the values without a change in
        its history. A page that takes the field from a stronger value of its own is marked as well, and a run finds
        its version in the cache.

        :param project_id: Identifier of the project.
        :type project_id: ProjectId
        :param targets: The changes taken back, of any scope, of which those of pages are left to the caller.
        :type targets: Sequence[PageStepChange]
        :param moment: When the changes are taken back.
        :type moment: datetime
        :param batch: The batch the undos of one action share, or None.
        :type batch: ChangeBatchId | None
        :returns: The undos of the changes of the parts of the pages, and the stage records of the pages that went
                  stale.
        :rtype: tuple[Sequence[PageStepChange], Sequence[PageStage]]
        :raises ConflictError: If the values of a part changed after the changes, by a change that is not taken back.
        """
        parts: dict[StepValuesKey, list[PageStepChange]] = {}
        for target in targets:
            if target.scope is not ValueScope.PAGES:
                parts.setdefault(
                    StepValuesKey(project_id, target.step_id, target.scope, target.group_label), []
                ).append(target)
        undone: list[PageStepChange] = []
        stale: list[PageStage] = []
        book = await book_pages(self._uow.pages, project_id) if parts else []
        for key, changes in parts.items():
            stored = await self._uow.step_values.find(key)
            current = {} if stored is None else dict(stored.params)
            # One before and after for each batch, since every page a batch reached holds the same pair
            remaining = {change.batch_id or change.id: (change.before or {}, change.after or {}) for change in changes}
            while remaining:
                latest = next((batch_id for batch_id, (_, after) in remaining.items() if after == current), None)
                if latest is None:
                    raise ConflictError(CHANGED_SINCE.format(layer=changes[0].layer.label))
                current = dict(remaining.pop(latest)[0])
            stage = changes[0].stage
            holder = stored or StepValues(
                project_id=project_id,
                stage=stage,
                step_id=key.step_id,
                scope=key.scope,
                group_label=key.group_label,
                updated_at=moment,
            )
            if current:
                await self._uow.step_values.save(evolve(holder, params=current, updated_at=moment))
            elif stored is not None:
                await self._uow.step_values.delete(key)
            undone.extend(change.taken_back(moment, batch) for change in changes)
            kinds = {
                recipe.kind
                for recipe in await self._uow.recipes.list_for_stage(project_id, stage)
                if any(step.step_id == key.step_id for step in recipe.steps)
            }
            covered = [
                page.id
                for place, page in enumerate(book, start=1)
                if page.recipe_kind in kinds and holder.covers(group_label=page.group_label, position=place)
            ]
            stale.extend(await self._records.mark_pages_stale(covered, {stage}))
        return undone, stale

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
