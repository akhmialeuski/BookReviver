"""Tests for the history of a step on a page and for the undo that takes its changes back."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import EDIT_HASH_FIELD, Actor, PageStepChange
from bookreviver.domain.enums import ChangeSource, EditorKind, JobKind, Stage, StageState, StepLayer
from bookreviver.domain.errors import ConflictError, NotFoundError
from bookreviver.domain.events import PageStageChanged
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import ChangeBatchId, PageStepChangeId, StepId
from bookreviver.domain.keys import ProjectKeys
from bookreviver.domain.values import NewPageEdit, PageStageKey, PageStepKey, RecipeDraft, StageRun, Step, TileCut
from tests.helpers.builders import make_page_step_state, make_result_mark_change, new_account_id
from tests.helpers.processors import FAILING_PARAMETER, STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import run_stage
from tests.services.test_processing_versions import ran_geometry, ran_two_steps

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, PageEdit, PageVersion, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
STRONGER: int = 2
STRONGEST: int = 3
NOT_FAILING: bool = False
OTHER_ORDER_KEY: str = 'a1'
# A setting, an edit and the undo of the edit, which the clear test writes before it deletes them
STEP_CHANGES: int = 3
FIRST_ANGLE: float = 1.5
SECOND_ANGLE: float = 2.5
FIRST: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=FIRST_ANGLE))
SECOND: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=SECOND_ANGLE))


async def step_key(kit: ProcessingKit, page: Page) -> PageStepKey:
    """Give the key of the step of the active geometry recipe that runs the fake processor on a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param page: The page.
    :type page: Page
    :returns: The key of the step on the page.
    :rtype: PageStepKey
    """
    return await kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY)


async def history_of(kit: ProcessingKit, actor: Actor, project: Project, key: PageStepKey) -> list[PageStepChange]:
    """Read the changes of a step on a page as the history lists them, oldest first.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param actor: The signed-in account.
    :type actor: Actor
    :param project: The book.
    :type project: Project
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The changes.
    :rtype: list[PageStepChange]
    """
    return list((await kit.page_history().list(actor, project.id, key)).changes)


async def stored_edit(kit: ProcessingKit, key: PageStepKey) -> PageEdit | None:
    """Read the manual edit a step has on a page.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The edit, or None.
    :rtype: PageEdit | None
    """
    state = await kit.uow().page_step_states.find(key)
    return None if state is None else state.edit


async def has_files(kit: ProcessingKit, project: Project, version: PageVersion) -> bool:
    """Tell whether the directory of a version is in the store of the derived files.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param project: The book owning the version.
    :type project: Project
    :param version: The version.
    :type version: PageVersion
    :returns: Whether the store has the directory.
    :rtype: bool
    """
    try:
        async with kit.assets.readable(ProjectKeys(project.id).version_directory(version)):
            return True
    except NotFoundError:
        return False


class TestHistoryOfEdits:
    """Tests for the changes of the manual layer, which saving and deleting an edit write."""

    async def test_saving_and_deleting_an_edit_write_the_manual_layer(self, fx_kit: ProcessingKit) -> None:
        """Verify each save and the delete write a change of the manual layer with the edit before and after.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        first = await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        second = await fx_kit.edits().save(actor, project.id, key, SECOND, None)
        await fx_kit.edits().delete(actor, project.id, key)
        history = await history_of(fx_kit, actor, project, key)
        expect([change.layer for change in history] == [StepLayer.HAND] * 3)
        expect(all(change.source is ChangeSource.USER for change in history))
        expect([change.before is None for change in history] == [True, False, False])
        expect([change.after is None for change in history] == [False, False, True])
        expect(history[1].before == history[0].after)
        expect(history[0].after is not None and history[0].after[EDIT_HASH_FIELD] == first.edit_hash)
        expect(history[1].after is not None and history[1].after[EDIT_HASH_FIELD] == second.edit_hash)
        assert_expectations()

    async def test_the_same_edit_saved_again_writes_no_change(self, fx_kit: ProcessingKit) -> None:
        """Verify saving an edit equal to the stored one leaves the history as it was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        assert len(await history_of(fx_kit, actor, project, key)) == 1

    async def test_the_changes_of_a_page_list_only_for_its_step(self, fx_kit: ProcessingKit) -> None:
        """Verify the history of a step lists its own changes and not those of another page or another step.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key=OTHER_ORDER_KEY)
        key = await step_key(fx_kit, page)
        await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        await fx_kit.edits().save(actor, project.id, evolve(key, page_id=other.id), SECOND, None)
        history = await history_of(fx_kit, actor, project, key)
        elsewhere = await history_of(fx_kit, actor, project, evolve(key, step_id=StepId(uuid4())))
        expect([change.page_id for change in history] == [page.id])
        expect(elsewhere == [])
        assert_expectations()


