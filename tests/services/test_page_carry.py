"""Tests for carrying the shape a page set by hand over to the following, the selected and the same kind pages."""

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from attrs import evolve
from delayed_assert import assert_expectations, expect

from bookreviver.domain.entities import Actor
from bookreviver.domain.enums import (
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
from bookreviver.domain.ids import StepId
from bookreviver.domain.values import CarryRequest, NewPageEdit, StageRun
from tests.helpers.builders import new_account_id
from tests.helpers.page_batches import PageValues, carry_over
from tests.helpers.processors import FAILING_PARAMETER, STRENGTH_PARAMETER, FakeProcessor
from tests.helpers.spreads import run_stage, stage_of

if TYPE_CHECKING:
    from bookreviver.domain.entities import BookRecord, Page
    from bookreviver.domain.ids import BookId
    from bookreviver.domain.values import PageStepKey
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


async def carry_book(kit: ProcessingKit) -> tuple[Actor, BookRecord, list[Page], list[PageStepKey]]:
    """Seed a book of four text pages whose second page has changed the strength of the geometry step to a value.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :returns: The actor, the book, the pages and the key of the geometry step on each of them.
    :rtype: tuple[Actor, BookRecord, list[Page], list[PageStepKey]]
    """
    actor, book_record = await kit.seed_record()
    pages = [(await kit.seed_scan_page(book_record, order_key=order_key))[0] for order_key in ORDER_KEYS]
    keys = [await kit.edit_key(book_record.id, page, Stage.GEOMETRY, FAKE_KEY) for page in pages]
    await PageValues(kit, actor, book_record.id).set(keys[SOURCE_INDEX], STRENGTH_PARAMETER, CARRIED)
    return actor, book_record, pages, keys


async def strength_of(kit: ProcessingKit, book_id: BookId, key: PageStepKey) -> object:
    """Read the strength a page changes for the step, or None when it changes none.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Book that holds the page.
    :type book_id: BookId
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The value the page stores for the field.
    :rtype: object
    """
    state = await kit.uow().read_book(book_id, lambda repos: repos.page_step_states.find(key))
    return None if state is None else state.params.get(STRENGTH_PARAMETER)


async def angle_of(kit: ProcessingKit, book_id: BookId, key: PageStepKey) -> float | None:
    """Read the angle of the rotation a page has set by hand for the step, or None when it set no shape.

    :param kit: What the processing services of the test share.
    :type kit: ProcessingKit
    :param book_id: Book that holds the page.
    :type book_id: BookId
    :param key: The page, the stage and the step.
    :type key: PageStepKey
    :returns: The angle in degrees.
    :rtype: float | None
    """
    state = await kit.uow().read_book(book_id, lambda repos: repos.page_step_states.find(key))
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
    return CarryRequest(key=key, scope=scope, overwrite=overwrite)


class TestShapeSetByHand:
    """Tests for the carry-over of the shape a page set by hand, which is the manual layer of the step."""

    async def test_the_following_pages_take_the_whole_shape_and_keep_their_settings(
        self, fx_kit: ProcessingKit
    ) -> None:
        """Verify the pages after the source get its shape, the earlier ones do not, and no setting is touched.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages, keys = await carry_book(fx_kit)
        await PageValues(fx_kit, actor, book_record.id).set(keys[2], FAILING_PARAMETER, NOT_FAILING)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX]))
        expect(sorted(change.page_id for change in carried.changes) == sorted(page.id for page in pages[2:]))
        expect(
            [await angle_of(fx_kit, book_record.id, key) for key in keys]
            == [None, SHAPE_ANGLE, SHAPE_ANGLE, SHAPE_ANGLE]
        )
        own = await fx_kit.uow().read_book(book_record.id, lambda repos: repos.page_step_states.get(keys[2]))
        expect(own.params == {FAILING_PARAMETER: NOT_FAILING})
        expect(await strength_of(fx_kit, book_record.id, keys[3]) is None)
        expect(all(change.layer is StepLayer.HAND for change in carried.changes))
        expect(all(change.source is ChangeSource.CARRY_OVER for change in carried.changes))
        assert_expectations()

    async def test_a_page_set_by_hand_separately_is_skipped_and_counted(self, fx_kit: ProcessingKit) -> None:
        """Verify a page with a shape of its own keeps it, is listed as skipped, and the other pages take the shape.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, book_record.id, keys[2], OWN_SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX]))
        expect(carried.skipped == (pages[2].id,))
        expect([change.page_id for change in carried.changes] == [pages[3].id])
        expect([await angle_of(fx_kit, book_record.id, key) for key in keys[2:]] == [OWN_ANGLE, SHAPE_ANGLE])
        assert_expectations()

    async def test_a_page_with_the_same_shape_is_neither_written_nor_skipped(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that set the very same shape writes no change and is not counted as skipped.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, book_record.id, keys[2], SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX]))
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
        actor, book_record, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, book_record.id, keys[2], OWN_SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX], overwrite=True))
        written = next(change for change in carried.changes if change.page_id == pages[2].id)
        expect(carried.skipped == ())
        expect(written.before is not None and written.after is not None and written.before != written.after)
        expect([await angle_of(fx_kit, book_record.id, key) for key in keys[2:]] == [SHAPE_ANGLE, SHAPE_ANGLE])
        assert_expectations()

    async def test_the_kind_scope_reaches_the_pages_before_the_source_too(self, fx_kit: ProcessingKit) -> None:
        """Verify the scope of the kind gives the shape to every other page the step processes.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX], CarryScope.KIND))
        assert [await angle_of(fx_kit, book_record.id, key) for key in keys] == [SHAPE_ANGLE] * len(keys)

    async def test_the_pages_of_other_kinds_do_not_take_the_shape(self, fx_kit: ProcessingKit) -> None:
        """Verify the shape goes to the text pages of the book and not to a plate.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record = await fx_kit.seed_record()
        text = [(await fx_kit.seed_scan_page(book_record, order_key=order_key))[0] for order_key in ('a0', 'a2')]
        plate, _ = await fx_kit.seed_scan_page(book_record, order_key='a1', kind=PageKind.PLATE)
        keys = [await fx_kit.edit_key(book_record.id, page, Stage.GEOMETRY, FAKE_KEY) for page in (*text, plate)]
        await fx_kit.edits().save(actor, book_record.id, keys[0], SHAPE, None)
        await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[0], CarryScope.KIND))
        assert [await angle_of(fx_kit, book_record.id, key) for key in keys] == [SHAPE_ANGLE, SHAPE_ANGLE, None]

    async def test_one_undo_takes_the_shape_back_from_every_page(self, fx_kit: ProcessingKit) -> None:
        """Verify an undo on one target takes the batch back everywhere, and the source keeps its shape.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        await fx_kit.edits().save(actor, book_record.id, keys[2], OWN_SHAPE, None)
        carried = await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX], overwrite=True))
        # The undo is asked on the last page, so it names the change written there
        last = next(change for change in carried.changes if change.page_id == pages[3].id)
        undone = await fx_kit.page_history().undo(actor, book_record.id, keys[3], last.id)
        expect(sorted(undo.page_id for undo in undone) == sorted(page.id for page in pages[2:]))
        expect([await angle_of(fx_kit, book_record.id, key) for key in keys] == [None, SHAPE_ANGLE, OWN_ANGLE, None])
        assert_expectations()

    async def test_the_stage_of_a_page_that_took_the_shape_goes_stale(self, fx_kit: ProcessingKit) -> None:
        """Verify a run page that takes the shape has its stage marked stale, and the source page stays fresh.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages, keys = await carry_book(fx_kit)
        for page in pages:
            await fx_kit.seed_base_version(book_record.id, page)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        await run_stage(fx_kit, actor, book_record, StageRun(stage=Stage.GEOMETRY))
        await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX]))
        states = [(await stage_of(fx_kit, book_record.id, page, Stage.GEOMETRY)).state for page in pages]
        assert states == [StageState.FRESH, StageState.FRESH, StageState.STALE, StageState.STALE]

    async def test_a_source_with_no_shape_of_its_own_is_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify a page that set no shape by hand has nothing to carry, though it changes a setting.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, keys = await carry_book(fx_kit)
        with pytest.raises(NotFoundError):
            await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX]))


