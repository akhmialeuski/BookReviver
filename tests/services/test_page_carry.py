"""Tests for carrying a setting of one page over to the following, the selected and the condition pages of its step."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import (
    AppliesTo,
    CarryScope,
    ChangeSource,
    EditorKind,
    PageKind,
    Stage,
    StageState,
    StepLayer,
)
from bookreviver.domain.errors import InvalidParametersError, NotFoundError
from bookreviver.domain.geometry import Rotation
from bookreviver.domain.ids import PageId, StepId
from bookreviver.domain.values import CarryRequest, NewPageEdit, PageStageKey, PageStepKey, RecipeDraft, StageRun, Step
from tests.helpers.builders import new_account_id
from tests.helpers.page_batches import carry_over
from tests.helpers.processors import FAILING_PARAMETER, STRENGTH_PARAMETER, FakeProcessor

if TYPE_CHECKING:
    from bookreviver.domain.entities import Page, Project
    from tests.helpers.processing import ProcessingKit

pytestmark = pytest.mark.anyio

FAKE_KEY: str = FakeProcessor.spec.key
CARRIED: int = 3
OWN: int = 5
ORDER_KEYS: tuple[str, ...] = ('a0', 'a1', 'a2', 'a3')
SOURCE_INDEX: int = 1
NOT_FAILING: bool = False
SHAPE_ANGLE: float = 1.5
OWN_ANGLE: float = 2.5
SHAPE: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=SHAPE_ANGLE))
OWN_SHAPE: NewPageEdit = NewPageEdit(kind=EditorKind.ROTATION, geometry=Rotation(degrees=OWN_ANGLE))


async def carry_book(kit: ProcessingKit) -> tuple[Actor, Project, list[Page], list[PageStepKey]]:
    """Seed a book of four text pages whose second page has changed the strength of the geometry step to a value.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the project, the pages and the key of the geometry step on each of them.
    :rtype: tuple[Actor, Project, list[Page], list[PageStepKey]]
    """
    actor, project = await kit.seed_project()
    pages = [(await kit.seed_scan_page(project, order_key=order_key))[0] for order_key in ORDER_KEYS]
    keys = [await kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY) for page in pages]
    await kit.page_settings().change(actor, project.id, keys[SOURCE_INDEX], STRENGTH_PARAMETER, CARRIED)
    return actor, project, pages, keys


async def strength_of(kit: ProcessingKit, key: PageStepKey) -> object:
    """Read the strength a page changes for the step, or None when it changes none.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The value the page stores for the field.
    :rtype: object
    """
    state = await kit.uow().page_step_states.find(key)
    return None if state is None else state.params.get(STRENGTH_PARAMETER)


def following(key: PageStepKey, *, overwrite: bool = False) -> CarryRequest:
    """Ask for the strength of a page to go to the pages after it.

    :param key: The source page, the stage and the step.
    :type key: PageStepKey
    :param overwrite: Whether a page with another value of its own takes the value too.
    :type overwrite: bool
    :returns: The request.
    :rtype: CarryRequest
    """
    return CarryRequest(key=key, name=STRENGTH_PARAMETER, scope=CarryScope.FOLLOWING, overwrite=overwrite)


class TestFollowing:
    """Tests for the carry-over to the pages after the source."""

    async def test_the_pages_after_the_source_take_the_value_and_the_pages_before_do_not(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the value reaches the two pages after the source, and neither the page before it nor the source.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        carried = await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX]))
        expect(sorted(change.page_id for change in carried.changes) == sorted(page.id for page in pages[2:]))
        expect([await strength_of(fx_kit, key) for key in keys] == [None, CARRIED, CARRIED, CARRIED])
        expect(carried.skipped == ())
        assert_expectations()

    async def test_each_change_is_a_carry_over_of_the_settings_layer_in_one_batch(self, fx_kit: ProcessingKit) -> None:
        """Verify the history of each target has one change from a carry-over, which all targets share the batch of.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        carried = await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX]))
        listed = [(await fx_kit.page_history().list(actor, project.id, key)).changes for key in keys[2:]]
        expect(all(len(changes) == 1 for changes in listed))
        expect({changes[0].batch_id for changes in listed} == {carried.batch_id})
        expect(all(changes[0].source is ChangeSource.CARRY_OVER for changes in listed))
        expect(all(changes[0].layer is StepLayer.SETTINGS for changes in listed))
        expect(all(changes[0].before is None for changes in listed))
        expect(all(changes[0].after == {STRENGTH_PARAMETER: CARRIED} for changes in listed))
        assert_expectations()

    async def test_the_other_fields_of_a_target_stay_as_they_are(self, fx_kit: ProcessingKit) -> None:
        """Verify a target that changes another field of the step keeps it, and the carried field joins it.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, keys[2], FAILING_PARAMETER, NOT_FAILING)
        await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX]))
        state = await fx_kit.uow().page_step_states.get(keys[2])
        assert state.params == {FAILING_PARAMETER: NOT_FAILING, STRENGTH_PARAMETER: CARRIED}

    async def test_the_stage_of_a_page_that_took_the_value_goes_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that was run has its stage marked stale by the carry-over, and the source page is left fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        for page in pages:
            await fx_kit.seed_base_version(page)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.jobs().run_stage(job.id)
        await fx_kit.work_queue()
        await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX]))
        states = [(await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).state for page in pages]
        assert states == [StageState.FRESH, StageState.FRESH, StageState.STALE, StageState.STALE]


class TestOwnValues:
    """Tests for the pages that have a value of their own for the field."""

    async def test_a_page_with_another_value_of_its_own_is_skipped_and_counted(self, fx_kit: ProcessingKit) -> None:
        """Verify the page keeps its value, is listed as skipped, and the pages without one take the value.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, keys[2], STRENGTH_PARAMETER, OWN)
        carried = await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX]))
        expect(carried.skipped == (pages[2].id,))
        expect([change.page_id for change in carried.changes] == [pages[3].id])
        expect([await strength_of(fx_kit, key) for key in keys[2:]] == [OWN, CARRIED])
        assert_expectations()

    async def test_a_page_with_the_value_already_is_neither_changed_nor_skipped(self, fx_kit: ProcessingKit) -> None:
        """Verify a page whose own value equals the carried one writes no change and is not counted as skipped.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, keys[2], STRENGTH_PARAMETER, CARRIED)
        carried = await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX]))
        expect(carried.skipped == ())
        expect([change.page_id for change in carried.changes] == [pages[3].id])
        assert_expectations()

    async def test_the_choice_to_overwrite_writes_over_the_value_of_a_page(self, fx_kit: ProcessingKit) -> None:
        """Verify a carry-over that overwrites takes the page along, with the value it had as the change before.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, keys[2], STRENGTH_PARAMETER, OWN)
        carried = await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX], overwrite=True))
        written = next(change for change in carried.changes if change.page_id == pages[2].id)
        expect(carried.skipped == ())
        expect((written.before, written.after) == ({STRENGTH_PARAMETER: OWN}, {STRENGTH_PARAMETER: CARRIED}))
        expect([await strength_of(fx_kit, key) for key in keys[2:]] == [CARRIED, CARRIED])
        assert_expectations()


