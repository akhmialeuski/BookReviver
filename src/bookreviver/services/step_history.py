"""A step on a page inside one transaction: its history, the undo of its changes, and the clear of the step.

``StepOnPage`` is opened by ``PageHistoryService`` in the work it hands to ``Book.read`` or ``Book.change``. It holds
the repositories of that transaction, the page, the stage and the step, and reads the history and the recipes of the
step. ``StepUndo`` and ``StepClear`` belong to it and reach the transaction through it, so neither is handed the
repositories or the key from call to call.

An undo writes the content a change replaced back into the layer, as a new change whose source is an undo and which
names the change it takes back, so the history only grows and shows what the undo did. Taking a change back needs the
layer to hold what the change left in it, since writing ``before`` over a layer that has changed since would lose the
later change. A change of a batch is taken back with the rest of its batch, on every page the batch reached, and the
undos of one batch share a batch of their own, so the undo of a batch is one action as well.

A clear is the one way a history shrinks: it takes the settings and the edit of a step away from one page, deletes that
step's history on that page, and deletes the results of the step on the page with every result that read them.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from attrs import evolve

from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.history import ChangeSource, ClearedStep, PageHistoryEntry, StepHistory
from bookreviver.domain.ids import ChangeBatchId
from bookreviver.domain.page_steps import PageStepState
from bookreviver.domain.step_values import StepValues, StepValuesKey, ValueScope
from bookreviver.domain.version_chains import StepVersions, step_places

if TYPE_CHECKING:
    from collections.abc import Sequence

    from bookreviver.domain.ids import PageHistoryEntryId, PageId, PageVersionId, RecipeId, StepId
    from bookreviver.domain.page_steps import PageStepKey
    from bookreviver.domain.recipes import Recipe
    from bookreviver.ports.persistence import BookRepositories
    from bookreviver.services.stage_records import StageRecords

CHANGED_SINCE: str = 'The layer {layer} of a step changed after this change, so it cannot be taken back on its own.'


class StepOnPage:
    """One step on one page inside one transaction.

    :ivar records: Writer of the stage records, which an undo and a clear mark stale.
    :ivar repos: Repositories of the transaction.
    :ivar key: The page, the stage and the step.
    """

    def __init__(self, records: StageRecords, repos: BookRepositories, key: PageStepKey) -> None:
        """Open a step on a page for the work of one transaction.

        :param records: Writer of the stage records.
        :type records: StageRecords
        :param repos: Repositories of the transaction.
        :type repos: BookRepositories
        :param key: The page, the stage and the step.
        :type key: PageStepKey
        """
        self.records = records
        self.repos = repos
        self.key = key

    def history(self) -> StepHistory:
        """Read the history of the step on the page.

        :returns: The changes of the step, oldest first, with which of them stand.
        :rtype: StepHistory
        :raises NotFoundError: If the book has no such page.
        """
        key = self.key
        self.repos.pages.get(key.page_id)
        changes = self.repos.page_history.list_for_page(key.page_id, key.stage)
        return StepHistory(tuple(change for change in changes if change.step_id == key.step_id))

    def recipes(self) -> Sequence[Recipe]:
        """Read the recipes of the stage of the page, which must include the step.

        It only reads, so it is called in a ``read``, to refuse a request before anything is cancelled, and in the work
        of the ``Book.change``, to decide on what the transaction holds.

        :returns: The recipes of the stage.
        :rtype: Sequence[Recipe]
        :raises NotFoundError: If the book has no such page, or no recipe of the stage has the step.
        """
        key = self.key
        self.repos.pages.get(key.page_id)
        recipes = self.repos.recipes.list_for_stage(key.stage)
        if not any(step.step_id == key.step_id for recipe in recipes for step in recipe.steps):
            raise NotFoundError(key.step_id)
        return recipes

    def undo(self, change_id: PageHistoryEntryId | None) -> Sequence[PageHistoryEntry]:
        """Take back the newest change of the step on the page, or every change back to a chosen one.

        :param change_id: The oldest change to take back, or None for the newest change alone.
        :type change_id: PageHistoryEntryId | None
        :returns: The undos that were written, which are none when the step has no change to take back.
        :rtype: Sequence[PageHistoryEntry]
        :raises NotFoundError: If the book has no such page, or the step has no standing change with this identifier.
        :raises ConflictError: If a layer changed after the change that is taken back.
        """
        return StepUndo(self, change_id).write()

    def clear(self) -> tuple[ClearedStep, Sequence[PageVersionId]]:
        """Delete the history, the state and the results of the step on the page, and mark the stages stale.

        :returns: What was deleted, and the versions that became current without a pyramid.
        :rtype: tuple[ClearedStep, Sequence[PageVersionId]]
        :raises NotFoundError: If the book has no such page, or no recipe of the stage has the step.
        """
        return StepClear(self).delete()


class StepUndo:
    """One undo of the changes of a step on a page, with the rest of the batches of those changes.

    :ivar step: The step on the page, through which the transaction is reached.
    :ivar moment: When the changes are taken back.
    :ivar targets: The changes taken back, of any scope, newest first within each page.
    :ivar batch: The batch the undos of the action share, or None for the undo of one change.
    """

    def __init__(self, step: StepOnPage, change_id: PageHistoryEntryId | None) -> None:
        """Choose the changes an undo takes back.

        :param step: The step on the page.
        :type step: StepOnPage
        :param change_id: The oldest change to take back, which takes back every change after it as well, or None for
                          the newest change alone.
        :type change_id: PageHistoryEntryId | None
        :raises NotFoundError: If the book has no such page, or the step has no standing change with this identifier.
        """
        self.step = step
        self.moment = datetime.now(UTC)
        history = step.history()
        chosen = history.standing[-1:] if change_id is None else history.back_to(change_id)
        self.targets = self._with_batches(chosen)
        self.batch = ChangeBatchId(uuid4()) if len(self.targets) > 1 else None

    def write(self) -> Sequence[PageHistoryEntry]:
        """Write the undos, put the layers and the values back, and mark the stages of the pages stale.

        :returns: The undos that were written.
        :rtype: Sequence[PageHistoryEntry]
        :raises ConflictError: If a layer changed after the change that is taken back.
        """
        if not self.targets:
            return ()
        repos = self.step.repos
        added = repos.page_history.add_many([*self._pages_back(), *self._parts_back()])
        for page_id, stage in dict.fromkeys((change.page_id, change.stage) for change in added):
            self.step.records.mark_stale(repos, page_id, stage)
        return added

    def _pages_back(self) -> Sequence[PageHistoryEntry]:
        """Put back the layers of the pages that the targets changed, and store the states that result.

        :returns: The undos of the changes of single pages.
        :rtype: Sequence[PageHistoryEntry]
        :raises ConflictError: If a layer changed after the change that is taken back.
        """
        repos, moment = self.step.repos, self.moment
        stored: dict[PageStepKey, PageStepState | None] = {}
        working: dict[PageStepKey, PageStepState] = {}
        written: list[PageHistoryEntry] = []
        for target in (target for target in self.targets if target.scope is ValueScope.PAGES):
            if target.key not in working:
                stored[target.key] = repos.page_step_states.find(target.key)
                working[target.key] = stored[target.key] or PageStepState(
                    page_id=target.page_id, stage=target.stage, step_id=target.step_id, updated_at=moment
                )
            state = working[target.key]
            if state.layer(target.layer) != target.after:
                raise ConflictError(CHANGED_SINCE.format(layer=target.layer.label))
            reverted = state.with_layer(target.layer, target.before, moment)
            working[target.key] = reverted
            undo = PageHistoryEntry.between(state, reverted, target.layer, ChangeSource.UNDO)
            written.append(evolve(undo, batch_id=self.batch, undoes=target.id))
        for state_key, final in working.items():
            if not final.is_empty:
                repos.page_step_states.save(final)
            elif stored[state_key] is not None:
                repos.page_step_states.delete(state_key)
        return written

    def _parts_back(self) -> Sequence[PageHistoryEntry]:
        """Write back the values of the odd pages, the even pages and the groups that the targets changed.

        The values of a part of the pages are stored once, and so is a change of them, which the history of every page
        it reached names under its one identifier. The batches of one part are taken back from the newest, which is the
        one that left the values as they are now, to the oldest, and the values are written once. Every change is then
        taken back in the history of its page.

        The pages the part covers now are marked stale in the stage of the step, which are not the pages its changes
        name, since a page added, moved or put into the group after the change takes the values without a change in
        its history. A page that takes the field from a stronger value of its own is marked as well, and a run finds
        its version in the cache.

        :returns: The undos of the changes of the parts of the pages.
        :rtype: Sequence[PageHistoryEntry]
        :raises ConflictError: If the values of a part changed after the changes, by a change that is not taken back.
        """
        repos, moment = self.step.repos, self.moment
        parts: dict[StepValuesKey, list[PageHistoryEntry]] = {}
        for target in self.targets:
            if target.scope is not ValueScope.PAGES:
                parts.setdefault(StepValuesKey(target.step_id, target.scope, target.group_label), []).append(target)
        undone: list[PageHistoryEntry] = []
        book = repos.pages.list_all() if parts else []
        for key, changes in parts.items():
            stored = repos.step_values.find(key)
            current = {} if stored is None else dict(stored.params)
            current = self._values_before(changes, current)
            stage = changes[0].stage
            holder = stored or StepValues(
                stage=stage,
                step_id=key.step_id,
                scope=key.scope,
                group_label=key.group_label,
                updated_at=moment,
            )
            if current:
                repos.step_values.save(evolve(holder, params=current, updated_at=moment))
            elif stored is not None:
                repos.step_values.delete(key)
            # The undo of a change is one change too, so the undos of its pages share one identifier
            undos: dict[PageHistoryEntryId, PageHistoryEntryId] = {}
            for change in changes:
                undo = change.taken_back(moment, self.batch)
                undone.append(evolve(undo, id=undos.setdefault(change.id, undo.id)))
            kinds = {
                recipe.kind
                for recipe in repos.recipes.list_for_stage(stage)
                if any(step.step_id == key.step_id for step in recipe.steps)
            }
            covered = [
                page.id
                for place, page in enumerate(book, start=1)
                if page.recipe_kind in kinds and holder.covers(group_label=page.group_label, position=place)
            ]
            self.step.records.mark_pages_stale(repos, covered, {stage})
        return undone

    @staticmethod
    def _values_before(changes: Sequence[PageHistoryEntry], current: dict[str, Any]) -> dict[str, Any]:
        """Return the values of a part of the pages as they were before the changes, taken back from the newest batch.

        :param changes: The changes of the part that are taken back.
        :type changes: Sequence[PageHistoryEntry]
        :param current: The values of the part now.
        :type current: dict[str, Any]
        :returns: The values before the oldest of the changes.
        :rtype: dict[str, Any]
        :raises ConflictError: If the values changed after the changes, by a change that is not taken back.
        """
        # One before and after for each batch, since every page a batch reached holds the same pair
        remaining = {change.batch_id or change.id: (change.before or {}, change.after or {}) for change in changes}
        while remaining:
            latest = next((batch_id for batch_id, (_, after) in remaining.items() if after == current), None)
            if latest is None:
                raise ConflictError(CHANGED_SINCE.format(layer=changes[0].layer.label))
            current = dict(remaining.pop(latest)[0])
        return current

    def _with_batches(self, chosen: Sequence[PageHistoryEntry]) -> Sequence[PageHistoryEntry]:
        """Add the rest of the batch of each chosen change, and keep the changes that still stand.

        :param chosen: The standing changes of the step that are taken back.
        :type chosen: Sequence[PageHistoryEntry]
        :returns: The changes to take back, newest first within each page.
        :rtype: Sequence[PageHistoryEntry]
        :raises NotFoundError: If a batch reaches a page that is not one of the book.
        """
        repos = self.step.repos
        # A change of the values of a part of the pages is one entry for each page, under one identifier
        members = {(change.id, change.page_id): change for change in chosen}
        for batch_id in {change.batch_id for change in chosen if change.batch_id is not None}:
            for member in repos.page_history.list_for_batch(batch_id):
                members.setdefault((member.id, member.page_id), member)
        undone = {undo.undoes for undo in repos.page_history.list_undoing({change_id for change_id, _ in members})}
        standing = [
            change for change in members.values() if change.source is not ChangeSource.UNDO and change.id not in undone
        ]
        pages: set[PageId] = {change.page_id for change in standing}
        if pages:
            repos.pages.list_by_ids(pages)
        return sorted(standing, key=lambda change: (change.page_id, -change.sequence))


class StepClear:
    """The clear of a step on a page, which returns the step to the state it had before it first ran or changed there.

    The results of the step on the page go with the versions that read them, directly or through a chain, whatever
    their stage. The stage of the page then stands on the version the step read, marked stale, or has no current
    version when the step is the first of its recipe.

    :ivar step: The step on the page, through which the transaction is reached.
    """

    def __init__(self, step: StepOnPage) -> None:
        """Clear a step on a page.

        :param step: The step on the page.
        :type step: StepOnPage
        """
        self.step = step

    def delete(self) -> tuple[ClearedStep, Sequence[PageVersionId]]:
        """Delete the history, the state and the results of the step on the page, and mark the stages stale.

        :returns: What was deleted, and the versions that became current without a pyramid.
        :rtype: tuple[ClearedStep, Sequence[PageVersionId]]
        :raises NotFoundError: If the book has no such page, or no recipe of the stage has the step.
        """
        repos, key = self.step.repos, self.step.key
        recipes = self.step.recipes()
        stored = repos.page_step_states.find(key)
        if stored is not None:
            repos.page_step_states.delete(key)
        changes = repos.page_history.delete_for_step(key)
        versions = repos.page_versions.list_for_page(key.page_id)
        chain = StepVersions.of(versions, key.stage, step_places(recipes, key.step_id))
        if stored is None and not changes and not chain.doomed:
            return ClearedStep(changes=0, versions=()), ()
        untiled = self._restore_heads(chain, recipes)
        self.step.records.mark_stale(repos, key.page_id, key.stage)
        deleted = tuple(version for version in versions if version.id in chain.doomed)
        repos.page_versions.delete_many([version.id for version in deleted])
        return ClearedStep(changes=changes, versions=deleted), untiled

    def _restore_heads(self, chain: StepVersions, recipes: Sequence[Recipe]) -> Sequence[PageVersionId]:
        """Stand each stage of the page whose current version goes on the version the step read, or on none.

        :param chain: The versions of the page the clear deletes, and what each of them read.
        :type chain: StepVersions
        :param recipes: The recipes of the stage of the step.
        :type recipes: Sequence[Recipe]
        :returns: The versions that became current without a pyramid, whose cutting the caller queues.
        :rtype: Sequence[PageVersionId]
        """
        repos, key, records = self.step.repos, self.step.key, self.step.records
        untiled: list[PageVersionId] = []
        for record in repos.page_stages.list_for_page(key.page_id):
            if record.head_version_id is None or record.head_version_id not in chain.doomed:
                continue
            head = chain.input_of(record.head_version_id) if record.stage is key.stage else None
            if head is None:
                records.clear(repos, record.key)
                continue
            if (kept := chain.versions[head]).renditions is not None and not kept.tiles_ready:
                untiled.append(head)
            through_step_id = self._through_step(recipes, record.recipe_id, chain.depths[head])
            records.set_head(
                repos, record.key, head_version_id=head, recipe_id=record.recipe_id, through_step_id=through_step_id
            )
        return untiled

    @staticmethod
    def _through_step(recipes: Sequence[Recipe], recipe_id: RecipeId | None, place: int) -> StepId | None:
        """Return the step a page was run through when its new current version is the result at this depth.

        The new head is the result of the step at its depth in the recipe the page was run by, and the page was run
        through that step only, which is before the last step that is on.

        :param recipes: The recipes of the stage.
        :type recipes: Sequence[Recipe]
        :param recipe_id: The recipe the page was run by, or None.
        :type recipe_id: RecipeId | None
        :param place: The depth of the new current version among the steps that are on.
        :type place: int
        :returns: The step the run stopped at, or None when the version is the result of the last step that is on.
        :rtype: StepId | None
        """
        recipe = next((recipe for recipe in recipes if recipe.id == recipe_id), None)
        enabled = () if recipe is None else recipe.indexed_steps_through(None)
        return None if recipe is None or place >= len(enabled) else recipe.stopped_at(enabled[place][1].step_id)