class TestSelected:
    """Tests for the carry-over to the pages the user selected."""

    async def test_only_the_selected_pages_take_the_shape(self, fx_kit: ProcessingKit) -> None:
        """Verify the pages named take the shape, the source among them is left out, and the others are untouched.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        request = CarryRequest(
            key=keys[SOURCE_INDEX],
            scope=CarryScope.SELECTED,
            page_ids=(pages[0].id, pages[SOURCE_INDEX].id, pages[3].id),
        )
        carried = await carry_over(fx_kit).carry(actor, book_record.id, request)
        expect(sorted(change.page_id for change in carried.changes) == sorted([pages[0].id, pages[3].id]))
        expect(
            [await angle_of(fx_kit, book_record.id, key) for key in keys]
            == [SHAPE_ANGLE, SHAPE_ANGLE, None, SHAPE_ANGLE]
        )
        assert_expectations()

    async def test_a_carry_over_to_no_selected_page_is_refused(self, fx_kit: ProcessingKit) -> None:
        """Verify the scope of the selected pages with no page named is refused.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        with pytest.raises(InvalidParametersError):
            await carry_over(fx_kit).carry(actor, book_record.id, shape_of(keys[SOURCE_INDEX], CarryScope.SELECTED))

    async def test_a_page_of_another_book_is_not_found_and_nothing_is_written(self, fx_kit: ProcessingKit) -> None:
        """Verify a selected page the book does not have is refused before any page takes the shape.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, pages, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        _, other_record = await fx_kit.seed_record()
        stranger_page, _ = await fx_kit.seed_scan_page(other_record)
        request = CarryRequest(
            key=keys[SOURCE_INDEX], scope=CarryScope.SELECTED, page_ids=(pages[3].id, stranger_page.id)
        )
        with pytest.raises(NotFoundError):
            await carry_over(fx_kit).carry(actor, book_record.id, request)
        assert await angle_of(fx_kit, book_record.id, keys[3]) is None


class TestRefusals:
    """Tests for the requests a carry-over refuses."""

    async def test_a_stranger_and_a_step_no_recipe_has_are_not_found(self, fx_kit: ProcessingKit) -> None:
        """Verify the book of another account and a step of no recipe are refused, and nothing is written.

        :param fx_kit: What the processing services of the test share.
        :type fx_kit: ProcessingKit
        """
        actor, book_record, _, keys = await carry_book(fx_kit)
        await fx_kit.edits().save(actor, book_record.id, keys[SOURCE_INDEX], SHAPE, None)
        unknown = evolve(keys[SOURCE_INDEX], step_id=StepId(uuid4()))
        attempts = ((Actor(account_id=new_account_id()), keys[SOURCE_INDEX]), (actor, unknown))
        for who, key in attempts:
            with pytest.raises(NotFoundError):
                await carry_over(fx_kit).carry(who, book_record.id, shape_of(key))
        assert [await angle_of(fx_kit, book_record.id, key) for key in keys] == [None, SHAPE_ANGLE, None, None]