class TestSelected:
    """Tests for the carry-over to the pages the user selected."""

    async def test_only_the_selected_pages_take_the_value(self, fx_kit: ProcessingKit) -> None:
        """Verify the pages named take the value, the source among them is left out, and the others are untouched.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        request = CarryRequest(
            key=keys[SOURCE_INDEX],
            name=STRENGTH_PARAMETER,
            scope=CarryScope.SELECTED,
            page_ids=(pages[0].id, pages[SOURCE_INDEX].id, pages[3].id),
        )
        carried = await carry_over(fx_kit).carry(actor, project.id, request)
        expect(sorted(change.page_id for change in carried.changes) == sorted([pages[0].id, pages[3].id]))
        expect([await strength_of(fx_kit, key) for key in keys] == [CARRIED, CARRIED, None, CARRIED])
        assert_expectations()

    async def test_a_carry_over_to_no_selected_page_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Verify the scope of the selected pages with no page named is refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        request = CarryRequest(key=keys[SOURCE_INDEX], name=STRENGTH_PARAMETER, scope=CarryScope.SELECTED)
        with pytest.raises(InvalidParametersError):
            await carry_over(fx_kit).carry(actor, project.id, request)

    async def test_a_page_of_another_book_is_not_found_and_nothing_is_written(self, fx_kit: ProcessingKit) -> None:
        """Verify a selected page the project does not have is refused before any page takes the value.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        _, other_project = await fx_kit.seed_project()
        stranger_page, _ = await fx_kit.seed_scan_page(other_project)
        request = CarryRequest(
            key=keys[SOURCE_INDEX],
            name=STRENGTH_PARAMETER,
            scope=CarryScope.SELECTED,
            page_ids=(pages[3].id, stranger_page.id),
        )
        with pytest.raises(NotFoundError):
            await carry_over(fx_kit).carry(actor, project.id, request)
        assert await strength_of(fx_kit, keys[3]) is None