class TestUndo:
    """Tests for PageHistoryService.undo of the newest change."""

    async def test_a_setting_is_taken_back_and_the_undo_is_a_change_of_its_own(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo empties the layer, writes a change that names the one it took back, and deletes no row.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        [change] = await history_of(fx_kit, actor, project, key)
        [undo] = await fx_kit.page_history().undo(actor, project.id, key, None)
        history = await fx_kit.page_history().list(actor, project.id, key)
        expect(await fx_kit.uow().page_step_states.find(key) is None)
        expect(history.changes == (change, undo))
        expect((undo.source, undo.layer, undo.undoes) == (ChangeSource.UNDO, StepLayer.SETTINGS, change.id))
        expect((undo.before, undo.after) == (change.after, change.before))
        expect(history.undone == {change.id} and history.standing == ())
        assert_expectations()

    async def test_the_changes_are_taken_back_one_after_another_newest_first(self, fx_kit: ProcessingKit) -> None:
        """Verify each undo takes back the newest change that still stands, and an empty history takes back nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGEST)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        after_one = await fx_kit.uow().page_step_states.get(key)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        nothing = await fx_kit.page_history().undo(actor, project.id, key, None)
        expect(after_one.params == {STRENGTH_PARAMETER: STRONGER})
        expect(await fx_kit.uow().page_step_states.find(key) is None)
        expect(len(nothing) == 0)
        expect(len(await history_of(fx_kit, actor, project, key)) == 4)
        assert_expectations()

    async def test_a_saved_edit_is_taken_back_to_the_edit_before_it_or_to_none(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo puts the previous edit back, and the first edit is taken back to none.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        first = await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        await fx_kit.edits().save(actor, project.id, key, SECOND, None)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        back = await stored_edit(fx_kit, key)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        expect(back is not None and (back.edit_hash, back.geometry) == (first.edit_hash, first.geometry))
        expect(await stored_edit(fx_kit, key) is None)
        assert_expectations()

    async def test_a_deleted_edit_comes_back_and_the_settings_stay_as_they_are(self, fx_kit: ProcessingKit) -> None:
        """Verify taking back the delete restores the edit, and a setting made in between is not touched by it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        saved = await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        await fx_kit.edits().delete(actor, project.id, key)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        expect(await fx_kit.uow().page_step_states.find(key) is None)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        back = await stored_edit(fx_kit, key)
        expect(back is not None and (back.edit_hash, back.geometry) == (saved.edit_hash, saved.geometry))
        assert_expectations()

    async def test_the_stage_goes_stale_and_the_change_is_announced(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo marks the stage of the page stale and publishes it, like the change it takes back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        uow = fx_kit.uow()
        record = await uow.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        await uow.page_stages.save(evolve(record, state=StageState.FRESH))
        await uow.commit()
        fx_kit.events.published.clear()
        await fx_kit.page_history().undo(actor, project.id, key, None)
        stored = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect(stored.state is StageState.STALE)
        expect(any(isinstance(event, PageStageChanged) for event in fx_kit.events.published))
        assert_expectations()

    async def test_a_layer_that_changed_since_cannot_be_taken_back(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo that would write over a later change is a conflict and changes nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        [change] = await history_of(fx_kit, actor, project, key)
        uow = fx_kit.uow()
        await uow.page_step_states.save(make_page_step_state(page_id=page.id, step_id=key.step_id, params={'x': 1}))
        await uow.commit()
        with pytest.raises(ConflictError):
            await fx_kit.page_history().undo(actor, project.id, key, change.id)
        expect((await fx_kit.uow().page_step_states.get(key)).params == {'x': 1})
        expect(await history_of(fx_kit, actor, project, key) == [change])
        assert_expectations()

    async def test_other_book_other_owner_and_unknown_change_are_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a page of another book, a stranger and a change the step does not have are not found.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        _, other_project = await fx_kit.seed_project()
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        attempts = (
            (actor, other_project, None),
            (Actor(account_id=new_account_id()), project, None),
            (actor, project, PageStepChangeId(uuid4())),
        )
        for who, book, change_id in attempts:
            with pytest.raises(NotFoundError):
                await fx_kit.page_history().undo(who, book.id, key, change_id)
        assert len(await history_of(fx_kit, actor, project, key)) == 1


class TestUndoBackTo:
    """Tests for PageHistoryService.undo back to a chosen change."""

    async def test_every_change_after_the_chosen_one_is_taken_back_with_it(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo back to a change takes back it and the later changes of both layers, newest first.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        await fx_kit.page_settings().change(actor, project.id, key, FAILING_PARAMETER, NOT_FAILING)
        await fx_kit.edits().save(actor, project.id, key, SECOND, None)
        chosen = (await history_of(fx_kit, actor, project, key))[1]
        undone = await fx_kit.page_history().undo(actor, project.id, key, chosen.id)
        state = await fx_kit.uow().page_step_states.get(key)
        history = await fx_kit.page_history().list(actor, project.id, key)
        expect([undo.undoes for undo in undone] == [change.id for change in reversed(history.changes[1:4])])
        expect(state.params == {STRENGTH_PARAMETER: STRONGER} and state.edit is None)
        expect([change.id for change in history.standing] == [history.changes[0].id])
        expect(len({undo.batch_id for undo in undone}) == 1 and undone[0].batch_id is not None)
        assert_expectations()

    async def test_a_change_that_was_taken_back_already_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo back to a change that no longer stands, or to an undo, is not found.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        [change] = await history_of(fx_kit, actor, project, key)
        [undo] = await fx_kit.page_history().undo(actor, project.id, key, None)
        for gone in (change.id, undo.id):
            with pytest.raises(NotFoundError):
                await fx_kit.page_history().undo(actor, project.id, key, gone)


class TestUndoOfABatch:
    """Tests for taking back the changes of a batch, which a batch run or a carry-over writes on several pages."""

    async def test_a_batch_is_taken_back_as_one_action_on_every_page_it_reached(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo of one change of a batch takes back the changes of the other pages, in one batch of undos.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key=OTHER_ORDER_KEY)
        key = await step_key(fx_kit, page)
        other_key = evolve(key, page_id=other.id)
        batch = ChangeBatchId(uuid4())
        uow = fx_kit.uow()
        for at in (key, other_key):
            state = make_page_step_state(page_id=at.page_id, step_id=at.step_id, params={STRENGTH_PARAMETER: STRONGER})
            await uow.page_step_states.save(state)
            await uow.page_step_changes.add(
                evolve(
                    PageStepChange.between(
                        make_page_step_state(page_id=at.page_id, step_id=at.step_id),
                        state,
                        StepLayer.SETTINGS,
                        ChangeSource.CARRY_OVER,
                    ),
                    batch_id=batch,
                )
            )
        await uow.commit()
        undone = await fx_kit.page_history().undo(actor, project.id, key, None)
        expect(sorted(undo.page_id for undo in undone) == sorted([page.id, other.id]))
        expect(len({undo.batch_id for undo in undone}) == 1 and undone[0].batch_id not in {None, batch})
        expect(await fx_kit.uow().page_step_states.find(key) is None)
        expect(await fx_kit.uow().page_step_states.find(other_key) is None)
        expect(len(await fx_kit.page_history().undo(actor, project.id, other_key, None)) == 0)
        assert_expectations()

    async def test_a_page_of_the_batch_that_changed_since_stops_the_whole_undo(self, fx_kit: ProcessingKit) -> None:
        """Verify one page whose layer changed after the batch leaves every page of the batch as it was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key=OTHER_ORDER_KEY)
        key = await step_key(fx_kit, page)
        other_key = evolve(key, page_id=other.id)
        batch = ChangeBatchId(uuid4())
        uow = fx_kit.uow()
        for at, params in ((key, {STRENGTH_PARAMETER: STRONGER}), (other_key, {STRENGTH_PARAMETER: STRONGEST})):
            empty = make_page_step_state(page_id=at.page_id, step_id=at.step_id)
            carried = evolve(empty, params={STRENGTH_PARAMETER: STRONGER})
            await uow.page_step_states.save(evolve(empty, params=params))
            await uow.page_step_changes.add(
                evolve(
                    PageStepChange.between(empty, carried, StepLayer.SETTINGS, ChangeSource.CARRY_OVER), batch_id=batch
                )
            )
        await uow.commit()
        with pytest.raises(ConflictError):
            await fx_kit.page_history().undo(actor, project.id, key, None)
        expect((await fx_kit.uow().page_step_states.get(key)).params == {STRENGTH_PARAMETER: STRONGER})
        expect(len(await fx_kit.uow().page_step_changes.list_for_page(page.id)) == 1)
        assert_expectations()


class TestClear:
    """Tests for PageHistoryService.clear, which deletes the history of one step on one page."""

    async def test_the_history_the_settings_and_the_edit_go_with_the_result_of_the_step(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a clear deletes the changes, the settings, the hand edit and the result the run made, and counts them.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        await fx_kit.edits().save(actor, project.id, key, FIRST, None)
        await fx_kit.page_history().undo(actor, project.id, key, None)
        written = len(await history_of(fx_kit, actor, project, key))
        cleared = await fx_kit.page_history().clear(actor, project.id, key)
        expect(cleared.changes == written == STEP_CHANGES)
        expect([deleted.id for deleted in cleared.versions] == [version.id])
        expect(await history_of(fx_kit, actor, project, key) == [])
        expect(await fx_kit.uow().page_step_states.find(key) is None)
        expect(await fx_kit.uow().page_versions.find(version.id) is None)
        assert_expectations()

    async def test_the_stage_stands_on_what_the_step_read_stale_and_the_change_is_announced(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a clear of the second step puts the stage back on the result of the first, stale, and announces it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, made = await ran_two_steps(fx_kit)
        fx_kit.events.published.clear()
        await fx_kit.page_history().clear(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step_ids[1]))
        stored = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect((stored.head_version_id, stored.state, stored.through_step) == (made[0].id, StageState.STALE, 0))
        expect(any(isinstance(event, PageStageChanged) for event in fx_kit.events.published))
        assert_expectations()

    async def test_the_version_the_stage_stands_on_gets_its_pyramid_cut_since_the_page_is_shown_by_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a clear that makes a step in the middle of the recipe current queues the cutting of its pyramid.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, made = await ran_two_steps(fx_kit)
        expect(not made[0].tiles_ready)
        queued = len(fx_kit.recording.enqueued)
        await fx_kit.page_history().clear(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step_ids[1]))
        cutting = fx_kit.recording.enqueued[queued:]
        expect([job.kind for job in cutting] == [JobKind.CUT_TILES])
        expect([TileCut.from_map(job.params).version_ids for job in cutting] == [(made[0].id,)])
        assert_expectations()

    async def test_a_stage_left_with_no_current_version_or_a_cut_pyramid_queues_nothing(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a clear of the first step, which leaves no head, and one that leaves a tiled head cut nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, made = await ran_two_steps(fx_kit)
        uow = fx_kit.uow()
        await uow.page_versions.update(evolve(made[0], tiles_ready=True))
        await uow.commit()
        queued = len(fx_kit.recording.enqueued)
        await fx_kit.page_history().clear(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step_ids[1]))
        await fx_kit.page_history().clear(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step_ids[0]))
        expect(fx_kit.recording.enqueued[queued:] == [])
        assert_expectations()

    async def test_a_page_run_by_a_variant_stands_on_the_step_before_in_the_recipe_that_ran_it(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the index the record keeps is the one of the new head in the recipe of the page, which lacks the step.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, made = await ran_two_steps(fx_kit)
        draft = RecipeDraft(name='V', steps=[Step(processor_key=FAKE_KEY), Step(processor_key=FAKE_KEY)])
        variant = await fx_kit.service().add_variant(actor, project.id, Stage.GEOMETRY, draft)
        uow = fx_kit.uow()
        record = await uow.page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        await uow.page_stages.save(evolve(record, recipe_id=variant.id))
        await uow.commit()
        await fx_kit.page_history().clear(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step_ids[1]))
        stored = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))
        expect((stored.head_version_id, stored.recipe_id, stored.through_step) == (made[0].id, variant.id, 0))
        assert_expectations()

    async def test_the_first_step_leaves_the_stage_with_no_current_version(self, fx_kit: ProcessingKit) -> None:
        """Verify a clear of the first step leaves the stage of the page as it was before its first run.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, _ = await ran_two_steps(fx_kit)
        await fx_kit.page_history().clear(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step_ids[0]))
        expect(await fx_kit.uow().page_stages.find(PageStageKey(page.id, Stage.GEOMETRY)) is None)
        assert_expectations()

    async def test_the_results_that_read_the_step_go_with_their_marks_their_rows_and_their_files(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a clear of the first step deletes both results of the chain, their mark logs and their directories.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, made = await ran_two_steps(fx_kit)
        key = PageStepKey(page.id, Stage.GEOMETRY, step_ids[0])
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        uow = fx_kit.uow()
        for version in made:
            await uow.result_mark_changes.add(make_result_mark_change(version_id=version.id))
        await uow.commit()
        expect([await has_files(fx_kit, project, version) for version in made] == [True, True])
        cleared = await fx_kit.page_history().clear(actor, project.id, key)
        stored = fx_kit.uow()
        remaining = await stored.page_versions.list_for_page(page.id)
        expect({version.id for version in cleared.versions} == {version.id for version in made})
        expect({version.id for version in remaining} == {made[0].input_id})
        expect(await stored.page_step_changes.list_for_page(page.id, Stage.GEOMETRY) == [])
        expect(await stored.page_step_states.list_for_step([page.id], Stage.GEOMETRY, key.step_id) == [])
        expect([await stored.result_mark_changes.list_for_version(version.id) for version in made] == [[], []])
        expect([await has_files(fx_kit, project, version) for version in made] == [False, False])
        assert_expectations()

    async def test_the_versions_the_step_did_not_read_stay_with_those_of_other_pages_and_stages(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify a clear of the second step keeps the first result, the base version and the results of another page.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, made = await ran_two_steps(fx_kit)
        first, second = made
        other, _ = await fx_kit.seed_scan_page(project, order_key=OTHER_ORDER_KEY)
        await fx_kit.seed_base_version(other)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.GEOMETRY))
        uow = fx_kit.uow()
        before = {version.id for version in await uow.page_versions.list_for_page(other.id)}
        await uow.result_mark_changes.add(make_result_mark_change(version_id=first.id))
        await uow.commit()
        await fx_kit.page_history().clear(actor, project.id, PageStepKey(page.id, Stage.GEOMETRY, step_ids[1]))
        stored = fx_kit.uow()
        kept = {version.id for version in await stored.page_versions.list_for_page(page.id)}
        expect(second.id not in kept and first.id in kept and first.input_id in kept)
        expect(len(before) > 1)
        expect({version.id for version in await stored.page_versions.list_for_page(other.id)} == before)
        expect(len(await stored.result_mark_changes.list_for_version(first.id)) == 1)
        expect(await has_files(fx_kit, project, first))
        assert_expectations()

    async def test_a_later_stage_that_read_the_step_loses_its_result_and_its_record(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the result of the cleanup stage that read the cleared step goes, and its record with it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, step_ids, made = await ran_two_steps(fx_kit)
        await run_stage(fx_kit, actor, project, StageRun(stage=Stage.CLEANUP))
        later = await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.CLEANUP))
        assert later.head_version_id is not None
        key = PageStepKey(page.id, Stage.GEOMETRY, step_ids[1])
        cleared = await fx_kit.page_history().clear(actor, project.id, key)
        stored = fx_kit.uow()
        expect(later.head_version_id in {version.id for version in cleared.versions})
        expect(await stored.page_versions.find(later.head_version_id) is None)
        expect(await stored.page_stages.find(PageStageKey(page.id, Stage.CLEANUP)) is None)
        expect(await stored.page_versions.find(made[0].id) is not None)
        assert_expectations()

    async def test_a_store_that_cannot_remove_the_files_does_not_fail_the_clear(
        self, fx_kit: ProcessingKit, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify the rows are gone and the answer is given when the store fails to remove the directories.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        :param monkeypatch: Replaces the removal of directories with one that fails.
        :type monkeypatch: pytest.MonkeyPatch
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        monkeypatch.setattr(fx_kit.assets, 'delete_prefix', AsyncMock(side_effect=OSError('The disk is read only.')))
        cleared = await fx_kit.page_history().clear(actor, project.id, key)
        expect([deleted.id for deleted in cleared.versions] == [version.id])
        expect(await fx_kit.uow().page_versions.find(version.id) is None)
        expect(await has_files(fx_kit, project, version))
        assert_expectations()

    async def test_a_step_with_nothing_on_the_page_is_a_clear_of_nothing(self, fx_kit: ProcessingKit) -> None:
        """Verify clearing a step that has no change, no setting and no result writes nothing and announces nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        page, _ = await fx_kit.seed_scan_page(project)
        key = await step_key(fx_kit, page)
        fx_kit.events.published.clear()
        cleared = await fx_kit.page_history().clear(actor, project.id, key)
        expect((cleared.changes, cleared.versions) == (0, ()))
        expect(not fx_kit.events.published)
        assert_expectations()

    async def test_another_page_of_a_batch_keeps_its_change_and_its_undo_works(self, fx_kit: ProcessingKit) -> None:
        """Verify a clear on one page leaves the other page of a batch with its change, which an undo still takes back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        other, _ = await fx_kit.seed_scan_page(project, order_key=OTHER_ORDER_KEY)
        key = await step_key(fx_kit, page)
        other_key = evolve(key, page_id=other.id)
        batch = ChangeBatchId(uuid4())
        uow = fx_kit.uow()
        for at in (key, other_key):
            state = make_page_step_state(page_id=at.page_id, step_id=at.step_id, params={STRENGTH_PARAMETER: STRONGER})
            await uow.page_step_states.save(state)
            await uow.page_step_changes.add(
                evolve(
                    PageStepChange.between(
                        make_page_step_state(page_id=at.page_id, step_id=at.step_id),
                        state,
                        StepLayer.SETTINGS,
                        ChangeSource.CARRY_OVER,
                    ),
                    batch_id=batch,
                )
            )
        await uow.commit()
        await fx_kit.page_history().clear(actor, project.id, key)
        kept = await history_of(fx_kit, actor, project, other_key)
        undone = await fx_kit.page_history().undo(actor, project.id, other_key, None)
        expect(len(kept) == 1 and kept[0].batch_id == batch)
        expect([undo.page_id for undo in undone] == [other.id])
        expect(await fx_kit.uow().page_step_states.find(other_key) is None)
        assert_expectations()

    async def test_a_page_of_another_book_or_another_owner_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a clear on a page of another book, or by a stranger, is not found and deletes nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, _ = await ran_geometry(fx_kit)
        _, other_project = await fx_kit.seed_project()
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        for who, book in ((actor, other_project), (Actor(account_id=new_account_id()), project)):
            with pytest.raises(NotFoundError):
                await fx_kit.page_history().clear(who, book.id, key)
        assert len(await history_of(fx_kit, actor, project, key)) == 1

    async def test_a_clear_is_refused_while_the_project_is_processing_something_and_deletes_nothing(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Reject a clear while a job may be reading or deleting the versions, and leave the step as it was.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        written = len(await history_of(fx_kit, actor, project, key))
        await fx_kit.service().start_collection(actor, project.id)
        with pytest.raises(ConflictError, match='project is busy'):
            await fx_kit.page_history().clear(actor, project.id, key)
        stored = fx_kit.uow()
        expect(len(await history_of(fx_kit, actor, project, key)) == written)
        expect(await stored.page_step_states.find(key) is not None)
        expect(await stored.page_versions.find(version.id) is not None)
        expect(await has_files(fx_kit, project, version))
        assert_expectations()

    async def test_a_step_that_no_recipe_of_the_stage_has_is_not_found_and_deletes_nothing(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Reject a clear of a step that is in no recipe, as the listing of its versions does, and delete nothing.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, page, version = await ran_geometry(fx_kit)
        key = await step_key(fx_kit, page)
        await fx_kit.page_settings().change(actor, project.id, key, STRENGTH_PARAMETER, STRONGER)
        with pytest.raises(NotFoundError):
            await fx_kit.page_history().clear(actor, project.id, evolve(key, step_id=StepId(uuid4())))
        stored = fx_kit.uow()
        expect(len(await history_of(fx_kit, actor, project, key)) == 1)
        expect(await stored.page_step_states.find(key) is not None)
        expect(await stored.page_versions.find(version.id) is not None)
        assert_expectations()