class TestCondition:
    """Tests for the carry-over to the pages the step processes."""

    async def test_the_pages_the_condition_leaves_out_do_not_take_the_value(self, fx_kit: ProcessingKit) -> None:
        """Verify a step for text takes the value to every text page of the book, before and after, and not a plate.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        text = [(await fx_kit.seed_scan_page(project, order_key=order_key))[0] for order_key in ('a0', 'a2', 'a3')]
        plate, _ = await fx_kit.seed_scan_page(project, order_key='a1', kind=PageKind.PLATE)
        draft = RecipeDraft(
            name='Text', steps=[Step(processor_key=FAKE_KEY, params={STRENGTH_PARAMETER: 1}, applies_to=AppliesTo.TEXT)]
        )
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, draft)
        keys = [await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY) for page in (*text, plate)]
        await fx_kit.page_settings().change(actor, project.id, keys[1], STRENGTH_PARAMETER, CARRIED)
        request = CarryRequest(key=keys[1], name=STRENGTH_PARAMETER, scope=CarryScope.CONDITION)
        carried = await carry_over(fx_kit).carry(actor, project.id, request)
        expect(sorted(change.page_id for change in carried.changes) == sorted([text[0].id, text[2].id]))
        expect([await strength_of(fx_kit, key) for key in keys] == [CARRIED, CARRIED, CARRIED, None])
        assert_expectations()

    async def test_a_step_for_everything_takes_every_other_page_of_the_book(self, fx_kit: ProcessingKit) -> None:
        """Verify the pages before the source are reached by this scope, which the following pages do not reach.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        request = CarryRequest(key=keys[SOURCE_INDEX], name=STRENGTH_PARAMETER, scope=CarryScope.CONDITION)
        await carry_over(fx_kit).carry(actor, project.id, request)
        assert [await strength_of(fx_kit, key) for key in keys] == [CARRIED] * len(keys)


class TestUndoOfACarryOver:
    """Tests for taking a carry-over back, which is one action by the undo of the history."""

    async def test_one_undo_takes_the_value_back_from_every_page(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo on one target page takes the batch back on all targets, and the source page keeps its value.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        carried = await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX]))
        undone = await fx_kit.page_history().undo(actor, project.id, keys[2], carried.changes[0].id)
        expect(sorted(undo.page_id for undo in undone) == sorted(page.id for page in pages[2:]))
        expect(len({undo.batch_id for undo in undone}) == 1 and undone[0].batch_id != carried.batch_id)
        expect([await strength_of(fx_kit, key) for key in keys] == [None, CARRIED, None, None])
        assert_expectations()

    async def test_the_value_a_page_had_before_comes_back(self, fx_kit: ProcessingKit) -> None:
        """Verify the undo of a carry-over that overwrote puts the value of the page back.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, keys[2], STRENGTH_PARAMETER, OWN)
        await carry_over(fx_kit).carry(actor, project.id, following(keys[SOURCE_INDEX], overwrite=True))
        await fx_kit.page_history().undo(actor, project.id, keys[3], None)
        assert [await strength_of(fx_kit, key) for key in keys[2:]] == [OWN, None]


class TestRefusals:
    """Tests for the requests a carry-over refuses."""

    async def test_a_source_that_does_not_change_the_field_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that keeps the value of the recipe has nothing to carry.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        with pytest.raises(NotFoundError):
            await carry_over(fx_kit).carry(actor, project.id, following(keys[0]))

    async def test_a_stranger_and_a_step_no_recipe_has_are_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the project of another account and a step of no recipe are refused, and nothing is written.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        unknown = evolve(keys[SOURCE_INDEX], step_id=StepId(uuid4()))
        attempts = ((Actor(account_id=new_account_id()), keys[SOURCE_INDEX]), (actor, unknown))
        for who, key in attempts:
            with pytest.raises(NotFoundError):
                await carry_over(fx_kit).carry(who, project.id, following(key))
        assert [await strength_of(fx_kit, key) for key in keys] == [None, CARRIED, None, None]


async def angle_of(kit: ProcessingKit, key: PageStepKey) -> float | None:
    """Read the angle of the rotation a page has set by hand for the step, or None when it set no shape.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The angle in degrees.
    :rtype: float | None
    """
    state = await kit.uow().page_step_states.find(key)
    if state is None or state.edit is None or not isinstance(state.edit.geometry, Rotation):
        return None
    return state.edit.geometry.degrees


def shape_of(key: PageStepKey, scope: CarryScope = CarryScope.FOLLOWING, *, overwrite: bool = False) -> CarryRequest:
    """Ask for the shape a page set by hand to go to other pages.

    :param key: The source page, the stage and the step.
    :type key: PageStepKey
    :param scope: The pages the shape goes to.
    :type scope: CarryScope
    :param overwrite: Whether a page with a shape of its own takes this one too.
    :type overwrite: bool
    :returns: The request.
    :rtype: CarryRequest
    """
    return CarryRequest(key=key, layer=StepLayer.HAND, scope=scope, overwrite=overwrite)


class TestShapeSetByHand:
    """Tests for the carry-over of the shape a page set by hand, which is the manual layer of the step."""

    async def test_the_following_pages_take_the_whole_shape_and_keep_their_settings(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the pages after the source get its shape, the earlier ones do not, and no setting is touched.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.page_settings().change(actor, project.id, keys[2], FAILING_PARAMETER, NOT_FAILING)
        await fx_kit.edits().save(actor, project.id, keys[SOURCE_INDEX], SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX]))
        expect(sorted(change.page_id for change in carried.changes) == sorted(page.id for page in pages[2:]))
        expect([await angle_of(fx_kit, key) for key in keys] == [None, SHAPE_ANGLE, SHAPE_ANGLE, SHAPE_ANGLE])
        expect((await fx_kit.uow().page_step_states.get(keys[2])).params == {FAILING_PARAMETER: NOT_FAILING})
        expect(await strength_of(fx_kit, keys[3]) is None)
        expect(all(change.layer is StepLayer.HAND for change in carried.changes))
        expect(all(change.source is ChangeSource.CARRY_OVER for change in carried.changes))
        assert_expectations()

    async def test_a_page_set_by_hand_separately_is_skipped_and_counted(self, fx_kit: ProcessingKit) -> None:
        """Verify a page with a shape of its own keeps it, is listed as skipped, and the other pages take the shape.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, project.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, project.id, keys[2], OWN_SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX]))
        expect(carried.skipped == (pages[2].id,))
        expect([change.page_id for change in carried.changes] == [pages[3].id])
        expect([await angle_of(fx_kit, key) for key in keys[2:]] == [OWN_ANGLE, SHAPE_ANGLE])
        assert_expectations()

    async def test_a_page_with_the_same_shape_is_neither_written_nor_skipped(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that set the very same shape writes no change and is not counted as skipped.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, project.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, project.id, keys[2], SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX]))
        expect(carried.skipped == ())
        expect([change.page_id for change in carried.changes] == [pages[3].id])
        assert_expectations()

    async def test_the_choice_to_overwrite_takes_the_page_with_a_shape_of_its_own_along(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify an overwriting carry-over replaces the shape of a page, with the old shape as the change before.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, project.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, project.id, keys[2], OWN_SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX], overwrite=True))
        written = next(change for change in carried.changes if change.page_id == pages[2].id)
        expect(carried.skipped == ())
        expect(written.before is not None and written.after is not None and written.before != written.after)
        expect([await angle_of(fx_kit, key) for key in keys[2:]] == [SHAPE_ANGLE, SHAPE_ANGLE])
        assert_expectations()

    async def test_the_condition_scope_reaches_the_pages_before_the_source_too(self, fx_kit: ProcessingKit) -> None:
        """Verify the scope of the condition gives the shape to every other page the step processes.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, project.id, keys[SOURCE_INDEX], SHAPE, None)
        await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX], CarryScope.CONDITION))
        assert [await angle_of(fx_kit, key) for key in keys] == [SHAPE_ANGLE] * len(keys)

    async def test_the_pages_the_condition_leaves_out_do_not_take_the_shape(self, fx_kit: ProcessingKit) -> None:
        """Verify a step for text gives the shape to the text pages of the book and not to a plate.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project = await fx_kit.seed_project()
        text = [(await fx_kit.seed_scan_page(project, order_key=order_key))[0] for order_key in ('a0', 'a2')]
        plate, _ = await fx_kit.seed_scan_page(project, order_key='a1', kind=PageKind.PLATE)
        draft = RecipeDraft(name='Text', steps=[Step(processor_key=FAKE_KEY, applies_to=AppliesTo.TEXT)])
        await fx_kit.service().save_recipe(actor, project.id, Stage.GEOMETRY, draft)
        keys = [await fx_kit.edit_key(page, Stage.GEOMETRY, FAKE_KEY) for page in (*text, plate)]
        await fx_kit.edits().save(actor, project.id, keys[0], SHAPE, None)
        await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[0], CarryScope.CONDITION))
        assert [await angle_of(fx_kit, key) for key in keys] == [SHAPE_ANGLE, SHAPE_ANGLE, None]

    async def test_one_undo_takes_the_shape_back_from_every_page(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo on one target takes the batch back everywhere, and the source keeps its shape.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, project.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, project.id, keys[2], OWN_SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX], overwrite=True))
        undone = await fx_kit.page_history().undo(actor, project.id, keys[3], carried.changes[0].id)
        expect(sorted(undo.page_id for undo in undone) == sorted(page.id for page in pages[2:]))
        expect([await angle_of(fx_kit, key) for key in keys] == [None, SHAPE_ANGLE, OWN_ANGLE, None])
        assert_expectations()

    async def test_the_stage_of_a_page_that_took_the_shape_goes_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a run page that takes the shape has its stage marked stale, and the source page stays fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, pages, keys = await carry_book(fx_kit)
        for page in pages:
            await fx_kit.seed_base_version(page)
        await fx_kit.edits().save(actor, project.id, keys[SOURCE_INDEX], SHAPE, None)
        job = await fx_kit.service().start_run(actor, project.id, Stage.GEOMETRY, StageRun(stage=Stage.GEOMETRY))
        await fx_kit.jobs().run_stage(job.id)
        await fx_kit.work_queue()
        await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX]))
        states = [(await fx_kit.uow().page_stages.get(PageStageKey(page.id, Stage.GEOMETRY))).state for page in pages]
        assert states == [StageState.FRESH, StageState.FRESH, StageState.STALE, StageState.STALE]

    async def test_a_source_with_no_shape_of_its_own_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that set no shape by hand has nothing to carry, though it changes a setting.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, project, _, keys = await carry_book(fx_kit)
        with pytest.raises(NotFoundError):
            await carry_over(fx_kit).carry(actor, project.id, shape_of(keys[SOURCE_INDEX]))

    async def test_a_request_must_name_a_field_exactly_when_it_carries_the_settings(self) -> None:
        """Verify the request for the settings with no field and the request for the shape with one are not built."""
        key = PageStepKey(PageId(uuid4()), Stage.GEOMETRY, StepId(uuid4()))
        with pytest.raises(ValueError, match='exactly when'):
            CarryRequest(key=key, scope=CarryScope.FOLLOWING)
        with pytest.raises(ValueError, match='exactly when'):
            CarryRequest(key=key, layer=StepLayer.HAND, name=STRENGTH_PARAMETER, scope=CarryScope.FOLLOWING)
